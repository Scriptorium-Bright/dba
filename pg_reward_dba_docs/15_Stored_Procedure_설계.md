# 15. Stored Procedure 상세 설계 및 호출 예시

## 1. 목적

이 문서는 05 테이블 설계 기준으로 `CLAIM`과 `BATCH`의 지급을 하나의 프로시저에 모으는 설계와 구현 결과를 기록한다. 예전 `reward_claims` 전용 SQL과는 이름/입출력이 다르다. 실제 적용본은 [`V3__reward_procedure_and_batch_runs.sql`](../database/migrations/V3__reward_procedure_and_batch_runs.sql)이며 PostgreSQL 17.12에서 적용하고 P2 acceptance 흐름을 실행했다. 아래의 긴 SQL은 계약을 설명하는 설계 예시다.

**실행 확인:** 100개 동일 키 요청은 `PAID=1`, `REPLAYED=99`; 새 키 중복은 `ALREADY_GRANTED`; 다른 사용자에 request ID 재사용은 HTTP 409. 원장 실패/호출자 `ROLLBACK` 뒤 grant 잔존 0과 지갑 불변, BATCH 1,000명 지급 후 crash recovery 및 남은 대상 재처리도 확인했다. 상세 출력과 미검증 범위는 [구현 기록](implementation/exec.md)에 있다.

## 2. 입출력 계약

- Procedure: `grant_event_reward`
- 입력: `p_event_id`, `p_user_id`, `p_request_id`, `p_trigger`(`CLAIM` 또는 `BATCH`)
- 출력(`INOUT`): `p_result` (`PAID`/`REPLAYED`/`ALREADY_GRANTED`/`NOT_AVAILABLE`/`EVENT_NOT_FOUND`/`REQUEST_ID_CONFLICT`), `p_grant_id`
- 호출 트랜잭션: Spring Boot API 또는 배치 작업자가 `BEGIN/COMMIT` 소유.
- 지갑 없음이나 SQL 오류는 예외로 발생시켜 **호출 트랜잭션 전체를 롤백**한다. 다른 대상에 이미 사용한 request ID는 `REQUEST_ID_CONFLICT` 결과를 반환하며 API가 HTTP 409로 바꾼다. 이 경우 DB 변경은 없다.
- `p_trigger`는 HTTP 클라이언트의 임의 입력이 아닌 인증된 서버 경로가 지정. 사용자 요청에서 `BATCH`로 위장하지 못하도록 엔드포인트 권한 분리.

## 3. 전체 Procedure 초안

```sql
CREATE OR REPLACE PROCEDURE grant_event_reward(
    IN p_event_id BIGINT,
    IN p_user_id BIGINT,
    IN p_request_id UUID,
    IN p_trigger TEXT,
    INOUT p_result TEXT,
    INOUT p_grant_id BIGINT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_event events%ROWTYPE;
    v_grant reward_grants%ROWTYPE;
    v_money BIGINT;
    v_point BIGINT;
BEGIN
    p_result := NULL;
    p_grant_id := NULL;

    IF p_request_id IS NULL OR p_trigger IS NULL
       OR p_trigger NOT IN ('CLAIM', 'BATCH') THEN
        RAISE EXCEPTION 'invalid request_id or trigger';
    END IF;

    -- 1. 요청 ID가 이미 사용되었다면 대상 일치 여부 확인.
    SELECT * INTO v_grant
    FROM reward_grants
    WHERE request_id = p_request_id;

    IF FOUND THEN
        IF v_grant.event_id <> p_event_id OR v_grant.user_id <> p_user_id THEN
            p_result := 'REQUEST_ID_CONFLICT';
            RETURN;
        END IF;
        p_result := 'REPLAYED';
        p_grant_id := v_grant.id;
        RETURN;
    END IF;

    -- 2. 동일 이벤트의 기존 지급 확인.
    SELECT * INTO v_grant
    FROM reward_grants
    WHERE event_id = p_event_id AND user_id = p_user_id;

    IF FOUND THEN
        p_result := 'ALREADY_GRANTED';
        p_grant_id := v_grant.id;
        RETURN;
    END IF;

    -- 3. 이벤트 설정 읽기. 이벤트 정책 UPDATE와 경합을 관리.
    SELECT * INTO v_event
    FROM events
    WHERE id = p_event_id
    FOR SHARE;

    IF NOT FOUND THEN
        p_result := 'EVENT_NOT_FOUND';
        RETURN;
    END IF;

    -- 4. 이벤트 정책과 지급 방식 검증.
    IF v_event.status <> 'OPEN' OR v_event.mode <> p_trigger THEN
        p_result := 'NOT_AVAILABLE';
        RETURN;
    END IF;

    IF p_trigger = 'CLAIM' THEN
        IF clock_timestamp() < v_event.starts_at
           OR clock_timestamp() >= v_event.ends_at THEN
            p_result := 'NOT_AVAILABLE';
            RETURN;
        END IF;
    ELSE
        IF clock_timestamp() < v_event.scheduled_at
           OR NOT EXISTS (
               SELECT 1
               FROM event_recipients
               WHERE event_id = p_event_id
                 AND user_id = p_user_id
           ) THEN
            p_result := 'NOT_AVAILABLE';
            RETURN;
        END IF;
    END IF;

    v_money := v_event.reward_money;
    v_point := v_event.reward_point;

    -- 5. 실제 지급 기록 생성: 여기서 새 ID를 변수에 받는다.
    INSERT INTO reward_grants (
        event_id, user_id, request_id, money_awarded, points_awarded
    ) VALUES (
        p_event_id, p_user_id, p_request_id, v_money, v_point
    )
    ON CONFLICT (event_id, user_id) DO NOTHING
    RETURNING id INTO p_grant_id;

    -- 6. 동시 요청에서 다른 Tx가 먼저 지급한 경우.
    IF p_grant_id IS NULL THEN
        -- READ COMMITTED에서 새 명령의 Snapshot으로 확인.
        SELECT * INTO v_grant
        FROM reward_grants
        WHERE event_id = p_event_id AND user_id = p_user_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'conflict but existing grant missing';
        END IF;

        p_grant_id := v_grant.id;
        p_result := CASE
            WHEN v_grant.request_id = p_request_id THEN 'REPLAYED'
            ELSE 'ALREADY_GRANTED'
        END;
        RETURN;
    END IF;

    -- 7. 지갑의 두 재화를 한 행에 갱신.
    UPDATE wallets
    SET money_balance = money_balance + v_money,
        point_balance = point_balance + v_point
    WHERE user_id = p_user_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'wallet missing for user %', p_user_id;
    END IF;

    -- 8. 원장 기록. INSERT가 반환한 실제 grant_id 사용.
    IF v_money > 0 THEN
        INSERT INTO wallet_ledger (
            user_id, reward_grant_id, currency_type, amount
        ) VALUES (
            p_user_id, p_grant_id, 'MONEY', v_money
        );
    END IF;

    IF v_point > 0 THEN
        INSERT INTO wallet_ledger (
            user_id, reward_grant_id, currency_type, amount
        ) VALUES (
            p_user_id, p_grant_id, 'POINT', v_point
        );
    END IF;

    p_result := 'PAID';
END;
$$;
```

**경계조건 메모:** 적용본은 기본 `READ COMMITTED`와 요청별 `request_id` 유일성 전제를 사용한다. 100개 동시 CLAIM, 동일 ID 재시도, 다른 ID 중복, 타 사용자 ID 충돌, 원장 실패를 실험했다. 이벤트 종료와 지급의 정확한 Lock 경합 타이밍, 네트워크 응답이 COMMIT 뒤 사라지는 장애, 더 높은 격리 수준은 별도 검증 범위다.

## 4. 호출 방식

```sql
BEGIN;
CALL grant_event_reward(
    1,
    101,
    '00000000-0000-0000-0000-000000000001',
    'CLAIM',
    NULL,
    NULL
);
COMMIT;
```

성공적으로 새로 INSERT한 행 ID가 37이라면 `p_grant_id=37`; 원장 Money/Point 행의 `reward_grant_id`도 37이 된다. **가정 결과**다.

Spring Boot에서는 `@Transactional` 범위로 호출하고, API 성공은 COMMIT 완료 후 반환한다. 한 명의 Batch Worker 호출 또한 같은 SP를 사용하며 `p_trigger='BATCH'`, request_id는 대상자/이벤트별로 안정적이어야 한다.

## 5. 재시도·중복 지급의 예시

| 호출 | 기대 SP 결과 | 실제 추가 지급 |
|---|---|---|
| 최초 `event=1,user=101,request=A` | PAID, `grant_id=37` (예시) | Money/Point 1회 |
| 같은 A 재시도 | REPLAYED, `grant_id=37` | 0 |
| 다른 B로 같은 이벤트·사용자 | ALREADY_GRANTED, `grant_id=37` | 0 |
| 이벤트 2, 사용자 101, 요청 C | PAID, 다른 grant_id | 이벤트2 지급 1회 |
| 다른 사용자에 요청 A 재사용 | `REQUEST_ID_CONFLICT` / HTTP 409 | 0 |
| 원장 INSERT에서 예외 | 전체 트랜잭션 ROLLBACK | 0 |

## 6. SQL Server와의 관계

펄어비스 등 SQL Server 업무를 위해 SP 개념은 유효하지만 실제 문법은 T-SQL과 다르다. PostgreSQL `CREATE PROCEDURE`/`CALL`, `PL/pgSQL`, `RETURNING INTO` 사용 경험을 SQL Server 직접 실습 경험이라고 표현하지 않는다.

## 7. 로컬 지갑 초기화 함수 (V5)

V5는 요청을 다시 보내는 UI 실습을 추가하지 않는다. 사용자가 이벤트/API에서 직접 확인할 수 있도록 현재 데모 사용자 한 명 또는 전체 DB 사용자의 지갑을 초기화한다.

- `reward.reset_local_user_wallet(user_id)`는 해당 사용자의 Money·Point 잔액을 0으로 만들고 모든 `reward_grants`와 `wallet_ledger` 행을 지운다.
- `reward.reset_all_local_wallets()`는 DB의 모든 사용자와 지급 기록을 같은 방식으로 초기화한다. P2 실험 사용자를 포함한다.
- 사용자·이벤트·BATCH 대상 명단·`batch_runs` 실행 이력은 보존한다. 전체 DB 볼륨 초기화와 목적 및 범위를 구분한다.
- 지급 프로시저와 같은 순서로 이벤트 행과 지갑을 잠가 동시 지급이 초기화 경계에 끼어들지 않게 한다.
- BatchService가 이벤트별 세션 advisory lock을 보유 중이면 reset을 `BATCH_RUNNING`으로 거부한다. V6 wrapper는 같은 키를 transaction advisory lock으로 검사해 배치가 대상자별 커밋 사이에 reset을 통과하지 않게 한다.
- 삭제 전에 grant snapshot ↔ grant별 ledger 합, wallet 잔액 ↔ 사용자별 ledger 합을 확인한다. 불일치가 하나라도 있으면 아무 행도 삭제하지 않는다.
- `SECURITY DEFINER`, 고정 `search_path`, `PUBLIC` 실행 권한 회수를 적용한다. `reward_app`에는 테이블 `DELETE` 권한을 주지 않고 두 함수의 `EXECUTE`만 허용한다.
- API route는 기본 비활성이며 `APP_DEMO_WALLET_RESET_ENABLED=true`인 로컬 Compose에서만 연결된다. `POST /api/admin/wallets/reset-all`은 데모 `ADMIN` 역할을 요구한다.

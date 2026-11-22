# P2a 구현 및 실험 기록

**구현 전 계획:** [P2a~P2b 계획](../plan.md#p2ap2b-구현-전-계획)  
**프로젝트 문서:** [11 구현 Phase](../../11_구현Phase.md), [15 Stored Procedure 설계](../../15_Stored_Procedure_설계.md)  
**개념 참고:** `dba/foundations/04-transactions-and-correctness.md`, `dba/concepts/04-versions-and-transactions.md`, `dba/labs/sql/transfer.sql`

## 구현한 기능

- Flyway V3의 `reward.grant_event_reward`가 이벤트 지급 판단, grant 생성, wallet 갱신, Money/Point ledger 기록을 한 DB 경로로 처리한다.
- CLAIM API가 Spring 트랜잭션 안에서 프로시저를 호출한다. 프로시저가 COMMIT하지 않아 API가 전체 원자 작업의 경계를 소유한다.
- `request_id` 재사용은 `REPLAYED`, 다른 키로 같은 이벤트/사용자가 재요청하면 `ALREADY_GRANTED`, 다른 사용자/이벤트가 키를 가져가면 HTTP 409로 구분한다.
- 이벤트 정책은 `FOR SHARE`로 보호하고, `UNIQUE(event_id,user_id)`와 `ON CONFLICT ... DO NOTHING RETURNING`을 동시 지급의 최종 방어선으로 둔다.
- 순차 UUID 재사용 시험만으로는 서로 다른 사용자·이벤트가 같은 UUID를 동시에 보낼 때 결과를 보장하지 못하는 허점이 확인됐다. V12에서 UUID별 transaction-level advisory lock을 사전 조회보다 먼저 얻고, 두 UNIQUE 제약은 계속 최종 무결성 방어선으로 둔다.
- V12의 지급 프로시저는 migration owner 권한으로 실행하며 `search_path`를 `pg_catalog, reward, pg_temp`로 고정한다. `reward_app`의 wallets/grants/ledger/compensations 직접 DML을 회수하고 SELECT와 지급 프로시저 EXECUTE를 유지한다.

## 추가·변경한 파일과 이유

| 파일 | 내용 | 결정 이유 |
|---|---|---|
| `database/migrations/V3__reward_procedure_and_batch_runs.sql` | 공용 지급 프로시저와 권한 부여 | 지급 변경을 DB 한 곳에 두고 API가 DDL 권한 없이 `EXECUTE`만 하게 한다. 기존 V1/V2를 수정하지 않고 새 migration으로 추가한다. |
| `backend/src/main/java/dev/reward/service/RewardGrantProcedure.java` | JDBC `CallableStatement` 호출과 결과 코드 변환 | CLAIM과 BATCH가 같은 지급 절차를 호출한다. INOUT 파라미터는 명시적으로 등록한다. |
| `backend/src/main/java/dev/reward/service/RewardService.java` | CLAIM의 직접 SQL 제거 및 프로시저 호출 | SQL 지급 규칙이 API와 worker에서 갈라지지 않도록 한다. |
| `backend/src/main/java/dev/reward/api/ApiModels.java`, `RewardController.java` | 지급 응답과 기존 claim API 연결 | `PAID`, `REPLAYED`, `ALREADY_GRANTED` 등 저장된 결과를 구분해 반환한다. |
| `experiments/sql/rollback_after_ledger_failure.sql` | ledger insert 오류와 명시적 ROLLBACK 재현 | grant, wallet, ledger가 같은 트랜잭션에 묶였는지 중간 실패로 확인한다. |
| `experiments/sql/verify_p2a_event.sql`, `experiments/p2_acceptance.py` | 지급/원장 대사 및 동시·재시도 실험 | HTTP 응답뿐 아니라 DB 행과 잔액도 대조한다. |
| `database/migrations/V12__serialize_request_ids_and_restrict_wallet_writes.sql` | UUID별 transaction advisory lock, 제한된 procedure 권한과 앱 DML 회수 | 동시에 같은 request ID가 들어와도 선행 조회부터 직렬화하고 잔액 변경 경로를 procedure로 제한한다. |
| `experiments/sql/verify_request_id_lock_race.sql`, `verify_reward_app_permissions.sql` | 세션 경합, wallet/ledger 대사, 앱 권한과 임시 객체 확인 | PostgreSQL wait event와 privilege catalog, 실제 procedure 실행 결과를 함께 확인한다. |

## 의사결정

- 프로시저는 트랜잭션을 소유하지 않는다. 호출자가 명시적으로 커밋·롤백해 API/worker 흐름 전체를 원자적으로 묶는다.
- UNIQUE 제약은 요청 처리 코드의 사전 조회만 믿지 않고 동시 실행에서도 한 번만 지급되도록 한다.
- advisory lock은 같은 `request_id`를 가진 두 업무 범위 요청의 사전 조회 순서를 정한다. 해시 키 충돌은 다른 요청을 추가로 직렬화할 수 있지만, 데이터 무결성 보장은 기존 UNIQUE 제약이 담당한다.
- 원장 입력 중 예외가 발생하면 앞선 grant와 wallet 변경도 함께 롤백되도록 한다.

## 실행·검증 결과

- Docker 이미지 빌드 및 Flyway V3 적용 성공. 당시 Docker 로그는 PostgreSQL 17.12 연결과 API health `UP`을 확인했다.
- `frontend`에서 `npm run build` 성공. `experiments/p2_acceptance.py` 구문 확인도 통과했다.
- 실행 명령: `python3 experiments/p2_acceptance.py p2a` (당시에는 `/private/tmp/reward-colima/default/docker.sock`을 지정했으며, 이후 엔진 혼선을 발견해 P3에서는 격리 프로젝트와 올바른 엔진을 분명히 기록한다.)

```text
same request ID, 100 parallel calls: PAID=1, REPLAYED=99
different request ID for same event/user: ALREADY_GRANTED
same request ID used by another user: HTTP 409
DB verification: grants=1, ledger_rows=2, ledger_mismatches=0
forced wallet_ledger INSERT error + explicit ROLLBACK: leftover_grants=0, test_trigger=false
user 102 wallet before == after: true
```

V12 적용 뒤 다시 실행한 `python3 experiments/p2_acceptance.py p2a` 결과:

```text
same request ID, 100 parallel calls: PAID=1, REPLAYED=99
different request ID for same event/user: ALREADY_GRANTED
sequential request ID reused by another user: HTTP 409
forced wallet_ledger INSERT error + ROLLBACK: leftover_grants=0, wallet unchanged=true
same UUID across different event/users: waiter Lock/advisory, concurrent pair winner=PAID, reused-key request HTTP 409
race reconciliation: event grants=1/1, wallet=7/3 each, ledger_rows=4, grant/wallet mismatches=0/0
reward_app direct UPDATE: denied; procedure EXECUTE: allowed; temp type shadow check: REPLAYED
```

세 세션 경합만 따로 재현할 때는 `python3 experiments/p2_acceptance.py p2a-request-id-race`를 실행한다. 이어 확인할 실험은 COMMIT 뒤 응답 유실, 이벤트 상태 경합, 격리 수준별 동작이다.

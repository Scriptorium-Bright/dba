# P0~P1 구현 계획

**작성 시점:** 구현 전 · 2026-11-22  
**기준 문서:** 01 도메인, 02 요구사항, 03 프로젝트 설계, 04 아키텍처, 05 테이블 설계, 11 구현 Phase  
**범위:** P0와 P1. P2 이후는 제외한다.

## 목표

문서만 있던 작업 폴더에 로컬에서 재구축할 수 있는 PostgreSQL + Spring Boot + React 구현을 추가한다. 브라우저에서 이벤트를 확인하고, 운영자 역할로 이벤트를 등록·오픈·종료하며, 사용자 역할로 CLAIM 보상을 지급하고 실제 지갑·원장을 확인할 수 있게 한다.

## P0 — 실행 기반

- PostgreSQL 17의 현재 패치 버전을 Compose에 고정하고 로컬 전용 영속 볼륨과 헬스 체크를 둔다.
- `reward_app`, `reward_migration`, `reward_observer` 데이터베이스 역할을 분리한다. API는 DML, Flyway는 DDL, observer는 읽기 권한만 받는다.
- Flyway V1에 도메인 테이블·제약·조회 인덱스를 추가하고 V2에 데모 사용자·이벤트를 넣는다.
- Spring Boot의 애플리케이션 연결과 별도 Flyway 연결, Actuator 헬스 엔드포인트를 구성한다.
- 개발자가 다시 만들 수 있는 실행 안내와 데모 전용 환경 변수를 제공한다.

## P1 — 실제 사용자·운영자 흐름

- 이벤트 목록·상세 조회와 CLAIM/BATCH·상태·일정·보상 표시.
- 개발용 역할 선택으로 운영자 이벤트 생성(DRAFT), OPEN, CLOSED 제공.
- CLAIM API를 서버 트랜잭션으로 구현해 `reward_grants`, `wallets`, `wallet_ledger`를 함께 갱신한다.
- `Idempotency-Key`와 DB UNIQUE 제약으로 같은 사용자의 이벤트 중복 지급을 막고, 재시도/중복/거부 결과를 UI에 구분해 표시한다.
- 사용자 지갑과 최신 원장 조회.
- BATCH 일정은 표시만 하며 대상 등록이나 지급 실행은 구현하지 않는다.

## 설계 결정

| 결정 | 이유 |
|---|---|
| Spring Boot API는 컨테이너 안에서 실행하고 Vite 개발 서버는 로컬에서 실행 | 문서의 P0 토폴로지에 맞추고 프런트 코드 변경을 빠르게 확인한다. |
| Flyway 계정과 런타임 계정을 분리 | API가 DDL 권한을 갖지 않도록 한다. |
| 지급은 P1에서 Spring `@Transactional`로 구현 | 구현 Phase 문서가 P1에 초기 CLAIM API를 두고 SP 확정은 P2로 분리한다. |
| 로컬 데모 사용자·역할은 헤더로 선택 | 인증 제품화는 범위 밖이므로 UI 흐름을 시험할 최소 수단만 둔다. Compose 포트도 로컬에만 공개한다. |
| `event_status_audit`를 추가 | BF-03이 상태 전이 이력 기록을 요구하지만 기준 스키마에 상태 감사 테이블이 없다. 보상 정책 감사 테이블과 목적을 섞지 않는다. |

## 제외 범위

P2 저장 프로시저, 동시 요청 시험·재시도 실험 자동화, BATCH 대상 등록/워커, 보상액 변경, P3 이후 변경·튜닝·HA·백업/PITR, 실제 인증과 권한 제품화, 운영 배포.

## 완료 후 확인할 것

- 새 DB에 Compose 초기화와 Flyway migration이 연결되는지 확인.
- 사용자 UI의 CLAIM 결과가 지급 행·잔액·재화별 원장에 반영되는지 확인.
- 운영자 UI가 이벤트 생성과 DRAFT→OPEN→CLOSED만 허용하는지 확인.
- 실행 여부와 남은 검증 항목을 `exec.md`에 사실대로 기록한다. 실행하지 않은 테스트/검증은 완료로 표시하지 않는다.

---

# P2a~P2b 구현 전 계획

**작성 시점:** P2 구현 전 · 2026-11-22  
**기준 문서:** [11 구현 Phase](../11_구현Phase.md), [15 Stored Procedure 설계](../15_Stored_Procedure_설계.md), [10 검증 및 실험 계획](../10_검증및실험계획.md), [07 운영 런북](../07_DBA_운영런북.md)  
**개념 참고:** `dba/foundations/04-transactions-and-correctness.md`, `dba/concepts/04-versions-and-transactions.md`, `dba/chapters/01-crash-recovery.md`, `dba/labs/sql/transfer.sql`

## 목표

P1의 애플리케이션 지급 SQL을 PostgreSQL의 버전 관리되는 `reward.grant_event_reward` 프로시저로 옮기고, 사용자 수령과 대상별 배치 지급이 같은 지급 규칙을 사용하게 한다. 이벤트·대상·지급·지갑·원장 변경을 실제 DB에서 관측하고, 멱등성과 WAL 복구 이후 미지급 재처리를 확인한다.

## P2a — 지급 프로시저와 동시성·원자성

- Flyway V3에 `CLAIM`/`BATCH` 공용 프로시저와 명시적 실행 권한을 추가한다.
- API가 `CALL`을 트랜잭션 안에서 실행한다. 프로시저는 자체 `COMMIT`을 하지 않고 호출자가 트랜잭션 경계를 소유한다.
- 같은 `request_id` 재시도, 다른 요청 ID의 사용자·이벤트 중복, 다른 대상에서의 UUID 재사용을 구분한다.
- 이벤트 행을 `FOR SHARE`로 잠그고 정책 확인 → UNIQUE 기반 지급 insert → 지갑 원자 갱신 → 재화별 원장 기록 순서를 고정한다.
- 100개 병렬 요청, 동일 키 재시도, 다른 키 중복, 명시적 롤백, 원장 오류 상황에서 세 테이블의 원자성을 확인한다.
- 로컬 전용 실험 스크립트와 DB 정합성 SQL을 추가한다. 성능 수치와 확인하지 않은 조건은 결과 문서에 사실대로 남긴다.

## P2b — 대상 명단·배치 작업·재개

- DRAFT 상태에서만 BATCH 대상 명단을 확정·교체하고, 미등록 사용자와 중복 입력을 거부한다. OPEN 이후에는 명단을 동결한다.
- 운영자가 개발 환경에서 작업을 수동 시작할 수 있게 한다. 실행 시각 전, 잘못된 상태, 빈 대상은 DB에서도 거부한다.
- 미지급자는 `event_recipients LEFT JOIN reward_grants`로 다시 계산한다. 대상자 한 명마다 독립 트랜잭션을 열어 부분 커밋 후에도 이미 지급된 사용자를 건너뛴다.
- 각 `(event_id,user_id)`에 안정적인 request ID를 사용한다. 프로세스가 살아 있는 동안에는 PostgreSQL advisory lock으로 이벤트당 워커를 한 개로 제한한다.
- `batch_runs`에 대상 수·결과 분류를 기록한다. 워커/DB가 비정상 종료해 남은 RUNNING 기록은 다음 재실행에서 중단 이력으로 닫고, 미지급 대상만 처리한다.
- 운영 UI에 대상/지급/미지급 수, 최근 실행, 오류를 보이고 명단 확정·수동 실행을 제공한다.
- 1,000명 배치, 일부 커밋 후 PostgreSQL 비정상 종료, DB 재시작, 미지급 재처리, 지갑·원장 대사를 확인한다.

---

# 로컬 지갑 초기화 UI 계획 (V5)

**목표:** `내 지갑`에서 현재 데모 사용자 한 명을 초기화하고, `운영 관리`에서 DB의 전체 사용자를 초기화한다. CLAIM 재시도/멱등성은 사용자가 이벤트 화면과 API에서 직접 확인한다.

## 작업 범위와 판단

- 내 지갑 초기화는 현재 선택 사용자 한 명의 Money/Point, grant, ledger만 지운다. 전체 초기화는 DB의 등록 사용자 전부(P2 실험 사용자 포함)를 대상으로 한다.
- 잔액만 0으로 쓰지 않는다. 재수령을 막는 grant와 지갑 합계를 구성하는 ledger도 같은 트랜잭션에서 정리하고, 사용자별 대사를 선행한다.
- 사용자·이벤트·BATCH 대상 명단·배치 실행 이력은 보존해 DB 전체 재생성 없이 지급 흐름을 반복할 수 있게 한다.
- `reward_app`에 grant/ledger DELETE 권한을 추가하지 않는다. 좁은 `SECURITY DEFINER` 함수를 사용하고 route는 Compose 밖에서 기본 비활성화한다.
- 내 지갑은 inline 2단계 확인, 전체 초기화는 `전체 초기화` 문구 입력으로 보호한다.

## 구현 순서와 완료 조건

1. Flyway V5에 현재 사용자/전체 사용자 reset 함수를 추가하고 구형 단건 CLAIM reset 함수를 제거한다. V6 wrapper는 실행 중인 BATCH worker와 reset이 겹치지 않게 한다.
2. API route를 로컬 Compose에서만 켜고 전체 초기화는 데모 ADMIN 역할로 제한한다.
3. `내 지갑`과 `운영 관리`에 reset 확인 UI를 둔다. 자동 멱등성 호출 UI는 추가하지 않는다.
4. Phase/실험 안내서에 버튼 범위, 보존 데이터, SQL 대안과 주의 사항을 기록한다.
5. 애플리케이션 빌드와 V5/V6 적용을 확인하되 실제 지갑 초기화는 실행하지 않는다.

**상태:** 구현 완료. 사용자/전체 초기화는 실제로 실행하지 않았으며 결과는 [exec.md](exec.md)의 “내 지갑·전체 유저 초기화 추가” 절을 참고한다.

## 판단 기준

| 판단 | 계획 | 이유 |
|---|---|---|
| 커밋 경계 | 워커 전체가 아니라 사용자 1명씩 커밋 | 한 사용자의 오류가 앞서 확정된 다른 지급을 취소하지 않으며, 재실행 때 남은 대상만 선별할 수 있다. |
| 지급 진실의 원천 | `reward_grants`를 기준으로 미지급 여부 판정 | 작업 로그나 워커 메모리 카운터는 커밋 사실을 대신하지 못한다. |
| request ID | BATCH에서 이벤트·사용자 조합으로 결정적으로 파생 | HTTP 응답/워커 중단 뒤 재실행해도 같은 지급 요청을 식별한다. |
| 워커 중복 방지 | DB 세션 advisory lock + 지급 UNIQUE 제약 | 같은 이벤트 작업을 직렬화하고, 락 경계를 벗어난 재실행도 DB 제약으로 보호한다. |
| BATCH 시간 정책 | 이벤트가 OPEN이고 `scheduled_at`에 도달한 뒤 허용; 명시적 CLOSED는 중지 | 예약 지연 복구는 허용하고 운영자 종료 의사를 우선한다. `ends_at`은 CLAIM 수령 기간으로 사용하며 스케줄러 지연 때문에 BATCH 보상을 영구 누락시키지 않는다. |
| 장애 실험 | 기존 named volume은 유지하고 별도 실험 이벤트/사용자만 추가 | P1 데모 데이터와 사용자의 로컬 상태를 삭제하지 않고 재현 가능한 실험을 만든다. |

## P2 Gate

- [x] V3를 실제 PostgreSQL 17.12에 적용하고 `reward_app`의 프로시저 실행을 확인했다.
- [x] 동시 100 요청에서 신규 지급 1건·재시도 99건이며 grant 1, ledger 2, 불일치 0을 확인했다.
- [x] 같은 키 `REPLAYED`, 다른 키 `ALREADY_GRANTED`, 타 사용자 UUID 재사용 HTTP 409; 원장 실패와 호출자 롤백은 잔액/grant 잔존이 없었다.
- [x] BATCH 명단 1,000명, 사용자별 커밋, 실행 중 DB crash recovery, 미지급자 재실행을 확인했다.
- [x] 추가 파일·결정·명령·관측 결과와 제한을 `exec.md`에 기록했다.

## 지갑 초기화가 화면에서 동작하지 않는 문제 복구 계획

### 구현 전 확인

- 브라우저 API 포트(8082)와 Compose 명령이 같은 Docker 엔진을 보는지 Engine ID, 컨테이너 목록, 읽기 전용 지갑 조회로 대조한다.
- 실제 화면이 연결된 DB volume은 보존한다. 원인 확인 중 reset POST나 `docker compose down -v`는 실행하지 않는다.
- 오래된 API가 실행 중이면 기존 DB를 쓰는 Compose 엔진에서 API만 현재 소스로 다시 빌드한다.

### 구현 후 완료 조건

- [x] 화면 포트와 다른 Docker 엔진을 구분하고 올바른 Colima Docker 소켓 경로를 문서화한다.
- [x] 기존 DB 컨테이너/volume을 유지한 채 API를 V5/V6로 올린다.
- [x] UI 프록시 지갑 GET이 정상이고 초기화 경로의 GET이 `405 Allow: POST`로 등록을 확인한다.
- [x] grant snapshot·wallet-ledger 대사를 읽기 전용으로 확인하고 reset POST는 보내지 않는다.

`/private/tmp/reward-colima/default/docker.sock`은 화면 포트 포워딩이 연결된 엔진과 달랐다. 관리형 환경에서는 SSH 포워딩된 `/private/tmp/reward-colima/active-docker.sock`을 사용해 Compose/실습 명령이 화면과 같은 DB에 연결되게 한다. 실행 결과는 [exec.md](exec.md)의 “지갑 초기화가 반영되지 않던 원인과 복구” 절에 기록한다.

# P3 구현 전 계획 — 운영 중 보상 변경과 DB 마이그레이션

**상태:** 구현·실습 완료 (2026-11-22). 기존 API 바이너리 교차 실행과 기간/cursor 검색 경계를 확인했습니다. 동일 요청 재전송은 사용자가 직접 확인할 실습으로 남겼습니다. 실행 결과는 [P3 실행 기록](exec/p3.md)에 남겼습니다.

**기준 문서:** [11 구현 Phase](../11_구현Phase.md), [S02/S03 시나리오](../06_시나리오목록.md), [DB 변경 배포 계획](../09_DB변경배포계획.md), [DBA 운영 런북](../07_DBA_운영런북.md)

**개념 참고:** `dba/foundations/04-transactions-and-correctness.md`, `dba/concepts/04-versions-and-transactions.md`, `dba/prerequisites/03-mvcc-and-transaction-state.md`, `dba/labs/operations/sql/schema-migration.sql`

## 목표

이벤트 지급만 기록할 수 있는 현재 원장에 고객지원 장애 보상이라는 두 번째 지급 출처를 추가한다. 기존 이벤트 지급을 유지하면서 원장 구조·저장 프로시저·API를 단계적으로 바꾸고, 요청이 들어오는 동안 DDL Lock·앱 호환성·Backfill·migration 실패가 서비스와 데이터에 미치는 영향을 측정한다.

실습은 Lock 대기, 구·신 코드 호환성, 일부 적용 후 재시도 상황을 정해진 절차로 재현한다.

## 범위

### S02 — 고객지원 장애 보상

- 특정 이벤트 장애의 영향 사용자에게 Money 또는 Point를 추가 지급하는 CS 기능을 설계한다. 원래 이벤트 지급을 삭제하거나 잔액만 직접 수정하지 않는다.
- `compensations`에 수령자, 관련 이벤트, 장애 식별자, 요청 ID, 사유, 처리 운영자, 통화별 지급 snapshot을 보존한다. `(event_id, incident_reference, user_id)` UNIQUE로 동일 장애 대상 중복 지급을 막는다.
- 원장의 출처를 `EVENT`와 `COMPENSATION`으로 구분하고 출처별 FK를 둔다. 행 하나가 정확히 한 출처만 가리키도록 CHECK 제약을 설계한다.
- CS 지급용 DB 프로시저/API는 보상 기록, 지갑 갱신, 원장 입력을 한 트랜잭션으로 처리한다. 요청 ID UNIQUE 제약으로 재시도 중복 지급을 막되 별도 자동 멱등성 실습 UI는 추가하지 않는다.
- 화면 원장 조회에서 이벤트 지급과 CS 보상을 출처와 사유를 구분해 보여준다. ADMIN 지급 검색은 사용자·기간·출처·cursor를 받는다. 개발용 ADMIN 헤더는 유지하되 실제 인증/운영 권한 제품화는 범위에서 제외한다.

### S03 — DDL Lock 대기

- 별도 P3 DB에서 세션 A가 `wallet_ledger` 조회 트랜잭션을 연 채 유지하고, 세션 B가 `ALTER TABLE`을 시도하며, 세션 C가 같은 테이블을 조회하는 상황을 만든다.
- `pg_stat_activity`, `pg_locks`, `pg_blocking_pids()`로 blocker, 대기자, 이후 요청의 대기 여부를 확인한다.
- DDL 실행 시간과 Lock 획득 대기 시간을 나눠 기록한다. 짧은 `lock_timeout`, 장기 트랜잭션 종료 후 재시도, 호환 가능한 단계별 변경을 비교한다.

## 구현 순서

1. **기준선과 실험 DB:** 현재 schema/Flyway 버전, 지급 프로시저와 API 의존성, ledger 대사, 장기 트랜잭션, API 요청 지연을 기록한다. P2/사용자 데이터 volume을 그대로 보존하고 P3용 별도 DB 또는 복제한 fixture에서 변경한다.
2. **확장 migration:** 새 `compensations` 테이블과 원장 출처 컬럼을 추가한다. 기존 이벤트 API가 새 컬럼을 몰라도 이벤트 지급을 계속 기록하도록 기본값과 nullable 단계부터 적용한다.
3. **읽기 호환 코드:** 이벤트와 보상 두 출처를 함께 조회하는 새 코드를 배포하고, 기존 CLAIM 지급 및 잔액·원장 대사가 계속 맞는지 확인한다. 보상 쓰기는 아직 열지 않는다.
4. **제약과 지급 경로:** `reward_grant_id` nullable 전환, 출처별 FK/CHECK/UNIQUE를 호환 순서에 따라 추가한다. 검증이 필요한 CHECK/FK는 `NOT VALID` 후 별도 검증한다. 이후 CS 보상 프로시저/API를 추가하고, 기존 CLAIM 경로의 구·신 버전 호환성을 확인한 뒤 기능을 켠다.
5. **화면과 운영 기록:** ADMIN이 대상 사용자·사유·금액을 확인하고 CS 보상을 지급할 수 있게 한다. 지급 내역과 지갑의 합계, 요청 ID, 운영자, 사유를 조회할 수 있게 한다.
6. **Lock 실험:** S03을 별도 DB의 세션 A/B/C로 재현한다. DDL의 Lock 대기, 후속 SELECT 대기, 제한 시간 초과 여부와 회복 후 요청 결과를 기록한다.
7. **Backfill 판단:** `source_type='EVENT'` 같은 상수 기본값으로 기존 행을 표현할 수 있는지 먼저 확인한다. 행별 실제 매핑이 필요한 경우에만 별도 migration/스크립트를 계획하고, 기본 키 범위별 작은 트랜잭션, 진행률, 재시작 조건, WAL 사용량, 잔여 NULL/불일치 검증을 추가한다.
8. **실패 대응:** CHECK 검증 전에 잘못된 과거 행이 있는 상황을 별도 DB에서 재현한다. Flyway history와 실제 catalog를 대조하고, 원인 행을 forward fix한 뒤 검증 migration을 재시도한다. 이미 새 형식 데이터가 기록된 뒤에는 테이블을 단순 DROP하는 롤백을 기본값으로 두지 않는다.

## 검증 시나리오

| 실험 | 실행 상황 | 관측하고 남길 결과 |
|---|---|---|
| P3-A · 온라인 확장 | 기존 CLAIM 요청을 이어가며 `source_type` 등 호환 가능한 컬럼을 추가 | 기존 API 성공/오류, DDL Lock 대기, 지갑·원장 대사 |
| P3-B · DDL Lock 대기 | 장기 조회 트랜잭션 A, ALTER 세션 B, 후속 조회 C를 분리 실행 | `pg_blocking_pids()` 대기 사슬, Lock 획득 대기 시간, C의 지연/오류, `lock_timeout` 결과 |
| P3-C · Backfill | 행마다 실제 계산이 필요한 별도 데이터 fixture를 사용. 그런 요구가 없으면 생략 근거 기록 | chunk별 처리/커밋 수, 중단 후 재개, WAL 증가, 잔여 NULL과 최종 대사 |
| P3-D · SP/API 호환 | 기존 CLAIM 코드와 새 SP/API 배포 단계를 교차해 요청 | 각 배포 구간의 요청 성공 여부와 EVENT/COMPENSATION ledger 무결성 |
| P3-E · migration 실패 | CHECK 검증에 실패할 legacy row를 별도 DB에서 만든 뒤 실패와 forward fix 재시도를 재현 | Flyway schema history의 적용 버전, constraint validation 상태, 원인 행과 안전한 재시도 순서 |

## 설계 결정과 이유

| 결정 | 계획 | 이유 |
|---|---|---|
| 지급 출처 | 기존 `reward_grants`를 없애지 않고 `EVENT`/`COMPENSATION` 출처를 명시 | 과거 이벤트 지급의 의미와 snapshot을 유지하면서 새 업무를 추가한다. |
| 원장 무결성 | 출처에 따라 grant FK 또는 compensation FK 하나만 허용하고 통화별 중복 입력을 제약 | 원장 행이 어느 업무의 잔액 변경인지 DB가 검증할 수 있어야 한다. |
| 장애 중복 기준 | 이벤트·장애 식별자·사용자 조합에 UNIQUE를 두고 request ID도 별도 UNIQUE로 유지 | 재시도 키가 새로 생성돼도 같은 업무 사건에 같은 사용자에게 다시 지급하지 않는다. |
| 적용 순서 | 확장 → 읽기 호환 → 쓰기 기능 → 제약 검증/강화 | 구버전 코드가 새 schema를 모르는 동안에도 기존 지급을 이어가도록 한다. |
| Backfill | 실제로 행별 값 계산이 필요할 때만 수행 | 상수 기본값만으로 표현 가능한 기존 행을 대량 UPDATE해 Lock/WAL 비용을 만들지 않는다. |
| 변경 실패 | 데이터가 새 형식으로 쓰이기 전과 후를 구분해 롤백/Forward Fix를 결정 | DB 구조를 되돌리는 것과 이미 커밋된 업무 데이터를 되돌리는 것은 다른 작업이다. |
| 실험 경계 | 잠금·migration 실패 실험은 격리된 P3 DB에서만 수행 | 현재 화면의 P2 데이터와 보상 지급 흐름을 보존한다. |

## 완료 조건

- [x] 기존 CLAIM은 V6 기준선, V7/V8 확장 중, V11 적용 후에 성공했고 grant/wallet/ledger 대사를 확인했다.
- [x] 한 CS 보상 지급에서 실패 CLAIM은 잔액/원장에 남지 않았고, 보상 snapshot·wallet·source ledger는 600/50으로 일치했다.
- [x] 요청 ID UNIQUE와 (event_id, incident_reference, user_id) UNIQUE, source 제약 및 ADMIN 필터 경로를 구현했다. 보상 조회는 userId/sourceType으로 확인했다.
- [x] 두 출처의 FK/CHECK가 V9 후 validated이고 현재 잘못된 출처 조합은 0행이었다. 잘못된 신규 INSERT를 일부러 시도하지는 않았다.
- [x] 구버전 API 이미지로 V11 이후 P3 DB에 CLAIM을 보내고 신버전 API의 EVENT 원장을 확인했다.
- [x] DDL Lock blocker, B 대기, 뒤따른 C의 대기, lock_timeout, 회복 후 재시도 시간을 기록했다.
- [x] 상수 기본값 EVENT가 기존 행을 표현하는 catalog 근거를 확인하고 행별 UPDATE backfill을 생략했다.
- [x] V9 실패 때 Flyway/catalog 상태를 확인하고 원인 행 forward fix 후 재적용했다.
- [x] P2 컨테이너/볼륨은 P3 작업에서 사용하지 않았고, 종료 시에도 P2 API/DB가 분리되어 실행 중임을 읽기 전용 확인했다.
- [x] ADMIN 검색의 inclusive `from`, exclusive `to`, `beforeId` cursor 경계를 확인했다.
- [ ] 보상 재전송 중복 실습은 사용자가 직접 확인할 범위로 남긴다.

## 제외 범위

Primary/Standby·자동 Failover, PITR/Backup 복원, 인증/권한 제품화는 P3 범위에 포함하지 않는다.

---

# P4 구현 전 계획 — 대량 지급 이력 검색과 인덱스 배포

**상태:** P4 실습 완료 (2026-11-22). 실행 결과와 파일별 설명은 [P4 실행 기록](exec/p4.md)에 정리했습니다.

## 목표

1,000,000행의 wallet ledger에서 관리자의 보상 이력 조회를 측정하고, 최신순 cursor와 기간 검색에 맞는 부분 인덱스를 선택합니다. 일반 `CREATE INDEX`와 `CREATE INDEX CONCURRENTLY`가 쓰기 세션과 어떤 관계를 갖는지도 P4 DB에서 관찰합니다.

## 구현 전 확인과 가설

| 항목 | 구현 전 계획 |
|---|---|
| 데이터 분포 | EVENT 990,000행, COMPENSATION 10,000행으로 보상 비율 1%를 구성하고 사용자 wallet 대사를 확인합니다. |
| 기준 쿼리 | 현재 `/api/admin/payouts`의 전체 출처 JOIN을 그대로 측정합니다. 최신순 100개와 좁은 날짜 범위를 별도로 봅니다. |
| 인덱스 가설 | 전체 출처 인덱스, 보상 전용 ID 커서 인덱스, 보상 전용 created_at 인덱스를 크기·실행계획과 함께 비교합니다. |
| SQL 가설 | 출처별 JOIN 분기가 더 적은 작업을 할 것으로 예상하지만, 실제 PostgreSQL join 순서와 실행계획으로 유지 여부를 결정합니다. |
| 쓰기 비용 | 같은 50,000행을 PK-only scratch table과 두 부분 인덱스 scratch table에 넣고 WAL 및 인덱스 크기를 비교합니다. |
| 배포 방식 | 정상 DDL의 lock queue와 lock timeout, `CONCURRENTLY` 중 DML 진행 및 오래된 트랜잭션 대기를 관찰합니다. |

## P4 완료 조건

- [x] P4 전용 DB에 백만 ledger fixture와 source/wallet 정합성 확인.
- [x] 두 조회의 baseline `EXPLAIN (ANALYZE, BUFFERS)` 확보.
- [x] 전체 인덱스와 보상 전용 부분 인덱스를 비교하고 두 검색 경로에 맞는 인덱스 반영.
- [x] SQL JOIN 분기 가설을 측정하고 계획을 유지할지 결정.
- [x] 추가 WAL/인덱스 크기 기록.
- [x] normal `CREATE INDEX`와 `CREATE INDEX CONCURRENTLY` 차이 관찰.
- [x] P4 API와 UI에서 전체·기간·cursor 검색 응답 확인.

# P5 구현 전 계획 — 운영 진단과 Lock Wait 대응

**목표:** P4까지의 기능 개발과 별개로, DB에서 대기 장애를 관측하고 원인을 좁혀 복구하는 과정을 재현 가능한 운영 기록으로 남깁니다. 첫 실습은 오래 열린 트랜잭션이 행 Lock을 보유해 UPDATE가 기다리는 상황입니다.

**개념 참고:** [U15 모니터링·트러블슈팅](../../dba/chapters/15-monitoring-troubleshooting.md), [M01 Lock Wait](../../dba/labs/operations/manual-fault-scenarios.md#2-m01-lock-wait와-deadlock), [P5 운영 진단 SQL](../../experiments/sql/p5_observe_lock_wait.sql).

## P5-A — 행 Lock Wait 진단

- 별도 `rewardp5db`와 named volume을 사용하고 P2~P4의 사용자·지급 데이터는 읽거나 수정하지 않습니다.
- `p5_ops.lock_lab` 단일 행에서 세션 A가 `SELECT ... FOR UPDATE`를 실행한 뒤 트랜잭션을 열린 채 둡니다. 세션 B의 UPDATE가 같은 행에서 기다리게 합니다.
- 읽기 전용 `reward_observer`에 P5 클러스터 범위 `pg_monitor` 권한을 주어 세션·Lock 통계를 관측합니다.
- `pg_stat_activity`, `pg_blocking_pids()`, `pg_locks`로 Waiter, Blocker, wait event, 트랜잭션 나이와 SQL을 연결합니다.
- 세션 C에서 같은 행을 SELECT해 이전 committed 값을 읽는지와 지연을 기록합니다.
- Blocker를 COMMIT한 뒤 B가 끝나는지, 잔액 변화가 한 번인지 확인하고 fixture를 기준값으로 되돌립니다.

## 구현 순서

1. P5 전용 Compose DB, `reward_p5_pgdata`, loopback 포트 55434를 정의합니다.
2. 읽기 전용 관측 SQL과 단일 행 Lock Wait fixture를 추가합니다. 모든 준비 SQL은 현재 DB가 `rewardp5db`인지 먼저 검사합니다.
3. 표준 라이브러리와 로컬 `psql`만 사용하는 incident harness로 A/B/C 세션을 순서대로 열고 관측·복구합니다.
4. 실행 전 가설, 타임라인, 관측된 PID·Wait Event·SQL, 조치, 복구 후 대사와 재발 방지 판단을 P5 실행 기록에 남깁니다.

## 확인할 운영 역량

| 역량 | P5에서 남기는 증거 |
|---|---|
| 증상 분류 | Lock wait와 단순 실행 시간을 `wait_event_type`으로 나눔 |
| 원인 추적 | `pg_blocking_pids()`로 A→B 관계와 각각의 SQL을 연결 |
| 사용자 영향 판단 | 대기 UPDATE와 동시 SELECT의 응답을 나눠 관찰 |
| 조치 선택 | 실습 Blocker의 업무 트랜잭션을 확인한 뒤 COMMIT해 대기 해소 |
| 데이터 정합성 | waiter 반영 횟수와 cleanup 후 기준 잔액을 재확인 |
| 운영 커뮤니케이션 | 사건 시각·영향·가설·관측·조치·결과·후속 예방을 한 장의 기록에 정리 |

## 설계 결정

| 결정 | 이유 |
|---|---|
| UI/API보다 DB 진단 시나리오부터 구현 | P5는 사용자 기능 추가가 아니라 세션·Lock 관측과 대응을 직접 보여주는 단계입니다. |
| P5 전용 PostgreSQL과 볼륨 | 실험 세션과 fixture를 P2~P4의 실행 환경·데이터에서 격리합니다. |
| SELECT FOR UPDATE를 Blocker로 사용 | 행을 변경하지 않고 Lock을 보유해, 관측 대상과 데이터 변경 영향을 분리합니다. |
| 관측 계정은 `pg_monitor`만 추가 | 다른 세션 상태와 통계를 확인하되 P5 업무 테이블 쓰기 권한은 주지 않습니다. |
| blocker 해소 뒤 fixture를 100으로 복원 | 같은 실습을 반복할 수 있고, 예상 밖 종료 후 상태를 쉽게 대조할 수 있습니다. |
| SQL 파일과 자동 진행 harness를 함께 제공 | 수동 학습 시 쿼리를 단계별 실행할 수 있고, 포트폴리오 결과는 같은 절차로 다시 수집할 수 있습니다. |

## P5 완료 조건

- [ ] P5 DB가 P2~P4와 다른 DB·볼륨·포트로 기동한다.
- [ ] A가 `idle in transaction`으로 Lock을 보유하고 B가 `Lock` Wait Event에서 대기한다.
- [ ] observer 출력에서 B의 blocker PID가 A와 일치하고 양쪽 SQL을 구분한다.
- [ ] C의 SELECT가 Blocker 종료 전 완료되고 이전 committed 값을 읽는다.
- [ ] A 조치 후 B가 완료되고 fixture가 기준값 100으로 복구된다.
- [ ] 실행 시각·명령·버전·관측값·판단·복구·재발 방지 후보를 P5 실행 기록에 남긴다.

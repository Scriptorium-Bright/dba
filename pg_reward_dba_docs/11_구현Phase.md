# 11. 구현 Phase와 구체 작업

## 1. Phase 총괄

**구현 기록:** P0~P1 서비스 기반, P2 동시 지급·BATCH 재개, P3 장애 보상·migration 복구, P4 대량 검색·인덱스 비용, P5 Lock Wait와 API 지연 진단 결과를 정리했다. phase별 실행 방법은 [Phase 안내서](implementation/phase-guide.md), 변경과 수치는 [구현 기록](implementation/exec.md)에서 확인한다.

| Phase | 목적 | 주요 구현 | 종료 조건 |
|---|---|---|---|
| **P0** | 기본 설계·개발 환경 | DDL, 역할, Compose, Flyway, API 연결, 초기 데이터 | DB 초기화부터 재실행 가능 |
| **P1** | 서비스 기능 | React 이벤트·지갑, 이벤트 CRUD/OPEN/CLOSED, CLAIM 지급 | 화면에서 실제 DB 지급 확인 |
| **P2** | DB 핵심·배치·정합성 | SP, UNIQUE, 트랜잭션, Money/Point 원장, 배치·재시도, 오류 검증 | 100 동시요청, 예약 1000명/중단/재실행 검증 |
| **P3** | 운영 중 DB 변경 | 장애 보상 CS 기능, DDL·SP 마이그레이션, Lock/Forward Fix | 이전·이후 요청/원장/잠금 영향 증명 |
| P4 | 튜닝과 온라인 변경 | 대량 이력, 실행 계획, 인덱스, CONCURRENTLY | 성능 전후+쓰기 비용 비교 |
| P5 | 운영 진단 | 세션·Lock 관측, incident 절차·복구 기록 | Blocker/Waiter와 wait event 확인, 조치 뒤 정합성 대사 |
| P6 | Backup/PITR | Base Backup, WAL Archive, 별도 복원 | 복구 지점 전후 데이터와 서비스 상태 대조 |
| P7 | HA 운영 | Replication, 수동 Failover, 연결 전환, read-lag | 복제 상태·쓰기 역할·RPO/RTO 기록 |
| P8 | 운영 자동화 | Python/Bash CLI, 관측 수집, 실험 보고서 | 재현·진단·검증을 반복 실행 |
| P9 선택 | CloudNativePG | Kubernetes 운영 및 DB 이전 | 장애/백업/연결 전환 검증 |

## 2. P0 — 첫 시작

- [x] PostgreSQL 17.12, Docker 볼륨·포트·시드 구성.
- [x] 프로젝트 디렉터리 `frontend/backend/database/migrations/experiments/pg_reward_dba_docs` 구성.
- [x] `users/wallets/events/event_recipients/reward_grants/wallet_ledger` 기본 테이블 생성.
- [x] `event_policy_audit`, `event_status_audit`, `batch_runs` 테이블 생성.
- [x] DB 권한 `reward_app`, `reward_migration`, `reward_observer` 분리.
- [x] Flyway migration으로 새 DB 구축.
- [x] Spring Boot DB 연결, transaction 설정, health check.
- [x] 샘플 user/event/wallet 생성 및 API 연결 확인.

**Gate:** DB·API를 깨끗한 상태로 재구축하는 명령과 기대 결과 문서화.

## 3. P1 — 사용자·운영자 화면

- [x] 이벤트 목록/상세와 CLAIM/BATCH mode 표시.
- [x] 운영자 이벤트 생성(DRAFT), OPEN, CLOSED.
- [x] 사용자 보상 수령 API와 공용 지급 procedure 연결.
- [x] 지갑 Money·Point와 원장 조회.
- [x] 성공·중복·거부·결과 재조회 UI 상태 처리.
- [x] 운영자 BATCH 대상·예약 시각 표시.

**Gate:** 이벤트 A의 지급 결과가 실제 `reward_grants/wallets/wallet_ledger`에 일치.

## 4. P2 — DBA 기본 구현과 정합성

- [x] `grant_event_reward` 저장 프로시저 정의, Flyway V3 적용과 API 실행 권한 부여.
- [x] 이벤트 기간·상태·mode·BATCH 대상 검증.
- [x] `ON CONFLICT (event_id,user_id) DO NOTHING RETURNING id INTO ...` 활용.
- [x] 지급액 snapshot, Money/Point 잔액 갱신과 재화별 원장 연결.
- [x] 사용자 1명별 JDBC 트랜잭션, 원장 오류와 호출자 ROLLBACK 검증.
- [x] 같은 요청 ID 재시도, 다른 요청 ID 중복, 다른 사용자 request ID 재사용 거부.
- [x] 100 동시 요청에서 신규 지급 1건, 중복 지급 0건 확인.
- [x] 운영자 수동 BATCH worker로 대상 1,000명을 사용자별 독립 커밋.
- [x] `event_recipients LEFT JOIN reward_grants`로 미지급자 재조회.
- [x] 중복 worker 거부, 실행 중 중단·재실행, PostgreSQL 비정상 종료 후 복구.
- [x] 지급·원장·지갑 검증 SQL과 P2 수용 스크립트 추가.
- **후속 설계:** 이벤트 상태 변경과 지급 사이의 경합을 별도 부하·Lock 시나리오로 계측.
- **후속 설계:** 자동 scheduler와 1,000명 초과 장시간 작업의 batch 정책 수립.

**P2a/P2b Gate:** 100 동시 CLAIM, rollback, 1,000명 user별 지급, 중복 worker 거부, SIGKILL 뒤 recovery와 재개를 확인했다. 최종 `recipients/grants/pending=1,000/1,000/0`, grant-ledger 및 wallet-ledger 불일치 0건이었다. 재현 수치는 [P2 실행 기록](implementation/exec/p2a.md)과 [BATCH recovery 기록](implementation/exec/p2b.md)에 있습니다.

### P2 안에서도 작업 순서

1. **P2a:** 직접 수령 SP·동시성·원자성.
2. **P2b:** 예약 대상·일괄 지급 워커·재실행·Crash Recovery 실험.

P2b는 배치를 쉽게 이해하기 위한 확장이다. P2a를 마친 직후 중간 결과를 Git에 남겨도 된다.

## 5. P3 — 운영 중 변경

- [x] 격리된 `rewardp3db`/`reward_p3_pgdata`에서 baseline과 P2 volume 분리를 확인.
- [x] V7 확장 → V8 NOT VALID 제약 → V9 검증 → V10 보상 SP → V11 지갑 reset 호환 migration 적용.
- [x] CLAIM을 V6 기준선, V7/V8 변경 중, V11 뒤에 실행했고 EVENT 원장 대사를 확인.
- [x] `compensations`, source별 FK/CHECK/UNIQUE, `/api/admin/compensations`, 통합 ledger 검색 구현.
- [x] DB failure trigger로 CLAIM 실패를 재현하고 보상 지급·wallet·ledger 대사.
- [x] 세션 A/B/C에서 DDL Lock 대기, `lock_timeout`, 후속 SELECT와 재시도 관측.
- [x] 상수 기본값만으로 기존 행을 EVENT로 표현함을 catalog에서 확인하고 실제 Backfill은 생략.
- [x] migration 검증 실패 후 legacy 행을 forward fix하고 안전하게 재시도.
- [x] 구버전 API 이미지로 V11 schema에 CLAIM하고 신규 API에서 EVENT 원장 2행·40/10 snapshot을 확인.
- [x] ADMIN 검색에서 inclusive `from`, exclusive `to`, `beforeId` cursor 동작 확인.

**P3 실습 Gate:** 기존/구버전 CLAIM, 장애 보상 원자성·재시도, DDL 대기 사슬, migration forward fix, 검색 경계, P2 데이터 분리를 [P3 실행 기록](implementation/exec/p3.md)에서 확인했다.

## 6. P4 — 성능

- [x] P4 전용 DB에 EVENT 990,000행/COMPENSATION 10,000행 fixture 생성 및 wallet/ledger 대사.
- [x] 인덱스 전 global cursor와 date query에서 filter 제거·parallel sequential scan 확인.
- [x] source JOIN 분기 가설 측정 후 공통 JOIN 유지, 검증된 source literal로 partial predicate 사용.
- [x] INCLUDE/key-only 보상 부분 인덱스를 비교해 최종 240KB/328KB로 조정하고, sourceType 미지정 기간 검색에는 all-source date 인덱스(31.5MB) 적용.
- [x] `EXPLAIN (ANALYZE, BUFFERS)`, 같은 8컬럼·50,000행 4구성 WAL, 인덱스 크기, DDL lock/writer 대기 비교.
- [x] normal `CREATE INDEX`와 `CREATE INDEX CONCURRENTLY` 관찰 및 P4 API/UI 응답 확인.
- [x] P4-only Flyway V15 concurrent index migration을 fresh database와 기존 P4 schema에서 검증.

**P4 Gate:** 조회 계획 변경, source별 API 결과, 부분 인덱스 크기, 추가 WAL, DDL 중 writer 동작을 [P4 실행 기록](implementation/exec/p4.md)에 기록했다.

## 7. P6~P9 (확장)

- P6: Base Backup/WAL Archive → PITR → 데이터 검증 → 복원 런북.
- P7: Primary–Standby → 수동 Failover → 연결 전환 → 읽기 지연 → 비동기/동기 정책.
- P8: 실험 통합 실행기/운영 CLI/지표·보고서 자동화, 배치 크기 최적화 실험.
- P9: CloudNativePG(K8s)로 DB 이전, 복구·연결 전환 비교(선택).

## 8. 운영·개발 역량 흐름

P0~P1에서 관계 모델과 서비스 연결을 만들고, P2에서 지급 transaction과 데이터 정합성을 확인합니다. P3는 schema 변경, P4는 실행계획과 index 비용, P5는 session·Lock·API 지연 진단을 다룹니다. P6 이후에는 backup/PITR, replication/failover, 운영 automation을 이어갑니다.

## 9. 프로젝트 관리 지침

- 각 Phase는 목표·실행 절차·측정값을 정리한 뒤 다음 작업으로 이동.
- 장애 scenario와 제품 코드 변경을 따로 기록하고, 재현 결과에서 나온 문제를 회귀 확인으로 연결.
- 가정 fixture와 실제 측정 결과를 구분하고 DB identity·데이터 규모·시각·실행 SQL을 기록.

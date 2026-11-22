# 11. 구현 Phase와 구체 작업

## 1. Phase 총괄

**구현 기록:** P0~P1 실행 기반, P2a/P2b 핵심 구현과 수용 실험, P3 장애 보상·DDL Lock·migration 복구·구버전 API 교차 실행, P4 대량 검색·부분 인덱스·쓰기 비용·동시성 DDL, P5 Lock Wait 진단과 복구를 기록했다. 보상 요청 재전송은 사용자가 직접 확인할 항목으로 남겼다. 전체 TC-001~016과 P1 화면 Gate는 [검증 계획](10_검증및실험계획.md)에서 이어간다. 현재 단계는 [Phase 안내서](implementation/phase-guide.md), 실행 과정은 [구현 기록](implementation/exec.md)에서 확인한다.

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

- [ ] PostgreSQL 17 버전 고정, Docker 볼륨·포트·시드 구성.
- [ ] Git 작업 구조 `frontend/backend/database/migrations/experiments/docs`.
- [ ] `users/wallets/events/event_recipients/reward_grants/wallet_ledger` 기본 테이블 생성.
- [ ] `event_policy_audit`, `event_status_audit`, `batch_runs` 테이블 생성.
- [ ] DB 권한 `app`, `migration`, `observer` 분리(최초엔 최소 동작만).
- [ ] Flyway V1으로 새 DB 구축 성공.
- [ ] Spring Boot DB 연결, 트랜잭션 관리 설정, 헬스 체크.
- [ ] 샘플 유저/이벤트/잔액 생성, API 연결 확인.

**Gate:** DB·API를 깨끗한 상태로 재구축하는 명령과 기대 결과 문서화.

## 3. P1 — 사용자·운영자 화면

- [ ] 이벤트 목록/상세 + CLAIM/BATCH 방식 표시.
- [ ] 운영자 이벤트 생성(DRAFT), 오픈, 종료.
- [ ] 사용자 직접 보상 수령 API 연결(초기 구현, P2에서 SP로 확정).
- [ ] 지갑 Money·Point와 최근 원장 조회.
- [ ] 성공/중복/거부/결과 미확정 UI 상태 처리.
- [ ] 운영자에게 BATCH 대상·실행 시각 표시 UI(가볍게).

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
- [ ] 이벤트 상태 변경과 지급의 정밀한 경합 타이밍을 별도 부하/Lock 실험으로 확인.
- [ ] 자동 스케줄러와 1,000명을 초과하는 장시간 작업의 비동기 운영 방식 추가.

**P2a/P2b Gate:** 100동시 요청, 원자성 롤백, 1,000명 대상별 지급, 중복 worker 방지, crash recovery 뒤 미지급 재개와 DB 대사를 확인했다. **Phase 전체 Gate:** [10_검증및실험계획.md](10_검증및실험계획.md)의 TC-001~016 중 아직 수행하지 않은 사례와 P1 UI 흐름이 남아 있어 완료로 표시하지 않는다.

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

**P3 실습 Gate:** 기존/구버전 CLAIM, 장애 보상 원자성·원장 대사, DDL 대기 사슬, migration 실패/복구, 검색 경계, P2 데이터 분리를 [P3 실행 기록](implementation/exec/p3.md)에서 확인했다. 보상 요청 재전송은 사용자가 직접 확인할 항목으로 남겼다.

## 6. P4 — 성능

- [x] P4 전용 DB에 EVENT 990,000행/COMPENSATION 10,000행 fixture 생성 및 wallet/ledger 대사.
- [x] 인덱스 전 global cursor와 date query에서 filter 제거·parallel sequential scan 확인.
- [x] source JOIN 분기 가설 측정 후 공통 JOIN 유지, 검증된 source literal로 partial predicate 사용.
- [x] full source index(30MB)와 부분 cursor/date index(각 672KB) 비교 후 두 부분 인덱스 반영.
- [x] `EXPLAIN (ANALYZE, BUFFERS)`, 50,000행 INSERT WAL, 인덱스 크기, DDL lock/writer 대기 비교.
- [x] normal `CREATE INDEX`와 `CREATE INDEX CONCURRENTLY` 관찰 및 P4 API/UI 응답 확인.

**P4 Gate:** 조회 계획 변경, source별 API 결과, 부분 인덱스 크기, 추가 WAL, DDL 중 writer 동작을 [P4 실행 기록](implementation/exec/p4.md)에 기록했다.

## 7. P6~P9 (확장)

- P6: Base Backup/WAL Archive → PITR → 데이터 검증 → 복원 런북.
- P7: Primary–Standby → 수동 Failover → 연결 전환 → 읽기 지연 → 비동기/동기 정책.
- P8: 실험 통합 실행기/운영 CLI/지표·보고서 자동화, 배치 크기 최적화 실험.
- P9: CloudNativePG(K8s)로 DB 이전, 복구·연결 전환 비교(선택).

## 8. 업무 우선순위

펄어비스의 DB 설계·SP·버전 관리·패치 요구는 P0~P5에 직접 연결. 커넥트웨이브의 PostgreSQL 운영·백업·복제·CloudNativePG는 P3~P9에 연결. **어느 하나의 회사만을 위한 게임 기능을 추가하지 않는다.**

## 9. 프로젝트 관리 지침

- Phase가 통과하기 전 다음 Phase의 기술 스택만 먼저 대량 도입하지 않는다.
- 실험 시나리오는 구현 Phase와 별도 추적. 시나리오에서 관측한 문제만 실제 개선/회귀 검증에 반영.
- 미실행·실측 없음·가정·실제 결과를 모든 문서에서 구분.

# 07. DBA 운영 런북(Runbook)

**목적:** 개발 실험 환경에서 진단·조치·복구 작업을 동일한 절차로 재현하고 실행 결과를 기록한다.

## 1. 표준 운영 점검

| 확인 대상 | 조회/점검 위치 | 정상 여부 판단 |
|---|---|---|
| DB 연결 | `pg_isready`, API 오류율 | 접속 상태와 오류율 추이 |
| DB 세션 | `pg_stat_activity` | 장시간 실행·Lock 대기와 관련 SQL |
| SQL 실행 | `pg_stat_statements`, Slow SQL 로그 | 누적 시간·빈도·평균 실행 시간 |
| 장기 트랜잭션 | `pg_stat_activity.xact_start` | 시작 시각·세션 상태·대기 관계 |
| 테이블 통계 | `pg_stat_user_tables` | 대량 변경, Dead Tuple 경향 |
| WAL·복제 | `pg_stat_replication`, Standby LSN | 전송·쓰기·플러시·재생 상태 |
| 아카이브 | `pg_stat_archiver` + 저장소 파일 | 아카이브 실패·연속성 확인 |
| 백업 | 실제 복원 테스트 결과 | 복원 시간과 업무 데이터 대사 |

## 2. 사고 대응 기본 절차

1. **현재 영향 확인:** 어떤 API/지급 배치가 실패/타임아웃인지, 언제부터인지.
2. **증거 보존:** 시각·요청 ID·DB 로그·관련 SQL·지표·설정 변경 이력 수집.
3. **영향 범위 축소:** 추가 배치 실행 일시 중단 여부 결정. 이미 진행 중인 Tx에 미칠 영향 파악.
4. **DB 상태 판정:** 프로세스, 포트, 저장소, 트랜잭션, 복제, WAL 상태 확인.
5. **복구 전략 선택:** 단일 인스턴스 재시작 / Failover / PITR 등 사고 유형별로 선택.
6. **복구 검증:** DB 쓰기 성공뿐 아니라 `reward_grants`, `wallet_ledger`, `wallets` 대조.
7. **업무 재처리:** `event_recipients`에서 미지급 대상 조회 후 안정적인 request_id로 실행.
8. **사후 보고:** 타임라인, 원인, 조치, 증거, 재발 방지 항목을 기록.

## 3. S01 — 일괄 지급 중 DB 중단

### 사전 점검

- 실험 DB임을 확인, 기본 테스트 데이터와 대상 1000명 준비.
- 외부 요청/워커 로그에 `event_id`, `user_id`, `request_id`, 시작·응답 시각, 결과 기록.
- 커밋 완료와 클라이언트 성공 응답을 구분해 보관.
- WAL 설정, 체크포인트 시각, 실험 전 지급자 수 기록.

### 장애 주입

- 안전하게 분리된 로컬 환경에서 **비정상 종료** 실험. 정상 `docker stop`은 동일한 Crash Recovery 실험이 아님.
- 종료 시각과 워커의 연결 예외·UNKNOWN 결과를 기록.

### DBMS 복구

- PostgreSQL 재시작 로그에서 Crash Recovery와 WAL REDO 관련 기록 확인.
- 커밋된 WAL 기록 및 원래 데이터 볼륨이 보존되었다는 조건을 명시.
- CHECKPOINT는 WAL REDO 시작 지점을 제공한다. 지급 대상 1000명의 완료 현황은 `event_recipients`와 `reward_grants`를 대조해 계산한다.

### 업무 복구

```sql
SELECT r.user_id
FROM event_recipients r
LEFT JOIN reward_grants g
  ON g.event_id = r.event_id AND g.user_id = r.user_id
WHERE r.event_id = :event_id AND g.id IS NULL
ORDER BY r.user_id;
```

위 SQL의 `:event_id`는 JDBC 바인딩 자리표시자 예시이며 psql에서 단독 실행할 때는 실제 수치로 대체.

- 미지급 대상에만 지급 SP를 호출. 재처리 경쟁에서도 UNIQUE/멱등성으로 추가 지급을 차단.
- 최종 `대상 수=지급 수`, `중복 0`, 지갑·원장 합계 일치, 장애 전/후 미확정 요청의 최종 상태를 확인.
- 저장소가 사라진 장애라면 단순 Crash Recovery로 해결되지 않으므로 백업/HA 전략으로 전환.

## 4. S03 — DDL Lock 대기

### 확인 SQL

```sql
SELECT pid, usename, state, xact_start, wait_event_type, wait_event,
       pg_blocking_pids(pid) AS blocking_pids, query
FROM pg_stat_activity
WHERE datname = current_database()
ORDER BY xact_start NULLS LAST;
```

- 먼저 누가 Blocker인지, 왜 트랜잭션이 열린 채인지 확인.
- DDL은 `lock_timeout`을 갖는 전용 트랜잭션으로 짧게 실행.

```sql
BEGIN;
SET LOCAL lock_timeout = '2s';
ALTER TABLE wallet_ledger ADD COLUMN example_flag BOOLEAN;
COMMIT;
```

**주의:** 이 `ALTER`는 별도 연습용 컬럼이다. 실제 Migration에서는 컬럼명/기능/호환성을 정하고 테스트 결과를 남긴다. 세션 종료/강제 취소는 영향 확인 후 실행한다.

## 5. S04 — SQL 느림 신고

1. API/CS 화면의 느린 작업 식별.
2. 실제 호출 SQL, 인자, 데이터 건수·분포, 시간대 확보.
3. DB SQL 통계 및 실행 계획 획득.
4. Lock, CPU/I/O, 통계값, 인덱스 접근, 정렬 비용 등 원인 분해.
5. SQL/인덱스 변경 후보 비교.
6. 동일 워크로드 전·후, 조회/쓰기 성능·공간 사용량 보고.

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, amount, currency_type, created_at
FROM wallet_ledger
WHERE user_id = 101
ORDER BY created_at DESC, id DESC
LIMIT 20;
```

## 6. S07 — Failover 런북(구현 후 활성화)

- Primary 장애 상태와 **기존 Primary 격리 여부**를 확인.
- 승격 후보 Standby의 WAL 수신/재생 위치, 복제 방식, 지연 확인.
- HA 관리 도구의 전환 이벤트 기록·Proxy 대상 확인.
- 새 Primary에서 쓰기 성공 여부 검증.
- 외부 성공 응답 목록과 새 Primary 지급 기록 비교해 RPO 추정.
- 이전 Primary의 데이터 재합류 시나리오(재초기화/rewind 등)는 별도 설계 후 수행.

## 7. S08 — PITR 런북(구현 후 활성화)

- 삭제 시각과 대상 테이블 확인; 현 운영 DB에 추가 위험 작업 중지.
- 유효한 Base Backup + 중단 없는 WAL Archive 구간 확인.
- 목표 시간 직전으로 **새로운 복원 인스턴스** 생성.
- 복원된 지급·원장과 삭제 이후 새로 발생한 정상 데이터 대조.
- 어떤 기록을 어떻게 원 DB로 복사할지 변경 승인/검증 계획 작성.
- 별도 복원 성공만으로 프로덕션 복구 성공이라고 간주하지 않음.

## 8. 사고 기록 양식

```md
# INC-YYYYMMDD-NN
- 사고/실험 구분:
- 시작/발견/완화/복구 시각:
- 사용자가 본 증상:
- 영향 범위(요청/지급):
- DB 근거(SQL·로그·통계):
- 확인된 원인 / 미확인 가설:
- 수행한 조치 및 이유:
- 지급/원장/지갑 데이터 검증:
- RTO / RPO (해당 시):
- 재발 방지 변경:
- 추적할 남은 위험:
```

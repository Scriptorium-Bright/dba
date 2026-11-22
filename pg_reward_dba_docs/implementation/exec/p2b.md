# P2b 구현 및 실험 기록

**구현 전 계획:** [P2a~P2b 계획](../plan.md#p2ap2b-구현-전-계획)  
**프로젝트 문서:** [11 구현 Phase](../../11_구현Phase.md), [07 운영 런북](../../07_DBA_운영런북.md)  
**개념 참고:** `dba/concepts/04-versions-and-transactions.md`, `dba/chapters/01-crash-recovery.md`, `dba/prerequisites/03-mvcc-and-transaction-state.md`

## 구현한 기능

- BATCH 대상 명단은 DRAFT에서만 교체하고 중복·미등록 사용자를 거부한다. OPEN 이후 명단을 동결한다.
- worker는 이벤트별 세션 advisory lock을 잡고, 사용자마다 별도 `REQUIRES_NEW` 트랜잭션에서 P2a 공용 지급 프로시저를 호출한다.
- `(event_id,user_id)`에서 결정적으로 만든 UUID를 사용해 중단 후에도 같은 지급을 식별한다. 재개 대상은 실행 로그가 아니라 실제 `event_recipients LEFT JOIN reward_grants`에서 다시 계산한다.
- 회당 최대 1,000명까지 운영자가 수동 실행한다. 자동 scheduler/queue는 추가하지 않았다. 예약 시각 이전과 CLOSED 상태는 거부한다.

## 추가·변경한 파일과 이유

| 파일 | 내용 | 결정 이유 |
|---|---|---|
| `backend/src/main/java/dev/reward/service/BatchService.java` | 세션 advisory lock, 사용자별 트랜잭션, 미지급자 재계산, 실행 결과 | 한 사용자 실패가 이전 사용자의 커밋을 취소하지 않으며, 재실행이 DB의 실제 지급 상태를 따른다. |
| `backend/src/main/java/dev/reward/api/RewardController.java`, `ApiModels.java` | 대상 교체, 분포 조회, 수동 실행 API와 응답 제한 | worker 호출을 최대 1,000명으로 제한하고 UI가 결과를 읽게 한다. 헤더 역할은 데모 전용이다. |
| `frontend/src/App.jsx`, `styles.css` | 대상 ID 입력, 지급/미지급 요약, 최근 실행·오류, 수동 재개 | 기존 운영 관리 화면에서 준비와 실행을 이어서 확인한다. |
| `experiments/sql/seed_p2b_1000_users.sql`, `verify_p2b_event.sql` | 별도 ID 범위 사용자 생성과 분포/지갑/원장 대사 | 데모 사용자를 덮지 않고 지급 결과를 DB에서 확인한다. |
| `experiments/p2_acceptance.py`, `experiments/README.md` | 1,000명, 중복 worker, crash/recovery/retry 실험 | 강제 종료는 해당 실험 컨테이너에 한정하고 named volume은 삭제하지 않는다. |

## 의사결정

- 사용자별 커밋을 써서 일부 완료된 뒤 중단되어도 앞선 지급을 유지하고 남은 사용자만 이어서 처리한다.
- advisory lock은 실행 중 중복 worker를 막고, UNIQUE 제약은 worker 종료 뒤 요청이 다시 와도 중복 지급을 막는다.
- `batch_runs`는 실행 관측용이다. 지급 완료 여부는 grant와 ledger 실제 행으로 판정한다.
- 예약 시각이 지나면 worker 지연 복구를 허용하지만 운영자가 명시적으로 이벤트를 CLOSED로 바꾸면 추가 지급을 멈춘다.

## 실행·검증 결과

- 명령: `python3 experiments/p2_acceptance.py p2b --crash-after-first-page`.
- 이벤트 73에 1,000명 대상 명단을 만들고, 같은 이벤트의 동시 worker 요청은 HTTP 409로 거부됐다.
- 첫 페이지 600명은 각각 독립 커밋됐다. 다음 페이지에서 종료 신호 직전 688건을 관찰했으며 신호 전달 중 추가 커밋이 생겨 재시작 후 실제 지급은 825건, 미지급은 175명이었다. `688`은 중지 순간의 정확한 WAL 경계가 아닌 관찰치로 기록한다.
- PostgreSQL 로그에서 비정상 종료 감지, automatic recovery, `redo starts`, `redo done`, ready 상태를 확인했다. API 연결 종료로 worker 응답은 받지 못했지만 재시작 후 RUNNING 기록을 FAILED로 정리하고 175명만 다시 지급했다.

```text
resume run: target_count=175, paid_count=175, failed_count=0, status=COMPLETED
final distribution: recipients=1000, grants=1000, pending=0
reconciliation: 1000|1000|0|0|0  (대상|grant|미지급|grant-ledger 불일치|wallet-ledger 불일치)
```

- 첫 시도에서 readiness 확인이 port-forward 연결 종료를 재시도하지 못해 완료 판정이 지연됐다. readiness의 `RemoteDisconnected`/I/O 오류 재시도 후 재개 run을 완료했다. 사용자 초기 지갑 데이터는 삭제하지 않았다.

### V12 권한 변경 후 Crash Recovery 재검증

V12에서 지급 프로시저를 `SECURITY DEFINER`로 바꾸고 reward_app의 wallets/grants/ledger 직접 DML을 회수한 뒤 같은 실험을 다시 실행했다.

| 측정 | 결과 |
|---|---:|
| 첫 페이지 | 600명 지급 완료 |
| SIGKILL 당시 grants 관측 | 701 |
| PostgreSQL recovery 완료 뒤 grants | 796 |
| 재개 대상 및 지급 | 204 / 204 |
| 최종 대상·grants·pending | 1,000 / 1,000 / 0 |
| grant-ledger / wallet-ledger 불일치 | 0 / 0 |

PostgreSQL 로그에서 비정상 종료, automatic recovery, REDO 완료와 ready 상태를 확인했다. 종료 신호와 recovery 사이에 추가 커밋이 생겨 701건 관측 이후 복구 완료 시점에는 796건이었고, 실제 미지급 204명을 다시 계산해 처리했다. 최종 SQL은 `1000|1000|0|0|0`을 반환했다.

## 다음 실험

다음으로 COMMIT 뒤 API 응답 유실, 사용자별 트랜잭션 경계, worker 재시작 중 중복 호출을 별도로 측정한다. 현재 BATCH는 운영자가 API로 시작하는 동기 worker이며, 화면은 사용자별 지급과 실행 결과를 확인한다.

# P2 실험 도구

P2a 동시 요청·롤백과 P2b 배치 재개·Crash Recovery를 로컬 Compose DB에서 재현한다. 스크립트는 기존 볼륨을 지우지 않고 P2 전용 이벤트와 사용자 ID `910001–911000`을 추가한다.

## 준비

API와 PostgreSQL을 먼저 실행한다. 관리형 Colima에서는 화면 포트가 연결된 Docker 엔진의 Unix 소켓을 사용한다. 아래 포워딩은 SSH 연결당 한 번 설정한다. 이미 소켓이 있으면 포워딩 줄은 건너뛰고 `DOCKER_HOST`만 지정한다.

```sh
ssh -S /private/tmp/reward-lima/colima/ssh.sock -O forward \
  -L /private/tmp/reward-colima/active-docker.sock:/var/run/docker.sock localhost
export DOCKER_HOST=unix:///private/tmp/reward-colima/active-docker.sock
curl http://127.0.0.1:8082/actuator/health
```

API 주소가 다르면 `REWARD_API` 환경 변수로 바꾼다. Python 3 표준 라이브러리 외 패키지는 사용하지 않는다.

## P2a — 같은 사용자 100개 병렬 요청

```sh
python3 experiments/p2_acceptance.py p2a
```

새 CLAIM 이벤트를 만들고 같은 사용자·같은 `Idempotency-Key`로 100개 요청을 동시에 보낸다. `PAID=1`, `REPLAYED=99`, 다른 요청 ID의 `ALREADY_GRANTED`, 지급 1건·재화 원장 2건을 확인한다. 다음으로 원장 INSERT를 임시 트리거로 실패시킨 호출과 명시적 `ROLLBACK`을 실행하고 grant 잔존 0 및 지갑 잔액 불변을 확인한다. 임시 함수/트리거는 시험 트랜잭션과 함께 롤백된다.

CLAIM 재시도는 일반 이벤트 화면/API에서 직접 수행한다. 지급 뒤 사용자 한 명의 지갑을 다시 초기화하려면 `내 지갑` 화면을 사용한다. SQL로 초기화할 때는 아래 스크립트를 쓴다.

```sh
export DOCKER_HOST=unix:///private/tmp/reward-colima/active-docker.sock
docker compose exec -T db psql -U reward_bootstrap -d rewarddb \
  -v ON_ERROR_STOP=1 -v user_id=101 -f - \
  < experiments/sql/reset_local_user_wallet.sql
```

이 스크립트는 선택 사용자 하나의 모든 grant와 ledger를 지우고 Money/Point 잔액을 0으로 설정한다. users/events/recipients/batch_runs는 보존한다. grant snapshot이나 wallet-ledger 합계가 맞지 않으면 아무 변경 없이 실패한다. **로컬 실습 데이터 삭제용**이므로 운영 DB에서 실행하지 않는다.

운영 관리의 `전체 유저 초기화`는 DB 전체 사용자와 지급 내역을 대상으로 한다. 실험 사용자도 초기화되므로 화면에서 확인 문구를 입력해야 한다. API endpoint는 `APP_DEMO_WALLET_RESET_ENABLED=true`인 로컬 Compose에서만 활성화되고 기본 설정에서는 등록되지 않는다.

P2a acceptance 명령은 매회 새 이벤트를 만들고 사용자 101에게 보상을 지급한다. 개별 실습 지급만 지울 수는 없으며, 재실습이 필요하면 선택 사용자 지갑 초기화 또는 별도 로컬 DB를 사용한다.

## P2b — 대상 1,000명과 재개

```sh
python3 experiments/p2_acceptance.py p2b
```

사용자/지갑을 준비하고 API로 대상 명단을 등록한 뒤 첫 600명을 처리한다. 다음 실행은 남은 대상만 처리한다.

두 번째 실행이 진행 중이고 최소 650건이 커밋된 시점에 PostgreSQL과 API를 강제 종료하고 Crash Recovery 로그까지 확인하려면 명시적으로 다음 옵션을 사용한다. 종료 신호를 보내는 사이 추가 커밋이 생길 수 있어 정확한 장애 시점의 행 수는 실행 결과로 기록한다.

```sh
python3 experiments/p2_acceptance.py p2b --crash-after-first-page
```

이 옵션은 **이 프로젝트 Compose의 `db`와 진행 중인 `api` 컨테이너에만 SIGKILL**을 보낸다. 기존 워커가 DB 복구 뒤 자동으로 이어서 끝내지 않게 API도 중지한 다음, 같은 named volume로 DB와 API를 다시 시작한다. 로컬 실험 DB 전용이며 다른 프로젝트/운영 DB에서 실행하지 않는다.

최종 SQL 검증은 대상 1,000명, grant 1,000건, 미지급 0명, grant-원장 불일치 0건, 테스트 사용자 지갑-전체 원장 불일치 0명을 출력해야 한다. 실행한 날짜·명령·서버 버전·출력은 `pg_reward_dba_docs/implementation/exec.md`에 기록한다.

## P3 — 장애 보상과 schema 변경

P3 장애 주입, DDL Lock 대기, migration 검증 실패와 forward fix 순서는 [P3 실행 안내](../pg_reward_dba_docs/implementation/phase-guide.md#p3--장애-보상과-온라인-db-변경)에 있습니다. 실행 결과는 [P3 기록](../pg_reward_dba_docs/implementation/exec/p3.md)을 참고합니다.

## P4 — 대량 ledger, 쿼리 계획과 인덱스

P4는 `compose.p4.yaml`의 `rewardp4db`와 `reward_p4_pgdata`를 사용합니다. 데이터 seed, 기준 실행계획, 후보/최종 인덱스, 쓰기 WAL 측정과 동시 DDL 스크립트 실행 순서는 [P4 실행 안내](../pg_reward_dba_docs/implementation/phase-guide.md#현재-phase-p4--대량-지급-이력-검색과-인덱스-튜닝)를 참고합니다. 파일별 목적, 주석과 결정 이유, 측정 결과는 [P4 실행 기록](../pg_reward_dba_docs/implementation/exec/p4.md)에 있습니다.

## P5 — 운영 진단과 Lock Wait 대응

P5는 별도 PostgreSQL `rewardp5db`, volume `reward_p5_pgdata`, loopback 포트 `55434`를 사용합니다. API/UI를 추가하지 않고 DB 세션 상태, blocker, waiter, read 응답과 복구 순서를 관찰합니다.

```sh
export DOCKER_HOST=unix:///private/tmp/reward-colima/active-docker.sock
docker compose -p reward-p5 -f compose.p5.yaml up -d
docker compose -p reward-p5 -f compose.p5.yaml ps

psql -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p 55434 \
  -U reward_migration -d rewardp5db -f experiments/sql/p5_prepare_lock_lab.sql
python3 experiments/p5_lock_incident.py
psql -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p 55434 \
  -U reward_observer -d rewardp5db -f experiments/sql/p5_observe_lock_wait.sql
```

기본 계정 비밀번호는 로컬 전용 값이며 `.env.example`의 `P5_DB_*` 변수로 바꿀 수 있습니다. 컨테이너 초기화가 끝난 뒤 `reward_observer`는 P5 인스턴스의 `pg_monitor` 권한으로 관측 SQL을 실행합니다. P5 볼륨을 기존에 만들어 둔 경우 init SQL은 다시 실행되지 않으므로 `reward_bootstrap`으로 `GRANT pg_monitor TO reward_observer;`를 한 번 적용합니다.

`p5_lock_incident.py`는 A의 열린 트랜잭션이 보유한 행 Lock을 B의 UPDATE가 기다리게 하고, C의 SELECT가 이전 committed 값을 읽는지 측정합니다. 관측 기록을 남긴 뒤 A를 COMMIT해 B를 진행시키고 fixture 값을 100으로 복원합니다. 실행 결과와 운영 판단은 [P5 실행 기록](../pg_reward_dba_docs/implementation/exec/p5.md)에 추가합니다.

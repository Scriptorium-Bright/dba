# P2 운영 화면과 지갑 초기화 실행 기록

## 지갑 초기화 기능

`내 지갑` 화면에서 현재 사용자 한 명을 초기화하고, `운영 관리` 화면에서 전체 DB 사용자를 초기화합니다. 전체 초기화는 확인 문구 `전체 초기화`를 입력한 뒤 실행합니다. 사용자·이벤트·BATCH 대상 명단·`batch_runs` 이력은 남기고, 모든 사용자의 지갑 잔액과 grant·ledger 지급 이력을 정리합니다.

| 파일 | 내용 | 주석과 결정 이유 |
|---|---|---|
| `database/migrations/V5__local_wallet_activity_reset.sql` | 현재 사용자와 전체 사용자 초기화 함수, 권한 설정 | 잔액·grant·ledger를 한 단위로 정리하고, 삭제 전 grant snapshot 및 wallet-ledger 합계를 확인합니다. `SECURITY DEFINER`와 고정 `search_path`를 사용해 앱 역할의 실행 권한을 함수로 제한합니다. |
| `database/migrations/V6__protect_wallet_reset_from_batch_workers.sql` | BATCH 실행 상태를 확인하는 reset wrapper | BatchService가 이벤트 session advisory lock을 보유하는 동안 reset을 `BATCH_RUNNING`으로 반환합니다. 사용자별 commit 사이에 reset이 들어가는 경합을 제어합니다. |
| `backend/src/main/java/dev/reward/api/LocalWalletResetController.java` | `POST /api/me/wallet/reset`, `POST /api/admin/wallets/reset-all` | `APP_DEMO_WALLET_RESET_ENABLED` 설정과 데모 ADMIN 역할을 검사해 화면 권한 흐름을 구성합니다. |
| `backend/src/main/java/dev/reward/service/RewardService.java`, `backend/src/main/java/dev/reward/api/ApiModels.java` | 두 reset procedure 호출과 정리 건수 응답 | API transaction에서 함수를 실행하고 사용자 수, grant·ledger 삭제 수, 잔액 결과를 화면에 전달합니다. |
| `backend/src/main/resources/application.yml`, `compose.yaml` | reset endpoint 설정과 로컬 Compose 구성 | 화면과 API가 같은 P2 database/volume을 사용하도록 service·port 설정을 연결합니다. |
| `frontend/src/App.jsx`, `frontend/src/styles.css` | 내 지갑 초기화, 전체 사용자 초기화, 확인 단계와 결과 표시 | 사용자 한 명과 전체 사용자의 범위를 화면 위치와 확인 문구로 구별합니다. |
| `experiments/sql/reset_local_user_wallet.sql` | 선택 사용자 reset SQL 실행 | UI와 같은 DB 함수를 호출해 수동 실습 경로를 제공합니다. |
| `pg_reward_dba_docs/implementation/phase-guide.md`, `pg_reward_dba_docs/15_Stored_Procedure_설계.md` | reset 사용 순서, 범위, 제약과 권한 설명 | 버튼 위치와 보존 데이터, reset 전 대사 기준을 이어서 볼 수 있게 정리합니다. |

### 동작 순서

1. reset 함수가 대상 사용자와 연결된 이벤트·지갑·지급 기록을 잠급니다.
2. grant snapshot과 grant별 ledger 합, wallet 잔액과 사용자별 ledger 합을 대조합니다.
3. 정합성이 맞으면 해당 사용자의 grant와 ledger를 지우고 Money/Point 잔액을 0으로 갱신합니다.
4. 전체 사용자 reset은 같은 규칙을 DB 사용자 전체에 적용합니다.
5. BatchService의 session advisory lock을 확인해 실행 중인 BATCH와 초기화를 직렬화합니다.

## 화면과 API가 서로 다른 DB에 연결되던 문제 수정

화면의 `127.0.0.1:5174`와 API `127.0.0.1:8082`는 관리형 Colima가 제공하는 Docker daemon에 연결됩니다. 이전 API build는 별도 daemon에 배포되어 화면에서 호출하는 API에 V5/V6 route가 등록되지 않았습니다. Compose 명령의 `DOCKER_HOST`를 화면 포워딩과 같은 `/private/tmp/reward-colima/active-docker.sock`으로 맞춰 기존 database volume에 API를 배포했습니다.

| 확인 항목 | 결과 |
|---|---|
| Docker Engine | 화면 포워딩 엔진 ID `087938eb-2f8f-4d2f-bbde-9866fa90e37d` |
| 기존 P2 DB volume | `dbapj_reward_pgdata` 유지 |
| API schema | Flyway V5·V6 적용, schema version 6 |
| API health | `UP` |
| 화면 wallet API | `/api/me/wallet`이 기존 P2 DB의 잔액 반환 |
| reset endpoint mapping | GET 요청에 `405 Method Not Allowed`, `Allow: POST` 반환 |
| 화면 | `http://127.0.0.1:5174` HTTP 200 |

### 실행 명령

```sh
export DOCKER_HOST=unix:///private/tmp/reward-colima/active-docker.sock
docker compose up --build -d --no-deps api
docker compose ps
```

P2 서비스는 UI `5174`, API `8082`, PostgreSQL `5432`를 사용합니다. 지갑 초기화 버튼은 `내 지갑` 또는 `운영 관리`에서 실행합니다. 초기화 후 같은 사용자로 이벤트를 수령하면 지갑과 원장 변화를 다시 확인할 수 있습니다.

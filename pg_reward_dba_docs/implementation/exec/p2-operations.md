# 현재 Phase 안내서 추가 기록

## 추가한 파일

| 파일 | 담긴 내용 | 주석·의사결정 이유 |
|---|---|---|
| `pg_reward_dba_docs/implementation/phase-guide.md` | P0~P2 단계 지도, 현재 Phase의 목적과 검증 범위, CLAIM/BATCH 흐름도, 로컬 서비스 시작, UI/API/SQL 실습 순서, 기대 결과, 개념별 `dba/` 참고 자료, 미완료 항목과 데이터 주의사항 | 구현 이력만 읽지 않아도 현재 무엇을 실습할 수 있는지 바로 찾도록 별도 사용 안내서로 구성했다. Mermaid 흐름도와 “이 결과가 보장하지 않는 범위”를 함께 둬 실험 결과를 과도하게 일반화하지 않도록 했다. RAG라고 오해하지 않도록 `dba/`가 로컬 참고 자료 모음이고 런타임 retrieval 기능은 아니라는 설명을 넣었다. |
| `pg_reward_dba_docs/00_README.md` | 현재 P0~P2 상태와 Phase 안내서 링크 | 기존의 “실측 결과 없음” 문구는 P2 acceptance 결과와 모순돼 현재 기록에 맞게 고쳤다. 다만 전체 Gate 미완료 상태도 함께 표시했다. |
| `pg_reward_dba_docs/11_구현Phase.md` | Phase 총괄에서 Phase 안내서와 실행 기록으로 이동하는 링크 | 실행 순서를 확인하던 독자가 구현 개요에서 바로 실습 안내로 이동할 수 있게 연결했다. |
| `README.md` | Phase 안내서 바로가기 | 프로젝트를 처음 열었을 때 실행법 다음에 현재 단계의 목적과 확인 방법을 찾을 수 있도록 연결했다. |

문서 안의 인용 블록은 실습 주의나 관측 해석을 본문과 구분하기 위한 문서 주석이다. 구현 코드나 런타임 동작은 변경하지 않았다. 안내서에 적은 기대값과 미완료 경계는 기존 acceptance 결과 및 Phase 기록을 기준으로 했으며, 별도 기능 테스트는 실행하지 않았다.

## CLAIM 초기화와 멱등성 반복 실습 추가

| 파일 | 담긴 내용 | 주석·의사결정 이유 |
|---|---|---|
| `experiments/sql/reset_claim_test_reward.sql` | 로컬 지급 초기화 DB 함수를 호출하는 짧은 CLI 실행 파일 | 중복된 삭제 로직을 두지 않고 UI와 SQL 실습이 같은 DB 함수를 사용하게 했다. `result` 값을 출력해 초기화/미초기화 상태를 구분한다. |
| `pg_reward_dba_docs/implementation/phase-guide.md` | 같은 UUID의 `PAID → REPLAYED`, 새 UUID의 `ALREADY_GRANTED`, 대상 지급 reset 후 재실습 방법과 P2a 병렬 acceptance 연결 | 기존 acceptance 도구에 멱등 검증이 이미 있으므로 새 API를 만들지 않았다. UI/API 수동 실습과 100동시 요청 실험을 구분하고, 출력된 이벤트 ID로 해당 실행의 금액만 되돌리는 방법을 추가했다. |
| `experiments/README.md`, `README.md` | reset 스크립트 사용법과 안전 범위를 실험/프로젝트 시작 문서에서 안내 | 도구 위치를 찾아 실행할 수 있도록 연결하되, reset은 로컬 CLAIM 데이터 삭제이고 전체 DB 초기화는 별도 동작임을 명시했다. |

reset 스크립트는 문서와 SQL만 추가했다. 기존 지갑·원장 데이터에 실행하지 않았고, acceptance 실험도 새로 실행하지 않았다. 사용자가 로컬 실습에서 선택한 CLAIM grant에 직접 적용할 수 있도록 예시와 기대 대사 결과를 안내서에 넣었다.

## 프런트에서 멱등성 테스트와 지급 초기화 사용

> 이 절은 최초 구현 이력이다. 이후 요청으로 자동 멱등성 패널과 이벤트 단건 초기화 UI/API를 제거했다. 현재 프런트의 초기화 방법은 아래 “내 지갑·전체 유저 초기화 추가” 절을 따른다.

사용자 피드백으로 실습 경로를 문서/SQL에서 프런트까지 연결했다. 운영자 화면에서 상단의 현재 데모 사용자 기준으로 CLAIM 이벤트에 대해 동일 키 두 번 호출하고, `PAID → REPLAYED` 및 지갑 증가량을 보여준다. 지급이 이미 있으면 별도 확인 단계를 거친 뒤 선택한 CLAIM grant 1건을 원장과 wallet에서 함께 되돌릴 수 있다.

| 파일 | 담긴 내용 | 주석·의사결정 이유 |
|---|---|---|
| `database/migrations/V4__local_claim_reset_function.sql` | 선택한 CLAIM 지급 한 건의 grant/원장 삭제, wallet 조정, 데이터 대사 검사를 하는 DB 함수와 앱 역할 `EXECUTE` 권한 | V3에서 `reward_app`에 불필요한 DELETE 권한을 주지 않기로 했으므로, 테이블 권한을 넓히는 대신 좁은 `SECURITY DEFINER` 함수로 한 지급만 처리한다. `search_path` 고정, PUBLIC 권한 회수, 이벤트·grant·wallet 잠금, 원장 대사 검사를 둬 대상과 권한을 제한한다. |
| `pg_reward_dba_docs/15_Stored_Procedure_설계.md` | V4 로컬 반복 실습 함수의 범위와 권한 설계 | 지급용 프로시저와 테스트 초기화 함수가 다른 목적임을 분리해 운영 보상 취소로 오해하지 않도록 기록한다. |
| `backend/src/main/java/dev/reward/api/LocalClaimResetController.java` | `/api/admin/events/{eventId}/users/{userId}/claim-reset` 로컬 실습 endpoint | 설정이 켜진 때만 controller를 등록하고 관리자 데모 역할을 확인한다. 보상 취소 제품 API가 아니라 개발 실습 route임을 클래스 주석에 명시했다. |
| `backend/src/main/java/dev/reward/service/RewardService.java`, `backend/src/main/java/dev/reward/api/ApiModels.java` | DB 함수 결과 매핑과 초기화 수량·잔액 응답 | Spring 트랜잭션이 함수 호출을 감싸며 함수의 상태 코드를 기존 API 404/409 의미로 바꿔 화면에 문제 원인을 전달한다. |
| `backend/src/main/resources/application.yml`, `compose.yaml` | `APP_DEMO_CLAIM_RESET_ENABLED` 기본값 false, 로컬 Compose에서만 true 설정 | 일반 실행에서 route를 숨기고, loopback으로 공개한 프로젝트 Compose에만 UI 기능을 켠다. |
| `frontend/src/App.jsx`, `frontend/src/styles.css` | 운영자 이벤트 카드의 `멱등성 2회 테스트`, 지급 초기화, inline 확정·결과 표시 및 버튼 상태 | 같은 요청 키를 코드가 직접 재사용하고 실제 지갑 변화도 비교해 사용자가 DB/API를 따로 조작하지 않고 결과를 볼 수 있다. 삭제 동작은 이벤트/사용자/금액을 보여주는 inline 확인 단계를 둔다. |
| `pg_reward_dba_docs/implementation/phase-guide.md`, `experiments/README.md`, `README.md` | 화면에서 실습하는 경로와 버튼이 안 보일 때 Compose rebuild 안내 | 사용자가 화면에서 찾고 바로 재현할 수 있도록 기존 SQL 절차와 함께 UI 순서를 추가했다. |

### 빌드와 안전한 endpoint 확인

- `DOCKER_HOST=unix:///private/tmp/reward-colima/default/docker.sock docker compose up --build -d` 성공. Docker Maven 빌드가 완료됐고 Flyway가 V4를 적용해 schema version 4가 됐다.
- API health `UP`, 화면 `http://127.0.0.1:5174` HTTP 200을 확인했다.
- 관리자 역할 헤더로 존재하지 않는 이벤트 ID를 초기화 endpoint에 전달했다. 함수가 `EVENT_NOT_FOUND`를 API 404로 반환해 Compose flag, route, DB 함수 호출 경로를 확인했다. grant/지갑 행은 변경되지 않았다.
- `frontend`에서 `npm run build` 성공.
- 실제 idempotency 지급이나 초기화 동작은 기존 데모 데이터에 실행하지 않았다. 브라우저 자동화는 로컬 탭 관찰을 시도했지만 자동 승인 검토가 페이지 열람을 거부해 화면 캡처/클릭 검증은 완료하지 못했다. 전체 기능 빌드와 안전한 API 경로 확인을 기준으로 기록하며, 수령·초기화 상호작용은 사용자가 화면에서 확인할 수 있다.

## 내 지갑·전체 유저 초기화 추가

사용자 요청을 다시 확인해 초기화 위치와 범위를 정정했다. 자동 멱등성 2회 호출 UI와 이벤트별 초기화 UI를 제거했다. 일반 이벤트 수령과 멱등성 방어 로직은 유지하고, 초기화는 `내 지갑`의 현재 사용자 한 명과 `운영 관리`의 전체 DB 사용자로 제공한다.

| 파일 | 담긴 내용 | 주석·의사결정 이유 |
|---|---|---|
| `database/migrations/V5__local_wallet_activity_reset.sql` | 현재 사용자 reset 함수와 전체 사용자 reset 함수, 권한 설정, V4의 단건 CLAIM reset 함수 제거 | 잔액만 0으로 덮지 않고 grant와 원장을 함께 정리해야 원장 합계가 맞고 다음 지급이 가능하다. 기존 지급 SP와 같은 순서로 이벤트/지갑을 잠그고, grant snapshot과 wallet-ledger 합계를 삭제 전에 확인한다. 불일치면 아무 행도 지우지 않는다. 앱 계정에 직접 DELETE 권한을 주지 않는다. |
| `database/migrations/V6__protect_wallet_reset_from_batch_workers.sql` | 두 reset entry point가 실행 중 BATCH 이벤트의 advisory lock을 확인하는 wrapper | BATCH는 사용자별 커밋 사이에 reset이 끼어들 수 있어 이벤트 행 잠금만으로 전체 worker 중단을 보장하지 못한다. 같은 advisory lock key를 transaction lock으로 확인하고 worker가 있으면 `BATCH_RUNNING`을 반환해 reset을 거부한다. 내부 V5 함수의 앱 직접 실행 권한은 회수한다. |
| `backend/src/main/java/dev/reward/api/LocalWalletResetController.java` | `POST /api/me/wallet/reset`과 ADMIN 전용 `POST /api/admin/wallets/reset-all` | `APP_DEMO_WALLET_RESET_ENABLED`가 켜진 로컬 Compose에서만 controller가 등록된다. 전체 reset은 개발용 ADMIN 헤더도 검사한다. |
| `backend/src/main/java/dev/reward/service/RewardService.java`, `backend/src/main/java/dev/reward/api/ApiModels.java` | 두 DB 함수를 Spring 트랜잭션에서 호출하고 정리한 사용자/grant/원장 수를 응답 | DB 대사 불일치나 지갑 누락은 API 충돌/오류로 바꿔 화면에 알린다. 호출 중 오류가 나면 함수와 API 트랜잭션이 함께 롤백된다. |
| `backend/src/main/resources/application.yml`, `compose.yaml` | reset controller 기본 비활성, 로컬 Compose에서만 `APP_DEMO_WALLET_RESET_ENABLED=true` | 파괴성 기능을 일반 실행 환경에서 실수로 열지 않도록 기본값을 닫아 둔다. |
| `frontend/src/App.jsx`, `frontend/src/styles.css` | 내 지갑의 현재 사용자 reset, 운영 관리의 전체 사용자 reset, inline 확인·전체 초기화 문구 입력·결과 피드백. 자동 멱등성 실습 UI는 제거 | 한 명 reset과 전체 reset을 화면 위치와 확인 문구로 구분한다. 전체 대상에는 선택 목록 밖의 P2 실험 사용자도 포함됨을 사전에 알리고, 사용자/이벤트/대상 명단/배치 실행 이력은 유지된다고 표시한다. |
| `experiments/sql/reset_local_user_wallet.sql` | UI 대신 SQL로 선택 사용자 지갑 및 지급 이력을 초기화하는 로컬 스크립트 | API/UI와 같은 DB 함수를 호출해 정리 규칙을 한 곳에 둔다. 전체 사용자 SQL reset은 추가하지 않아 광범위한 초기화는 UI 확인 단계에서만 실행하게 했다. |
| `pg_reward_dba_docs/implementation/plan.md`, `phase-guide.md`, `pg_reward_dba_docs/15_Stored_Procedure_설계.md`, `experiments/README.md`, `README.md` | 새 초기화 범위와 절차, API 경로, 보존 범위, DB/원장 판단을 구현 전·후 문서에 반영 | 초기 문서의 “멱등성 버튼” 안내를 제거하고 사용자가 직접 이벤트/API에서 테스트한 뒤 적절한 wallet reset을 선택하게 했다. |
| `backend/src/main/java/dev/reward/api/LocalClaimResetController.java`, `experiments/sql/reset_claim_test_reward.sql` | 이전 이벤트 단건 초기화 UI/API와 SQL wrapper 제거 | 요청 범위에 없던 이벤트별 버튼을 없애고 `V5`에서 이전 DB 함수도 제거한다. `V4` migration 파일은 적용 이력을 바꾸지 않도록 그대로 둔다. |

### 판단

- 현재 사용자 reset은 그 사용자 하나의 Money/Point, 전체 grant, ledger만 정리한다.
- 전체 reset은 DB의 모든 사용자와 지급 데이터를 대상으로 하며 P2 실험 사용자도 포함한다. 입력 문구를 요구해 버튼 오작동을 줄인다.
- 두 reset 모두 사용자·이벤트·BATCH 명단·`batch_runs` 이력은 보존한다. run history는 과거 실행의 사실 기록이므로 잔액 reset과 함께 삭제하지 않는다.
- BATCH worker는 전체 실행 동안 session advisory lock을 보유하므로 reset wrapper가 대응하는 transaction advisory lock을 먼저 시도한다. 실행 중이면 정리보다 안전한 conflict 응답을 우선한다.
- 지갑이나 원장 합계가 이미 맞지 않으면 “정리”로 불일치를 숨기지 않고 중단한다.
- 사용자가 직접 시험할 수 있도록 이 구현에서는 자동 중복 요청/멱등성 성공 판정 기능을 두지 않는다.

### 적용 및 확인

- `frontend`에서 `npm run build` 성공.
- `DOCKER_HOST=unix:///private/tmp/reward-colima/default/docker.sock docker compose up --build -d` 성공. Maven 패키지 빌드 `BUILD SUCCESS`, Flyway V1~V6 검증 및 V6 적용 완료.
- 최초 API `/actuator/health`의 `UP`은 분리된 엔진에서 온 응답이었다. 화면과 API가 같은 엔진을 쓰는지는 지갑 값 대조 전까지 확인되지 않았으며, 아래의 복구 기록에 원인과 올바른 실행 방법을 남겼다.
- 실제 내 지갑 reset, 전체 유저 reset, CLAIM 요청/멱등성 동작은 실행하지 않았다. UI build·마이그레이션·서버 상태만 확인했다.
- 화면 캡처/클릭 검증은 완료하지 않았다. 이전 브라우저 접근 시 자동 승인 검토가 현재 Chrome 탭 열람을 계정 세션 데이터 접근 가능성 때문에 거부했다. 이 제한을 우회하지 않고 코드/빌드/HTTP 응답으로 확인 범위를 기록했다.

## 지갑 초기화가 반영되지 않던 원인과 복구

### 구현 전 확인

- 사용자는 화면에서 지갑 초기화가 되지 않는다고 보고했다. 지갑 데이터 변경을 피하기 위해 reset POST는 실행하지 않았다.
- `127.0.0.1:8082`의 지갑 GET은 현재 사용자의 기존 잔액을 돌려줬지만 초기화 경로 GET은 `404`였다. 화면 프록시는 이 포트로 요청하고 있었다.
- Compose 명령에 사용했던 `/private/tmp/reward-colima/default/docker.sock`의 Docker Engine ID는 `a3a8a2ae-decb-4a0e-8ba9-c177cdd8ef4c`였고, 화면 포트를 제공하는 SSH Colima 엔진 ID는 `087938eb-2f8f-4d2f-bbde-9866fa90e37d`였다. 전자는 seed-only 별도 DB였고, 후자는 사용자와 P2 실험 데이터가 있는 기존 `dbapj_reward_pgdata` 볼륨을 사용하고 있었다.
- 기존 API 컨테이너는 Flyway V4에서 멈춰 있었다. 따라서 화면에서 보낸 POST를 처리할 `/api/me/wallet/reset` route 자체가 없었다.
- 기존 DB에서 사용자별 wallet과 ledger 합계 불일치 0건, grant snapshot 불일치 0건, 활성 advisory lock 0건을 SELECT로 확인했다. 초기화 기능이 정합성 검사나 실행 중 BATCH 때문에 막힌 상태는 아니었다.

### 적용한 변경과 결정

| 파일 | 담긴 내용 | 주석·의사결정 이유 |
|---|---|---|
| `compose.yaml` | API는 화면 포워딩과 연결된 `127.0.0.1:8082`에서 계속 제공 | 포트를 바꾸면 브라우저의 기존 포워딩은 계속 이전 엔진을 가리킨다. 올바른 Docker 엔진에 API를 배포하는 쪽으로 고쳤다. YAML 주석에도 포트와 엔진 연결 이유를 적었다. |
| `frontend/vite.config.js` | API/actuator 프록시가 `localhost:8082`를 사용한다는 주석 | 프런트는 이미 올바른 로컬 포트를 요청하고 있었다. 우회 포트 대신 백엔드를 그 포트의 실제 Colima 엔진에 맞췄다. |
| `experiments/p2_acceptance.py` | Docker 명령 기본값을 `active-docker.sock`으로 바꾸고 함수 설명을 같은 화면 DB 사용으로 수정 | P2 crash 옵션은 DB/API를 내렸다 다시 올리므로 다른 엔진을 선택하면 화면 상태와 실험 상태가 분리된다. 이후 실습도 같은 DB를 쓰게 했다. |
| `experiments/README.md` | SSH 포워딩된 Docker API socket 설정과 올바른 `DOCKER_HOST`, SQL reset 실행법 | SQL 실습이 사용자가 실제 화면에서 보는 DB에 적용되게 했다. 포워딩은 한 SSH 연결당 한 번 설정한다. |
| `pg_reward_dba_docs/implementation/phase-guide.md` | 현재 API 주소와 Compose 시작, reset SQL, P2 실습 명령을 올바른 socket 기준으로 정리 | 초기 안내가 잘못된 Docker 엔진을 선택해 같은 문제가 반복되지 않도록 실행 절차를 함께 고쳤다. |
| `README.md` | 관리형 Colima에서는 Phase 안내서의 서비스 시작 절차를 따르도록 연결 | 일반 Docker 환경과 현재 포워딩 환경의 실행법을 구분했다. |
| `pg_reward_dba_docs/implementation/plan.md` | 엔진 일치 확인, volume 보존, API만 재빌드하는 계획과 완료 조건 | 대상 DB를 식별하고 변경 범위를 좁힌 판단을 남겼다. |
| `pg_reward_dba_docs/implementation/exec.md` | 원인, 엔진 ID, 적용·검증 결과와 미실행 항목 | 이전의 “health UP”만으로 화면 서버를 확인했다고 오해하지 않도록 실행 기록을 정정했다. |

새 제품 파일은 추가하지 않았다. API 초기화 기능은 이미 구현돼 있었고, 문제는 현재 UI를 제공하는 엔진에 그 코드가 배포되지 않은 것이었다. 기존 사용자 DB volume을 유지하고 API만 그 엔진에서 다시 빌드했다. 분리된 진단용 Compose 컨테이너는 `docker compose down`으로 중지했으며 `-v`를 사용하지 않아 해당 volume도 남아 있다. 테스트 중 만든 8083 포워딩도 제거했고, 최종 API 포트는 8082로 유지한다.

### 적용 및 확인

- 실제 화면 엔진에서 `docker compose up --build -d --no-deps api`를 실행했다. 기존 DB 컨테이너와 `dbapj_reward_pgdata` 볼륨은 그대로 유지됐다.
- Flyway V5와 V6가 해당 DB에 적용되어 schema version 6이 됐다. migration은 reset 함수를 추가·보호하며 지급 행을 삭제하지 않는다.
- API health는 `UP`; `http://127.0.0.1:5174/api/me/wallet`은 현재 DB의 기존 지갑 조회를 반환한다.
- `http://127.0.0.1:5174/api/me/wallet/reset`의 GET은 `405 Method Not Allowed`, `Allow: POST`를 반환한다. 초기화 경로가 프런트 프록시 뒤에 등록됐다는 확인이다.
- 실제 wallet reset POST, 전체 사용자 reset, CLAIM 재요청은 실행하지 않았다. 기존 잔액과 지급 내역을 보존했다.

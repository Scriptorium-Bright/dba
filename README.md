# PostgreSQL Reward Operations Lab

게임 이벤트 보상 지급을 작은 서비스로 재현해 PostgreSQL 스키마와 운영 흐름을 학습하는 로컬 프로젝트입니다. 기능 범위와 구현 이력은 [프로젝트 문서](pg_reward_dba_docs/00_README.md)에서 관리합니다.

## 실행

필요 환경: Docker Compose, Node.js 22.12 이상, npm.

```sh
cp .env.example .env
docker compose up --build
```

현재 관리형 Colima 환경에서는 화면/API 포트 포워딩이 SSH로 연결된 Docker 엔진을 사용합니다. 그 환경에서 실행할 때는 [Phase 안내서](pg_reward_dba_docs/implementation/phase-guide.md)의 서비스 시작 명령을 사용하세요. 다른 Docker 환경에서는 위 명령을 그대로 사용할 수 있습니다.

다른 터미널에서 화면을 실행합니다.

```sh
cd frontend
npm ci
npm run dev
```

브라우저에서 `http://127.0.0.1:5174`을 엽니다. API는 `http://127.0.0.1:8082`, 헬스 확인은 `http://127.0.0.1:8082/actuator/health`입니다. 처음 실행할 때 Flyway가 테이블과 데모 데이터를 구성합니다.

## 데모 흐름

- 상단에서 사용자 101~103을 선택해 이벤트 수령 결과와 사용자별 지갑을 확인합니다.
- CLAIM 이벤트를 선택해 보상을 받으면 `reward_grants`, `wallets`, `wallet_ledger`가 반영됩니다.
- 운영자 모드에서 DRAFT 이벤트를 만들고 `DRAFT → OPEN → CLOSED` 상태 전이를 수행합니다.
- 운영자 모드에서 BATCH 대상 명단을 DRAFT 동안 교체하고, OPEN 후 예약 시각이 지나면 미지급자를 수동 실행할 수 있습니다. 재실행은 DB 지급 기록 기준으로 미지급자만 처리합니다.

화면의 사용자·운영자 전환은 개발용 헤더 기반 데모입니다. 인증이나 운영 권한 검증으로 사용할 수 없습니다. Compose는 DB와 API 포트를 loopback에만 공개하고, 데모 비밀번호도 로컬 개발 전용입니다.

## 데이터 초기화

`docker compose down`은 컨테이너를 내리고 로컬 DB 볼륨은 유지합니다. 샘플 DB를 처음부터 다시 만들 때만 다음 명령을 사용합니다. 이 명령은 로컬 볼륨의 데이터를 삭제합니다.

관리형 Colima에서는 이 명령을 실행하기 전에 [Phase 안내서의 서비스 시작 절차](pg_reward_dba_docs/implementation/phase-guide.md#1-로컬-서비스-열기)를 따라 화면과 같은 Docker 엔진을 선택하세요. 다른 엔진에서 실행하면 화면 DB는 그대로 남거나 예상하지 않은 DB를 지울 수 있습니다.

```sh
docker compose down -v
docker compose up --build
```

전체 DB를 삭제하지 않고 한 사용자의 지갑을 초기화하려면 `내 지갑` 화면의 `내 지갑 초기화`를 사용하세요. DB에 있는 모든 사용자 지갑을 초기화하려면 운영자 역할로 `운영 관리`에 들어가 `전체 유저 초기화`를 사용합니다. 두 동작은 잔액과 지급 grant·원장을 함께 초기화하며, 사용자·이벤트·배치 실행 이력은 보존합니다. 자세한 단계는 [Phase 안내서의 지갑 초기화 절차](pg_reward_dba_docs/implementation/phase-guide.md#프런트에서-지갑-초기화하기)를 참고하세요. 로컬 개발 DB 전용입니다.

P0~P5 phase별 구현 기록과 현재 P5 실습 안내는 [`pg_reward_dba_docs/implementation/`](pg_reward_dba_docs/implementation/)에 있습니다.

현재 구현 Phase의 목적과 화면·API·SQL 확인 방법은 [Phase 안내서](pg_reward_dba_docs/implementation/phase-guide.md)를 참고하세요.

# PostgreSQL 이벤트 보상 DB 운영 실습

게임 이벤트 보상 서비스를 예제로 RDBMS 설계, 지급 트랜잭션, 스키마 배포, 대량 조회 튜닝, Lock Wait와 API 지연 진단을 실습하는 프로젝트입니다. PostgreSQL 17.12, Spring Boot, React, Docker Compose를 사용합니다.

`reward_grants`는 지급 사실, `wallets`는 현재 잔액, `wallet_ledger`는 재화별 변동 이력을 기록합니다. Flyway migration과 운영 SQL, 재현 harness, 실행 결과는 저장소에서 함께 관리합니다.

## 빠른 실행

필요 환경은 Docker Compose, Node.js 22.12 이상, npm입니다.

```sh
cp .env.example .env
docker compose up --build
```

다른 터미널에서 화면을 실행합니다.

```sh
cd frontend
npm ci
npm run dev
```

화면은 `http://127.0.0.1:5174`, API는 `http://127.0.0.1:8082`, PostgreSQL은 `127.0.0.1:5432`에서 사용할 수 있습니다. 첫 API 기동 때 Flyway가 테이블과 데모 데이터를 구성합니다.

관리형 Colima에서는 화면, API, 데이터베이스가 같은 Docker Engine을 바라보도록 [Phase 안내서](pg_reward_dba_docs/implementation/phase-guide.md)의 서비스 시작 절차를 실행합니다.

## 화면에서 확인할 흐름

- 사용자 101~103을 선택해 이벤트 목록, 보상 수령, 지갑 잔액과 원장을 확인합니다.
- 운영자 화면에서 DRAFT 이벤트를 만들고 `DRAFT → OPEN → CLOSED` 전이를 수행합니다.
- BATCH 대상자를 등록하고 예약 시각 이후 지급 작업을 실행합니다. 재실행 시 DB 지급 기록을 기준으로 미지급자를 계산합니다.
- `내 지갑`과 `운영 관리` 화면에서 각각 한 사용자 또는 전체 사용자의 지갑 데이터를 초기화합니다.

개발 환경은 사용자와 역할을 요청 헤더로 전환합니다. Compose 포트는 loopback에 공개되며 데모 계정은 `.env.example`에 있습니다.

## 로컬 데이터 초기화

한 사용자 또는 전체 사용자의 지갑을 초기화할 때는 화면의 `내 지갑 초기화` 또는 `전체 유저 초기화`를 사용합니다. 잔액, 지급 기록, 원장을 함께 초기화하고 사용자·이벤트·배치 실행 이력은 보존합니다.

전체 샘플 데이터베이스를 처음부터 구성할 때는 volume을 삭제합니다. 아래 명령은 Compose가 관리하는 로컬 PostgreSQL 데이터를 제거합니다.

```sh
docker compose down -v
docker compose up --build
```

## 운영·개발 문서

- [문서 인덱스](pg_reward_dba_docs/00_README.md): 데이터 모델, 아키텍처, 운영 절차, migration과 실험 목록
- [Phase 실행 안내](pg_reward_dba_docs/implementation/phase-guide.md): 각 실습의 목적, 포트, API·SQL 실행 방법
- [구현 계획](pg_reward_dba_docs/implementation/plan.md): 작업 범위와 설계 결정
- [구현 실행 기록](pg_reward_dba_docs/implementation/exec.md): 변경 파일, 결정 이유, 실행 결과
- [실험 harness 설명](experiments/README.md): 데이터 적재, 장애 재현, 지표 수집

P2부터 P5까지 지급 동시성·Crash Recovery, schema 변경, 100만 행 검색, Lock Wait 및 API 지연 진단 기록을 단계별 문서와 원본 결과에서 확인할 수 있습니다.

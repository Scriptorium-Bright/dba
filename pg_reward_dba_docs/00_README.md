# PostgreSQL 보상 데이터베이스 운영 실습 — 문서 인덱스

- **문서 기준:** 설계 v2.0, 2026-11-21
- **현재 구현:** P0~P1 서비스와 모델, P2 동시 지급·BATCH 재개, P3 장애 보상·schema 배포, P4 100만 건 조회 튜닝, P5 Lock Wait·API 지연 진단
- **기술 구성:** PostgreSQL 17.12, Spring Boot/JDBC, React, Docker Compose, Flyway, Python 실험 harness
- **시작점:** [Phase 안내](implementation/phase-guide.md)에서 환경과 실습 순서를 확인하고 [구현 기록](implementation/exec.md)에서 변경과 측정 결과를 읽습니다.

## 설계·운영 문서

| 파일 | 주제 |
|---|---|
| [01_도메인.md](01_도메인.md) | 이벤트 보상, CLAIM/BATCH, 지갑과 원장 관계 |
| [02_요구사항분석서.md](02_요구사항분석서.md) | 기능·비기능 요구와 인수 기준 |
| [03_프로젝트설계서.md](03_프로젝트설계서.md) | 데이터 흐름과 업무 규칙 |
| [04_아키텍처.md](04_아키텍처.md) | React, Spring Boot, PostgreSQL, migration·관측 연결 |
| [05_테이블설계서.md](05_테이블설계서.md) | ERD, 키, 제약, 정규화와 의도한 중복 |
| [06_시나리오목록.md](06_시나리오목록.md) | 장애·변경·조회 실험 시나리오와 입력 |
| [07_DBA_운영런북.md](07_DBA_운영런북.md) | 지급 확인, 재처리, Lock 대응, 백업 복원 절차 |
| [08_모니터링및진단.md](08_모니터링및진단.md) | API·Hikari·PostgreSQL 지표와 관측 SQL |
| [09_DB변경배포계획.md](09_DB변경배포계획.md) | Flyway 배포, 데이터 검증, 실패 복구 |
| [10_검증및실험계획.md](10_검증및실험계획.md) | 인수 조건, 실행 절차, 결과 기록 방식 |
| [11_구현Phase.md](11_구현Phase.md) | phase 순서와 완료 기준 |
| [12_설계결정기록.md](12_설계결정기록.md) | 데이터 모델·트랜잭션·인덱스 선택 이유 |
| [13_운영실습_증거.md](13_운영실습_증거.md) | 재현 절차, 원본 결과, 운영 변경 이력을 연결하는 방법 |
| [14_용어및공식참조.md](14_용어및공식참조.md) | PostgreSQL 개념과 공식 문서 링크 |
| [15_Stored_Procedure_설계.md](15_Stored_Procedure_설계.md) | 지급 procedure의 입력, 상태 결과, 트랜잭션 |

## 구현과 실행

| 파일 | 내용 |
|---|---|
| [implementation/plan.md](implementation/plan.md) | 실행 전 범위, 설계 선택, phase별 확인 항목 |
| [implementation/exec.md](implementation/exec.md) | phase별 변경 파일, 코드 주석, 결정 이유와 결과 |
| [implementation/phase-guide.md](implementation/phase-guide.md) | 서버·UI·API·SQL 실습 안내와 포트 |
| [../experiments/README.md](../experiments/README.md) | 데이터 fixture, 재현 harness, 결과 파일 설명 |

## 프로젝트 흐름

```text
CLAIM/BATCH 지급
  → reward_grants 업무 지급 사실
  → wallets 현재 잔액 + wallet_ledger 변동 기록
  → 대사 SQL로 지급·잔액 정합성 확인
```

현재 실습은 동시 요청, 사용자 단위 커밋, PostgreSQL Crash Recovery, forward-only schema 변경, 100만 행 실행계획, 인덱스 비용, Hikari pool 포화, row Lock wait, 느린 SQL을 순서대로 다룹니다. P2~P5 실행 수치와 재현 파일은 각 phase의 실행 기록에서 확인합니다.

## 기록 기준

- 실험 결과에는 DB 버전, 데이터 행 수, 실행 SQL·도구, 시각, 반환 행과 정합성 확인을 함께 기록합니다.
- 실행계획은 actual rows, filter 제거 행, buffers와 index definition을 포함합니다.
- API 응답 시간, SQL 실행 시간, Hikari wait, PostgreSQL wait event를 측정 구간별로 구분합니다.
- reset·DROP·SIGKILL 같은 작업은 이름이 고정된 로컬 disposable 환경과 SQL guard를 사용합니다.
- 설계 변경은 [12_설계결정기록.md](12_설계결정기록.md)에 배경·선택·운영 영향을 남깁니다.

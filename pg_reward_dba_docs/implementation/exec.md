# 구현 실행 기록

각 phase의 변경 파일, 목적과 코드 주석, 설계 결정의 이유, 실행 명령과 관찰값을 기록합니다. 실행 기록은 phase별 문서로 나누고, P3는 진행 순서대로 로그를 남깁니다.

| Phase | 실행 기록 | 상태 |
|---|---|---|
| P0~P1 | [환경·초기 사용자 흐름](exec/p0-p1.md) | PostgreSQL·API·UI 기반과 첫 CLAIM 흐름 |
| P2a | [지급 프로시저·원자성](exec/p2a.md) | 핵심 acceptance 실험 통과 |
| P2b | [BATCH·중단 복구·재개](exec/p2b.md) | 핵심 acceptance 실험 통과 |
| P2 운영 | [지갑 초기화와 실행 환경 연결](exec/p2-operations.md) | 내 지갑·전체 사용자 reset, Compose daemon 정렬 |
| P3 | [운영 중 보상 변경·장애 보상 실습](exec/p3.md) | procedure 권한, 동일/상이 요청 동시성, 업무 키 payload 충돌과 원장 대사 확인 |
| P4 | [대량 지급 이력 검색·인덱스 튜닝](exec/p4.md) | 출처 선택·생략 조회 계획, 부분·전체 날짜 인덱스 비용, DDL 동시성 및 사용자 원장 계획 확인 |
| P5 | [운영 진단·API 지연 원인 분리](exec/p5.md) | 100개 동시 요청의 Hikari timeout, Lock wait, 백만 행 scan/index 전후 비교와 blocker 복구 확인 |

P0~P2의 기존 통합 기록을 phase 파일로 나누었습니다. P2의 Docker daemon 연결과 지갑 초기화 경로도 [P2 운영 기록](exec/p2-operations.md)에 정리했습니다. 계획과 완료 결과는 [plan.md](plan.md)에서 확인합니다.

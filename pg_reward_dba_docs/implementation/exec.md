# 구현 실행 기록

각 phase의 변경 파일, 파일 목적과 코드 주석, 설계 결정의 이유, 실행 명령, 관찰값과 미완료 항목을 분리해 기록합니다. P3는 계획표가 아니라 진행 순서대로 로그를 추가합니다.

| Phase | 실행 기록 | 상태 |
|---|---|---|
| P0~P1 | [환경·초기 사용자 흐름](exec/p0-p1.md) | 구현 기록 완료, 전체 gate는 일부 미확인 |
| P2a | [지급 프로시저·원자성](exec/p2a.md) | 핵심 acceptance 실험 통과 |
| P2b | [BATCH·중단 복구·재개](exec/p2b.md) | 핵심 acceptance 실험 통과 |
| P2 운영 UI | [현재 Phase 안내와 wallet reset](exec/p2-operations.md) | 반영 기록 완료 |
| P3 | [운영 중 보상 변경·장애 보상 실습](exec/p3.md) | 구현·실습 완료; 동일 요청 재전송은 사용자 직접 실습으로 남김 |
| P4 | [대량 지급 이력 검색·인덱스 튜닝](exec/p4.md) | query plans, partial indexes, WAL·크기, DDL 동시성 실습 완료 |

P0~P2의 기존 통합 기록을 phase 파일로 나누었습니다. P2 단계의 Docker 엔진 혼선, 지갑 초기화 경로 복구 등 뒤늦게 추가한 운영 기록은 P2 운영 UI 문서에 보존했습니다. 계획과 완료 판정은 [plan.md](plan.md)에서 구분합니다.

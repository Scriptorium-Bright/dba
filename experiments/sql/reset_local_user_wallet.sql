-- 로컬 반복 실습에서 사용자 한 명의 지갑과 모든 지급 이력을 초기화한다.
-- users/events/event_recipients/batch_runs는 보존하며, 운영 DB에서는 실행하지 않는다.
-- 함수가 지갑·grant별 원장 대사에 실패하면 result=DATA_MISMATCH로 아무것도 지우지 않는다.

\set ON_ERROR_STOP on

BEGIN;
SELECT *
FROM reward.reset_local_user_wallet(:user_id);
COMMIT;

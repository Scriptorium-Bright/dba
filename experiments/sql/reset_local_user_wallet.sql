-- 로컬 반복 실습에서 사용자 한 명의 지갑과 모든 지급 이력을 초기화한다.
-- users/events/event_recipients/batch_runs는 보존한다. reward.reset_local_user_wallet 내부 guard가 초기화 범위와 대사를 확인한다.

\set ON_ERROR_STOP on

BEGIN;
SELECT *
FROM reward.reset_local_user_wallet(:user_id);
COMMIT;

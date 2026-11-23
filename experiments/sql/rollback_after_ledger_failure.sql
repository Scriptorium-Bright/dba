-- 대상 이벤트의 원장 INSERT를 일부러 실패시킨 뒤, 지급·지갑 변경이 전부 롤백되는지 확인한다.
-- 로컬 실험 DB의 reward_bootstrap 계정으로만 실행하고, 실패 트리거는 같은 트랜잭션에서 만든다.
\set ON_ERROR_STOP off

BEGIN;
SET LOCAL reward.p2_event_id = :'event_id';

CREATE FUNCTION reward.p2_fail_test_ledger_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_event_id BIGINT := NULLIF(current_setting('reward.p2_event_id', true), '')::BIGINT;
BEGIN
    IF EXISTS (
        SELECT 1 FROM reward.reward_grants g
        WHERE g.id = NEW.reward_grant_id AND g.event_id = v_event_id
    ) THEN
        RAISE EXCEPTION 'P2 intentional wallet_ledger failure';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER p2_fail_test_ledger_insert
BEFORE INSERT ON reward.wallet_ledger
FOR EACH ROW EXECUTE FUNCTION reward.p2_fail_test_ledger_insert();

CALL reward.grant_event_reward(
    :event_id, :user_id, :'failure_request_id'::UUID, 'CLAIM', NULL, NULL
);

-- CALL이 실패 상태에 둔 트랜잭션과 임시 DDL을 동시에 취소한다.
ROLLBACK;

-- 별도 request_id로 호출자의 COMMIT 전 명시적 ROLLBACK도 확인한다.
BEGIN;
CALL reward.grant_event_reward(
    :event_id, :user_id, :'rollback_request_id'::UUID, 'CLAIM', NULL, NULL
);
ROLLBACK;

SELECT
    (SELECT COUNT(*) FROM reward.reward_grants
     WHERE request_id IN (:'failure_request_id'::UUID, :'rollback_request_id'::UUID)) AS leftover_grants,
    EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'p2_fail_test_ledger_insert') AS leftover_test_trigger;

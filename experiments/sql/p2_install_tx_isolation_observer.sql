-- P2-only temporary trigger: capture isolation settings from real grant transactions.
DO $guard$
BEGIN
    IF current_database() <> 'rewarddb' THEN
        RAISE EXCEPTION 'transaction isolation observer is restricted to rewarddb';
    END IF;
END;
$guard$;

BEGIN;
SET LOCAL ROLE reward_migration;

DROP TRIGGER IF EXISTS p2_tx_isolation_observer ON reward.reward_grants;
DROP FUNCTION IF EXISTS reward.capture_p2_tx_isolation();
DROP TABLE IF EXISTS reward.p2_tx_isolation_observation;

CREATE TABLE reward.p2_tx_isolation_observation (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id BIGINT NOT NULL,
    request_id UUID NOT NULL,
    transaction_isolation TEXT NOT NULL,
    session_user_name TEXT NOT NULL,
    current_user_name TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE FUNCTION reward.capture_p2_tx_isolation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, reward, pg_temp
AS $function$
BEGIN
    INSERT INTO reward.p2_tx_isolation_observation (
        event_id, request_id, transaction_isolation, session_user_name, current_user_name
    ) VALUES (
        NEW.event_id, NEW.request_id, current_setting('transaction_isolation'),
        session_user::TEXT, current_user::TEXT
    );
    RETURN NEW;
END;
$function$;

REVOKE ALL ON FUNCTION reward.capture_p2_tx_isolation() FROM PUBLIC;
CREATE TRIGGER p2_tx_isolation_observer
AFTER INSERT ON reward.reward_grants
FOR EACH ROW EXECUTE FUNCTION reward.capture_p2_tx_isolation();

COMMIT;

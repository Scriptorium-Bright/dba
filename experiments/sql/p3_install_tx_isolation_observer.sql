-- P3-only temporary trigger: record the isolation level used by real reward API inserts.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp3db' THEN
        RAISE EXCEPTION 'transaction isolation observer is restricted to rewardp3db';
    END IF;
END;
$guard$;

BEGIN;
SET LOCAL ROLE reward_migration;

DROP TRIGGER IF EXISTS p3_tx_isolation_observer ON reward.compensations;
DROP FUNCTION IF EXISTS reward.capture_p3_tx_isolation();
DROP TABLE IF EXISTS reward.p3_tx_isolation_observation;

CREATE TABLE reward.p3_tx_isolation_observation (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_table TEXT NOT NULL,
    request_id UUID NOT NULL,
    transaction_isolation TEXT NOT NULL,
    session_user_name TEXT NOT NULL,
    current_user_name TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE FUNCTION reward.capture_p3_tx_isolation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, reward, pg_temp
AS $function$
BEGIN
    INSERT INTO reward.p3_tx_isolation_observation (
        source_table, request_id, transaction_isolation, session_user_name, current_user_name
    ) VALUES (
        TG_TABLE_NAME, NEW.request_id, current_setting('transaction_isolation'),
        session_user::TEXT, current_user::TEXT
    );
    RETURN NEW;
END;
$function$;

REVOKE ALL ON FUNCTION reward.capture_p3_tx_isolation() FROM PUBLIC;
CREATE TRIGGER p3_tx_isolation_observer
AFTER INSERT ON reward.compensations
FOR EACH ROW EXECUTE FUNCTION reward.capture_p3_tx_isolation();

COMMIT;

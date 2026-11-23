-- Remove only the temporary P2 transaction observation objects.
DO $guard$
BEGIN
    IF current_database() <> 'rewarddb' THEN
        RAISE EXCEPTION 'transaction isolation observer cleanup is restricted to rewarddb';
    END IF;
END;
$guard$;

BEGIN;
SET LOCAL ROLE reward_migration;
DROP TRIGGER IF EXISTS p2_tx_isolation_observer ON reward.reward_grants;
DROP FUNCTION IF EXISTS reward.capture_p2_tx_isolation();
DROP TABLE IF EXISTS reward.p2_tx_isolation_observation;
COMMIT;

-- Remove only the temporary P3 observation objects after the isolation experiment.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp3db' THEN
        RAISE EXCEPTION 'transaction isolation observer cleanup is restricted to rewardp3db';
    END IF;
END;
$guard$;

BEGIN;
SET LOCAL ROLE reward_migration;
DROP TRIGGER IF EXISTS p3_tx_isolation_observer ON reward.compensations;
DROP FUNCTION IF EXISTS reward.capture_p3_tx_isolation();
DROP TABLE IF EXISTS reward.p3_tx_isolation_observation;
COMMIT;

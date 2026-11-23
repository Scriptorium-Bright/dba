\set ON_ERROR_STOP on

-- Repeat-run reset for the successful V15 test on the named disposable 1M-row database.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp4_flyway_populated_probe' THEN
        RAISE EXCEPTION 'run only in rewardp4_flyway_populated_probe, current database is %', current_database();
    END IF;
    IF (SELECT count(*) FROM reward.wallet_ledger) <> 1000000 THEN
        RAISE EXCEPTION 'expected the 1,000,000-row ledger fixture before reset';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM reward.flyway_schema_history WHERE version = '15' AND success
    ) THEN
        RAISE EXCEPTION 'expected a successful V15 entry before repeat-run reset';
    END IF;
END;
$guard$;

DROP INDEX CONCURRENTLY IF EXISTS reward.wallet_ledger_compensation_cursor_idx;
DROP INDEX CONCURRENTLY IF EXISTS reward.wallet_ledger_compensation_created_idx;
DROP INDEX CONCURRENTLY IF EXISTS reward.wallet_ledger_any_source_created_idx;

-- Reset Flyway history only after these disposable-test indexes have been removed.
DELETE FROM reward.flyway_schema_history WHERE version = '15' AND success;

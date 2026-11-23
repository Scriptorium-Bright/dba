\set ON_ERROR_STOP on

-- Clean partial V15 objects after a canceled run only in the named 1M-row disposable probe.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp4_flyway_populated_probe' THEN
        RAISE EXCEPTION 'run only in rewardp4_flyway_populated_probe, current database is %', current_database();
    END IF;
    IF (SELECT count(*) FROM reward.wallet_ledger) <> 1000000 THEN
        RAISE EXCEPTION 'expected the 1,000,000-row ledger fixture before cleanup';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM reward.flyway_schema_history WHERE version = '14' AND success
    ) THEN
        RAISE EXCEPTION 'expected V14 success before cleaning the interrupted V15 attempt';
    END IF;
    IF EXISTS (SELECT 1 FROM reward.flyway_schema_history WHERE version = '15') THEN
        RAISE EXCEPTION 'V15 history exists; inspect catalog and use Flyway repair when the row failed';
    END IF;
END;
$guard$;

DROP INDEX CONCURRENTLY IF EXISTS reward.wallet_ledger_compensation_cursor_idx;
DROP INDEX CONCURRENTLY IF EXISTS reward.wallet_ledger_compensation_created_idx;
DROP INDEX CONCURRENTLY IF EXISTS reward.wallet_ledger_any_source_created_idx;

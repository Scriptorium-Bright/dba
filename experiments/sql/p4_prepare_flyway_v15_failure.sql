\set ON_ERROR_STOP on

-- Deliberately creates a conflicting index name only in the disposable recovery database.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp4_flyway_recovery_probe' THEN
        RAISE EXCEPTION 'run only in rewardp4_flyway_recovery_probe, current database is %', current_database();
    END IF;
    IF to_regclass('reward.wallet_ledger') IS NULL THEN
        RAISE EXCEPTION 'V1-V14 must be applied before preparing the V15 failure';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM reward.flyway_schema_history WHERE version = '14' AND success
    ) OR EXISTS (
        SELECT 1 FROM reward.flyway_schema_history WHERE version = '15'
    ) THEN
        RAISE EXCEPTION 'expected V14 success and no V15 history row';
    END IF;
    IF to_regclass('reward.wallet_ledger_compensation_cursor_idx') IS NOT NULL
       OR to_regclass('reward.wallet_ledger_compensation_created_idx') IS NOT NULL
       OR to_regclass('reward.wallet_ledger_any_source_created_idx') IS NOT NULL THEN
        RAISE EXCEPTION 'remove pre-existing V15 indexes before preparing this failure';
    END IF;
END;
$guard$;

-- IF NOT EXISTS skips the deliberately wrong same-name definition; V15's catalog check must catch it.
CREATE INDEX CONCURRENTLY wallet_ledger_compensation_created_idx
    ON reward.wallet_ledger (id);

\set ON_ERROR_STOP on

-- Read-only V15 history/catalog snapshot for the disposable recovery database.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp4_flyway_recovery_probe' THEN
        RAISE EXCEPTION 'run only in rewardp4_flyway_recovery_probe, current database is %', current_database();
    END IF;
    IF to_regclass('reward.flyway_schema_history') IS NULL THEN
        RAISE EXCEPTION 'Flyway schema history does not exist; run the recovery setup first';
    END IF;
END;
$guard$;

SELECT version, description, success, installed_by
FROM reward.flyway_schema_history
WHERE version = '15';

WITH expected(index_name, expected_definition) AS (
    VALUES
        ('reward.wallet_ledger_compensation_cursor_idx',
         'CREATE INDEX wallet_ledger_compensation_cursor_idx ON reward.wallet_ledger USING btree (id DESC) WHERE (source_type = ''COMPENSATION''::text)'),
        ('reward.wallet_ledger_compensation_created_idx',
         'CREATE INDEX wallet_ledger_compensation_created_idx ON reward.wallet_ledger USING btree (created_at DESC, id DESC) WHERE (source_type = ''COMPENSATION''::text)'),
        ('reward.wallet_ledger_any_source_created_idx',
         'CREATE INDEX wallet_ledger_any_source_created_idx ON reward.wallet_ledger USING btree (created_at DESC, id DESC)')
), resolved AS (
    SELECT expected.index_name,
           expected.expected_definition,
           index_class.oid AS index_oid
    FROM expected
    LEFT JOIN pg_catalog.pg_class AS index_class
      ON index_class.oid = pg_catalog.to_regclass(expected.index_name)
)
SELECT resolved.index_name,
       resolved.index_oid IS NOT NULL AS present,
       pg_index.indisready,
       pg_index.indisvalid,
       pg_catalog.pg_get_indexdef(resolved.index_oid) AS actual_definition,
       pg_catalog.pg_get_indexdef(resolved.index_oid) = resolved.expected_definition AS definition_matches
FROM resolved
LEFT JOIN pg_catalog.pg_index ON pg_index.indexrelid = resolved.index_oid
ORDER BY resolved.index_name;

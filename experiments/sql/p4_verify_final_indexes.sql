-- Inspect the selected P4 indexes before or after a concurrent build attempt.
DO $guard$
DECLARE
    expected RECORD;
    index_oid OID;
    index_ready BOOLEAN;
    index_valid BOOLEAN;
    actual_definition TEXT;
BEGIN
    IF current_database() <> 'rewardp4db' THEN
        RAISE EXCEPTION 'P4 final index inspection is restricted to rewardp4db';
    END IF;

    FOR expected IN
        SELECT * FROM (VALUES
            ('reward.wallet_ledger_compensation_cursor_idx',
             'CREATE INDEX wallet_ledger_compensation_cursor_idx ON reward.wallet_ledger USING btree (id DESC) WHERE (source_type = ''COMPENSATION''::text)'),
            ('reward.wallet_ledger_compensation_created_idx',
             'CREATE INDEX wallet_ledger_compensation_created_idx ON reward.wallet_ledger USING btree (created_at DESC, id DESC) WHERE (source_type = ''COMPENSATION''::text)'),
            ('reward.wallet_ledger_any_source_created_idx',
             'CREATE INDEX wallet_ledger_any_source_created_idx ON reward.wallet_ledger USING btree (created_at DESC, id DESC)')
        ) AS definitions(index_name, expected_definition)
    LOOP
        index_oid := pg_catalog.to_regclass(expected.index_name);
        IF index_oid IS NULL THEN
            RAISE EXCEPTION 'required P4 index is missing: %', expected.index_name;
        END IF;

        SELECT indisready, indisvalid
          INTO index_ready, index_valid
          FROM pg_catalog.pg_index
         WHERE indexrelid = index_oid;
        IF NOT index_ready OR NOT index_valid THEN
            RAISE EXCEPTION 'required P4 index is not ready/valid: % (%/%)',
                expected.index_name, index_ready, index_valid;
        END IF;

        actual_definition := pg_catalog.pg_get_indexdef(index_oid);
        IF actual_definition <> expected.expected_definition THEN
            RAISE EXCEPTION 'unexpected definition for %: %', expected.index_name, actual_definition;
        END IF;
    END LOOP;
END;
$guard$;

WITH expected(index_name) AS (
    VALUES
        ('reward.wallet_ledger_compensation_cursor_idx'),
        ('reward.wallet_ledger_compensation_created_idx'),
        ('reward.wallet_ledger_any_source_created_idx')
)
SELECT expected.index_name,
       index_class.oid IS NOT NULL AS present,
       pg_index.indisready,
       pg_index.indisvalid,
       pg_catalog.pg_relation_size(index_class.oid) AS bytes,
       pg_catalog.pg_size_pretty(pg_catalog.pg_relation_size(index_class.oid)) AS size,
       pg_catalog.pg_get_indexdef(index_class.oid) AS definition
FROM expected
LEFT JOIN pg_catalog.pg_class AS index_class
       ON index_class.oid = pg_catalog.to_regclass(expected.index_name)
LEFT JOIN pg_catalog.pg_index
       ON pg_index.indexrelid = index_class.oid
ORDER BY expected.index_name;

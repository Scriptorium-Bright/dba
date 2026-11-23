-- P4-only schema change: version the measured search indexes for repeatable database creation.
-- Keep this in a P4-only Flyway location so smaller P2/P3 databases do not receive the 30 MiB index.
CREATE INDEX CONCURRENTLY IF NOT EXISTS wallet_ledger_compensation_cursor_idx
    ON reward.wallet_ledger (id DESC)
    WHERE source_type = 'COMPENSATION';

CREATE INDEX CONCURRENTLY IF NOT EXISTS wallet_ledger_compensation_created_idx
    ON reward.wallet_ledger (created_at DESC, id DESC)
    WHERE source_type = 'COMPENSATION';

-- sourceType is optional on the admin API, so this all-source date index supports that query shape.
CREATE INDEX CONCURRENTLY IF NOT EXISTS wallet_ledger_any_source_created_idx
    ON reward.wallet_ledger (created_at DESC, id DESC);

-- IF NOT EXISTS only checks a name; verify the existing or newly built objects are usable and exact.
DO $verify$
DECLARE
    expected RECORD;
    index_oid OID;
    index_ready BOOLEAN;
    index_valid BOOLEAN;
    actual_definition TEXT;
BEGIN
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
            RAISE EXCEPTION 'required wallet ledger index is missing: %', expected.index_name;
        END IF;

        SELECT indisready, indisvalid
          INTO index_ready, index_valid
          FROM pg_catalog.pg_index
         WHERE indexrelid = index_oid;
        IF NOT index_ready OR NOT index_valid THEN
            RAISE EXCEPTION 'wallet ledger index is not ready/valid: % (%/%)',
                expected.index_name, index_ready, index_valid;
        END IF;

        actual_definition := pg_catalog.pg_get_indexdef(index_oid);
        IF actual_definition <> expected.expected_definition THEN
            RAISE EXCEPTION 'unexpected wallet ledger index definition for %: %',
                expected.index_name, actual_definition;
        END IF;
    END LOOP;
END;
$verify$;

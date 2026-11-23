-- One-time comparison performed before the selected P4 indexes were changed to key-only.
-- Re-run is refused after the final indexes no longer use INCLUDE; see saved result artifacts.
-- Run outside an explicit transaction because both builds use CONCURRENTLY.
DO $guard$
DECLARE
    v_cursor_definition TEXT;
    v_created_definition TEXT;
BEGIN
    IF current_database() <> 'rewardp4db' THEN
        RAISE EXCEPTION 'P4 no-INCLUDE candidates are restricted to rewardp4db';
    END IF;
    IF pg_catalog.to_regclass('reward.wallet_ledger_compensation_cursor_idx') IS NULL
       OR pg_catalog.to_regclass('reward.wallet_ledger_compensation_created_idx') IS NULL THEN
        RAISE EXCEPTION 'expected prior P4 final indexes are missing; inspect the saved artifacts';
    END IF;
    v_cursor_definition := pg_catalog.pg_get_indexdef(
        pg_catalog.to_regclass('reward.wallet_ledger_compensation_cursor_idx')
    );
    v_created_definition := pg_catalog.pg_get_indexdef(
        pg_catalog.to_regclass('reward.wallet_ledger_compensation_created_idx')
    );
    IF v_cursor_definition NOT LIKE '%INCLUDE%'
       OR v_created_definition NOT LIKE '%INCLUDE%' THEN
        RAISE EXCEPTION 'one-time comparison requires the earlier INCLUDE indexes; read the saved result artifacts';
    END IF;
    IF to_regclass('reward.p4_candidate_comp_cursor_no_include_idx') IS NOT NULL
       OR to_regclass('reward.p4_candidate_comp_created_no_include_idx') IS NOT NULL THEN
        RAISE EXCEPTION 'P4 no-INCLUDE candidate already exists; inspect it before cleanup';
    END IF;
END;
$guard$;

CREATE INDEX CONCURRENTLY p4_candidate_comp_cursor_no_include_idx
    ON reward.wallet_ledger (id DESC)
    WHERE source_type = 'COMPENSATION';

CREATE INDEX CONCURRENTLY p4_candidate_comp_created_no_include_idx
    ON reward.wallet_ledger (created_at DESC, id DESC)
    WHERE source_type = 'COMPENSATION';

ANALYZE reward.wallet_ledger;

SELECT indexrelid::regclass::TEXT AS index_name,
       pg_catalog.pg_relation_size(indexrelid) AS bytes,
       indisready,
       indisvalid
FROM pg_catalog.pg_index
WHERE indrelid = 'reward.wallet_ledger'::regclass
  AND indexrelid::regclass::TEXT IN (
      'reward.wallet_ledger_compensation_cursor_idx',
      'reward.wallet_ledger_compensation_created_idx',
      'reward.p4_candidate_comp_cursor_no_include_idx',
      'reward.p4_candidate_comp_created_no_include_idx'
  )
ORDER BY indexrelid::regclass::TEXT;

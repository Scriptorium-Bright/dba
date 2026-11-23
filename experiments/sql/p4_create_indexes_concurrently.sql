-- Run this file with psql outside an explicit BEGIN/COMMIT transaction.
-- P4 final indexes support both the rare-source compensation searches and any-source date searches.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp4db' THEN
        RAISE EXCEPTION 'P4 indexes are restricted to rewardp4db';
    END IF;
END;
$guard$;

CREATE INDEX CONCURRENTLY IF NOT EXISTS wallet_ledger_compensation_cursor_idx
    ON reward.wallet_ledger (id DESC)
    WHERE source_type = 'COMPENSATION';

CREATE INDEX CONCURRENTLY IF NOT EXISTS wallet_ledger_compensation_created_idx
    ON reward.wallet_ledger (created_at DESC, id DESC)
    WHERE source_type = 'COMPENSATION';

-- The API permits sourceType to be omitted; this full index serves date windows across both sources.
CREATE INDEX CONCURRENTLY IF NOT EXISTS wallet_ledger_any_source_created_idx
    ON reward.wallet_ledger (created_at DESC, id DESC);

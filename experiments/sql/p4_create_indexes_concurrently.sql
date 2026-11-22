-- Run this file with psql outside an explicit BEGIN/COMMIT transaction.
-- P4 final indexes keep rare COMPENSATION rows small and support both ID and time searches.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp4db' THEN
        RAISE EXCEPTION 'P4 indexes are restricted to rewardp4db';
    END IF;
END;
$guard$;

CREATE INDEX CONCURRENTLY wallet_ledger_compensation_cursor_idx
    ON reward.wallet_ledger (id DESC)
    INCLUDE (user_id, compensation_id, currency_type, amount, created_at)
    WHERE source_type = 'COMPENSATION';

CREATE INDEX CONCURRENTLY wallet_ledger_compensation_created_idx
    ON reward.wallet_ledger (created_at DESC, id DESC)
    INCLUDE (user_id, compensation_id, currency_type, amount)
    WHERE source_type = 'COMPENSATION';

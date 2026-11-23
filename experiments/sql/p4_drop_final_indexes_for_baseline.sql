-- P4-only baseline helper: remove V15 search indexes before replaying the pre-index plan.
-- Run this file outside an explicit BEGIN/COMMIT transaction because DROP INDEX CONCURRENTLY
-- cannot run inside a transaction block. Reapply V15 definitions after the baseline experiment.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp4db' THEN
        RAISE EXCEPTION 'P4 baseline index removal is restricted to rewardp4db';
    END IF;
END;
$guard$;

DROP INDEX CONCURRENTLY IF EXISTS reward.wallet_ledger_compensation_cursor_idx;
DROP INDEX CONCURRENTLY IF EXISTS reward.wallet_ledger_compensation_created_idx;
DROP INDEX CONCURRENTLY IF EXISTS reward.wallet_ledger_any_source_created_idx;

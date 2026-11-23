-- Remove only P4 candidate indexes; keep the selected final indexes.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp4db' THEN
        RAISE EXCEPTION 'P4 candidate cleanup is restricted to rewardp4db';
    END IF;
END;
$guard$;

DROP INDEX IF EXISTS reward.p4_candidate_source_cursor_idx;
DROP INDEX IF EXISTS reward.p4_candidate_comp_cursor_idx;
DROP INDEX IF EXISTS reward.p4_candidate_comp_created_idx;
ANALYZE reward.wallet_ledger;

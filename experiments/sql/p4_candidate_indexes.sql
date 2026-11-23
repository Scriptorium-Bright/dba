-- Narrow partial candidates for the 1% COMPENSATION rows in the P4 fixture.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp4db' THEN
        RAISE EXCEPTION 'P4 candidate indexes are restricted to rewardp4db';
    END IF;
END;
$guard$;

CREATE INDEX p4_candidate_comp_cursor_idx
    ON reward.wallet_ledger (id DESC)
    WHERE source_type = 'COMPENSATION';
CREATE INDEX p4_candidate_comp_created_idx
    ON reward.wallet_ledger (created_at DESC, id DESC)
    WHERE source_type = 'COMPENSATION';

ANALYZE reward.wallet_ledger;

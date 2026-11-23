-- Wide candidate measured for the same global source cursor query.
-- Compare its 30 MB size with the sparse partial cursor candidate.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp4db' THEN
        RAISE EXCEPTION 'P4 full source index is restricted to rewardp4db';
    END IF;
END;
$guard$;

CREATE INDEX p4_candidate_source_cursor_idx
    ON reward.wallet_ledger (source_type, id DESC);

ANALYZE reward.wallet_ledger;

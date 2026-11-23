-- Remove only the temporary P4 comparison indexes; never drops the selected final indexes.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp4db' THEN
        RAISE EXCEPTION 'P4 no-INCLUDE cleanup is restricted to rewardp4db';
    END IF;
END;
$guard$;

DROP INDEX CONCURRENTLY IF EXISTS reward.p4_candidate_comp_cursor_no_include_idx;
DROP INDEX CONCURRENTLY IF EXISTS reward.p4_candidate_comp_created_no_include_idx;

-- Controlled tuning action after recording the unindexed baseline in rewardlatencydb.
\set ON_ERROR_STOP on
DO $guard$
BEGIN
    IF current_database() <> 'rewardlatencydb' THEN
        RAISE EXCEPTION 'latency tuning is restricted to rewardlatencydb';
    END IF;
    IF (SELECT count(*) FROM pg_indexes
        WHERE schemaname = 'diagnostics' AND tablename = 'scan_probe') <> 1 THEN
        RAISE EXCEPTION 'run the baseline before creating the tuning index';
    END IF;
END;
$guard$;

-- pg_trgm GIN supports leading-wildcard LIKE predicates used by the fixed scan API.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

BEGIN;
SET LOCAL ROLE reward_migration;
CREATE INDEX scan_probe_marker_trgm_idx
    ON diagnostics.scan_probe USING GIN (marker public.gin_trgm_ops);
ANALYZE diagnostics.scan_probe;
COMMIT;

SELECT current_database() AS database_name,
       (SELECT count(*) FROM pg_indexes
        WHERE schemaname = 'diagnostics' AND tablename = 'scan_probe') AS index_count,
       pg_size_pretty(pg_relation_size('diagnostics.scan_probe_marker_trgm_idx')) AS index_size;

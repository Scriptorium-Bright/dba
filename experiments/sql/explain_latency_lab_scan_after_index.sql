-- Capture the exact sequentialScan() SQL plan after the targeted GIN index action.
\set ON_ERROR_STOP on
DO $guard$
BEGIN
    IF current_database() <> 'rewardlatencydb' THEN
        RAISE EXCEPTION 'latency scan plan is restricted to rewardlatencydb';
    END IF;
    IF (SELECT count(*) FROM diagnostics.scan_probe) <> 1000000 THEN
        RAISE EXCEPTION 'expected the one-million-row latency fixture';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'diagnostics' AND tablename = 'scan_probe'
          AND indexname = 'scan_probe_marker_trgm_idx'
    ) THEN
        RAISE EXCEPTION 'the pg_trgm GIN index has not been installed';
    END IF;
END;
$guard$;

EXPLAIN (ANALYZE, BUFFERS, SETTINGS)
SELECT count(*)
FROM diagnostics.scan_probe
WHERE marker LIKE '%needle%';

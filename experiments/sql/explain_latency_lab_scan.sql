-- The fixed unindexed query is identical to ApiLatencyDiagnosticsController.sequentialScan().
\set ON_ERROR_STOP on
DO $guard$
BEGIN
    IF current_database() <> 'rewardlatencydb' THEN
        RAISE EXCEPTION 'latency scan plan is restricted to rewardlatencydb';
    END IF;
    IF (SELECT count(*) FROM diagnostics.scan_probe) <> 1000000 THEN
        RAISE EXCEPTION 'expected the one-million-row latency fixture';
    END IF;
    IF (SELECT count(*) FROM pg_indexes
        WHERE schemaname = 'diagnostics' AND tablename = 'scan_probe') <> 1 THEN
        RAISE EXCEPTION 'the scan fixture must have only its primary-key index';
    END IF;
END;
$guard$;

EXPLAIN (ANALYZE, BUFFERS, SETTINGS)
SELECT count(*)
FROM diagnostics.scan_probe
WHERE marker LIKE '%needle%';

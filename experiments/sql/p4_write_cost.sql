-- Compare the same insert against a primary-key-only ledger and its two partial indexes.
-- Scratch tables are removed at the end; only rewardp4db is accepted.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp4db' THEN
        RAISE EXCEPTION 'P4 write-cost probe is restricted to rewardp4db';
    END IF;
END;
$guard$;

DROP TABLE IF EXISTS reward.p4_write_probe_baseline;
DROP TABLE IF EXISTS reward.p4_write_probe_indexed;

CREATE TABLE reward.p4_write_probe_baseline (
    id BIGINT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    source_type TEXT NOT NULL,
    reward_grant_id BIGINT,
    compensation_id BIGINT,
    currency_type TEXT NOT NULL,
    amount BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE reward.p4_write_probe_indexed (
    id BIGINT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    source_type TEXT NOT NULL,
    reward_grant_id BIGINT,
    compensation_id BIGINT,
    currency_type TEXT NOT NULL,
    amount BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX p4_write_probe_cursor_idx
    ON reward.p4_write_probe_indexed (id DESC)
    INCLUDE (user_id, compensation_id, currency_type, amount, created_at)
    WHERE source_type = 'COMPENSATION';

CREATE INDEX p4_write_probe_created_idx
    ON reward.p4_write_probe_indexed (created_at DESC, id DESC)
    INCLUDE (user_id, compensation_id, currency_type, amount)
    WHERE source_type = 'COMPENSATION';

-- The fixture models a 1% compensation share while keeping both inserts identical.
EXPLAIN (ANALYZE, BUFFERS, WAL, TIMING)
INSERT INTO reward.p4_write_probe_baseline
SELECT n,
       2000000 + n,
       CASE WHEN n % 100 = 0 THEN 'COMPENSATION' ELSE 'EVENT' END,
       CASE WHEN n % 100 = 0 THEN NULL ELSE n END,
       CASE WHEN n % 100 = 0 THEN n ELSE NULL END,
       CASE WHEN n % 2 = 0 THEN 'MONEY' ELSE 'POINT' END,
       10,
       TIMESTAMPTZ '2026-11-01 00:00:00+00' + n * INTERVAL '1 minute'
FROM generate_series(1, 50000) AS n;

EXPLAIN (ANALYZE, BUFFERS, WAL, TIMING)
INSERT INTO reward.p4_write_probe_indexed
SELECT n,
       2000000 + n,
       CASE WHEN n % 100 = 0 THEN 'COMPENSATION' ELSE 'EVENT' END,
       CASE WHEN n % 100 = 0 THEN NULL ELSE n END,
       CASE WHEN n % 100 = 0 THEN n ELSE NULL END,
       CASE WHEN n % 2 = 0 THEN 'MONEY' ELSE 'POINT' END,
       10,
       TIMESTAMPTZ '2026-11-01 00:00:00+00' + n * INTERVAL '1 minute'
FROM generate_series(1, 50000) AS n;

SELECT 'baseline' AS fixture,
       pg_size_pretty(pg_total_relation_size('reward.p4_write_probe_baseline')) AS total_size;
SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_stat_user_indexes
WHERE schemaname = 'reward'
  AND relname = 'p4_write_probe_indexed'
ORDER BY indexrelname;

DROP TABLE reward.p4_write_probe_baseline;
DROP TABLE reward.p4_write_probe_indexed;

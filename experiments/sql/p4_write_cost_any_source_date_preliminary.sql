-- Preliminary narrow (6-column) check; the canonical same-shape comparison is p4_write_cost.sql.
-- Quantifies WAL and storage for the all-source date index on a 50k-row insert.
-- Scratch tables are removed at the end; only rewardp4db is accepted.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp4db' THEN
        RAISE EXCEPTION 'P4 write-cost probe is restricted to rewardp4db';
    END IF;
END;
$guard$;

DROP TABLE IF EXISTS reward.p4_write_any_source_baseline;
DROP TABLE IF EXISTS reward.p4_write_any_source_indexed;

CREATE TABLE reward.p4_write_any_source_baseline (
    id BIGINT PRIMARY KEY,
    source_type TEXT NOT NULL,
    compensation_id BIGINT,
    currency_type TEXT NOT NULL,
    amount BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE reward.p4_write_any_source_indexed (
    id BIGINT PRIMARY KEY,
    source_type TEXT NOT NULL,
    compensation_id BIGINT,
    currency_type TEXT NOT NULL,
    amount BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

-- The candidate must index every source to support an optional sourceType predicate.
CREATE INDEX p4_write_any_source_created_idx
    ON reward.p4_write_any_source_indexed (created_at DESC, id DESC);

-- The same generated rows and 1% compensation share are inserted into both tables.
EXPLAIN (ANALYZE, BUFFERS, WAL, TIMING)
INSERT INTO reward.p4_write_any_source_baseline
SELECT n,
       CASE WHEN n % 100 = 0 THEN 'COMPENSATION' ELSE 'EVENT' END,
       CASE WHEN n % 100 = 0 THEN n ELSE NULL END,
       CASE WHEN n % 2 = 0 THEN 'MONEY' ELSE 'POINT' END,
       10,
       TIMESTAMPTZ '2026-11-01 00:00:00+00' + n * INTERVAL '1 minute'
FROM generate_series(1, 50000) AS n;

EXPLAIN (ANALYZE, BUFFERS, WAL, TIMING)
INSERT INTO reward.p4_write_any_source_indexed
SELECT n,
       CASE WHEN n % 100 = 0 THEN 'COMPENSATION' ELSE 'EVENT' END,
       CASE WHEN n % 100 = 0 THEN n ELSE NULL END,
       CASE WHEN n % 2 = 0 THEN 'MONEY' ELSE 'POINT' END,
       10,
       TIMESTAMPTZ '2026-11-01 00:00:00+00' + n * INTERVAL '1 minute'
FROM generate_series(1, 50000) AS n;

SELECT c.relname, pg_relation_size(c.oid) AS table_bytes,
       pg_total_relation_size(c.oid) AS total_bytes
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'reward'
  AND c.relname IN ('p4_write_any_source_baseline', 'p4_write_any_source_indexed')
ORDER BY c.relname;

SELECT indexrelid::regclass AS index_name, pg_relation_size(indexrelid) AS bytes
FROM pg_index
WHERE indexrelid = 'reward.p4_write_any_source_created_idx'::regclass;

DROP TABLE reward.p4_write_any_source_baseline;
DROP TABLE reward.p4_write_any_source_indexed;

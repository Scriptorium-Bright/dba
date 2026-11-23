-- Compare the same insert against PK-only, INCLUDE partial, key-only partial, and all-source date indexes.
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
DROP TABLE IF EXISTS reward.p4_write_probe_key_only;
DROP TABLE IF EXISTS reward.p4_write_probe_any_source_date;

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

CREATE TABLE reward.p4_write_probe_key_only (
    id BIGINT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    source_type TEXT NOT NULL,
    reward_grant_id BIGINT,
    compensation_id BIGINT,
    currency_type TEXT NOT NULL,
    amount BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE reward.p4_write_probe_any_source_date (
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

CREATE INDEX p4_write_probe_key_only_cursor_idx
    ON reward.p4_write_probe_key_only (id DESC)
    WHERE source_type = 'COMPENSATION';

CREATE INDEX p4_write_probe_key_only_created_idx
    ON reward.p4_write_probe_key_only (created_at DESC, id DESC)
    WHERE source_type = 'COMPENSATION';

-- This full index is the candidate for optional-sourceType date searches.
CREATE INDEX p4_write_probe_any_source_created_idx
    ON reward.p4_write_probe_any_source_date (created_at DESC, id DESC);

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

EXPLAIN (ANALYZE, BUFFERS, WAL, TIMING)
INSERT INTO reward.p4_write_probe_key_only
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
INSERT INTO reward.p4_write_probe_any_source_date
SELECT n,
       2000000 + n,
       CASE WHEN n % 100 = 0 THEN 'COMPENSATION' ELSE 'EVENT' END,
       CASE WHEN n % 100 = 0 THEN NULL ELSE n END,
       CASE WHEN n % 100 = 0 THEN n ELSE NULL END,
       CASE WHEN n % 2 = 0 THEN 'MONEY' ELSE 'POINT' END,
       10,
       TIMESTAMPTZ '2026-11-01 00:00:00+00' + n * INTERVAL '1 minute'
FROM generate_series(1, 50000) AS n;

SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_catalog.pg_stat_user_tables
WHERE schemaname = 'reward'
  AND relname IN (
      'p4_write_probe_baseline', 'p4_write_probe_indexed',
      'p4_write_probe_key_only', 'p4_write_probe_any_source_date'
  )
ORDER BY relname;
SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_stat_user_indexes
WHERE schemaname = 'reward'
  AND relname IN ('p4_write_probe_indexed', 'p4_write_probe_key_only', 'p4_write_probe_any_source_date')
ORDER BY indexrelname;

DROP TABLE reward.p4_write_probe_baseline;
DROP TABLE reward.p4_write_probe_indexed;
DROP TABLE reward.p4_write_probe_key_only;
DROP TABLE reward.p4_write_probe_any_source_date;

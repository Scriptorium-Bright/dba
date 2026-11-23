-- Create fixed API latency probes only inside the dedicated rewardlatencydb database.
DO $guard$
BEGIN
    IF current_database() <> 'rewardlatencydb' THEN
        RAISE EXCEPTION 'latency lab is restricted to rewardlatencydb';
    END IF;
END;
$guard$;

BEGIN;
SET LOCAL ROLE reward_migration;

-- Schema creation needs database CREATE; bootstrap creates it, then migration owns objects inside.
RESET ROLE;
CREATE SCHEMA diagnostics AUTHORIZATION reward_migration;
SET LOCAL ROLE reward_migration;

CREATE TABLE diagnostics.lock_probe (
    id INTEGER PRIMARY KEY,
    value BIGINT NOT NULL
);
INSERT INTO diagnostics.lock_probe(id, value) VALUES (1, 0);

CREATE TABLE diagnostics.scan_probe (
    id BIGINT PRIMARY KEY,
    marker TEXT NOT NULL
);

INSERT INTO diagnostics.scan_probe(id, marker)
SELECT id,
       CASE WHEN id % 500 = 0 THEN 'needle-row-' || id ELSE 'ordinary-row-' || id END
FROM generate_series(1, 1000000) AS id;
ANALYZE diagnostics.scan_probe;

GRANT USAGE ON SCHEMA diagnostics TO reward_app, reward_observer;
GRANT SELECT ON diagnostics.scan_probe TO reward_app, reward_observer;
GRANT SELECT, UPDATE ON diagnostics.lock_probe TO reward_app;
GRANT SELECT ON diagnostics.lock_probe TO reward_observer;

COMMIT;

SELECT current_database() AS database_name,
       current_setting('server_version') AS postgres_version,
       (SELECT count(*) FROM diagnostics.scan_probe) AS scan_rows,
       (SELECT count(*) FROM diagnostics.scan_probe WHERE marker LIKE '%needle%') AS matching_rows,
       (SELECT count(*) FROM pg_indexes
        WHERE schemaname = 'diagnostics' AND tablename = 'scan_probe') AS scan_table_indexes;

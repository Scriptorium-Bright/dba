\set ON_ERROR_STOP on

-- This schema bootstrap is limited to the named disposable V15 recovery and populated-build probes.
DO $guard$
BEGIN
    IF current_database() NOT IN (
        'rewardp4_flyway_recovery_probe', 'rewardp4_flyway_populated_probe'
    ) THEN
        RAISE EXCEPTION 'run only in a named P4 Flyway probe, current database is %', current_database();
    END IF;
    IF to_regnamespace('reward') IS NOT NULL THEN
        RAISE EXCEPTION 'reward schema already exists; use a newly created probe database';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'reward_migration') THEN
        RAISE EXCEPTION 'reward_migration role is missing; start the P4 Compose database first';
    END IF;
END;
$guard$;

CREATE SCHEMA reward AUTHORIZATION reward_migration;
SELECT current_database() AS probe_database \gset
GRANT CONNECT ON DATABASE :"probe_database" TO reward_app, reward_migration, reward_observer;
GRANT USAGE ON SCHEMA reward TO reward_app, reward_observer;

ALTER DEFAULT PRIVILEGES FOR ROLE reward_migration IN SCHEMA reward
    GRANT SELECT, INSERT, UPDATE ON TABLES TO reward_app;
ALTER DEFAULT PRIVILEGES FOR ROLE reward_migration IN SCHEMA reward
    GRANT SELECT ON TABLES TO reward_observer;
ALTER DEFAULT PRIVILEGES FOR ROLE reward_migration IN SCHEMA reward
    GRANT USAGE ON SEQUENCES TO reward_app;

ALTER DATABASE :"probe_database" SET timezone TO 'UTC';

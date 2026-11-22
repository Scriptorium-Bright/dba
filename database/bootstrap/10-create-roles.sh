#!/usr/bin/env bash
set -euo pipefail

psql --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=app_password="$DB_APP_PASSWORD" \
  --set=migration_password="$DB_MIGRATION_PASSWORD" \
  --set=observer_password="$DB_OBSERVER_PASSWORD" \
  --set=database_name="$POSTGRES_DB" <<'SQL'
CREATE ROLE reward_app LOGIN PASSWORD :'app_password';
CREATE ROLE reward_migration LOGIN PASSWORD :'migration_password';
CREATE ROLE reward_observer LOGIN PASSWORD :'observer_password';

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA reward AUTHORIZATION reward_migration;
-- Compose may give the isolated P3 database a distinct name to prevent cross-use.
GRANT CONNECT ON DATABASE :"database_name" TO reward_app, reward_migration, reward_observer;
GRANT USAGE ON SCHEMA reward TO reward_app, reward_observer;

ALTER DEFAULT PRIVILEGES FOR ROLE reward_migration IN SCHEMA reward
    GRANT SELECT, INSERT, UPDATE ON TABLES TO reward_app;
ALTER DEFAULT PRIVILEGES FOR ROLE reward_migration IN SCHEMA reward
    GRANT SELECT ON TABLES TO reward_observer;
ALTER DEFAULT PRIVILEGES FOR ROLE reward_migration IN SCHEMA reward
    GRANT USAGE ON SEQUENCES TO reward_app;

ALTER DATABASE :"database_name" SET timezone TO 'UTC';
SQL

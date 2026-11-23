-- Scope detailed DB monitoring privileges to the disposable response-latency lab.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
GRANT pg_monitor TO reward_observer;

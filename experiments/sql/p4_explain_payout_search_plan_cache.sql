-- Observe PostgreSQL prepared-plan choices for the sourceType-omitted API query.
-- Run with psql in rewardp4db; the statement is executed with both recent and historical ranges.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp4db' THEN
        RAISE EXCEPTION 'P4 plan-cache check is restricted to rewardp4db';
    END IF;
    IF session_user <> 'reward_app' OR current_user <> 'reward_app' THEN
        RAISE EXCEPTION 'P4 plan-cache check must run as reward_app (session_user=%, current_user=%)',
            session_user, current_user;
    END IF;
END;
$guard$;

SELECT current_database() AS database_name, session_user, current_user;

-- Keep the same JOIN, date bind parameters, descending ID order, and limit as RewardService.
PREPARE p4_any_source_period_cache_check (TIMESTAMPTZ, TIMESTAMPTZ, INTEGER) AS
SELECT l.id, l.user_id, l.source_type, l.reward_grant_id, l.compensation_id,
       COALESCE(g.event_id, c.event_id) AS event_id,
       e.name AS event_name, c.reason,
       l.currency_type, l.amount, l.created_at
FROM reward.wallet_ledger l
LEFT JOIN reward.reward_grants g ON g.id = l.reward_grant_id
LEFT JOIN reward.compensations c ON c.id = l.compensation_id
JOIN reward.events e ON e.id = COALESCE(g.event_id, c.event_id)
WHERE 1 = 1
  AND l.created_at >= $1
  AND l.created_at < $2
ORDER BY l.id DESC
LIMIT $3;

-- Discard result rows while exercising one session-local prepared statement five times.
\o /dev/null
EXECUTE p4_any_source_period_cache_check(
    TIMESTAMPTZ '2026-11-07 00:00:00+00', TIMESTAMPTZ '2026-11-09 00:00:00+00', 100
);
EXECUTE p4_any_source_period_cache_check(
    TIMESTAMPTZ '2026-06-01 00:00:00+00', TIMESTAMPTZ '2026-06-01 00:30:00+00', 100
);
EXECUTE p4_any_source_period_cache_check(
    TIMESTAMPTZ '2026-11-07 00:00:00+00', TIMESTAMPTZ '2026-11-09 00:00:00+00', 100
);
EXECUTE p4_any_source_period_cache_check(
    TIMESTAMPTZ '2026-06-01 00:00:00+00', TIMESTAMPTZ '2026-06-01 00:30:00+00', 100
);
EXECUTE p4_any_source_period_cache_check(
    TIMESTAMPTZ '2026-11-07 00:00:00+00', TIMESTAMPTZ '2026-11-09 00:00:00+00', 100
);
\o

SHOW plan_cache_mode;
SELECT name, generic_plans, custom_plans
FROM pg_catalog.pg_prepared_statements
WHERE name = 'p4_any_source_period_cache_check';

-- The sixth and seventh executions expose whether AUTO has switched to a generic plan.
EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, TIMING)
EXECUTE p4_any_source_period_cache_check(
    TIMESTAMPTZ '2026-11-07 00:00:00+00', TIMESTAMPTZ '2026-11-09 00:00:00+00', 100
);
SELECT name, generic_plans, custom_plans
FROM pg_catalog.pg_prepared_statements
WHERE name = 'p4_any_source_period_cache_check';

EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, TIMING)
EXECUTE p4_any_source_period_cache_check(
    TIMESTAMPTZ '2026-06-01 00:00:00+00', TIMESTAMPTZ '2026-06-01 00:30:00+00', 100
);
SELECT name, generic_plans, custom_plans
FROM pg_catalog.pg_prepared_statements
WHERE name = 'p4_any_source_period_cache_check';

DEALLOCATE p4_any_source_period_cache_check;

-- Separately expose the worst-case shared plan for both bounds without changing the application setting.
SET plan_cache_mode = force_generic_plan;
PREPARE p4_any_source_period_generic (TIMESTAMPTZ, TIMESTAMPTZ, INTEGER) AS
SELECT l.id, l.user_id, l.source_type, l.reward_grant_id, l.compensation_id,
       COALESCE(g.event_id, c.event_id) AS event_id,
       e.name AS event_name, c.reason,
       l.currency_type, l.amount, l.created_at
FROM reward.wallet_ledger l
LEFT JOIN reward.reward_grants g ON g.id = l.reward_grant_id
LEFT JOIN reward.compensations c ON c.id = l.compensation_id
JOIN reward.events e ON e.id = COALESCE(g.event_id, c.event_id)
WHERE 1 = 1
  AND l.created_at >= $1
  AND l.created_at < $2
ORDER BY l.id DESC
LIMIT $3;

-- These two executions share one generic plan; compare its work for a recent and historical window.
EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, TIMING)
EXECUTE p4_any_source_period_generic(
    TIMESTAMPTZ '2026-11-07 00:00:00+00', TIMESTAMPTZ '2026-11-09 00:00:00+00', 100
);
EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, TIMING)
EXECUTE p4_any_source_period_generic(
    TIMESTAMPTZ '2026-06-01 00:00:00+00', TIMESTAMPTZ '2026-06-01 00:30:00+00', 100
);
SELECT name, generic_plans, custom_plans
FROM pg_catalog.pg_prepared_statements
WHERE name = 'p4_any_source_period_generic';
DEALLOCATE p4_any_source_period_generic;
RESET plan_cache_mode;

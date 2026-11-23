-- Reproduce RewardService.searchPayouts when userId and both period bounds are supplied.
-- `sourceType` is omitted, so both ledger sources share the same API query shape.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp4db' THEN
        RAISE EXCEPTION 'P4 user-period plans are restricted to rewardp4db';
    END IF;
    IF session_user <> 'reward_app' OR current_user <> 'reward_app' THEN
        RAISE EXCEPTION 'P4 API query plans must run as reward_app (session_user=%, current_user=%)',
            session_user, current_user;
    END IF;
END;
$guard$;

SELECT current_database() AS database_name, session_user, current_user;

PREPARE p4_user_period_search (BIGINT, TIMESTAMPTZ, TIMESTAMPTZ, INTEGER) AS
SELECT l.id, l.user_id, l.source_type, l.reward_grant_id, l.compensation_id,
       COALESCE(g.event_id, c.event_id) AS event_id,
       e.name AS event_name, c.reason,
       l.currency_type, l.amount, l.created_at
FROM reward.wallet_ledger l
LEFT JOIN reward.reward_grants g ON g.id = l.reward_grant_id
LEFT JOIN reward.compensations c ON c.id = l.compensation_id
JOIN reward.events e ON e.id = COALESCE(g.event_id, c.event_id)
WHERE 1 = 1
  AND l.user_id = $1
  AND l.created_at >= $2
  AND l.created_at < $3
ORDER BY l.id DESC
LIMIT $4;

-- Recent window: user 110000 has the newest rows in this fixture.
EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, TIMING)
EXECUTE p4_user_period_search(
    110000,
    TIMESTAMPTZ '2026-11-07 00:00:00+00',
    TIMESTAMPTZ '2026-11-09 00:00:00+00',
    100
);

-- Older 30-minute window: user 100001 has two matching ledger rows.
EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, TIMING)
EXECUTE p4_user_period_search(
    100001,
    TIMESTAMPTZ '2026-06-01 00:00:00+00',
    TIMESTAMPTZ '2026-06-01 00:30:00+00',
    100
);

-- Three more executions complete five AUTO custom plans with both selectivity ranges.
EXECUTE p4_user_period_search(
    110000,
    TIMESTAMPTZ '2026-11-07 00:00:00+00',
    TIMESTAMPTZ '2026-11-09 00:00:00+00',
    1
);
EXECUTE p4_user_period_search(
    100001,
    TIMESTAMPTZ '2026-06-01 00:00:00+00',
    TIMESTAMPTZ '2026-06-01 00:30:00+00',
    1
);
EXECUTE p4_user_period_search(
    110000,
    TIMESTAMPTZ '2026-11-07 00:00:00+00',
    TIMESTAMPTZ '2026-11-09 00:00:00+00',
    1
);

SELECT name, generic_plans, custom_plans
FROM pg_prepared_statements
WHERE name = 'p4_user_period_search';

-- The sixth and seventh AUTO executions compare recent and historical custom plans.
EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, TIMING)
EXECUTE p4_user_period_search(
    110000,
    TIMESTAMPTZ '2026-11-07 00:00:00+00',
    TIMESTAMPTZ '2026-11-09 00:00:00+00',
    100
);
EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, TIMING)
EXECUTE p4_user_period_search(
    100001,
    TIMESTAMPTZ '2026-06-01 00:00:00+00',
    TIMESTAMPTZ '2026-06-01 00:30:00+00',
    100
);

SELECT name, generic_plans, custom_plans
FROM pg_prepared_statements
WHERE name = 'p4_user_period_search';

-- Compare both selectivity ranges when PostgreSQL is required to use a generic plan.
SET plan_cache_mode = force_generic_plan;
EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, TIMING)
EXECUTE p4_user_period_search(
    110000,
    TIMESTAMPTZ '2026-11-07 00:00:00+00',
    TIMESTAMPTZ '2026-11-09 00:00:00+00',
    100
);
EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, TIMING)
EXECUTE p4_user_period_search(
    100001,
    TIMESTAMPTZ '2026-06-01 00:00:00+00',
    TIMESTAMPTZ '2026-06-01 00:30:00+00',
    100
);
SELECT name, generic_plans, custom_plans
FROM pg_prepared_statements
WHERE name = 'p4_user_period_search';
RESET plan_cache_mode;

DEALLOCATE p4_user_period_search;

-- Compare the optional-sourceType API branch with the COMPENSATION-only P4 queries.
-- Run after the P4 fixture. Compare before and after adding the all-source date index.
-- sourceType is omitted here exactly as it is when the optional request parameter is absent.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp4db' THEN
        RAISE EXCEPTION 'P4 payout plans are restricted to rewardp4db';
    END IF;
    IF session_user <> 'reward_app' OR current_user <> 'reward_app' THEN
        RAISE EXCEPTION 'P4 API query plans must run as reward_app (session_user=%, current_user=%)',
            session_user, current_user;
    END IF;
END;
$guard$;

SELECT current_database() AS database_name, session_user, current_user;

-- Recent window: it can be reached quickly from the descending primary-key cursor
-- because created_at and generated ledger IDs are correlated in this fixture.
EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, TIMING)
SELECT l.id, l.user_id, l.source_type, l.reward_grant_id, l.compensation_id,
       COALESCE(g.event_id, c.event_id) AS event_id,
       e.name AS event_name, c.reason,
       l.currency_type, l.amount, l.created_at
FROM reward.wallet_ledger l
LEFT JOIN reward.reward_grants g ON g.id = l.reward_grant_id
LEFT JOIN reward.compensations c ON c.id = l.compensation_id
JOIN reward.events e ON e.id = COALESCE(g.event_id, c.event_id)
WHERE l.created_at >= TIMESTAMPTZ '2026-11-07 00:00:00+00'
  AND l.created_at <  TIMESTAMPTZ '2026-11-09 00:00:00+00'
ORDER BY l.id DESC
LIMIT 100;

-- Historical narrow window: same API shape, but the descending primary-key scan
-- must pass newer rows before it reaches the requested interval.
EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, TIMING)
SELECT l.id, l.user_id, l.source_type, l.reward_grant_id, l.compensation_id,
       COALESCE(g.event_id, c.event_id) AS event_id,
       e.name AS event_name, c.reason,
       l.currency_type, l.amount, l.created_at
FROM reward.wallet_ledger l
LEFT JOIN reward.reward_grants g ON g.id = l.reward_grant_id
LEFT JOIN reward.compensations c ON c.id = l.compensation_id
JOIN reward.events e ON e.id = COALESCE(g.event_id, c.event_id)
WHERE l.created_at >= TIMESTAMPTZ '2026-06-01 00:00:00+00'
  AND l.created_at <  TIMESTAMPTZ '2026-06-01 00:30:00+00'
ORDER BY l.id DESC
LIMIT 100;

-- JdbcTemplate sends the date bounds and limit as bind parameters when sourceType is absent.
PREPARE p4_any_source_period_search (TIMESTAMPTZ, TIMESTAMPTZ, INTEGER) AS
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

-- Explain the prepared, parameterized shape used by the API on the P4 final indexes.
EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, TIMING)
EXECUTE p4_any_source_period_search(
    TIMESTAMPTZ '2026-06-01 00:00:00+00',
    TIMESTAMPTZ '2026-06-01 00:30:00+00',
    100
);
DEALLOCATE p4_any_source_period_search;

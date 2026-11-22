-- P4 query rewrite: build only the source-specific join branch requested by the filter.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp4db' THEN
        RAISE EXCEPTION 'P4 source-branch plans are restricted to rewardp4db';
    END IF;
END;
$guard$;

EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, TIMING)
SELECT l.id, l.user_id, 'COMPENSATION'::text AS source_type,
       NULL::BIGINT AS reward_grant_id, l.compensation_id,
       c.event_id, e.name AS event_name, c.reason,
       l.currency_type, l.amount, l.created_at
FROM reward.wallet_ledger l
JOIN reward.compensations c ON c.id = l.compensation_id
JOIN reward.events e ON e.id = c.event_id
WHERE l.source_type = 'COMPENSATION'
ORDER BY l.id DESC
LIMIT 100;

EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, TIMING)
SELECT l.id, l.user_id, 'COMPENSATION'::text AS source_type,
       NULL::BIGINT AS reward_grant_id, l.compensation_id,
       c.event_id, e.name AS event_name, c.reason,
       l.currency_type, l.amount, l.created_at
FROM reward.wallet_ledger l
JOIN reward.compensations c ON c.id = l.compensation_id
JOIN reward.events e ON e.id = c.event_id
WHERE l.source_type = 'COMPENSATION'
  AND l.created_at >= TIMESTAMPTZ '2026-11-07 00:00:00+00'
  AND l.created_at <  TIMESTAMPTZ '2026-11-09 00:00:00+00'
ORDER BY l.id DESC
LIMIT 100;

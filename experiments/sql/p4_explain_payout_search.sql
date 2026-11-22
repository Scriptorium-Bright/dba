-- P4 baseline and candidate query plans. Run against rewardp4db after p4_seed_ledger.sql.
-- First query reproduces the original generic two-source join.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp4db' THEN
        RAISE EXCEPTION 'P4 payout plans are restricted to rewardp4db';
    END IF;
END;
$guard$;

EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, TIMING)
SELECT l.id, l.user_id, l.source_type, l.reward_grant_id, l.compensation_id,
       COALESCE(g.event_id, c.event_id) AS event_id,
       e.name AS event_name, c.reason,
       l.currency_type, l.amount, l.created_at
FROM reward.wallet_ledger l
LEFT JOIN reward.reward_grants g ON g.id = l.reward_grant_id
LEFT JOIN reward.compensations c ON c.id = l.compensation_id
JOIN reward.events e ON e.id = COALESCE(g.event_id, c.event_id)
WHERE l.source_type = 'COMPENSATION'
ORDER BY l.id DESC
LIMIT 100;

-- Second query adds the API's narrow date window.
EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, TIMING)
SELECT l.id, l.user_id, l.source_type, l.reward_grant_id, l.compensation_id,
       COALESCE(g.event_id, c.event_id) AS event_id,
       e.name AS event_name, c.reason,
       l.currency_type, l.amount, l.created_at
FROM reward.wallet_ledger l
LEFT JOIN reward.reward_grants g ON g.id = l.reward_grant_id
LEFT JOIN reward.compensations c ON c.id = l.compensation_id
JOIN reward.events e ON e.id = COALESCE(g.event_id, c.event_id)
WHERE l.source_type = 'COMPENSATION'
  AND l.created_at >= TIMESTAMPTZ '2026-11-07 00:00:00+00'
  AND l.created_at <  TIMESTAMPTZ '2026-11-09 00:00:00+00'
ORDER BY l.id DESC
LIMIT 100;

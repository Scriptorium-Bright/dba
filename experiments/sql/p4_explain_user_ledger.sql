-- Reproduce RewardService.getLedger's first-page SELECT on the million-row P4 fixture.
-- user 100001 has 99 EVENT rows and 1 COMPENSATION row in this isolated seed.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp4db' THEN
        RAISE EXCEPTION 'P4 user-ledger plan is restricted to rewardp4db';
    END IF;
    IF session_user <> 'reward_app' OR current_user <> 'reward_app' THEN
        RAISE EXCEPTION 'P4 API query plans must run as reward_app (session_user=%, current_user=%)',
            session_user, current_user;
    END IF;
END;
$guard$;

SELECT current_database() AS database_name, session_user, current_user;

-- Keep the API joins and descending first-page limit so the plan answers the actual path.
EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, TIMING)
SELECT l.id, l.source_type, l.reward_grant_id, l.compensation_id,
       COALESCE(g.event_id, c.event_id) AS event_id,
       e.name AS event_name, c.reason,
       l.currency_type, l.amount, l.created_at
FROM reward.wallet_ledger l
LEFT JOIN reward.reward_grants g ON g.id = l.reward_grant_id
LEFT JOIN reward.compensations c ON c.id = l.compensation_id
JOIN reward.events e ON e.id = COALESCE(g.event_id, c.event_id)
WHERE l.user_id = 100001
ORDER BY l.id DESC
LIMIT 20;

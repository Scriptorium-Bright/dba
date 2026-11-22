-- P3 전용 read-only 대사: 지급 출처, 보상 snapshot, wallet 합계를 함께 확인한다.
-- reward_app이 아니라 reward_observer 또는 reward_bootstrap으로 실행한다.

SELECT source_type, count(*) AS ledger_rows,
       count(*) FILTER (WHERE reward_grant_id IS NOT NULL) AS grant_links,
       count(*) FILTER (WHERE compensation_id IS NOT NULL) AS compensation_links
FROM reward.wallet_ledger
GROUP BY source_type
ORDER BY source_type;

SELECT l.id, l.source_type, l.user_id, l.reward_grant_id, l.compensation_id
FROM reward.wallet_ledger l
WHERE NOT (
    (l.source_type = 'EVENT' AND l.reward_grant_id IS NOT NULL AND l.compensation_id IS NULL)
    OR
    (l.source_type = 'COMPENSATION' AND l.reward_grant_id IS NULL AND l.compensation_id IS NOT NULL)
)
ORDER BY l.id;

SELECT c.id AS compensation_id, c.event_id, c.user_id, c.incident_reference,
       c.reason, c.money_awarded, c.points_awarded,
       COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'MONEY'), 0) AS ledger_money,
       COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'POINT'), 0) AS ledger_points
FROM reward.compensations c
LEFT JOIN reward.wallet_ledger l ON l.compensation_id = c.id
GROUP BY c.id
ORDER BY c.id;

SELECT w.user_id, w.money_balance,
       COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'MONEY'), 0) AS ledger_money,
       w.point_balance,
       COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'POINT'), 0) AS ledger_points
FROM reward.wallets w
LEFT JOIN reward.wallet_ledger l ON l.user_id = w.user_id
GROUP BY w.user_id, w.money_balance, w.point_balance
HAVING w.money_balance <> COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'MONEY'), 0)
    OR w.point_balance <> COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'POINT'), 0)
ORDER BY w.user_id;

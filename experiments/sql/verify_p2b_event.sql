-- psql -v event_id=<id> 로 실행해 지급·원장·지갑 대사 결과를 한 행으로 출력한다.
\set ON_ERROR_STOP on

SELECT
    (SELECT COUNT(*) FROM reward.event_recipients WHERE event_id = :event_id) AS recipients,
    (SELECT COUNT(*) FROM reward.reward_grants WHERE event_id = :event_id) AS grants,
    (SELECT COUNT(*)
     FROM reward.event_recipients r
     LEFT JOIN reward.reward_grants g
       ON g.event_id = r.event_id AND g.user_id = r.user_id
     WHERE r.event_id = :event_id AND g.id IS NULL) AS pending,
    (SELECT COUNT(*) FROM (
        SELECT g.id
        FROM reward.reward_grants g
        LEFT JOIN reward.wallet_ledger l ON l.reward_grant_id = g.id
        WHERE g.event_id = :event_id
        GROUP BY g.id, g.money_awarded, g.points_awarded
        HAVING g.money_awarded <> COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'MONEY'), 0)
            OR g.points_awarded <> COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'POINT'), 0)
    ) grant_mismatches) AS grant_ledger_mismatches,
    (SELECT COUNT(*) FROM (
        SELECT w.user_id
        FROM reward.wallets w
        LEFT JOIN reward.wallet_ledger l ON l.user_id = w.user_id
        WHERE w.user_id BETWEEN 910001 AND 911000
        GROUP BY w.user_id, w.money_balance, w.point_balance
        HAVING w.money_balance <> COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'MONEY'), 0)
            OR w.point_balance <> COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'POINT'), 0)
    ) wallet_mismatches) AS wallet_ledger_mismatches;

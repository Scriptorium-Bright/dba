-- psql -v event_id=<id> 로 실행해 100동시 요청 이벤트의 지급/원장 건수를 확인한다.
\set ON_ERROR_STOP on

SELECT
    (SELECT COUNT(*) FROM reward.reward_grants WHERE event_id = :event_id) AS grants,
    (SELECT COUNT(*) FROM reward.wallet_ledger l
     JOIN reward.reward_grants g ON g.id = l.reward_grant_id
     WHERE g.event_id = :event_id) AS ledger_rows,
    (SELECT COUNT(*) FROM (
        SELECT g.id
        FROM reward.reward_grants g
        LEFT JOIN reward.wallet_ledger l ON l.reward_grant_id = g.id
        WHERE g.event_id = :event_id
        GROUP BY g.id, g.money_awarded, g.points_awarded
        HAVING g.money_awarded <> COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'MONEY'), 0)
            OR g.points_awarded <> COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'POINT'), 0)
    ) mismatches) AS ledger_mismatches;

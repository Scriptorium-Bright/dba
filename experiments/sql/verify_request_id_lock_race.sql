-- psql -v event_a=... -v event_b=... -v user_a=... -v user_b=... -v request_id=... -v second_request_id=...
\set ON_ERROR_STOP on

DO $guard$
BEGIN
    IF current_database() <> 'rewarddb' THEN
        RAISE EXCEPTION 'request ID race verification is restricted to rewarddb';
    END IF;
END;
$guard$;

SELECT
    (SELECT count(*) FROM reward.reward_grants
     WHERE event_id = :event_a AND user_id = :user_a AND request_id = :'request_id') AS winner_a,
    (SELECT count(*) FROM reward.reward_grants
     WHERE event_id = :event_b AND user_id = :user_b AND request_id = :'second_request_id') AS winner_c,
    (SELECT count(*) FROM reward.reward_grants
     WHERE request_id = :'request_id') AS shared_request_owner,
    (SELECT count(*) FROM reward.reward_grants
     WHERE event_id = :event_b AND user_id = :user_b) AS target_pair_grants,
    (SELECT money_balance FROM reward.wallets WHERE user_id = :user_a) AS wallet_a_money,
    (SELECT point_balance FROM reward.wallets WHERE user_id = :user_a) AS wallet_a_point,
    (SELECT money_balance FROM reward.wallets WHERE user_id = :user_b) AS wallet_b_money,
    (SELECT point_balance FROM reward.wallets WHERE user_id = :user_b) AS wallet_b_point,
    (SELECT count(*) FROM reward.wallet_ledger l
     JOIN reward.reward_grants g ON g.id = l.reward_grant_id
     WHERE g.event_id IN (:event_a, :event_b)) AS ledger_rows,
    (SELECT count(*) FROM (
        SELECT g.id
        FROM reward.reward_grants g
        LEFT JOIN reward.wallet_ledger l ON l.reward_grant_id = g.id
        WHERE g.event_id IN (:event_a, :event_b)
        GROUP BY g.id, g.money_awarded, g.points_awarded
        HAVING g.money_awarded <> COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'MONEY'), 0)
            OR g.points_awarded <> COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'POINT'), 0)
    ) mismatches) AS grant_ledger_mismatches,
    (SELECT count(*) FROM (
        SELECT w.user_id
        FROM reward.wallets w
        LEFT JOIN reward.wallet_ledger l ON l.user_id = w.user_id
        WHERE w.user_id IN (:user_a, :user_b)
        GROUP BY w.user_id, w.money_balance, w.point_balance
        HAVING w.money_balance <> COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'MONEY'), 0)
            OR w.point_balance <> COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'POINT'), 0)
    ) wallet_mismatches) AS wallet_ledger_mismatches;

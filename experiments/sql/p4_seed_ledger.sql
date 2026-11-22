-- P4 fixture: 1,000,000 ledger rows in an isolated rewardp4db.
-- 990,000 EVENT rows and 10,000 COMPENSATION rows (1%); each user ends at Money 10,000.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp4db' THEN
        RAISE EXCEPTION 'P4 bulk seed is restricted to rewardp4db';
    END IF;
    IF EXISTS (SELECT 1 FROM reward.wallet_ledger) THEN
        RAISE EXCEPTION 'P4 fixture expects an empty wallet_ledger';
    END IF;
END;
$guard$;

BEGIN;

INSERT INTO reward.users (id, nickname)
SELECT 100001 + n, 'P4-user-' || n
FROM generate_series(0, 9999) AS n;

INSERT INTO reward.wallets (user_id, money_balance, point_balance)
SELECT 100001 + n, 10000, 0
FROM generate_series(0, 9999) AS n;

INSERT INTO reward.events (
    id, name, mode, status, starts_at, ends_at, reward_money, reward_point
)
OVERRIDING SYSTEM VALUE
SELECT 1000 + n, 'P4 load event ' || n, 'CLAIM', 'CLOSED',
       TIMESTAMPTZ '2026-06-01 00:00:00+00',
       TIMESTAMPTZ '2026-12-31 00:00:00+00', 100, 0
FROM generate_series(1, 99) AS n;

SELECT setval(
    pg_get_serial_sequence('reward.events', 'id'),
    (SELECT max(id) FROM reward.events),
    true
);

-- One 100-money event grant per user/event pair: 99 x 10,000 = 990,000 grants.
INSERT INTO reward.reward_grants (
    event_id, user_id, request_id, money_awarded, points_awarded, created_at
)
SELECT 1001 + (seq / 10000),
       100001 + (seq % 10000),
       md5('p4-grant-' || seq)::uuid,
       100, 0,
       TIMESTAMPTZ '2026-06-01 00:00:00+00' + seq * INTERVAL '14 seconds'
FROM generate_series(0, 989999) AS seq;

-- One small synthetic incident record per user: 10,000 compensation rows.
INSERT INTO reward.compensations (
    event_id, user_id, request_id, incident_reference, reason,
    operator_user_id, money_awarded, points_awarded, created_at
)
SELECT 1099,
       100001 + n,
       md5('p4-compensation-' || n)::uuid,
       'P4-INCIDENT-001',
       'P4 generated compensation workload',
       101, 100, 0,
       TIMESTAMPTZ '2026-06-01 00:00:00+00'
           + (n * 99 + 49) * INTERVAL '14 seconds'
FROM generate_series(0, 9999) AS n;

-- Interleave the two sources by created_at so rare compensations are spread through the ID range.
INSERT INTO reward.wallet_ledger (
    user_id, reward_grant_id, compensation_id, source_type,
    currency_type, amount, created_at
)
SELECT user_id, reward_grant_id, compensation_id, source_type,
       currency_type, amount, created_at
FROM (
    SELECT g.user_id, g.id AS reward_grant_id, NULL::BIGINT AS compensation_id,
           'EVENT'::TEXT AS source_type, 'MONEY'::TEXT AS currency_type,
           g.money_awarded AS amount, g.created_at
    FROM reward.reward_grants g
    WHERE g.event_id BETWEEN 1001 AND 1099

    UNION ALL

    SELECT c.user_id, NULL::BIGINT, c.id, 'COMPENSATION'::TEXT,
           'MONEY'::TEXT, c.money_awarded, c.created_at
    FROM reward.compensations c
    WHERE c.incident_reference = 'P4-INCIDENT-001'
) AS ledger_rows
ORDER BY created_at, source_type, user_id;

COMMIT;

VACUUM (ANALYZE) reward.wallet_ledger;
ANALYZE reward.reward_grants;
ANALYZE reward.compensations;

SELECT source_type, count(*) AS rows
FROM reward.wallet_ledger
GROUP BY source_type
ORDER BY source_type;

SELECT count(*) AS grant_rows FROM reward.reward_grants WHERE event_id BETWEEN 1001 AND 1099;
SELECT count(*) AS compensation_rows FROM reward.compensations WHERE incident_reference='P4-INCIDENT-001';

-- Wallet-to-ledger mismatches should return zero rows before timing queries.
SELECT w.user_id, w.money_balance, sum(l.amount) AS ledger_money
FROM reward.wallets w
JOIN reward.wallet_ledger l ON l.user_id = w.user_id
WHERE w.user_id BETWEEN 100001 AND 110000
GROUP BY w.user_id, w.money_balance
HAVING w.money_balance <> sum(l.amount);

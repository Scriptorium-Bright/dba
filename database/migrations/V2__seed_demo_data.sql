INSERT INTO reward.users (id, nickname) VALUES
    (101, '하늘'),
    (102, '서윤'),
    (103, '도윤');

INSERT INTO reward.wallets (user_id, money_balance, point_balance) VALUES
    (101, 0, 0),
    (102, 0, 0),
    (103, 0, 0);

INSERT INTO reward.events (
    name, mode, status, starts_at, ends_at, scheduled_at, reward_money, reward_point
) VALUES
    (
        '겨울 출석 보상', 'CLAIM', 'OPEN',
        date_trunc('minute', CURRENT_TIMESTAMP) - INTERVAL '1 day',
        date_trunc('minute', CURRENT_TIMESTAMP) + INTERVAL '30 days',
        NULL, 1000, 200
    ),
    (
        '점검 기념 일괄 지급', 'BATCH', 'DRAFT',
        date_trunc('minute', CURRENT_TIMESTAMP) - INTERVAL '1 day',
        date_trunc('minute', CURRENT_TIMESTAMP) + INTERVAL '7 days',
        date_trunc('minute', CURRENT_TIMESTAMP) + INTERVAL '1 day',
        500, 100
    );

INSERT INTO reward.event_recipients (event_id, user_id)
SELECT id, user_id
FROM reward.events
CROSS JOIN (VALUES (101::BIGINT), (102::BIGINT), (103::BIGINT)) AS demo(user_id)
WHERE name = '점검 기념 일괄 지급';

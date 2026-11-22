-- 로컬 P2b 전용 사용자 1,000명과 빈 지갑을 만든다. 기존 행은 덮어쓰지 않는다.
INSERT INTO reward.users (id, nickname)
SELECT id, 'P2 Test ' || id
FROM generate_series(910001, 911000) AS id
ON CONFLICT (id) DO NOTHING;

INSERT INTO reward.wallets (user_id, money_balance, point_balance)
SELECT id, 0, 0
FROM generate_series(910001, 911000) AS id
ON CONFLICT (user_id) DO NOTHING;

-- 작업 실행 전에 사용자가 모두 생성되었는지 눈으로 확인할 수 있는 결과 행.
SELECT COUNT(*) AS seeded_users
FROM reward.users
WHERE id BETWEEN 910001 AND 911000;

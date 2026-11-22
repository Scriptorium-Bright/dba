-- P5 격리 DB에만 잠금 관측 fixture를 준비합니다. 기존 프로젝트 DB에서는 중단합니다.
DO $p5_database_guard$
BEGIN
    IF current_database() <> 'rewardp5db' THEN
        RAISE EXCEPTION 'P5 lock lab requires rewardp5db; current database is %', current_database();
    END IF;
END
$p5_database_guard$;

CREATE SCHEMA IF NOT EXISTS p5_ops AUTHORIZATION reward_migration;

-- 읽기 세션은 이전 committed 값을 볼 수 있고 UPDATE는 행 잠금을 기다리도록 단일 행을 둡니다.
CREATE TABLE IF NOT EXISTS p5_ops.lock_lab (
    id integer PRIMARY KEY,
    balance integer NOT NULL CHECK (balance >= 0)
);

INSERT INTO p5_ops.lock_lab(id, balance)
VALUES (1, 100)
ON CONFLICT (id) DO NOTHING;

COMMENT ON TABLE p5_ops.lock_lab IS
    'P5 lock-wait incident fixture; isolated from reward application tables.';
COMMENT ON COLUMN p5_ops.lock_lab.balance IS
    'Synthetic value used to check the blocked UPDATE and restore the fixture after the incident.';

GRANT USAGE ON SCHEMA p5_ops TO reward_observer;
GRANT SELECT ON p5_ops.lock_lab TO reward_observer;

SELECT current_database() AS database_name,
       current_setting('server_version') AS postgres_version,
       current_setting('transaction_isolation') AS session_isolation;

SELECT id, balance FROM p5_ops.lock_lab ORDER BY id;

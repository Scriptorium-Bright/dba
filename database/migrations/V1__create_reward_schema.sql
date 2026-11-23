CREATE TABLE reward.users (
    id BIGINT PRIMARY KEY,
    nickname TEXT NOT NULL
);

CREATE TABLE reward.wallets (
    user_id BIGINT PRIMARY KEY REFERENCES reward.users(id),
    money_balance BIGINT NOT NULL DEFAULT 0 CHECK (money_balance >= 0),
    point_balance BIGINT NOT NULL DEFAULT 0 CHECK (point_balance >= 0)
);

CREATE TABLE reward.events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 100),
    mode TEXT NOT NULL CHECK (mode IN ('CLAIM', 'BATCH')),
    status TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'OPEN', 'CLOSED')),
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    scheduled_at TIMESTAMPTZ,
    reward_money BIGINT NOT NULL DEFAULT 0 CHECK (reward_money >= 0),
    reward_point BIGINT NOT NULL DEFAULT 0 CHECK (reward_point >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (starts_at < ends_at),
    CHECK (reward_money > 0 OR reward_point > 0),
    CHECK (
        mode = 'CLAIM'
        OR (scheduled_at IS NOT NULL AND scheduled_at >= starts_at AND scheduled_at < ends_at)
    )
);

CREATE TABLE reward.event_recipients (
    event_id BIGINT NOT NULL REFERENCES reward.events(id),
    user_id BIGINT NOT NULL REFERENCES reward.users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (event_id, user_id)
);

CREATE TABLE reward.reward_grants (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES reward.events(id),
    user_id BIGINT NOT NULL REFERENCES reward.users(id),
    request_id UUID NOT NULL UNIQUE,
    money_awarded BIGINT NOT NULL CHECK (money_awarded >= 0),
    points_awarded BIGINT NOT NULL CHECK (points_awarded >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (event_id, user_id),
    UNIQUE (id, user_id),
    CHECK (money_awarded > 0 OR points_awarded > 0)
);

CREATE TABLE reward.wallet_ledger (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES reward.users(id),
    reward_grant_id BIGINT NOT NULL,
    currency_type TEXT NOT NULL CHECK (currency_type IN ('MONEY', 'POINT')),
    amount BIGINT NOT NULL CHECK (amount > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (reward_grant_id, user_id)
        REFERENCES reward.reward_grants(id, user_id),
    UNIQUE (reward_grant_id, currency_type)
);

CREATE TABLE reward.event_policy_audit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES reward.events(id),
    old_money BIGINT NOT NULL CHECK (old_money >= 0),
    old_point BIGINT NOT NULL CHECK (old_point >= 0),
    new_money BIGINT NOT NULL CHECK (new_money >= 0),
    new_point BIGINT NOT NULL CHECK (new_point >= 0),
    changed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE reward.event_status_audit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES reward.events(id),
    previous_status TEXT CHECK (previous_status IN ('DRAFT', 'OPEN', 'CLOSED')),
    new_status TEXT NOT NULL CHECK (new_status IN ('DRAFT', 'OPEN', 'CLOSED')),
    changed_by_user_id BIGINT NOT NULL REFERENCES reward.users(id),
    changed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE reward.batch_runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES reward.events(id),
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    finished_at TIMESTAMPTZ,
    error_summary TEXT
);

CREATE INDEX events_status_starts_at_idx ON reward.events (status, starts_at DESC);
CREATE INDEX wallet_ledger_user_id_id_idx ON reward.wallet_ledger (user_id, id DESC);
CREATE INDEX reward_grants_event_id_created_at_idx ON reward.reward_grants (event_id, created_at DESC);

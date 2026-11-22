-- 기존 CLAIM/BATCH 코드가 계속 동작하도록 출처 컬럼의 EVENT 기본값부터 추가한다.
-- PostgreSQL 11+의 상수 기본값은 기존 행에 값을 논리적으로 적용하므로 전체 행 UPDATE를 피한다.
CREATE TABLE reward.compensations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES reward.events(id),
    user_id BIGINT NOT NULL REFERENCES reward.users(id),
    request_id UUID NOT NULL UNIQUE,
    incident_reference TEXT NOT NULL
        CHECK (length(btrim(incident_reference)) BETWEEN 1 AND 100),
    reason TEXT NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 500),
    operator_user_id BIGINT NOT NULL REFERENCES reward.users(id),
    money_awarded BIGINT NOT NULL CHECK (money_awarded >= 0),
    points_awarded BIGINT NOT NULL CHECK (points_awarded >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (money_awarded > 0 OR points_awarded > 0),
    -- 원장 FK가 같은 사용자의 보상만 가리키도록 하는 복합 키다.
    UNIQUE (id, user_id),
    -- 요청 키가 달라도 같은 incident/user가 중복 보상되지 않게 한다.
    UNIQUE (event_id, incident_reference, user_id)
);

ALTER TABLE reward.wallet_ledger
    ADD COLUMN source_type TEXT NOT NULL DEFAULT 'EVENT',
    ADD COLUMN compensation_id BIGINT;

CREATE INDEX compensations_user_created_idx
    ON reward.compensations (user_id, created_at DESC, id DESC);

CREATE INDEX wallet_ledger_compensation_id_idx
    ON reward.wallet_ledger (compensation_id)
    WHERE compensation_id IS NOT NULL;

COMMENT ON COLUMN reward.wallet_ledger.source_type IS
    'Payout source. EVENT is the compatibility default until source constraints are staged.';
COMMENT ON COLUMN reward.wallet_ledger.compensation_id IS
    'Set only for a CS/outage compensation ledger row.';

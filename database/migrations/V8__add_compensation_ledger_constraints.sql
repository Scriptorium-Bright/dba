-- 기존 event grant insert는 reward_grant_id만 지정하므로 이 단계까지는 그대로 호환된다.
-- NOT VALID 제약은 새 쓰기부터 보호하고, 과거 행 전체 검사는 V9에서 따로 수행한다.
ALTER TABLE reward.wallet_ledger
    ALTER COLUMN reward_grant_id DROP NOT NULL,
    ADD CONSTRAINT wallet_ledger_compensation_user_fk
        FOREIGN KEY (compensation_id, user_id)
        REFERENCES reward.compensations (id, user_id)
        NOT VALID,
    ADD CONSTRAINT wallet_ledger_source_consistency_ck
        CHECK (
            (source_type = 'EVENT'
                AND reward_grant_id IS NOT NULL
                AND compensation_id IS NULL)
            OR
            (source_type = 'COMPENSATION'
                AND reward_grant_id IS NULL
                AND compensation_id IS NOT NULL)
        ) NOT VALID;

CREATE UNIQUE INDEX wallet_ledger_compensation_currency_uq
    ON reward.wallet_ledger (compensation_id, currency_type)
    WHERE compensation_id IS NOT NULL;

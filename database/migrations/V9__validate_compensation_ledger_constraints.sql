-- V8의 확장 적용과 과거 행 검증을 분리한다. 실패하면 원인 행을 고친 뒤 이 migration을 재시도한다.
ALTER TABLE reward.wallet_ledger
    VALIDATE CONSTRAINT wallet_ledger_compensation_user_fk;

ALTER TABLE reward.wallet_ledger
    VALIDATE CONSTRAINT wallet_ledger_source_consistency_ck;

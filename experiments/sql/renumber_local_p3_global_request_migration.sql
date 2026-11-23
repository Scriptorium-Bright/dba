-- One-time metadata alignment for the local P3 volume after common request-ID migration moved from V15 to V16.
-- The procedure bodies are unchanged; keep the original checksum while changing only the Flyway version key.
BEGIN;

DO $guard$
DECLARE
    v_rows BIGINT;
BEGIN
    IF current_database() <> 'rewardp3db' THEN
        RAISE EXCEPTION 'expected rewardp3db, got %', current_database();
    END IF;

    IF EXISTS (
        SELECT 1 FROM reward.flyway_schema_history WHERE version = '16'
    ) THEN
        RAISE EXCEPTION 'version 16 already exists; inspect Flyway history before renumbering';
    END IF;

    UPDATE reward.flyway_schema_history
    SET version = '16'
    WHERE version = '15'
      AND description = 'enforce global payout request ids'
      AND success
      AND checksum = -764694035;

    GET DIAGNOSTICS v_rows = ROW_COUNT;
    IF v_rows <> 1 THEN
        RAISE EXCEPTION 'expected one applied global request-ID V15 row, updated %', v_rows;
    END IF;
END;
$guard$;

COMMIT;

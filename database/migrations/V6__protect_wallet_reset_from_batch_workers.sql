-- BATCH 워커는 이벤트별 session advisory lock을 한 실행 전체 동안 보유한다.
-- 지갑 reset이 worker 한 명 단위 커밋 사이에 끼어들어 reset 뒤 지급이 이어지지 않도록
-- wrapper가 같은 lock key를 transaction advisory lock으로 먼저 확인한다.

ALTER FUNCTION reward.reset_local_user_wallet(BIGINT)
    RENAME TO reset_local_user_wallet_without_batch_lock;
ALTER FUNCTION reward.reset_all_local_wallets()
    RENAME TO reset_all_local_wallets_without_batch_lock;

-- 내부 함수는 wrapper에서만 호출한다. 앱 역할의 직접 호출은 회수한다.
REVOKE ALL ON FUNCTION reward.reset_local_user_wallet_without_batch_lock(BIGINT) FROM PUBLIC, reward_app;
REVOKE ALL ON FUNCTION reward.reset_all_local_wallets_without_batch_lock() FROM PUBLIC, reward_app;

CREATE FUNCTION reward.try_lock_local_batch_events()
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, reward
AS $function$
DECLARE
    v_event_id BIGINT;
BEGIN
    FOR v_event_id IN
        SELECT e.id
        FROM reward.events e
        WHERE e.mode = 'BATCH'
        ORDER BY e.id
    LOOP
        -- BatchService uses the matching session lock key for the full worker lifetime.
        IF NOT pg_catalog.pg_try_advisory_xact_lock(
            pg_catalog.hashtextextended('reward.batch:' || v_event_id::TEXT, 0)
        ) THEN
            RETURN FALSE;
        END IF;
    END LOOP;
    RETURN TRUE;
END;
$function$;

CREATE FUNCTION reward.reset_local_user_wallet(
    p_user_id BIGINT
)
RETURNS TABLE (
    result TEXT,
    users_reset BIGINT,
    grants_deleted BIGINT,
    ledger_rows_deleted BIGINT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, reward
AS $function$
BEGIN
    IF NOT reward.try_lock_local_batch_events() THEN
        result := 'BATCH_RUNNING';
        users_reset := 0;
        grants_deleted := 0;
        ledger_rows_deleted := 0;
        RETURN NEXT;
        RETURN;
    END IF;

    RETURN QUERY
    SELECT * FROM reward.reset_local_user_wallet_without_batch_lock(p_user_id);
END;
$function$;

CREATE FUNCTION reward.reset_all_local_wallets()
RETURNS TABLE (
    result TEXT,
    users_reset BIGINT,
    grants_deleted BIGINT,
    ledger_rows_deleted BIGINT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, reward
AS $function$
BEGIN
    IF NOT reward.try_lock_local_batch_events() THEN
        result := 'BATCH_RUNNING';
        users_reset := 0;
        grants_deleted := 0;
        ledger_rows_deleted := 0;
        RETURN NEXT;
        RETURN;
    END IF;

    RETURN QUERY
    SELECT * FROM reward.reset_all_local_wallets_without_batch_lock();
END;
$function$;

-- helper는 공개하지 않고 API 계정에는 두 명시적 reset 진입점만 허용한다.
REVOKE ALL ON FUNCTION reward.try_lock_local_batch_events() FROM PUBLIC, reward_app;
REVOKE ALL ON FUNCTION reward.reset_local_user_wallet(BIGINT) FROM PUBLIC;
REVOKE ALL ON FUNCTION reward.reset_all_local_wallets() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION reward.reset_local_user_wallet(BIGINT) TO reward_app;
GRANT EXECUTE ON FUNCTION reward.reset_all_local_wallets() TO reward_app;

COMMENT ON FUNCTION reward.reset_local_user_wallet(BIGINT) IS
    'Local demo only: reject active BATCH workers, then reconcile and clear one user wallet activity.';
COMMENT ON FUNCTION reward.reset_all_local_wallets() IS
    'Local demo only: reject active BATCH workers, then reconcile and clear all wallet activity.';

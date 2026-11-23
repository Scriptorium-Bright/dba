-- P3 adds a second ledger source. Local reset must delete that source too,
-- or the new zeroed wallet would retain compensation history and fail reconciliation.
-- Drop wrappers first because they depend on the internal functions' table return shape.
DROP FUNCTION reward.reset_local_user_wallet(BIGINT);
DROP FUNCTION reward.reset_all_local_wallets();
DROP FUNCTION reward.reset_local_user_wallet_without_batch_lock(BIGINT);
DROP FUNCTION reward.reset_all_local_wallets_without_batch_lock();

CREATE FUNCTION reward.reset_local_user_wallet_without_batch_lock(p_user_id BIGINT)
RETURNS TABLE (
    result TEXT,
    users_reset BIGINT,
    grants_deleted BIGINT,
    compensations_deleted BIGINT,
    ledger_rows_deleted BIGINT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, reward
AS $function$
DECLARE
    v_invalid_grants BIGINT;
    v_wallet_money BIGINT;
    v_wallet_points BIGINT;
    v_ledger_money NUMERIC;
    v_ledger_points NUMERIC;
    v_grants_deleted BIGINT;
    v_compensations_deleted BIGINT;
    v_ledger_rows_deleted BIGINT;
BEGIN
    users_reset := 0;
    grants_deleted := 0;
    compensations_deleted := 0;
    ledger_rows_deleted := 0;

    PERFORM 1 FROM reward.users u WHERE u.id = p_user_id;
    IF NOT FOUND THEN
        result := 'USER_NOT_FOUND';
        RETURN NEXT;
        RETURN;
    END IF;

    -- Preserve the established event → wallet lock order used by CLAIM/BATCH and V5.
    PERFORM e.id FROM reward.events e ORDER BY e.id FOR UPDATE;

    SELECT w.money_balance, w.point_balance
    INTO v_wallet_money, v_wallet_points
    FROM reward.wallets w
    WHERE w.user_id = p_user_id
    FOR UPDATE;
    IF NOT FOUND THEN
        result := 'WALLET_NOT_FOUND';
        RETURN NEXT;
        RETURN;
    END IF;

    SELECT count(*) INTO v_invalid_grants
    FROM reward.reward_grants g
    LEFT JOIN LATERAL (
        SELECT COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'MONEY'), 0) AS money_total,
               COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'POINT'), 0) AS point_total
        FROM reward.wallet_ledger l
        WHERE l.reward_grant_id = g.id
    ) ledger ON TRUE
    WHERE g.user_id = p_user_id
      AND (ledger.money_total <> g.money_awarded OR ledger.point_total <> g.points_awarded);

    IF v_invalid_grants > 0 THEN
        result := 'DATA_MISMATCH';
        RETURN NEXT;
        RETURN;
    END IF;

    SELECT COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'MONEY'), 0),
           COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'POINT'), 0)
    INTO v_ledger_money, v_ledger_points
    FROM reward.wallet_ledger l
    WHERE l.user_id = p_user_id;

    IF v_wallet_money <> v_ledger_money OR v_wallet_points <> v_ledger_points THEN
        result := 'DATA_MISMATCH';
        RETURN NEXT;
        RETURN;
    END IF;

    DELETE FROM reward.wallet_ledger l WHERE l.user_id = p_user_id;
    GET DIAGNOSTICS v_ledger_rows_deleted = ROW_COUNT;

    -- Ledger FK rows are gone; source records can now be cleared in the same reset transaction.
    DELETE FROM reward.compensations c WHERE c.user_id = p_user_id;
    GET DIAGNOSTICS v_compensations_deleted = ROW_COUNT;

    DELETE FROM reward.reward_grants g WHERE g.user_id = p_user_id;
    GET DIAGNOSTICS v_grants_deleted = ROW_COUNT;

    UPDATE reward.wallets w SET money_balance = 0, point_balance = 0 WHERE w.user_id = p_user_id;

    result := 'RESET';
    users_reset := 1;
    grants_deleted := v_grants_deleted;
    compensations_deleted := v_compensations_deleted;
    ledger_rows_deleted := v_ledger_rows_deleted;
    RETURN NEXT;
END;
$function$;

CREATE FUNCTION reward.reset_all_local_wallets_without_batch_lock()
RETURNS TABLE (
    result TEXT,
    users_reset BIGINT,
    grants_deleted BIGINT,
    compensations_deleted BIGINT,
    ledger_rows_deleted BIGINT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, reward
AS $function$
DECLARE
    v_users_reset BIGINT;
    v_users_without_wallet BIGINT;
    v_invalid_grants BIGINT;
    v_invalid_wallets BIGINT;
    v_grants_deleted BIGINT;
    v_compensations_deleted BIGINT;
    v_ledger_rows_deleted BIGINT;
BEGIN
    PERFORM e.id FROM reward.events e ORDER BY e.id FOR UPDATE;
    PERFORM w.user_id FROM reward.wallets w ORDER BY w.user_id FOR UPDATE;

    SELECT count(*) INTO v_users_reset FROM reward.users;
    SELECT count(*) INTO v_users_without_wallet
    FROM reward.users u LEFT JOIN reward.wallets w ON w.user_id = u.id
    WHERE w.user_id IS NULL;
    IF v_users_without_wallet > 0 THEN
        result := 'WALLET_NOT_FOUND';
        users_reset := 0;
        grants_deleted := 0;
        compensations_deleted := 0;
        ledger_rows_deleted := 0;
        RETURN NEXT;
        RETURN;
    END IF;

    SELECT count(*) INTO v_invalid_grants
    FROM reward.reward_grants g
    LEFT JOIN LATERAL (
        SELECT COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'MONEY'), 0) AS money_total,
               COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'POINT'), 0) AS point_total
        FROM reward.wallet_ledger l WHERE l.reward_grant_id = g.id
    ) ledger ON TRUE
    WHERE ledger.money_total <> g.money_awarded OR ledger.point_total <> g.points_awarded;

    SELECT count(*) INTO v_invalid_wallets
    FROM reward.wallets w
    LEFT JOIN LATERAL (
        SELECT COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'MONEY'), 0) AS money_total,
               COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'POINT'), 0) AS point_total
        FROM reward.wallet_ledger l WHERE l.user_id = w.user_id
    ) ledger ON TRUE
    WHERE ledger.money_total <> w.money_balance OR ledger.point_total <> w.point_balance;

    IF v_invalid_grants > 0 OR v_invalid_wallets > 0 THEN
        result := 'DATA_MISMATCH';
        users_reset := 0;
        grants_deleted := 0;
        compensations_deleted := 0;
        ledger_rows_deleted := 0;
        RETURN NEXT;
        RETURN;
    END IF;

    DELETE FROM reward.wallet_ledger;
    GET DIAGNOSTICS v_ledger_rows_deleted = ROW_COUNT;
    DELETE FROM reward.compensations;
    GET DIAGNOSTICS v_compensations_deleted = ROW_COUNT;
    DELETE FROM reward.reward_grants;
    GET DIAGNOSTICS v_grants_deleted = ROW_COUNT;
    UPDATE reward.wallets SET money_balance = 0, point_balance = 0;

    result := 'RESET';
    users_reset := v_users_reset;
    grants_deleted := v_grants_deleted;
    compensations_deleted := v_compensations_deleted;
    ledger_rows_deleted := v_ledger_rows_deleted;
    RETURN NEXT;
END;
$function$;

CREATE FUNCTION reward.reset_local_user_wallet(p_user_id BIGINT)
RETURNS TABLE (
    result TEXT,
    users_reset BIGINT,
    grants_deleted BIGINT,
    compensations_deleted BIGINT,
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
        compensations_deleted := 0;
        ledger_rows_deleted := 0;
        RETURN NEXT;
        RETURN;
    END IF;
    RETURN QUERY SELECT * FROM reward.reset_local_user_wallet_without_batch_lock(p_user_id);
END;
$function$;

CREATE FUNCTION reward.reset_all_local_wallets()
RETURNS TABLE (
    result TEXT,
    users_reset BIGINT,
    grants_deleted BIGINT,
    compensations_deleted BIGINT,
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
        compensations_deleted := 0;
        ledger_rows_deleted := 0;
        RETURN NEXT;
        RETURN;
    END IF;
    RETURN QUERY SELECT * FROM reward.reset_all_local_wallets_without_batch_lock();
END;
$function$;

-- Recreate the wrapper permissions after replacing their return type.
REVOKE ALL ON FUNCTION reward.reset_local_user_wallet_without_batch_lock(BIGINT) FROM PUBLIC, reward_app;
REVOKE ALL ON FUNCTION reward.reset_all_local_wallets_without_batch_lock() FROM PUBLIC, reward_app;
REVOKE ALL ON FUNCTION reward.reset_local_user_wallet(BIGINT) FROM PUBLIC;
REVOKE ALL ON FUNCTION reward.reset_all_local_wallets() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION reward.reset_local_user_wallet(BIGINT) TO reward_app;
GRANT EXECUTE ON FUNCTION reward.reset_all_local_wallets() TO reward_app;

COMMENT ON FUNCTION reward.reset_local_user_wallet(BIGINT) IS
    'Local demo only: reconcile then clear one user wallet, grants, compensations, and ledger.';
COMMENT ON FUNCTION reward.reset_all_local_wallets() IS
    'Local demo only: reconcile then clear all wallet grants, compensations, and ledger.';

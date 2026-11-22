-- 로컬 데모에서 사용자 지갑을 다시 실습할 수 있게 초기화한다.
-- 잔액만 0으로 덮으면 원장 합계와 지급 중복 방어가 어긋나므로 grant와 ledger도 함께 비운다.
-- 사용자·이벤트·BATCH 대상·batch_runs 기록은 보존한다.

-- V4의 이벤트 한 건 초기화는 UI 요구가 사용자/전체 지갑 초기화로 바뀌어 대체한다.
DROP FUNCTION reward.reset_claim_for_local_practice(BIGINT, BIGINT);

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
DECLARE
    v_wallet_user_id BIGINT;
    v_invalid_grants BIGINT;
    v_wallet_money BIGINT;
    v_wallet_points BIGINT;
    v_ledger_money NUMERIC;
    v_ledger_points NUMERIC;
    v_grants_deleted BIGINT;
    v_ledger_rows_deleted BIGINT;
BEGIN
    users_reset := 0;
    grants_deleted := 0;
    ledger_rows_deleted := 0;

    PERFORM 1 FROM reward.users u WHERE u.id = p_user_id;
    IF NOT FOUND THEN
        result := 'USER_NOT_FOUND';
        RETURN NEXT;
        RETURN;
    END IF;

    -- 지급 프로시저는 이벤트를 FOR SHARE로 잡고 지갑을 갱신한다.
    -- 먼저 모든 이벤트를 같은 순서로 잠가 진행 중 지급이 초기화 뒤에 섞이지 않게 한다.
    PERFORM e.id
    FROM reward.events e
    ORDER BY e.id
    FOR UPDATE;

    SELECT w.user_id, w.money_balance, w.point_balance
    INTO v_wallet_user_id, v_wallet_money, v_wallet_points
    FROM reward.wallets w
    WHERE w.user_id = p_user_id
    FOR UPDATE;
    IF NOT FOUND THEN
        result := 'WALLET_NOT_FOUND';
        RETURN NEXT;
        RETURN;
    END IF;

    -- 지우기 전 grant snapshot과 재화별 원장 합을 비교한다.
    SELECT count(*)
    INTO v_invalid_grants
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

    -- FK가 있는 원장을 먼저 지우고 지급 기록을 지운 뒤 잔액을 0으로 맞춘다.
    DELETE FROM reward.wallet_ledger l WHERE l.user_id = p_user_id;
    GET DIAGNOSTICS v_ledger_rows_deleted = ROW_COUNT;

    DELETE FROM reward.reward_grants g WHERE g.user_id = p_user_id;
    GET DIAGNOSTICS v_grants_deleted = ROW_COUNT;

    UPDATE reward.wallets w
    SET money_balance = 0, point_balance = 0
    WHERE w.user_id = p_user_id;

    result := 'RESET';
    users_reset := 1;
    grants_deleted := v_grants_deleted;
    ledger_rows_deleted := v_ledger_rows_deleted;
    RETURN NEXT;
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
DECLARE
    v_users_reset BIGINT;
    v_users_without_wallet BIGINT;
    v_invalid_grants BIGINT;
    v_invalid_wallets BIGINT;
    v_grants_deleted BIGINT;
    v_ledger_rows_deleted BIGINT;
BEGIN
    -- 이벤트 → 지갑 순으로 잠가 CLAIm/BATCH 지급과 같은 잠금 순서를 유지한다.
    PERFORM e.id
    FROM reward.events e
    ORDER BY e.id
    FOR UPDATE;

    PERFORM w.user_id
    FROM reward.wallets w
    ORDER BY w.user_id
    FOR UPDATE;

    SELECT count(*) INTO v_users_reset FROM reward.users;

    SELECT count(*)
    INTO v_users_without_wallet
    FROM reward.users u
    LEFT JOIN reward.wallets w ON w.user_id = u.id
    WHERE w.user_id IS NULL;
    IF v_users_without_wallet > 0 THEN
        result := 'WALLET_NOT_FOUND';
        users_reset := 0;
        grants_deleted := 0;
        ledger_rows_deleted := 0;
        RETURN NEXT;
        RETURN;
    END IF;

    SELECT count(*)
    INTO v_invalid_grants
    FROM reward.reward_grants g
    LEFT JOIN LATERAL (
        SELECT COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'MONEY'), 0) AS money_total,
               COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'POINT'), 0) AS point_total
        FROM reward.wallet_ledger l
        WHERE l.reward_grant_id = g.id
    ) ledger ON TRUE
    WHERE ledger.money_total <> g.money_awarded OR ledger.point_total <> g.points_awarded;

    SELECT count(*)
    INTO v_invalid_wallets
    FROM reward.wallets w
    LEFT JOIN LATERAL (
        SELECT COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'MONEY'), 0) AS money_total,
               COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'POINT'), 0) AS point_total
        FROM reward.wallet_ledger l
        WHERE l.user_id = w.user_id
    ) ledger ON TRUE
    WHERE ledger.money_total <> w.money_balance OR ledger.point_total <> w.point_balance;

    IF v_invalid_grants > 0 OR v_invalid_wallets > 0 THEN
        result := 'DATA_MISMATCH';
        users_reset := 0;
        grants_deleted := 0;
        ledger_rows_deleted := 0;
        RETURN NEXT;
        RETURN;
    END IF;

    DELETE FROM reward.wallet_ledger;
    GET DIAGNOSTICS v_ledger_rows_deleted = ROW_COUNT;

    DELETE FROM reward.reward_grants;
    GET DIAGNOSTICS v_grants_deleted = ROW_COUNT;

    UPDATE reward.wallets
    SET money_balance = 0, point_balance = 0;

    result := 'RESET';
    users_reset := v_users_reset;
    grants_deleted := v_grants_deleted;
    ledger_rows_deleted := v_ledger_rows_deleted;
    RETURN NEXT;
END;
$function$;

-- API 계정에는 테이블 DELETE 권한을 주지 않고, 로컬 전용 controller가 필요한 함수만 호출한다.
REVOKE ALL ON FUNCTION reward.reset_local_user_wallet(BIGINT) FROM PUBLIC;
REVOKE ALL ON FUNCTION reward.reset_all_local_wallets() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION reward.reset_local_user_wallet(BIGINT) TO reward_app;
GRANT EXECUTE ON FUNCTION reward.reset_all_local_wallets() TO reward_app;

COMMENT ON FUNCTION reward.reset_local_user_wallet(BIGINT) IS
    'Local demo only: reconcile then clear one user wallet, its grants, and its ledger.';
COMMENT ON FUNCTION reward.reset_all_local_wallets() IS
    'Local demo only: reconcile then clear every wallet, grant, and ledger while preserving users and events.';

-- Local demo cleanup for repeatable CLAIM/idempotency practice.
-- This is deliberately a narrow SECURITY DEFINER function: reward_app receives no
-- DELETE privilege on grants or ledger, and the function only removes one CLAIM grant.
CREATE OR REPLACE FUNCTION reward.reset_claim_for_local_practice(
    p_event_id BIGINT,
    p_user_id BIGINT
)
RETURNS TABLE (
    result TEXT,
    money_reversed BIGINT,
    points_reversed BIGINT,
    money_balance BIGINT,
    points_balance BIGINT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, reward
AS $function$
DECLARE
    v_event_mode TEXT;
    v_grant_id BIGINT;
    v_money_awarded BIGINT;
    v_points_awarded BIGINT;
    v_wallet_money BIGINT;
    v_wallet_points BIGINT;
    v_grant_ledger_money BIGINT;
    v_grant_ledger_points BIGINT;
    v_user_ledger_money BIGINT;
    v_user_ledger_points BIGINT;
    v_deleted_grants INTEGER;
    v_new_money BIGINT;
    v_new_points BIGINT;
BEGIN
    PERFORM 1 FROM reward.users u WHERE u.id = p_user_id;
    IF NOT FOUND THEN
        result := 'USER_NOT_FOUND';
        RETURN NEXT;
        RETURN;
    END IF;

    -- CLAIM procedure takes FOR SHARE on the event row; this lock serializes reset with claims.
    SELECT e.mode INTO v_event_mode
    FROM reward.events e
    WHERE e.id = p_event_id
    FOR UPDATE;
    IF NOT FOUND THEN
        result := 'EVENT_NOT_FOUND';
        RETURN NEXT;
        RETURN;
    END IF;
    IF v_event_mode <> 'CLAIM' THEN
        result := 'NOT_CLAIM';
        RETURN NEXT;
        RETURN;
    END IF;

    SELECT g.id, g.money_awarded, g.points_awarded
    INTO v_grant_id, v_money_awarded, v_points_awarded
    FROM reward.reward_grants g
    WHERE g.event_id = p_event_id AND g.user_id = p_user_id
    FOR UPDATE;
    IF NOT FOUND THEN
        result := 'NO_GRANT';
        RETURN NEXT;
        RETURN;
    END IF;

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

    SELECT COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'MONEY'), 0),
           COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'POINT'), 0)
    INTO v_grant_ledger_money, v_grant_ledger_points
    FROM reward.wallet_ledger l
    WHERE l.reward_grant_id = v_grant_id;
    IF v_grant_ledger_money <> v_money_awarded
       OR v_grant_ledger_points <> v_points_awarded THEN
        result := 'GRANT_LEDGER_MISMATCH';
        RETURN NEXT;
        RETURN;
    END IF;

    SELECT COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'MONEY'), 0),
           COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'POINT'), 0)
    INTO v_user_ledger_money, v_user_ledger_points
    FROM reward.wallet_ledger l
    WHERE l.user_id = p_user_id;
    IF v_wallet_money <> v_user_ledger_money OR v_wallet_points <> v_user_ledger_points THEN
        result := 'WALLET_LEDGER_MISMATCH';
        RETURN NEXT;
        RETURN;
    END IF;

    -- Remove dependent ledger rows first. The row locks and all changes remain caller-transactional.
    DELETE FROM reward.wallet_ledger l WHERE l.reward_grant_id = v_grant_id;
    DELETE FROM reward.reward_grants g
    WHERE g.id = v_grant_id AND g.event_id = p_event_id AND g.user_id = p_user_id;
    GET DIAGNOSTICS v_deleted_grants = ROW_COUNT;
    IF v_deleted_grants <> 1 THEN
        RAISE EXCEPTION 'Claim grant changed while local reset held its event lock';
    END IF;

    UPDATE reward.wallets w
    SET money_balance = w.money_balance - v_money_awarded,
        point_balance = w.point_balance - v_points_awarded
    WHERE w.user_id = p_user_id
    RETURNING w.money_balance, w.point_balance INTO v_new_money, v_new_points;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Wallet disappeared while local reset held its row lock';
    END IF;

    SELECT COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'MONEY'), 0),
           COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'POINT'), 0)
    INTO v_user_ledger_money, v_user_ledger_points
    FROM reward.wallet_ledger l
    WHERE l.user_id = p_user_id;
    IF v_new_money <> v_user_ledger_money OR v_new_points <> v_user_ledger_points THEN
        RAISE EXCEPTION 'Wallet/ledger mismatch after local claim reset';
    END IF;

    result := 'RESET';
    money_reversed := v_money_awarded;
    points_reversed := v_points_awarded;
    money_balance := v_new_money;
    points_balance := v_new_points;
    RETURN NEXT;
END;
$function$;

-- Do not expose the cleanup function to PUBLIC; only the application role may call it.
REVOKE ALL ON FUNCTION reward.reset_claim_for_local_practice(BIGINT, BIGINT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION reward.reset_claim_for_local_practice(BIGINT, BIGINT) TO reward_app;

COMMENT ON FUNCTION reward.reset_claim_for_local_practice(BIGINT, BIGINT) IS
    'Local demo only: remove one CLAIM grant and its ledger after reconciliation checks.';

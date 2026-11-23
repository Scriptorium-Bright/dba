-- CLAIM과 CS compensation 전역에서 request UUID 하나가 하나의 지급만 식별한다.
-- 동일 UUID 조회 전에 두 프로시저가 같은 transaction advisory lock을 획득한다.
DO $preflight$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM reward.reward_grants g
        JOIN reward.compensations c USING (request_id)
    ) THEN
        RAISE EXCEPTION 'request_id is already shared across payout tables';
    END IF;
END;
$preflight$;

-- 같은 Idempotency-Key의 동시 처리를 트랜잭션 수준 advisory lock으로 직렬화한다.
-- 테이블 UNIQUE 제약은 데이터 무결성 최종 방어선으로 유지한다.
CREATE OR REPLACE PROCEDURE reward.grant_event_reward(
    IN p_event_id BIGINT,
    IN p_user_id BIGINT,
    IN p_request_id UUID,
    IN p_trigger TEXT,
    INOUT p_result TEXT,
    INOUT p_grant_id BIGINT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, reward, pg_temp
AS $procedure$
DECLARE
    v_event reward.events%ROWTYPE;
    v_grant reward.reward_grants%ROWTYPE;
    v_now TIMESTAMPTZ;
BEGIN
    p_result := NULL;
    p_grant_id := NULL;

    IF p_request_id IS NULL OR p_trigger IS NULL OR p_trigger NOT IN ('CLAIM', 'BATCH') THEN
        RAISE EXCEPTION 'invalid request_id or trigger';
    END IF;

    -- 동일 UUID를 쓰는 요청은 최초 조회보다 먼저 같은 key lock을 획득한다.
    -- 해시 충돌은 다른 요청의 대기만 만들고, UNIQUE 제약의 역할을 대체하지 않는다.
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(p_request_id::TEXT, 0)
    );

    -- 지급 종류와 무관하게 UUID는 하나의 지급 요청만 소유한다.
    IF EXISTS (SELECT 1 FROM reward.compensations WHERE request_id = p_request_id) THEN
        p_result := 'REQUEST_ID_CONFLICT';
        RETURN;
    END IF;

    SELECT * INTO v_grant
    FROM reward.reward_grants
    WHERE request_id = p_request_id;

    IF FOUND THEN
        IF v_grant.event_id <> p_event_id OR v_grant.user_id <> p_user_id THEN
            p_result := 'REQUEST_ID_CONFLICT';
            RETURN;
        END IF;
        p_result := 'REPLAYED';
        p_grant_id := v_grant.id;
        RETURN;
    END IF;

    SELECT * INTO v_grant
    FROM reward.reward_grants
    WHERE event_id = p_event_id AND user_id = p_user_id;

    IF FOUND THEN
        p_result := 'ALREADY_GRANTED';
        p_grant_id := v_grant.id;
        RETURN;
    END IF;

    SELECT * INTO v_event
    FROM reward.events
    WHERE id = p_event_id
    FOR SHARE;

    IF NOT FOUND THEN
        p_result := 'EVENT_NOT_FOUND';
        RETURN;
    END IF;

    IF v_event.mode <> p_trigger OR v_event.status <> 'OPEN' THEN
        p_result := 'NOT_AVAILABLE';
        RETURN;
    END IF;

    v_now := clock_timestamp();
    IF p_trigger = 'CLAIM' THEN
        IF v_now < v_event.starts_at OR v_now >= v_event.ends_at THEN
            p_result := 'NOT_AVAILABLE';
            RETURN;
        END IF;
    ELSE
        IF v_event.scheduled_at IS NULL OR v_now < v_event.scheduled_at
           OR NOT EXISTS (
               SELECT 1
               FROM reward.event_recipients r
               WHERE r.event_id = p_event_id AND r.user_id = p_user_id
           ) THEN
            p_result := 'NOT_AVAILABLE';
            RETURN;
        END IF;
    END IF;

    INSERT INTO reward.reward_grants (
        event_id, user_id, request_id, money_awarded, points_awarded
    ) VALUES (
        p_event_id, p_user_id, p_request_id,
        v_event.reward_money, v_event.reward_point
    )
    ON CONFLICT (event_id, user_id) DO NOTHING
    RETURNING id INTO p_grant_id;

    IF p_grant_id IS NULL THEN
        -- 다른 요청 ID가 같은 사용자·이벤트를 먼저 지급한 경우 winner를 읽는다.
        SELECT * INTO v_grant
        FROM reward.reward_grants
        WHERE event_id = p_event_id AND user_id = p_user_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'grant conflict occurred but the winning row is missing';
        END IF;

        p_grant_id := v_grant.id;
        p_result := CASE WHEN v_grant.request_id = p_request_id
                         THEN 'REPLAYED' ELSE 'ALREADY_GRANTED' END;
        RETURN;
    END IF;

    UPDATE reward.wallets
    SET money_balance = money_balance + v_event.reward_money,
        point_balance = point_balance + v_event.reward_point
    WHERE user_id = p_user_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'wallet missing for user %', p_user_id;
    END IF;

    IF v_event.reward_money > 0 THEN
        INSERT INTO reward.wallet_ledger (user_id, reward_grant_id, currency_type, amount)
        VALUES (p_user_id, p_grant_id, 'MONEY', v_event.reward_money);
    END IF;

    IF v_event.reward_point > 0 THEN
        INSERT INTO reward.wallet_ledger (user_id, reward_grant_id, currency_type, amount)
        VALUES (p_user_id, p_grant_id, 'POINT', v_event.reward_point);
    END IF;

    p_result := 'PAID';
END;
$procedure$;

-- A business-key duplicate is only a replay when its immutable compensation snapshot matches.
CREATE OR REPLACE PROCEDURE reward.grant_compensation(
    IN p_user_id BIGINT,
    IN p_event_id BIGINT,
    IN p_request_id UUID,
    IN p_incident_reference TEXT,
    IN p_reason TEXT,
    IN p_operator_user_id BIGINT,
    IN p_money_awarded BIGINT,
    IN p_points_awarded BIGINT,
    INOUT p_result TEXT,
    INOUT p_compensation_id BIGINT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, reward, pg_temp
AS $procedure$
DECLARE
    v_compensation reward.compensations%ROWTYPE;
    v_compensation_id BIGINT;
BEGIN
    p_result := NULL;
    p_compensation_id := NULL;

    IF p_user_id IS NULL OR p_event_id IS NULL OR p_request_id IS NULL
       OR p_operator_user_id IS NULL OR p_money_awarded IS NULL OR p_points_awarded IS NULL
       OR p_money_awarded < 0 OR p_points_awarded < 0
       OR (p_money_awarded = 0 AND p_points_awarded = 0)
       OR length(btrim(COALESCE(p_incident_reference, ''))) NOT BETWEEN 1 AND 100
       OR length(btrim(COALESCE(p_reason, ''))) NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'invalid compensation input';
    END IF;

    -- One transaction owns a request UUID before either existing-row lookup runs.
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(p_request_id::TEXT, 0)
    );

    -- CLAIM과 같은 UUID 공간을 사용하므로 반대 출처도 먼저 확인한다.
    IF EXISTS (SELECT 1 FROM reward.reward_grants WHERE request_id = p_request_id) THEN
        p_result := 'REQUEST_ID_CONFLICT';
        RETURN;
    END IF;

    SELECT * INTO v_compensation
    FROM reward.compensations
    WHERE request_id = p_request_id;

    IF FOUND THEN
        IF v_compensation.user_id <> p_user_id
           OR v_compensation.event_id <> p_event_id
           OR v_compensation.incident_reference <> btrim(p_incident_reference)
           OR v_compensation.reason <> btrim(p_reason)
           OR v_compensation.operator_user_id <> p_operator_user_id
           OR v_compensation.money_awarded <> p_money_awarded
           OR v_compensation.points_awarded <> p_points_awarded THEN
            p_result := 'REQUEST_ID_CONFLICT';
            RETURN;
        END IF;
        p_result := 'REPLAYED';
        p_compensation_id := v_compensation.id;
        RETURN;
    END IF;

    INSERT INTO reward.compensations (
        event_id, user_id, request_id, incident_reference, reason,
        operator_user_id, money_awarded, points_awarded
    ) VALUES (
        p_event_id, p_user_id, p_request_id, btrim(p_incident_reference), btrim(p_reason),
        p_operator_user_id, p_money_awarded, p_points_awarded
    )
    ON CONFLICT DO NOTHING
    RETURNING id INTO v_compensation_id;

    IF v_compensation_id IS NULL THEN
        -- The business UNIQUE key can conflict across distinct request UUIDs.
        SELECT * INTO v_compensation
        FROM reward.compensations
        WHERE event_id = p_event_id
          AND incident_reference = btrim(p_incident_reference)
          AND user_id = p_user_id
        ORDER BY id
        LIMIT 1;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'compensation uniqueness conflict row is missing';
        END IF;

        -- Do not classify a changed operator instruction as an identical duplicate.
        IF v_compensation.reason <> btrim(p_reason)
           OR v_compensation.operator_user_id <> p_operator_user_id
           OR v_compensation.money_awarded <> p_money_awarded
           OR v_compensation.points_awarded <> p_points_awarded THEN
            p_result := 'COMPENSATION_CONFLICT';
            p_compensation_id := v_compensation.id;
            RETURN;
        END IF;

        p_result := 'ALREADY_COMPENSATED';
        p_compensation_id := v_compensation.id;
        RETURN;
    END IF;

    UPDATE reward.wallets
    SET money_balance = money_balance + p_money_awarded,
        point_balance = point_balance + p_points_awarded
    WHERE user_id = p_user_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'wallet missing for user %', p_user_id;
    END IF;

    IF p_money_awarded > 0 THEN
        INSERT INTO reward.wallet_ledger (
            user_id, reward_grant_id, compensation_id, source_type, currency_type, amount
        ) VALUES (
            p_user_id, NULL, v_compensation_id, 'COMPENSATION', 'MONEY', p_money_awarded
        );
    END IF;

    IF p_points_awarded > 0 THEN
        INSERT INTO reward.wallet_ledger (
            user_id, reward_grant_id, compensation_id, source_type, currency_type, amount
        ) VALUES (
            p_user_id, NULL, v_compensation_id, 'COMPENSATION', 'POINT', p_points_awarded
        );
    END IF;

    p_result := 'PAID';
    p_compensation_id := v_compensation_id;
END;
$procedure$;

-- CREATE OR REPLACE preserves routine ownership and grants; restate the callable security boundary.
ALTER PROCEDURE reward.grant_event_reward(BIGINT, BIGINT, UUID, TEXT, TEXT, BIGINT)
    SECURITY DEFINER;
ALTER PROCEDURE reward.grant_event_reward(BIGINT, BIGINT, UUID, TEXT, TEXT, BIGINT)
    SET search_path = pg_catalog, reward, pg_temp;
ALTER PROCEDURE reward.grant_compensation(
    BIGINT, BIGINT, UUID, TEXT, TEXT, BIGINT, BIGINT, BIGINT, TEXT, BIGINT
) SECURITY DEFINER;
ALTER PROCEDURE reward.grant_compensation(
    BIGINT, BIGINT, UUID, TEXT, TEXT, BIGINT, BIGINT, BIGINT, TEXT, BIGINT
) SET search_path = pg_catalog, reward, pg_temp;
REVOKE ALL ON PROCEDURE reward.grant_event_reward(BIGINT, BIGINT, UUID, TEXT, TEXT, BIGINT)
    FROM PUBLIC;
REVOKE ALL ON PROCEDURE reward.grant_compensation(
    BIGINT, BIGINT, UUID, TEXT, TEXT, BIGINT, BIGINT, BIGINT, TEXT, BIGINT
) FROM PUBLIC;
GRANT EXECUTE ON PROCEDURE reward.grant_event_reward(BIGINT, BIGINT, UUID, TEXT, TEXT, BIGINT)
    TO reward_app;
GRANT EXECUTE ON PROCEDURE reward.grant_compensation(
    BIGINT, BIGINT, UUID, TEXT, TEXT, BIGINT, BIGINT, BIGINT, TEXT, BIGINT
) TO reward_app;

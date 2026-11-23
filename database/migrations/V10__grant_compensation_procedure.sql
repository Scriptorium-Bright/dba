-- CS 장애 보상도 grant/wallet/ledger를 한 호출자 트랜잭션에 묶는다.
-- 기존 CLAIM 프로시저를 바꾸지 않아 이벤트 지급 배포 경로의 호환성을 유지한다.
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

    -- 최초 요청 조회는 빠른 replay 응답을 제공한다. UNIQUE 제약은 동시 요청도 막는다.
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
        -- 다른 request ID가 같은 incident/user를 먼저 처리한 경우 기존 행을 돌려준다.
        SELECT * INTO v_compensation
        FROM reward.compensations
        WHERE request_id = p_request_id
           OR (event_id = p_event_id
               AND incident_reference = btrim(p_incident_reference)
               AND user_id = p_user_id)
        ORDER BY CASE WHEN request_id = p_request_id THEN 0 ELSE 1 END
        LIMIT 1;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'compensation uniqueness conflict row is missing';
        END IF;

        IF v_compensation.request_id = p_request_id THEN
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
        ELSE
            p_result := 'ALREADY_COMPENSATED';
        END IF;
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

REVOKE ALL ON PROCEDURE reward.grant_compensation(
    BIGINT, BIGINT, UUID, TEXT, TEXT, BIGINT, BIGINT, BIGINT, TEXT, BIGINT
) FROM PUBLIC;
GRANT EXECUTE ON PROCEDURE reward.grant_compensation(
    BIGINT, BIGINT, UUID, TEXT, TEXT, BIGINT, BIGINT, BIGINT, TEXT, BIGINT
) TO reward_app;

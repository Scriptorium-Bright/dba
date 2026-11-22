-- P2 지급 규칙을 DB 한 곳에 모으고, 배치 실행별 결과를 재시작 후에도 관찰한다.
-- 각 CALL의 COMMIT/ROLLBACK은 호출자(Spring API/Worker)가 소유한다.

ALTER TABLE reward.batch_runs
    ADD COLUMN target_count INTEGER NOT NULL DEFAULT 0 CHECK (target_count >= 0),
    ADD COLUMN paid_count INTEGER NOT NULL DEFAULT 0 CHECK (paid_count >= 0),
    ADD COLUMN replayed_count INTEGER NOT NULL DEFAULT 0 CHECK (replayed_count >= 0),
    ADD COLUMN already_granted_count INTEGER NOT NULL DEFAULT 0 CHECK (already_granted_count >= 0),
    ADD COLUMN not_available_count INTEGER NOT NULL DEFAULT 0 CHECK (not_available_count >= 0),
    ADD COLUMN failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0);

CREATE INDEX batch_runs_event_started_idx
    ON reward.batch_runs (event_id, started_at DESC, id DESC);

CREATE OR REPLACE PROCEDURE reward.grant_event_reward(
    IN p_event_id BIGINT,
    IN p_user_id BIGINT,
    IN p_request_id UUID,
    IN p_trigger TEXT,
    INOUT p_result TEXT,
    INOUT p_grant_id BIGINT
)
LANGUAGE plpgsql
AS $$
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

    -- 응답을 잃고 재요청해도 이미 커밋한 요청은 기존 grant로 판정한다.
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

    -- 요청 ID가 달라도 이벤트·사용자 조합은 한 번만 지급한다.
    SELECT * INTO v_grant
    FROM reward.reward_grants
    WHERE event_id = p_event_id AND user_id = p_user_id;

    IF FOUND THEN
        p_result := 'ALREADY_GRANTED';
        p_grant_id := v_grant.id;
        RETURN;
    END IF;

    -- 상태/보상 정책 변경과 지급의 순서를 정해 닫힌 이벤트의 신규 지급을 막는다.
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
        -- BATCH는 예약 시각 이후라면 지연 재처리를 허용하되 명단 밖 지급은 금지한다.
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

    -- UNIQUE(event_id,user_id)가 동시 호출의 최종 중복 방어선이다.
    INSERT INTO reward.reward_grants (
        event_id, user_id, request_id, money_awarded, points_awarded
    ) VALUES (
        p_event_id, p_user_id, p_request_id,
        v_event.reward_money, v_event.reward_point
    )
    ON CONFLICT (event_id, user_id) DO NOTHING
    RETURNING id INTO p_grant_id;

    IF p_grant_id IS NULL THEN
        -- READ COMMITTED의 다음 SQL 문에서 먼저 커밋한 요청을 다시 읽는다.
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

    -- 지갑과 모든 재화 원장은 grant와 같은 호출자 트랜잭션에 묶인다.
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
$$;

-- 앱 계정은 테이블 직접 변경 대신 지급 규칙을 통해서만 보상을 추가한다.
REVOKE ALL ON PROCEDURE reward.grant_event_reward(BIGINT, BIGINT, UUID, TEXT, TEXT, BIGINT) FROM PUBLIC;
GRANT EXECUTE ON PROCEDURE reward.grant_event_reward(BIGINT, BIGINT, UUID, TEXT, TEXT, BIGINT)
    TO reward_app;

-- 대상 교체를 허용하되, 앱 계정에는 다른 테이블 DELETE 권한을 추가하지 않는다.
GRANT DELETE ON TABLE reward.event_recipients TO reward_app;


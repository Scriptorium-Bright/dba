-- P3 전용: 지정한 event_id의 reward_grants INSERT만 실패시키는 장애 시뮬레이터다.
-- 운영 DB에 실행하지 않는다. 호출 예: psql ... -v p3_event_id=123 -f p3_install_claim_failure.sql
\if :{?p3_event_id}
\else
  \echo '필수 값이 없습니다: -v p3_event_id=<P3 event id>'
  \quit 2
\endif

DO $guard$
BEGIN
    IF current_database() <> 'rewardp3db' THEN
        RAISE EXCEPTION 'P3 failure injection is restricted to rewardp3db';
    END IF;
END;
$guard$;

CREATE TABLE IF NOT EXISTS reward.p3_simulated_claim_failures (
    event_id BIGINT PRIMARY KEY REFERENCES reward.events(id),
    injected_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE OR REPLACE FUNCTION reward.p3_reject_injected_event_grant()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $function$
BEGIN
    IF EXISTS (
        SELECT 1 FROM reward.p3_simulated_claim_failures f WHERE f.event_id = NEW.event_id
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'P3_SIMULATED_EVENT_GRANT_FAILURE';
    END IF;
    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS p3_simulated_claim_failure ON reward.reward_grants;
CREATE TRIGGER p3_simulated_claim_failure
BEFORE INSERT ON reward.reward_grants
FOR EACH ROW EXECUTE FUNCTION reward.p3_reject_injected_event_grant();

INSERT INTO reward.p3_simulated_claim_failures (event_id)
VALUES (:'p3_event_id'::BIGINT)
ON CONFLICT (event_id) DO UPDATE SET injected_at = clock_timestamp();

\echo 'P3 failure trigger installed for the requested event only.'

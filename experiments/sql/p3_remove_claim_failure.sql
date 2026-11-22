-- p3_install_claim_failure.sql이 설치한 P3 전용 trigger와 fixture table을 제거한다.
DO $guard$
BEGIN
    IF current_database() <> 'rewardp3db' THEN
        RAISE EXCEPTION 'P3 failure injection cleanup is restricted to rewardp3db';
    END IF;
END;
$guard$;

DROP TRIGGER IF EXISTS p3_simulated_claim_failure ON reward.reward_grants;
DROP FUNCTION IF EXISTS reward.p3_reject_injected_event_grant();
DROP TABLE IF EXISTS reward.p3_simulated_claim_failures;

\echo 'P3 failure trigger, function, and fixture table removed.'

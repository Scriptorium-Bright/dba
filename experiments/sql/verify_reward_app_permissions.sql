-- psql -v event_id=... -v user_id=... -v request_id=... 로 앱 권한과 함수 검색 경로를 확인한다.
\set ON_ERROR_STOP on

DO $guard$
DECLARE
    v_proc RECORD;
BEGIN
    IF current_database() <> 'rewarddb' THEN
        RAISE EXCEPTION 'reward_app privilege verification is restricted to rewarddb';
    END IF;

    IF has_table_privilege('reward_app', 'reward.wallets', 'UPDATE')
       OR has_table_privilege('reward_app', 'reward.reward_grants', 'INSERT')
       OR has_table_privilege('reward_app', 'reward.wallet_ledger', 'INSERT')
       OR has_table_privilege('reward_app', 'reward.compensations', 'INSERT') THEN
        RAISE EXCEPTION 'reward_app retains direct wallet or payout mutation privileges';
    END IF;

    SELECT p.prosecdef, p.proconfig
    INTO v_proc
    FROM pg_catalog.pg_proc p
    JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'reward'
      AND p.proname = 'grant_event_reward'
      AND p.prokind = 'p';

    IF NOT FOUND OR NOT v_proc.prosecdef
       OR NOT (v_proc.proconfig @> ARRAY['search_path=pg_catalog, reward, pg_temp']) THEN
        RAISE EXCEPTION 'grant procedure owner/search_path configuration is not hardened: %', v_proc;
    END IF;

    IF NOT has_function_privilege(
        'reward_app',
        'reward.grant_event_reward(bigint,bigint,uuid,text,text,bigint)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'reward_app cannot execute the payout procedure';
    END IF;
END;
$guard$;

-- 실제 DML 권한 검사가 metadata 확인과 같은 결과인지 확인한다.
DO $direct_dml$
DECLARE
    v_denied BOOLEAN := FALSE;
BEGIN
    SET LOCAL ROLE reward_app;
    BEGIN
        UPDATE reward.wallets SET money_balance = NULL;
    EXCEPTION WHEN insufficient_privilege THEN
        v_denied := TRUE;
    END;
    RESET ROLE;

    IF NOT v_denied THEN
        RAISE EXCEPTION 'direct wallet UPDATE unexpectedly succeeded';
    END IF;
END;
$direct_dml$;

-- 임시 테이블이 기본 자료형 text를 가려도 definer procedure의 고정 경로가 유지되는지 확인한다.
BEGIN;
SET ROLE reward_app;
CREATE TEMP TABLE text (value INTEGER);
CALL reward.grant_event_reward(:event_id, :user_id, :'request_id', 'CLAIM', NULL, NULL);
RESET ROLE;
COMMIT;

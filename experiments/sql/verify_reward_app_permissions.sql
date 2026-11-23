-- psql -v event_id=... -v user_id=... -v request_id=... 로 앱 권한과 함수 검색 경로를 확인한다.
\set ON_ERROR_STOP on

DO $guard$
DECLARE
    v_proc RECORD;
    v_app_routines TEXT;
    v_unsafe_definers BIGINT;
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
           , pg_catalog.pg_get_userbyid(p.proowner) AS owner_name
           , owner_role.rolsuper AS owner_superuser
           , owner_role.rolcreaterole AS owner_create_role
           , owner_role.rolcreatedb AS owner_create_db
           , owner_role.rolreplication AS owner_replication
           , owner_role.rolbypassrls AS owner_bypass_rls
           , pg_catalog.pg_get_userbyid(n.nspowner) AS schema_owner
           , pg_catalog.pg_has_role('reward_app', p.proowner, 'MEMBER') AS app_owner_member
           , pg_catalog.pg_has_role('reward_app', p.proowner, 'USAGE') AS app_owner_inherit
           , pg_catalog.pg_has_role('reward_app', p.proowner, 'SET') AS app_can_set_owner
           , has_table_privilege(p.proowner, 'reward.wallets', 'UPDATE') AS owner_wallet_update
           , has_table_privilege(p.proowner, 'reward.reward_grants', 'INSERT') AS owner_grant_insert
           , has_table_privilege(p.proowner, 'reward.wallet_ledger', 'INSERT') AS owner_ledger_insert
           , EXISTS (
               SELECT 1 FROM pg_catalog.aclexplode(p.proacl) acl
               WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
           ) AS public_execute
           , (SELECT count(*) FROM pg_catalog.pg_auth_members m
              WHERE m.member = p.proowner) AS owner_memberships
           , app_role.rolsuper AS app_superuser
    INTO v_proc
    FROM pg_catalog.pg_proc p
    JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
    JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = p.proowner
    JOIN pg_catalog.pg_roles app_role ON app_role.rolname = 'reward_app'
    WHERE n.nspname = 'reward'
      AND p.proname = 'grant_event_reward'
      AND p.prokind = 'p';

    IF NOT FOUND OR NOT v_proc.prosecdef
       OR NOT (v_proc.proconfig @> ARRAY['search_path=pg_catalog, reward, pg_temp']) THEN
        RAISE EXCEPTION 'grant procedure owner/search_path configuration is not hardened: %', v_proc;
    END IF;

    IF v_proc.owner_name <> 'reward_migration'
       OR v_proc.schema_owner <> v_proc.owner_name
       OR v_proc.owner_superuser OR v_proc.owner_create_role OR v_proc.owner_create_db
       OR v_proc.owner_replication OR v_proc.owner_bypass_rls OR v_proc.app_superuser
       OR v_proc.owner_memberships <> 0
       OR v_proc.app_owner_member OR v_proc.app_owner_inherit OR v_proc.app_can_set_owner
       OR NOT v_proc.owner_wallet_update OR NOT v_proc.owner_grant_insert
       OR NOT v_proc.owner_ledger_insert OR v_proc.public_execute THEN
        RAISE EXCEPTION 'unexpected definer owner role or procedure ACL: %', v_proc;
    END IF;

    IF NOT has_function_privilege(
        'reward_app',
        'reward.grant_event_reward(bigint,bigint,uuid,text,text,bigint)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'reward_app cannot execute the payout procedure';
    END IF;

    SELECT string_agg(p.proname || '/' || p.prokind::TEXT, ',' ORDER BY p.proname)
    INTO v_app_routines
    FROM pg_catalog.pg_proc p
    JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'reward'
      AND has_function_privilege('reward_app', p.oid, 'EXECUTE');

    IF v_app_routines <> 'grant_compensation/p,grant_event_reward/p,reset_all_local_wallets/f,reset_local_user_wallet/f' THEN
        RAISE EXCEPTION 'unexpected reward_app routine EXECUTE surface: %', v_app_routines;
    END IF;

    SELECT count(*) INTO v_unsafe_definers
    FROM pg_catalog.pg_proc p
    JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'reward'
      AND p.prosecdef
      AND (
          p.proconfig IS NULL
          OR NOT (p.proconfig @> ARRAY['search_path=pg_catalog, reward, pg_temp'])
          OR EXISTS (
              SELECT 1 FROM pg_catalog.aclexplode(p.proacl) acl
              WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
          )
      );

    IF v_unsafe_definers <> 0 THEN
        RAISE EXCEPTION 'SECURITY DEFINER path/public EXECUTE review failed: % routines', v_unsafe_definers;
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

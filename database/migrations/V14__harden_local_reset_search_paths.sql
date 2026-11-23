-- Every local reset entry point runs as SECURITY DEFINER; put the temporary schema last.
-- All table and helper references are schema-qualified, and this makes type/relation lookup explicit.
ALTER FUNCTION reward.try_lock_local_batch_events()
    SET search_path = pg_catalog, reward, pg_temp;
ALTER FUNCTION reward.reset_local_user_wallet_without_batch_lock(BIGINT)
    SET search_path = pg_catalog, reward, pg_temp;
ALTER FUNCTION reward.reset_all_local_wallets_without_batch_lock()
    SET search_path = pg_catalog, reward, pg_temp;
ALTER FUNCTION reward.reset_local_user_wallet(BIGINT)
    SET search_path = pg_catalog, reward, pg_temp;
ALTER FUNCTION reward.reset_all_local_wallets()
    SET search_path = pg_catalog, reward, pg_temp;

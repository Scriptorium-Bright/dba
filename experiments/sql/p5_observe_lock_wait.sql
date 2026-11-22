-- 읽기 전용 P5 관측 쿼리: Waiter에서 Blocker까지 세션 상태와 SQL을 연결합니다.
SET statement_timeout = '5s';

SELECT clock_timestamp() AS observed_at,
       waiter.pid AS waiter_pid,
       waiter.application_name AS waiter_application,
       waiter.state AS waiter_state,
       waiter.wait_event_type,
       waiter.wait_event,
       age(clock_timestamp(), waiter.xact_start) AS waiter_xact_age,
       blocker.pid AS blocker_pid,
       blocker.application_name AS blocker_application,
       blocker.state AS blocker_state,
       age(clock_timestamp(), blocker.xact_start) AS blocker_xact_age,
       blocker.backend_xmin,
       left(regexp_replace(blocker.query, E'[\n\r\t]+', ' ', 'g'), 160) AS blocker_query,
       left(regexp_replace(waiter.query, E'[\n\r\t]+', ' ', 'g'), 160) AS waiter_query
FROM pg_stat_activity AS waiter
CROSS JOIN LATERAL unnest(pg_blocking_pids(waiter.pid)) AS blocked_by(pid)
LEFT JOIN pg_stat_activity AS blocker ON blocker.pid = blocked_by.pid
WHERE waiter.datname = current_database()
  AND waiter.wait_event_type = 'Lock'
ORDER BY waiter.xact_start NULLS LAST, waiter.pid;

-- 동일 relation과 transaction ID의 granted/waiting 상태를 세션 이름과 함께 확인합니다.
SELECT activity.application_name,
       locks.pid,
       locks.locktype,
       CASE WHEN locks.relation IS NOT NULL THEN locks.relation::regclass::text END AS relation_name,
       locks.transactionid,
       locks.mode,
       locks.granted
FROM pg_locks AS locks
JOIN pg_stat_activity AS activity ON activity.pid = locks.pid
WHERE activity.datname = current_database()
  AND activity.application_name LIKE 'p5_%'
ORDER BY activity.application_name, locks.granted, locks.locktype, locks.mode;

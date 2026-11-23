-- P5 전용 클러스터에서만 관측 계정에 PostgreSQL의 읽기 전용 모니터링 권한을 줍니다.
GRANT pg_monitor TO reward_observer;

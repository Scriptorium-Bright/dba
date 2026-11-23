#!/usr/bin/env python3
"""Measure pool saturation, row-lock wait, and SQL scan tuning in rewardlatencydb."""

from __future__ import annotations

import concurrent.futures
import json
import math
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


# Fixed loopback endpoints make it difficult to send diagnostic traffic to a reward phase.
PROJECT = "reward-latency"
DATABASE = "rewardlatencydb"
API_BASE = "http://127.0.0.1:8382"
PROMETHEUS_URL = API_BASE + "/actuator/prometheus"
DOCKER_HOST = os.environ.get(
    "DOCKER_HOST", "unix:///private/tmp/reward-colima/active-docker.sock"
)
COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", "compose.latency-lab.yaml"]
POOL_NAME = "RewardLatencyLabPool"
POOL_HOLD_URI = "/api/diagnostics/hold-connection"
LOCK_URI = "/api/diagnostics/lock-probe"
SCAN_URI = "/api/diagnostics/sequential-scan"
METRIC_LINE = re.compile(
    r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+([-+0-9.eE]+)(?:\s+\d+)?$"
)
LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"])*)"')


def connection_env() -> dict[str, str]:
    """Select the current local Docker engine without exporting credentials."""
    return {**os.environ, "DOCKER_HOST": DOCKER_HOST}


def inspect_compose_service(service: str) -> dict[str, object]:
    """Read the selected service container metadata without printing its environment."""
    container_id = subprocess.run(
        [*COMPOSE, "ps", "-q", service], capture_output=True, text=True,
        env=connection_env(), check=True, timeout=10,
    ).stdout.strip()
    if not container_id:
        raise RuntimeError(f"the {service!r} service is not running in {PROJECT!r}")
    inspected = subprocess.run(
        ["docker", "inspect", container_id], capture_output=True, text=True,
        env=connection_env(), check=True, timeout=10,
    )
    return json.loads(inspected.stdout)[0]


def verify_compose_topology() -> dict[str, object]:
    """Bind the loopback API port to this compose project and its isolated DB service."""
    api = inspect_compose_service("api")
    database = inspect_compose_service("db")
    api_labels = api["Config"]["Labels"]
    db_labels = database["Config"]["Labels"]
    if api_labels.get("com.docker.compose.project") != PROJECT \
            or api_labels.get("com.docker.compose.service") != "api":
        raise RuntimeError("8382 is not backed by the expected latency-lab API service")
    if db_labels.get("com.docker.compose.project") != PROJECT \
            or db_labels.get("com.docker.compose.service") != "db":
        raise RuntimeError("the selected PostgreSQL container is outside the latency-lab project")

    env = dict(item.split("=", 1) for item in api["Config"]["Env"] if "=" in item)
    expected_url = "jdbc:postgresql://db:5432/rewardlatencydb"
    if env.get("SPRING_PROFILES_ACTIVE") != "latency-lab" \
            or env.get("SPRING_DATASOURCE_USERNAME") != "reward_app" \
            or not env.get("SPRING_DATASOURCE_URL", "").startswith(expected_url) \
            or env.get("APP_LATENCY_LAB_ENABLED") != "true":
        raise RuntimeError("the selected API container is not configured for rewardlatencydb")

    api_bindings = api["NetworkSettings"]["Ports"].get("8080/tcp") or []
    db_bindings = database["NetworkSettings"]["Ports"].get("5432/tcp") or []
    if not any(binding.get("HostIp") == "127.0.0.1" and binding.get("HostPort") == "8382"
               for binding in api_bindings):
        raise RuntimeError("the expected latency API loopback port 8382 is not published")
    if not any(binding.get("HostIp") == "127.0.0.1" and binding.get("HostPort") == "55435"
               for binding in db_bindings):
        raise RuntimeError("the expected latency DB loopback port 55435 is not published")

    api_networks = set(api["NetworkSettings"]["Networks"])
    db_networks = set(database["NetworkSettings"]["Networks"])
    shared_networks = sorted(api_networks & db_networks)
    if not shared_networks or not any(network.startswith(f"{PROJECT}_")
                                      for network in shared_networks):
        raise RuntimeError("API and database do not share this compose project's network")
    return {
        "composeProject": PROJECT,
        "apiService": api_labels["com.docker.compose.service"],
        "databaseService": db_labels["com.docker.compose.service"],
        "apiLoopbackPort": "127.0.0.1:8382",
        "databaseLoopbackPort": "127.0.0.1:55435",
        "sharedComposeNetwork": shared_networks[0],
        "apiDatasource": "jdbc:postgresql://db:5432/rewardlatencydb",
    }


def api_database_identity() -> dict[str, object]:
    """Read current_database() from the actual API datasource before issuing load."""
    request = urllib.request.Request(API_BASE + "/api/diagnostics/identity")
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"API database identity returned HTTP {response.status}")
        identity = json.loads(response.read())
    expected = {
        "database_name": DATABASE,
        "database_user": "reward_app",
        "application_name": "reward-latency-api",
    }
    for key, value in expected.items():
        if identity.get(key) != value:
            raise RuntimeError(f"API datasource identity mismatch for {key}: {identity!r}")
    if not str(identity.get("server_version", "")).startswith("17.12"):
        raise RuntimeError(f"unexpected API PostgreSQL version: {identity!r}")
    return identity


def db_sql(sql: str, user: str = "reward_observer") -> str:
    """Run a short SQL statement inside the dedicated latency database container."""
    completed = subprocess.run(
        [*COMPOSE, "exec", "-T", "db", "psql", "-X", "-q", "-A", "-t", "-F", "|",
         "-v", "ON_ERROR_STOP=1", "-U", user, "-d", DATABASE, "-c", sql],
        capture_output=True, text=True, env=connection_env(), check=True, timeout=10,
    )
    return completed.stdout.strip()


def parse_prometheus(text: str) -> list[tuple[str, dict[str, str], float]]:
    """Parse Prometheus exposition samples without adding a client dependency."""
    samples = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = METRIC_LINE.match(line)
        if not match:
            continue
        raw_labels = match.group(2) or ""
        labels = {key: value.replace(r'\"', '"').replace(r"\\", "\\")
                  for key, value in LABEL.findall(raw_labels)}
        samples.append((match.group(1), labels, float(match.group(3))))
    return samples


def scrape_prometheus() -> list[tuple[str, dict[str, str], float]]:
    """Read Spring Boot's Prometheus endpoint over the local-only API port."""
    with urllib.request.urlopen(PROMETHEUS_URL, timeout=3) as response:
        if response.status != 200:
            raise RuntimeError(f"Prometheus endpoint returned HTTP {response.status}")
        return parse_prometheus(response.read().decode("utf-8"))


def metric_value(samples: list[tuple[str, dict[str, str], float]], name: str) -> float:
    """Read the single Hikari pool series, failing if Spring does not expose it."""
    matches = [value for metric, labels, value in samples
               if metric == name and labels.get("pool", labels.get("name")) == POOL_NAME]
    if not matches:
        # Older Micrometer exporters can omit a pool label when only one pool exists.
        matches = [value for metric, _, value in samples if metric == name]
    if not matches:
        raise RuntimeError(f"expected Spring/Hikari metric {name!r} was not scraped")
    return sum(matches)


def db_activity_snapshot() -> dict[str, object]:
    """Take one PostgreSQL pg_stat_activity/pg_blocking_pids snapshot for the API."""
    value = db_sql("""
        SELECT
            count(*) FILTER (WHERE application_name = 'reward-latency-api'
                               AND leader_pid IS NULL AND state = 'active'),
            count(*) FILTER (WHERE application_name = 'reward-latency-api'
                               AND leader_pid IS NULL AND state = 'idle'),
            count(*) FILTER (WHERE application_name = 'reward-latency-api'
                               AND leader_pid IS NOT NULL),
            count(*) FILTER (WHERE application_name = 'reward-latency-api'
                               AND leader_pid IS NULL AND wait_event_type = 'Lock'),
            count(*) FILTER (WHERE application_name = 'reward-latency-api'
                               AND leader_pid IS NULL
                               AND cardinality(pg_blocking_pids(pid)) > 0),
            count(*) FILTER (WHERE application_name = 'reward-latency-api'
                               AND leader_pid IS NULL
                               AND state = 'active'
                               AND query ILIKE '%diagnostics.scan_probe%'),
            COALESCE((
                SELECT string_agg(DISTINCT wait_event_type || '/' || wait_event, ','
                                  ORDER BY wait_event_type || '/' || wait_event)
                FROM pg_stat_activity
                WHERE application_name = 'reward-latency-api'
                  AND wait_event_type IS NOT NULL
            ), '')
        FROM pg_stat_activity
        WHERE datname = current_database();
    """)
    fields = value.split("|")
    if len(fields) != 7:
        raise RuntimeError(f"unexpected pg_stat_activity snapshot: {value!r}")
    return {
        "apiActive": int(fields[0]),
        "apiIdle": int(fields[1]),
        "parallelWorkerProcesses": int(fields[2]),
        "lockWaiters": int(fields[3]),
        "blockedSessions": int(fields[4]),
        "scanQueries": int(fields[5]),
        "waitEvents": fields[6],
    }


class ConcurrentSampler:
    """Poll HTTP/Hikari and PostgreSQL views together during one workload window."""

    def __init__(self, interval_seconds: float = 0.1):
        self.interval_seconds = interval_seconds
        self.samples: list[dict[str, object]] = []
        self.errors: list[str] = []
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        while not self.stopped.is_set():
            observed_at = datetime.now(timezone.utc).isoformat()
            try:
                prometheus = scrape_prometheus()
                database = db_activity_snapshot()
                self.samples.append({
                    "observedAtUtc": observed_at,
                    "poolActive": metric_value(prometheus, "hikaricp_connections_active"),
                    "poolIdle": metric_value(prometheus, "hikaricp_connections_idle"),
                    "poolPending": metric_value(prometheus, "hikaricp_connections_pending"),
                    "poolMax": metric_value(prometheus, "hikaricp_connections_max"),
                    "db": database,
                })
            except Exception as error:
                self.errors.append(f"{type(error).__name__}: {error}")
            self.stopped.wait(self.interval_seconds)

    def stop(self) -> list[dict[str, object]]:
        self.stopped.set()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise TimeoutError("diagnostic sampler did not stop")
        if self.errors:
            raise RuntimeError(f"concurrent observation failed: {self.errors[:3]}")
        if not self.samples:
            raise RuntimeError("diagnostic sampler collected no observations")
        return self.samples


def request_one(method: str, uri: str) -> dict[str, object]:
    """Measure one loopback HTTP request with monotonic duration and status."""
    started_at = time.monotonic()
    request = urllib.request.Request(API_BASE + uri, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
            return {
                "status": response.status,
                "durationMs": (time.monotonic() - started_at) * 1000,
                "body": body.decode("utf-8", errors="replace"),
            }
    except urllib.error.HTTPError as error:
        return {
            "status": error.code,
            "durationMs": (time.monotonic() - started_at) * 1000,
            "body": error.read().decode("utf-8", errors="replace"),
        }


def run_concurrent_requests(
    method: str, uri: str, count: int, maximum_concurrency: int | None = None,
    expected_statuses: set[int] | None = None,
) -> list[dict[str, object]]:
    """Run a fixed sample count, checking either HTTP 200 or explicit allowed statuses."""
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=maximum_concurrency or count
    ) as executor:
        results = list(executor.map(lambda _: request_one(method, uri), range(count)))
    allowed_statuses = expected_statuses or {200}
    failures = [result for result in results if result["status"] not in allowed_statuses]
    if failures:
        raise RuntimeError(f"diagnostic API requests failed: {failures[:3]}")
    return results


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def metric_delta(before, after, name: str, uri: str) -> float:
    """Read request Timer counter/sum delta for a single MVC route."""
    def total(samples):
        return sum(value for metric, labels, value in samples
                   if metric == name and labels.get("uri") == uri)
    return total(after) - total(before)


def summarize_case(
    name: str, method: str, uri: str, results: list[dict[str, object]],
    samples: list[dict[str, object]], before_metrics, after_metrics,
    maximum_concurrency: int,
) -> dict[str, object]:
    durations = [float(result["durationMs"]) for result in results]
    active = [float(sample["poolActive"]) for sample in samples]
    idle = [float(sample["poolIdle"]) for sample in samples]
    pending = [float(sample["poolPending"]) for sample in samples]
    database = [sample["db"] for sample in samples]
    request_metric_uri = uri.split("?", 1)[0]
    http_count = metric_delta(
        before_metrics, after_metrics, "http_server_requests_seconds_count", request_metric_uri
    )
    http_sum_seconds = metric_delta(
        before_metrics, after_metrics, "http_server_requests_seconds_sum", request_metric_uri
    )
    timeout_delta = metric_value(after_metrics, "hikaricp_connections_timeout_total") - metric_value(
        before_metrics, "hikaricp_connections_timeout_total"
    )
    return {
        "scenario": name,
        "method": method,
        "uri": request_metric_uri,
        "requests": len(results),
        "maximumConcurrency": maximum_concurrency,
        "percentileMethod": "nearest-rank client durations; local synthetic sample",
        "percentileSampleCount": len(durations),
        "statuses": {str(code): sum(row["status"] == code for row in results)
                     for code in sorted({row["status"] for row in results})},
        "clientDurationMs": {
            "p50": round(percentile(durations, 0.50), 3),
            "p95": round(percentile(durations, 0.95), 3),
            "max": round(max(durations), 3),
        },
        "httpServerRequestsMetric": {
            "countDelta": round(http_count, 3),
            "meanDurationMs": round(http_sum_seconds * 1000 / http_count, 3)
            if http_count else None,
        },
        "hikari": {
            "activePeak": max(active),
            "idleMinimum": min(idle),
            "pendingPeak": max(pending),
            "poolMaximum": max(float(sample["poolMax"]) for sample in samples),
            "timedOutBorrowers": round(timeout_delta, 3),
        },
        "postgresSamples": {
            "samples": len(samples),
            "apiActiveClientSessionsPeak": max(
                int(row["apiActive"]) for row in database
            ),
            "apiIdleClientSessionsPeak": max(
                int(row["apiIdle"]) for row in database
            ),
            "apiClientSessionsPeak": max(
                int(row["apiActive"]) + int(row["apiIdle"]) for row in database
            ),
            "parallelWorkerProcessesPeak": max(
                int(row["parallelWorkerProcesses"]) for row in database
            ),
            "lockWaitSamples": sum(int(row["lockWaiters"]) > 0 for row in database),
            "samplesWithBlockedSessions": sum(
                int(row["blockedSessions"]) > 0 for row in database
            ),
            "scanQuerySamples": sum(int(row["scanQueries"]) > 0 for row in database),
            "observedWaitEvents": sorted({str(row["waitEvents"]) for row in database
                                          if row["waitEvents"]}),
        },
    }


def run_pool_wait() -> dict[str, object]:
    """Hold all four connections across 100 clients and expose Hikari pending borrowers."""
    api_database_identity()  # Create/warm the pool meters before the first sampler scrape.
    uri = POOL_HOLD_URI + "?millis=2000"
    before = scrape_prometheus()
    sampler = ConcurrentSampler()
    sampler.start()
    started = time.monotonic()
    results = run_concurrent_requests(
        "GET", uri, count=100, maximum_concurrency=100,
        expected_statuses={200, 503},
    )
    elapsed = time.monotonic() - started
    samples = sampler.stop()
    summary = summarize_case("hikari-pool-wait", "GET", uri, results, samples, before,
                             scrape_prometheus(), maximum_concurrency=100)
    summary["wallElapsedSeconds"] = round(elapsed, 3)
    if summary["hikari"]["activePeak"] < 4 or summary["hikari"]["pendingPeak"] < 1:
        raise RuntimeError(f"the pool wait was not observed: {summary}")
    timeout_responses = [json.loads(result["body"]) for result in results
                         if result["status"] == 503]
    if not timeout_responses or any(
        body.get("reason") != "connection_pool_acquisition_timeout"
        for body in timeout_responses
    ):
        raise RuntimeError(
            f"HTTP 503 responses did not identify Hikari acquisition timeout: "
            f"{timeout_responses[:3]}"
        )
    summary["nonSuccessResponseReasons"] = {
        "connection_pool_acquisition_timeout": len(timeout_responses)
    }
    if int(summary["statuses"].get("503", 0)) != summary["hikari"]["timedOutBorrowers"]:
        raise RuntimeError(
            f"HTTP 503 count does not match Hikari timeout counter: "
            f"statuses={summary['statuses']}, hikari={summary['hikari']}"
        )
    if int(summary["statuses"].get("503", 0)) < 1:
        raise RuntimeError(f"the 100-client burst did not reach connection-timeout behavior: {summary}")
    if summary["httpServerRequestsMetric"]["countDelta"] != 100:
        raise RuntimeError(f"Spring HTTP request counter did not observe 100 requests: {summary}")
    if summary["postgresSamples"]["lockWaitSamples"] != 0:
        raise RuntimeError(f"pool case also had a DB Lock wait: {summary}")
    return summary


def start_lock_blocker() -> subprocess.Popen[str]:
    """Hold only the disposable diagnostics.lock_probe row in an open transaction."""
    process = subprocess.Popen(
        [*COMPOSE, "exec", "-T", "db", "psql", "-X", "-A", "-t", "-F", "|",
         "-v", "ON_ERROR_STOP=1", "-U", "reward_bootstrap", "-d", DATABASE],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=connection_env(),
    )
    if process.stdin is None:
        raise RuntimeError("lock blocker psql session has no stdin")
    process.stdin.write(
        "SET application_name = 'latency_lock_blocker';\n"
        "BEGIN;\n"
        "UPDATE diagnostics.lock_probe SET value = value WHERE id = 1;\n"
    )
    process.stdin.flush()
    return process


def finish_lock_blocker(process: subprocess.Popen[str], command: str) -> dict[str, object]:
    """Require the lock holder's psql command tag and zero exit before continuing."""
    if process.stdin is not None and process.poll() is None:
        process.stdin.write(command.rstrip() + "\n\\q\n")
        process.stdin.flush()
    try:
        stdout, stderr = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        stdout, stderr = process.communicate(timeout=5)
    tags = {line.strip() for line in stdout.splitlines()}
    expected_tag = "COMMIT" if command.strip().upper() == "COMMIT;" else "ROLLBACK"
    if process.returncode != 0 or expected_tag not in tags:
        raise RuntimeError(
            f"lock blocker did not confirm {expected_tag}: exit={process.returncode}, "
            f"stdout={stdout!r}, stderr={stderr!r}"
        )
    return {"exitCode": process.returncode, "commandTag": expected_tag, "stdout": stdout}


def wait_for_db_state(predicate, description: str, timeout: float = 10) -> dict[str, object]:
    """Wait for an exact live PostgreSQL state instead of assuming timing."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = db_activity_snapshot()
        if predicate(last):
            return last
        time.sleep(0.05)
    raise TimeoutError(f"PostgreSQL did not show {description}; last={last}")


def exact_lock_chain_snapshot(expected_waiters: int = 4) -> dict[str, object]:
    """Require each API waiter to reach the synthetic blocker through PostgreSQL wait chains."""
    value = db_sql("""
        WITH RECURSIVE api_waiters AS (
            SELECT pid, wait_event_type, wait_event
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND application_name = 'reward-latency-api'
              AND state = 'active'
              AND wait_event_type = 'Lock'
        ), blocking_paths(root_pid, current_pid, pid_path, depth) AS (
            SELECT pid, pid, ARRAY[pid], 0
            FROM api_waiters
            UNION ALL
            SELECT paths.root_pid, blocked.pid, paths.pid_path || blocked.pid,
                   paths.depth + 1
            FROM blocking_paths paths
            CROSS JOIN LATERAL unnest(pg_blocking_pids(paths.current_pid)) AS blocked(pid)
            WHERE paths.depth < 10
              AND NOT blocked.pid = ANY(paths.pid_path)
        ), paths_to_lab_blocker AS (
            SELECT DISTINCT ON (paths.root_pid)
                   paths.root_pid, paths.pid_path
            FROM blocking_paths paths
            JOIN pg_stat_activity target ON target.pid = paths.current_pid
            WHERE target.application_name = 'latency_lock_blocker'
            ORDER BY paths.root_pid, paths.depth, paths.pid_path::TEXT
        )
        SELECT count(DISTINCT paths.root_pid),
               string_agg(array_to_string(paths.pid_path, '->'), ','
                          ORDER BY paths.root_pid),
               string_agg(DISTINCT waiters.wait_event_type || '/' || waiters.wait_event,
                          ',' ORDER BY waiters.wait_event_type || '/' || waiters.wait_event),
               count(DISTINCT paths.pid_path[array_length(paths.pid_path, 1)])
        FROM paths_to_lab_blocker paths
        JOIN api_waiters waiters ON waiters.pid = paths.root_pid;
    """)
    fields = value.split("|")
    if len(fields) != 4 or int(fields[0]) != expected_waiters or fields[3] != "1":
        raise RuntimeError(
            f"expected {expected_waiters} API Lock waiters with a chain to one lab blocker, "
            f"got {value!r}"
        )
    chains = [edge.split("->") for edge in fields[1].split(",")]
    waiter_pids = {chain[0] for chain in chains}
    blocker_pids = {chain[-1] for chain in chains}
    if len(waiter_pids) != expected_waiters or len(blocker_pids) != 1 \
            or "Lock/transactionid" not in fields[2]:
        raise RuntimeError(f"wait chains do not reach one blocker through a transaction Lock: {value!r}")
    return {
        "waiterCount": int(fields[0]),
        "waiterPids": sorted(waiter_pids, key=int),
        "blockerPid": next(iter(blocker_pids)),
        "waitEvents": fields[2],
        "waiterToBlockerPidChains": fields[1],
        "source": "pg_stat_activity with recursive pg_blocking_pids() traversal",
    }


def run_lock_wait() -> dict[str, object]:
    """Repeat 25 four-waiter incidents for 100 client timings and exact wait-chain proofs."""
    db_sql("UPDATE diagnostics.lock_probe SET value = 0 WHERE id = 1", user="reward_bootstrap")
    before = scrape_prometheus()
    sampler = ConcurrentSampler()
    sampler.start()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    current_blocker = None
    blocker_released = False
    all_results = []
    round_evidence = []
    started = time.monotonic()
    try:
        for round_number in range(1, 26):
            current_blocker = start_lock_blocker()
            blocker_row = db_sql("""
                SELECT CASE WHEN count(*) = 1 THEN 1 ELSE 0 END
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND application_name = 'latency_lock_blocker'
                  AND state = 'idle in transaction'
            """)
            deadline = time.monotonic() + 10
            while blocker_row != "1" and time.monotonic() < deadline:
                time.sleep(0.05)
                blocker_row = db_sql("""
                    SELECT CASE WHEN count(*) = 1 THEN 1 ELSE 0 END
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                      AND application_name = 'latency_lock_blocker'
                      AND state = 'idle in transaction'
                """)
            if blocker_row != "1":
                raise TimeoutError("row-lock blocker did not enter idle-in-transaction state")

            futures = [executor.submit(request_one, "POST", LOCK_URI) for _ in range(4)]
            observed = wait_for_db_state(
                lambda row: row["lockWaiters"] == 4 and row["blockedSessions"] == 4,
                "all four API sessions blocked by a PostgreSQL Lock",
            )
            exact_chain = exact_lock_chain_snapshot(expected_waiters=4)
            blocker_result = finish_lock_blocker(current_blocker, "COMMIT;")
            current_blocker = None
            blocker_released = True
            results = [future.result(timeout=20) for future in futures]
            if any(result["status"] != 200 for result in results):
                raise RuntimeError(f"lock-wait API requests failed: {results}")
            value = db_sql("SELECT value FROM diagnostics.lock_probe WHERE id = 1")
            expected_value = str(round_number * 4)
            if value != expected_value:
                raise RuntimeError(
                    f"round {round_number} expected {expected_value} updates, got {value}"
                )
            all_results.extend(results)
            round_evidence.append({
                "round": round_number,
                "waiterCount": observed["lockWaiters"],
                "blockedSessions": observed["blockedSessions"],
                "exactChain": exact_chain,
                "blockerRelease": {
                    "exitCode": blocker_result["exitCode"],
                    "commandTag": blocker_result["commandTag"],
                },
            })
            blocker_released = False

        samples = sampler.stop()
        summary = summarize_case("postgres-row-lock-wait", "POST", LOCK_URI, all_results,
                                 samples, before, scrape_prometheus(), maximum_concurrency=4)
        summary["wallElapsedSeconds"] = round(time.monotonic() - started, 3)
        summary["exactWaitEvidence"] = {
            "rounds": len(round_evidence),
            "requestsWithObservedLock": sum(item["waiterCount"] for item in round_evidence),
            "waiterToBlockerChainsVerified": sum(
                item["exactChain"]["waiterCount"] for item in round_evidence
            ),
            "eachBlockerCommitConfirmed": all(
                item["blockerRelease"] == {"exitCode": 0, "commandTag": "COMMIT"}
                for item in round_evidence
            ),
            "firstRound": round_evidence[0],
            "lastRound": round_evidence[-1],
        }
        return summary
    finally:
        if current_blocker is not None and current_blocker.poll() is None:
            try:
                finish_lock_blocker(current_blocker, "ROLLBACK;")
            except Exception:
                current_blocker.kill()
                current_blocker.wait(timeout=5)
        executor.shutdown(wait=True, cancel_futures=True)
        if sampler.thread.is_alive():
            sampler.stop()
        db_sql("UPDATE diagnostics.lock_probe SET value = 0 WHERE id = 1",
               user="reward_bootstrap")


def run_sql_file(path: str, user: str = "reward_observer") -> str:
    """Run a checked-in SQL script as an observer or fixture administrator."""
    completed = subprocess.run(
        [*COMPOSE, "exec", "-T", "db", "psql", "-X", "-U", user,
         "-d", DATABASE, "-f", "-"],
        input=Path(path).read_text(encoding="utf-8"), capture_output=True,
        text=True, env=connection_env(), check=True, timeout=60,
    )
    return completed.stdout


def measure_scan_case(name: str) -> tuple[dict[str, object], str]:
    """Measure 100 API scans and pair them with PostgreSQL statement/plan evidence."""
    db_sql("SELECT pg_stat_statements_reset()", user="reward_bootstrap")
    before = scrape_prometheus()
    sampler = ConcurrentSampler()
    sampler.start()
    started = time.monotonic()
    results = run_concurrent_requests(
        "GET", SCAN_URI, count=100, maximum_concurrency=4
    )
    elapsed = time.monotonic() - started
    samples = sampler.stop()
    summary = summarize_case(name, "GET", SCAN_URI, results, samples, before,
                             scrape_prometheus(), maximum_concurrency=4)
    summary["wallElapsedSeconds"] = round(elapsed, 3)
    bodies = [json.loads(result["body"]) for result in results]
    if any(body.get("matchingRows") != 2000 for body in bodies):
        raise RuntimeError(f"slow SQL fixture returned unexpected counts: {bodies[:3]}")
    if summary["httpServerRequestsMetric"]["countDelta"] != 100:
        raise RuntimeError(f"Spring HTTP request counter did not observe 100 scans: {summary}")
    statement_stats = db_sql("""
        SELECT queryid::TEXT, calls, round(mean_exec_time::NUMERIC, 3),
               round(max_exec_time::NUMERIC, 3), shared_blks_hit, shared_blks_read, rows
        FROM public.pg_stat_statements
        WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
          AND query ILIKE '%FROM diagnostics.scan_probe%'
        ORDER BY total_exec_time DESC
        LIMIT 1
    """)
    fields = statement_stats.split("|")
    if len(fields) != 7 or fields[1] != "100" or fields[6] != "100":
        raise RuntimeError(f"unexpected pg_stat_statements result: {statement_stats!r}")
    statement_evidence = {
        "queryId": fields[0], "calls": int(fields[1]), "meanExecMs": float(fields[2]),
        "maxExecMs": float(fields[3]), "sharedBlocksHit": int(fields[4]),
        "sharedBlocksRead": int(fields[5]), "rows": int(fields[6]),
    }
    explain_path = (
        "experiments/sql/explain_latency_lab_scan.sql"
        if name == "unindexed-sequential-scan"
        else "experiments/sql/explain_latency_lab_scan_after_index.sql"
    )
    explain = run_sql_file(explain_path)
    if "Execution Time:" not in explain:
        raise RuntimeError(f"EXPLAIN ANALYZE did not report execution time: {explain}")
    if name == "unindexed-sequential-scan" and "Seq Scan on scan_probe" not in explain:
        raise RuntimeError(f"expected the baseline sequential scan: {explain}")
    if name == "gin-indexed-scan" and "Bitmap Index Scan on scan_probe_marker_trgm_idx" not in explain:
        raise RuntimeError(f"planner did not select the targeted GIN index: {explain}")
    summary["pgStatStatements"] = statement_evidence
    if summary["postgresSamples"]["scanQuerySamples"] < 1:
        raise RuntimeError(f"pg_stat_activity did not observe the API scan SQL: {summary}")
    if summary["hikari"]["pendingPeak"] > 0:
        raise RuntimeError(f"the scan case was also constrained by Hikari pool wait: {summary}")
    return summary, explain


def run_slow_sql() -> dict[str, object]:
    """Compare a million-row leading-wildcard scan before and after a GIN action."""
    baseline, baseline_plan = measure_scan_case(
        "unindexed-sequential-scan"
    )
    index_output = run_sql_file(
        "experiments/sql/create_latency_trigram_index.sql", user="reward_bootstrap"
    )
    tuned, tuned_plan = measure_scan_case("gin-indexed-scan")
    index_size = db_sql("""
        SELECT pg_relation_size('diagnostics.scan_probe_marker_trgm_idx')
    """)
    return {
        "predicate": "marker LIKE '%needle%'",
        "fixtureRows": 1_000_000,
        "matchingRowsPerRequest": 2_000,
        "baseline": baseline,
        "baselinePlan": baseline_plan,
        "tuningAction": {
            "index": "GIN (marker public.gin_trgm_ops)",
            "indexSizeBytes": int(index_size),
            "indexCommandOutput": index_output,
            "writeCostMeasured": False,
            "decision": "pg_trgm GIN targets the leading-wildcard LIKE predicate; this lab did not measure index write cost.",
        },
        "tuned": tuned,
        "tunedPlan": tuned_plan,
    }


def main() -> int:
    topology = verify_compose_topology()
    identity = db_sql("SELECT current_database() || '|' || current_setting('server_version')")
    if not identity.startswith(DATABASE + "|17.12"):
        raise RuntimeError(f"refusing unexpected latency DB/version: {identity!r}")
    fixture = db_sql("""
        SELECT (SELECT count(*) FROM diagnostics.scan_probe),
               (SELECT count(*) FROM diagnostics.scan_probe WHERE marker LIKE '%needle%'),
               (SELECT count(*) FROM pg_indexes
                WHERE schemaname='diagnostics' AND tablename='scan_probe')
    """)
    if fixture != "1000000|2000|1":
        raise RuntimeError(
            "prepare the fresh isolated fixture with experiments/sql/prepare_latency_lab.sql; "
            f"observed={fixture!r}"
        )
    if urllib.request.urlopen(API_BASE + "/actuator/health", timeout=3).status != 200:
        raise RuntimeError("latency lab API health endpoint is not UP")
    api_identity = api_database_identity()
    scrape_prometheus()  # Fail before generating load if metrics are not exposed.

    scan_indexes = int(fixture.split("|")[-1])
    if scan_indexes != 1:
        raise RuntimeError("start from a fresh latency-lab volume before this one-shot comparison")
    results = [run_pool_wait(), run_lock_wait(), run_slow_sql()]
    report = {
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "database": DATABASE,
        "postgresVersion": identity.split("|", 1)[1],
        "runtimeTopology": {**topology, "apiSessionIdentity": api_identity},
        "fixture": {"rows": 1_000_000, "matchingRows": 2_000, "scanIndexes": 1},
        "measurement": {
            "scope": "loopback synthetic scenarios; not production traffic",
            "percentileMethod": "nearest-rank from client monotonic durations",
            "percentileSampleCount": 100,
            "p95Rank": 95,
            "maximumRank": 100,
            "poolRequests": "100 concurrent clients; Tomcat max threads configured 128; Hikari maximum 4; acquisition timeout 1500ms; each holder requests 2000ms",
            "lockRequests": "25 rounds × four blocked API requests; one exact blocker chain per round",
            "scanRequests": "100 requests at four-client concurrency before and after the index",
        },
        "scenarios": results,
    }
    artifact = Path("experiments/results/api_latency_diagnostics_2026-11-23.json")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"result_artifact={artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

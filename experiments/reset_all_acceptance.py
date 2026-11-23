#!/usr/bin/env python3
"""Prove the all-wallet reset API on the isolated rewardresetdb database only."""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone


# Guard the dedicated project/database and ports; never redirect this script to P2/P3.
PROJECT = "reward-reset-validation"
DATABASE = "rewardresetdb"
# Do not allow a destructive acceptance script to be redirected to another phase API.
API_BASE = "http://127.0.0.1:8383"
DOCKER_HOST = os.environ.get(
    "DOCKER_HOST", "unix:///private/tmp/reward-colima/active-docker.sock"
)
COMPOSE = [
    "docker", "compose", "-p", PROJECT, "-f", "compose.reset-validation.yaml",
]
ADMIN_HEADERS = {"X-User-Id": "101", "X-Demo-Role": "ADMIN"}


def docker(sql: str) -> str:
    """Run only the dedicated Compose project's PostgreSQL client."""
    command = [*COMPOSE, "exec", "-T", "db", "psql", "-X", "-A", "-t",
               "-F", "|", "-v", "ON_ERROR_STOP=1", "-U", "reward_bootstrap",
               "-d", DATABASE]
    command.extend(["-c", sql])
    completed = subprocess.run(
        command, capture_output=True, text=True,
        env={**os.environ, "DOCKER_HOST": DOCKER_HOST}, check=True, timeout=20,
    )
    return completed.stdout.strip()


def verify_api_target() -> dict[str, str]:
    """Check this project's live API container and published loopback port before reset."""
    env = {**os.environ, "DOCKER_HOST": DOCKER_HOST}
    datasource = subprocess.run(
        [*COMPOSE, "exec", "-T", "api", "sh", "-c",
         'printf "%s" "$SPRING_DATASOURCE_URL"'],
        capture_output=True, text=True, env=env, check=True, timeout=10,
    ).stdout.strip()
    if datasource != (
        "jdbc:postgresql://db:5432/rewardresetdb?"
        "currentSchema=reward&escapeSyntaxCallMode=callIfNoReturn"
    ):
        raise RuntimeError(f"refusing API with unexpected datasource: {datasource!r}")

    published_port = subprocess.run(
        [*COMPOSE, "port", "api", "8080"],
        capture_output=True, text=True, env=env, check=True, timeout=10,
    ).stdout.strip()
    if published_port != "127.0.0.1:8383":
        raise RuntimeError(f"refusing API with unexpected published port: {published_port!r}")

    container_id = subprocess.run(
        [*COMPOSE, "ps", "-q", "api"],
        capture_output=True, text=True, env=env, check=True, timeout=10,
    ).stdout.strip()
    if not container_id:
        raise RuntimeError("reset-validation API container is missing")
    project_label = subprocess.run(
        ["docker", "inspect", "--format",
         '{{ index .Config.Labels "com.docker.compose.project" }}', container_id],
        capture_output=True, text=True, env=env, check=True, timeout=10,
    ).stdout.strip()
    if project_label != PROJECT:
        raise RuntimeError(f"refusing API from unexpected Compose project: {project_label!r}")
    return {"datasource": datasource, "publishedPort": published_port,
            "composeProject": project_label}


def request(method: str, path: str, payload: dict | None = None,
            headers: dict | None = None) -> tuple[int, dict]:
    """Call the loopback API and preserve status/body for exact response assertions."""
    data = None if payload is None else json.dumps(payload).encode()
    request_headers = {**ADMIN_HEADERS, **(headers or {})}
    if data is not None:
        request_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        API_BASE + path, data=data, headers=request_headers, method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def wait_for_api() -> None:
    """Wait for the freshly migrated disposable API before creating any fixture rows."""
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(API_BASE + "/actuator/health", timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(1)
    raise TimeoutError("reset-validation API did not become healthy")


def snapshot() -> dict[str, int]:
    """Read reset scope and preserved data directly from PostgreSQL."""
    values = docker("SELECT current_database() || '|' || "
                    "(SELECT count(*) FROM reward.users) || '|' || "
                    "(SELECT count(*) FROM reward.events) || '|' || "
                    "(SELECT count(*) FROM reward.event_recipients) || '|' || "
                    "(SELECT count(*) FROM reward.batch_runs) || '|' || "
                    "(SELECT count(*) FROM reward.wallets) || '|' || "
                    "(SELECT count(*) FROM reward.reward_grants) || '|' || "
                    "(SELECT count(*) FROM reward.compensations) || '|' || "
                    "(SELECT count(*) FROM reward.wallet_ledger) || '|' || "
                    "(SELECT count(*) FROM reward.wallets w "
                    " WHERE w.money_balance <> 0 OR w.point_balance <> 0) || '|' || "
                    "(SELECT COALESCE(sum(money_balance), 0) FROM reward.wallets) || '|' || "
                    "(SELECT COALESCE(sum(point_balance), 0) FROM reward.wallets);")
    parts = values.split("|")
    if len(parts) != 12 or parts[0] != DATABASE:
        raise RuntimeError(f"refusing unexpected reset database/snapshot: {values}")
    keys = (
        "database", "users", "events", "recipients", "batchRuns", "wallets",
        "grants", "compensations", "ledgerRows", "nonzeroWallets", "moneyTotal",
        "pointTotal",
    )
    return {key: int(value) if index else value
            for index, (key, value) in enumerate(zip(keys, parts))}


def install_reset_observer() -> None:
    """Temporarily record reset DML roles/xid in this disposable database only."""
    docker("""
        DO $guard$
        BEGIN
            IF current_database() <> 'rewardresetdb' THEN
                RAISE EXCEPTION 'reset observer is restricted to rewardresetdb';
            END IF;
        END;
        $guard$;
        BEGIN;
        SET LOCAL ROLE reward_migration;
        CREATE TABLE reward.reset_acceptance_observation (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            object_name TEXT NOT NULL,
            operation TEXT NOT NULL,
            transaction_id BIGINT NOT NULL,
            transaction_isolation TEXT NOT NULL,
            session_user_name TEXT NOT NULL,
            current_user_name TEXT NOT NULL
        );
        CREATE FUNCTION reward.capture_reset_acceptance()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        SET search_path = pg_catalog, reward, pg_temp
        AS $function$
        BEGIN
            INSERT INTO reward.reset_acceptance_observation (
                object_name, operation, transaction_id, transaction_isolation,
                session_user_name, current_user_name
            ) VALUES (
                TG_TABLE_NAME, TG_OP, txid_current(),
                current_setting('transaction_isolation'), session_user::TEXT, current_user::TEXT
            );
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $function$;
        REVOKE ALL ON FUNCTION reward.capture_reset_acceptance() FROM PUBLIC;
        CREATE TRIGGER reset_acceptance_ledger
            AFTER DELETE ON reward.wallet_ledger
            FOR EACH ROW EXECUTE FUNCTION reward.capture_reset_acceptance();
        CREATE TRIGGER reset_acceptance_compensation
            AFTER DELETE ON reward.compensations
            FOR EACH ROW EXECUTE FUNCTION reward.capture_reset_acceptance();
        CREATE TRIGGER reset_acceptance_grant
            AFTER DELETE ON reward.reward_grants
            FOR EACH ROW EXECUTE FUNCTION reward.capture_reset_acceptance();
        CREATE TRIGGER reset_acceptance_wallet
            AFTER UPDATE OF money_balance, point_balance ON reward.wallets
            FOR EACH ROW EXECUTE FUNCTION reward.capture_reset_acceptance();
        COMMIT;
    """)


def remove_reset_observer() -> None:
    """Remove the short-lived observation trigger/table after its result is captured."""
    docker("""
        DO $guard$
        BEGIN
            IF current_database() <> 'rewardresetdb' THEN
                RAISE EXCEPTION 'reset observer cleanup is restricted to rewardresetdb';
            END IF;
        END;
        $guard$;
        BEGIN;
        SET LOCAL ROLE reward_migration;
        DROP TRIGGER IF EXISTS reset_acceptance_ledger ON reward.wallet_ledger;
        DROP TRIGGER IF EXISTS reset_acceptance_compensation ON reward.compensations;
        DROP TRIGGER IF EXISTS reset_acceptance_grant ON reward.reward_grants;
        DROP TRIGGER IF EXISTS reset_acceptance_wallet ON reward.wallets;
        DROP FUNCTION IF EXISTS reward.capture_reset_acceptance();
        DROP TABLE IF EXISTS reward.reset_acceptance_observation;
        COMMIT;
    """)


def verify_reset_transaction_context() -> dict[str, str | int]:
    """Check actual DB login, SECURITY DEFINER owner, isolation, operations, and one XID."""
    value = docker("""
        SELECT count(*), count(DISTINCT transaction_id),
               string_agg(DISTINCT transaction_isolation, ',' ORDER BY transaction_isolation),
               string_agg(DISTINCT session_user_name, ',' ORDER BY session_user_name),
               string_agg(DISTINCT current_user_name, ',' ORDER BY current_user_name),
               (SELECT string_agg(object_name || ':' || operation || ':' || row_count,
                                  ',' ORDER BY object_name, operation)
                FROM (
                    SELECT object_name, operation, count(*)::TEXT AS row_count
                    FROM reward.reset_acceptance_observation
                    GROUP BY object_name, operation
                ) grouped)
        FROM reward.reset_acceptance_observation;
    """)
    parts = value.split("|")
    expected = [
        "18", "1", "read committed", "reward_app", "reward_migration",
        "compensations:DELETE:1,reward_grants:DELETE:4,wallet_ledger:DELETE:10,wallets:UPDATE:3",
    ]
    if parts != expected:
        raise RuntimeError(
            "reset DML did not use the expected role/isolation/one transaction "
            f"or affected rows: {parts}"
        )
    return {
        "observedRows": int(parts[0]),
        "transactionIds": int(parts[1]),
        "transactionIsolation": parts[2],
        "sessionUser": parts[3],
        "currentUser": parts[4],
        "operations": parts[5],
    }


def create_batch_event() -> int:
    """Create real BATCH history so reset can prove the batch_runs table is preserved."""
    now = datetime.now(timezone.utc)
    payload = {
        "name": f"Reset acceptance {uuid.uuid4().hex[:8]}",
        "mode": "BATCH",
        "startsAt": (now - timedelta(days=1)).isoformat(),
        "endsAt": (now + timedelta(days=7)).isoformat(),
        "scheduledAt": (now - timedelta(minutes=1)).isoformat(),
        "rewardMoney": 500,
        "rewardPoint": 100,
    }
    status, event = request("POST", "/api/admin/events", payload)
    if status != 200:
        raise RuntimeError(f"creating BATCH event failed: HTTP {status}, {event}")
    event_id = int(event["id"])
    status, recipients = request(
        "PUT", f"/api/admin/events/{event_id}/recipients", {"userIds": [101, 102, 103]},
    )
    if status != 200 or recipients.get("recipientCount") != 3:
        raise RuntimeError(f"registering reset test recipients failed: {recipients}")
    status, opened = request("POST", f"/api/admin/events/{event_id}/open")
    if status != 200 or opened.get("status") != "OPEN":
        raise RuntimeError(f"opening reset test BATCH event failed: {opened}")
    status, run = request("POST", f"/api/admin/events/{event_id}/run", {"maxRecipients": 3})
    if status != 200 or run.get("paidCount") != 3 or run.get("status") != "COMPLETED":
        raise RuntimeError(f"running reset test BATCH failed: HTTP {status}, {run}")
    return event_id


def main() -> int:
    wait_for_api()
    api_target = verify_api_target()
    # The volume must be freshly initialized; this guard prevents accidental reuse.
    baseline = snapshot()
    expected_baseline = {
        "database": DATABASE, "users": 3, "events": 2, "recipients": 3,
        "batchRuns": 0, "wallets": 3, "grants": 0, "compensations": 0,
        "ledgerRows": 0, "nonzeroWallets": 0, "moneyTotal": 0, "pointTotal": 0,
    }
    if baseline != expected_baseline:
        raise RuntimeError(
            "reset test requires its fresh isolated volume; it will not reset a reused DB: "
            f"{baseline}"
        )

    # One existing CLAIM, one support compensation, and a real BATCH run populate all sources.
    claim_event = 1
    status, claim = request(
        "POST", f"/api/events/{claim_event}/claims",
        headers={"X-User-Id": "101", "Idempotency-Key": str(uuid.uuid4())},
    )
    if status != 200 or claim.get("result") != "PAID":
        raise RuntimeError(f"pre-reset CLAIM fixture failed: HTTP {status}, {claim}")

    compensation_payload = {
        "userId": 102,
        "eventId": claim_event,
        "requestId": str(uuid.uuid4()),
        "incidentReference": f"RESET-{uuid.uuid4().hex}",
        "reason": "격리 DB 전체 초기화 acceptance fixture",
        "moneyAmount": 2500,
        "pointAmount": 500,
    }
    status, compensation = request(
        "POST", "/api/admin/compensations", compensation_payload,
    )
    if status != 200 or compensation.get("result") != "PAID":
        raise RuntimeError(f"pre-reset compensation fixture failed: HTTP {status}, {compensation}")

    batch_event = create_batch_event()
    before = snapshot()
    if before["grants"] != 4 or before["compensations"] != 1 \
            or before["ledgerRows"] != 10 or before["batchRuns"] != 1 \
            or before["nonzeroWallets"] != 3:
        raise RuntimeError(f"pre-reset fixtures are incomplete: {before}")

    # This is the actual UI endpoint, executed under the API's reward_app DB login.
    # Recheck immediately before the destructive POST to avoid a phase/port mix-up.
    api_target = verify_api_target()
    install_reset_observer()
    try:
        status, reset = request(
            "POST", "/api/admin/wallets/reset-all",
            headers={"X-Demo-Role": "ADMIN"},
        )
        if status == 200:
            transaction_context = verify_reset_transaction_context()
        else:
            transaction_context = None
    finally:
        remove_reset_observer()
    if status != 200:
        raise RuntimeError(f"whole-user reset failed: HTTP {status}, {reset}")
    expected_response = {
        "usersReset": before["users"],
        "grantsDeleted": before["grants"],
        "compensationsDeleted": before["compensations"],
        "ledgerRowsDeleted": before["ledgerRows"],
    }
    if any(reset.get(key) != value for key, value in expected_response.items()):
        raise RuntimeError(f"reset counts differ from pre-reset rows: {reset}, {before}")

    after = snapshot()
    expected_after = {
        "database": DATABASE,
        "users": before["users"],
        "events": before["events"],
        "recipients": before["recipients"],
        "batchRuns": before["batchRuns"],
        "wallets": before["wallets"],
        "grants": 0,
        "compensations": 0,
        "ledgerRows": 0,
        "nonzeroWallets": 0,
        "moneyTotal": 0,
        "pointTotal": 0,
    }
    if after != expected_after:
        raise RuntimeError(f"reset postcondition/preserved tables differ: {after}")

    print(json.dumps({
        "database": DATABASE,
        "batchEventId": batch_event,
        "apiTarget": api_target,
        "before": before,
        "httpStatus": status,
        "resetResponse": reset,
        "transactionContext": transaction_context,
        "after": after,
        "preserved": ["users", "events", "event_recipients", "batch_runs", "wallets"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

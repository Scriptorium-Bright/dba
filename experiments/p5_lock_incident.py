#!/usr/bin/env python3
"""Reproduce and diagnose one isolated P5 row-lock wait using container psql sessions."""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timezone


# Only this dedicated database is accepted; P2~P4 databases are never targets.
# Keep the database guard fixed so an environment override cannot redirect the lab.
DB_NAME = "rewardp5db"
PSQL = os.environ.get("P5_PSQL_BIN", "psql")
DOCKER = os.environ.get("P5_DOCKER_BIN", "docker")
DOCKER_HOST = os.environ.get(
    "DOCKER_HOST", "unix:///private/tmp/reward-colima/active-docker.sock"
)
DB_CONTAINER = os.environ.get("P5_DB_CONTAINER", "reward-p5-db-1")
POLL_SECONDS = 0.1
WAIT_SECONDS = 15
BASELINE_BALANCE = 100

MIGRATION_USER = os.environ.get("P5_DB_MIGRATION_USER", "reward_migration")
OBSERVER_USER = os.environ.get("P5_DB_OBSERVER_USER", "reward_observer")

BLOCKER_APP = "p5_blocker_a"
WAITER_APP = "p5_waiter_b"


def psql_args(
    application: str, user: str, sql: str | None = None, *, quiet: bool = True
) -> list[str]:
    """Build a psql command inside the isolated database container."""
    quiet_args = ["-q"] if quiet else []
    args = [
        DOCKER,
        "exec",
        "-i",
        "-e",
        f"PGAPPNAME={application}",
        DB_CONTAINER,
        PSQL,
        "-X",
        *quiet_args,
        "-A",
        "-t",
        "-F",
        "|",
        "-v",
        "ON_ERROR_STOP=1",
        "--no-password",
        "-U",
        user,
        "-d",
        DB_NAME,
    ]
    if sql is not None:
        args.extend(["-c", sql])
    return args


def connection_env() -> dict[str, str]:
    """Point docker exec at the selected local engine without exporting DB passwords."""
    return {**os.environ, "DOCKER_HOST": DOCKER_HOST}


def run_sql(application: str, user: str, sql: str) -> str:
    """Run a short command and return psql's tuple-only output."""
    args = psql_args(application, user, sql)
    args.remove("-i")
    result = subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
        env=connection_env(),
        timeout=10,
    )
    return result.stdout.strip()


def start_session(application: str, user: str) -> subprocess.Popen[str]:
    """Open an interactive psql session so a transaction can remain observable."""
    return subprocess.Popen(
        # Keep command tags visible so the incident action can be verified, not inferred.
        psql_args(application, user, quiet=False),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=connection_env(),
    )


def send_sql(session: subprocess.Popen[str], sql: str) -> None:
    """Send one command while leaving the database session open."""
    if session.stdin is None or session.poll() is not None:
        raise RuntimeError("psql session is not available for another command")
    session.stdin.write(sql.rstrip() + "\n")
    session.stdin.flush()


def session_row(application: str) -> list[str] | None:
    """Read the most recent session row for a synthetic workload connection."""
    sql = f"""
        SELECT pid::text,
               state,
               COALESCE(wait_event_type, '-'),
               COALESCE(wait_event, '-'),
               COALESCE(round(extract(epoch FROM clock_timestamp() - xact_start)::numeric, 3)::text, '-'),
               left(regexp_replace(query, E'[\\n\\r\\t]+', ' ', 'g'), 140)
        FROM pg_stat_activity
        WHERE datname = current_database()
          AND application_name = '{application}'
        ORDER BY backend_start DESC
        LIMIT 1
    """
    output = run_sql("p5_observer_poll", OBSERVER_USER, sql)
    return output.split("|") if output else None


def wait_for_session(application: str, predicate, session: subprocess.Popen[str]) -> list[str]:
    """Wait for a named session to reach a state required by the incident step."""
    deadline = time.monotonic() + WAIT_SECONDS
    last_row = None
    while time.monotonic() < deadline:
        if session.poll() is not None:
            stdout, stderr = session.communicate()
            raise RuntimeError(
                f"{application} exited early; stdout={stdout!r}; stderr={stderr!r}"
            )
        last_row = session_row(application)
        if last_row and predicate(last_row):
            return last_row
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"{application} state not reached; last snapshot={last_row!r}")


def finish_session(
    session: subprocess.Popen[str] | None,
    final_sql: str,
    expected_command_tag: str | None = None,
) -> tuple[str, str]:
    """Close a psql session and verify process status plus any required command tag."""
    if session is None:
        raise RuntimeError("cannot verify completion of a missing psql session")
    if session.poll() is None:
        if session.stdin is None:
            raise RuntimeError("psql session stdin is unavailable")
        try:
            if final_sql:
                session.stdin.write(final_sql.rstrip() + "\n")
                session.stdin.flush()
            session.stdin.write("\\q\n")
            session.stdin.flush()
        except (BrokenPipeError, ValueError):
            # Drain output below; a dead psql session must fail the exit/tag checks.
            pass
    try:
        stdout, stderr = session.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        session.terminate()
        stdout, stderr = session.communicate(timeout=5)

    return_code = session.returncode
    if return_code != 0:
        raise RuntimeError(
            f"psql session exited with status {return_code}; "
            f"stdout={stdout!r}; stderr={stderr!r}"
        )
    if expected_command_tag is not None:
        command_tags = {line.strip() for line in stdout.splitlines()}
        if expected_command_tag not in command_tags:
            raise RuntimeError(
                f"expected psql command tag {expected_command_tag!r}; "
                f"exit={return_code}; stdout={stdout!r}; stderr={stderr!r}"
            )
    return stdout, stderr


def incident_snapshot() -> str:
    """Capture the waiter-to-blocker chain with both SQL statements and ages."""
    return run_sql(
        "p5_observer_snapshot",
        OBSERVER_USER,
        f"""
        SELECT clock_timestamp()::text,
               waiter.pid::text,
               waiter.application_name,
               waiter.state,
               COALESCE(waiter.wait_event_type, '-'),
               COALESCE(waiter.wait_event, '-'),
               blocker.pid::text,
               blocker.application_name,
               blocker.state,
               COALESCE(round(extract(epoch FROM clock_timestamp() - blocker.xact_start)::numeric, 3)::text, '-'),
               left(regexp_replace(blocker.query, E'[\\n\\r\\t]+', ' ', 'g'), 120),
               left(regexp_replace(waiter.query, E'[\\n\\r\\t]+', ' ', 'g'), 120)
        FROM pg_stat_activity AS waiter
        CROSS JOIN LATERAL unnest(pg_blocking_pids(waiter.pid)) AS blocked_by(pid)
        LEFT JOIN pg_stat_activity AS blocker ON blocker.pid = blocked_by.pid
        WHERE waiter.datname = current_database()
          AND waiter.application_name = '{WAITER_APP}'
        """,
    )


def verify_incident_blocking_chain(
    chain: str, blocker_state: list[str], waiter_state: list[str]
) -> dict[str, str]:
    """Require pg_blocking_pids() to connect the exact sessions from this run."""
    fields = chain.split("|")
    if len(fields) != 12:
        raise RuntimeError(f"unexpected blocker-chain column count: {fields!r}")
    expected = {
        "waiter_pid": waiter_state[0],
        "waiter_application": WAITER_APP,
        "waiter_state": "active",
        "wait_event_type": "Lock",
        "wait_event": "transactionid",
        "blocker_pid": blocker_state[0],
        "blocker_application": BLOCKER_APP,
        "blocker_state": "idle in transaction",
    }
    observed = {
        "waiter_pid": fields[1],
        "waiter_application": fields[2],
        "waiter_state": fields[3],
        "wait_event_type": fields[4],
        "wait_event": fields[5],
        "blocker_pid": fields[6],
        "blocker_application": fields[7],
        "blocker_state": fields[8],
    }
    if observed != expected:
        raise RuntimeError(
            f"blocker chain is not the exact A→B Lock/transactionid pair: "
            f"expected={expected}, observed={observed}, raw={chain!r}"
        )
    if "UPDATE p5_ops.lock_lab SET balance = balance + 1 WHERE id = 1" not in fields[11]:
        raise RuntimeError(f"waiter PID is not running the expected UPDATE: {fields[11]!r}")
    if "SELECT id FROM p5_ops.lock_lab WHERE id = 1 FOR UPDATE" not in fields[10]:
        raise RuntimeError(f"blocker PID is not holding the expected row lock: {fields[10]!r}")
    return observed


def main() -> int:
    """Run the controlled incident, diagnose it, recover, and restore the fixture."""
    identity = run_sql(
        "p5_identity_check",
        OBSERVER_USER,
        "SELECT current_database() || '|' || current_setting('server_version')",
    )
    if not identity.startswith(DB_NAME + "|"):
        raise RuntimeError(f"refusing to use unexpected database: {identity!r}")

    before = int(
        run_sql(
            "p5_fixture_check",
            MIGRATION_USER,
            "SELECT balance FROM p5_ops.lock_lab WHERE id = 1",
        )
    )
    if before != BASELINE_BALANCE:
        raise RuntimeError(
            f"fixture balance must be {BASELINE_BALANCE} before the incident; got {before}"
        )

    blocker = waiter = None
    blocker_released = False
    completed = False
    incident_started = datetime.now(timezone.utc)
    try:
        blocker = start_session(BLOCKER_APP, MIGRATION_USER)
        send_sql(blocker, "BEGIN; SELECT id FROM p5_ops.lock_lab WHERE id = 1 FOR UPDATE;")
        blocker_state = wait_for_session(
            BLOCKER_APP,
            lambda row: row[1] == "idle in transaction",
            blocker,
        )
        print(f"incident_started_utc={incident_started.isoformat()}")
        print(f"database={identity.split('|', 1)[0]} postgres={identity.split('|', 1)[1]}")
        print(f"blocker_session pid={blocker_state[0]} state={blocker_state[1]} xact_age_s={blocker_state[4]}")

        waiter = start_session(WAITER_APP, MIGRATION_USER)
        # This client timer includes send/execution and polling; it is not pure DB lock time.
        waiter_request_started = time.monotonic()
        send_sql(
            waiter,
            "UPDATE p5_ops.lock_lab SET balance = balance + 1 WHERE id = 1;",
        )
        waiter_state = wait_for_session(
            WAITER_APP,
            lambda row: row[2] == "Lock" and row[3] == "transactionid",
            waiter,
        )
        print(
            f"waiter_session pid={waiter_state[0]} state={waiter_state[1]} "
            f"wait={waiter_state[2]}/{waiter_state[3]} query={waiter_state[5]}"
        )

        chain = incident_snapshot()
        if not chain:
            raise RuntimeError("observer query did not return the expected blocker chain")
        verified_chain = verify_incident_blocking_chain(chain, blocker_state, waiter_state)
        print(f"blocker_chain={chain}")
        print(f"blocker_chain_verified={verified_chain}")

        reader_started = time.monotonic()
        reader_value = run_sql(
            "p5_reader_c",
            MIGRATION_USER,
            "SELECT balance FROM p5_ops.lock_lab WHERE id = 1",
        )
        reader_elapsed = time.monotonic() - reader_started
        print(
            f"concurrent_select value={reader_value} elapsed_ms={reader_elapsed * 1000:.3f} "
            "while_update_waits=true"
        )
        if reader_value != str(BASELINE_BALANCE):
            raise RuntimeError(f"concurrent SELECT saw unexpected committed value: {reader_value}")

        blocker_commit_requested = datetime.now(timezone.utc)
        blocker_output, blocker_stderr = finish_session(
            blocker, "COMMIT;", expected_command_tag="COMMIT"
        )
        # This is the client confirmation time after exit 0 and the COMMIT tag, not DB commit time.
        blocker_commit_confirmed = datetime.now(timezone.utc)
        blocker_released = True
        blocker = None

        waiter_state = wait_for_session(
            WAITER_APP,
            lambda row: row[1] == "idle",
            waiter,
        )
        client_request_to_idle_observation = time.monotonic() - waiter_request_started
        waiter_output, waiter_stderr = finish_session(
            waiter, "", expected_command_tag="UPDATE 1"
        )
        waiter = None
        after = int(
            run_sql(
                "p5_after_incident",
                MIGRATION_USER,
                "SELECT balance FROM p5_ops.lock_lab WHERE id = 1",
            )
        )
        if after != BASELINE_BALANCE + 1:
            raise RuntimeError(f"waiter result not committed exactly once; balance={after}")

        print(
            f"blocker_commit_requested_client_utc={blocker_commit_requested.isoformat()} "
            f"blocker_commit_confirmed_client_utc={blocker_commit_confirmed.isoformat()} "
            "action=COMMIT "
            f"psql_exit=0 command_tag=COMMIT output={blocker_output!r} "
            f"stderr={blocker_stderr!r}"
        )
        print(
            f"waiter_recovered pid={waiter_state[0]} state={waiter_state[1]} "
            "timing=client_request_to_idle_observation "
            f"elapsed_s={client_request_to_idle_observation:.3f} "
            "includes_send_execution_and_polling=true pure_db_lock_duration=false "
            f"psql_exit=0 "
            f"command_tag=UPDATE 1 psql_output={waiter_output!r} "
            f"stderr={waiter_stderr!r}"
        )
        print(f"balance_before={before} balance_after_waiter_commit={after}")
        completed = True
    finally:
        # Release any held row lock first so the waiter can finish before fixture cleanup.
        if blocker is not None:
            try:
                finish_session(blocker, "ROLLBACK;")
            except RuntimeError as error:
                print(f"cleanup_blocker_release_error={error}")
        if waiter is not None:
            if not blocker_released:
                try:
                    wait_for_session(WAITER_APP, lambda row: row[1] == "idle", waiter)
                except (RuntimeError, TimeoutError):
                    waiter.terminate()
                    waiter.communicate(timeout=5)
            try:
                finish_session(waiter, "")
            except RuntimeError as error:
                print(f"cleanup_waiter_close_error={error}")
        if before == BASELINE_BALANCE:
            run_sql(
                "p5_fixture_restore",
                MIGRATION_USER,
                f"UPDATE p5_ops.lock_lab SET balance = {BASELINE_BALANCE} WHERE id = 1",
            )
            restored = run_sql(
                "p5_fixture_restore_check",
                MIGRATION_USER,
                "SELECT balance FROM p5_ops.lock_lab WHERE id = 1",
            )
            print(f"fixture_restored_balance={restored}")

    if completed:
        print("incident_result=PASS blocker_identified waiter_recovered fixture_restored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

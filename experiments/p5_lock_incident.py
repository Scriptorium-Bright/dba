#!/usr/bin/env python3
"""Reproduce and diagnose one isolated P5 row-lock wait using the local psql client."""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timezone


# Only this dedicated database is accepted; P2~P4 databases are never targets.
DB_NAME = os.environ.get("P5_DB_NAME", "rewardp5db")
DB_HOST = os.environ.get("P5_DB_HOST", "127.0.0.1")
DB_PORT = os.environ.get("P5_DB_PORT", "55434")
PSQL = os.environ.get("P5_PSQL_BIN", "psql")
POLL_SECONDS = 0.1
WAIT_SECONDS = 15
BASELINE_BALANCE = 100

MIGRATION_USER = os.environ.get("P5_DB_MIGRATION_USER", "reward_migration")
OBSERVER_USER = os.environ.get("P5_DB_OBSERVER_USER", "reward_observer")
MIGRATION_PASSWORD = os.environ.get(
    "P5_DB_MIGRATION_PASSWORD", "p5_migration_local_only"
)
OBSERVER_PASSWORD = os.environ.get(
    "P5_DB_OBSERVER_PASSWORD", "p5_observer_local_only"
)

BLOCKER_APP = "p5_blocker_a"
WAITER_APP = "p5_waiter_b"


def psql_args(application: str, user: str) -> list[str]:
    """Build a noninteractive psql command with an identifying application_name."""
    return [
        PSQL,
        "-X",
        "-q",
        "-A",
        "-t",
        "-F",
        "|",
        "-v",
        "ON_ERROR_STOP=1",
        "--no-password",
        "-h",
        DB_HOST,
        "-p",
        DB_PORT,
        "-U",
        user,
        "-d",
        DB_NAME,
        "-c",
        "SELECT 1",
    ]


def connection_env(application: str, user: str) -> dict[str, str]:
    """Supply local-demo credentials without placing them in command arguments."""
    password = MIGRATION_PASSWORD if user == MIGRATION_USER else OBSERVER_PASSWORD
    return {
        **os.environ,
        "PGAPPNAME": application,
        "PGPASSWORD": password,
    }


def run_sql(application: str, user: str, sql: str) -> str:
    """Run a short command and return psql's tuple-only output."""
    args = psql_args(application, user)
    args[-1] = sql
    result = subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
        env=connection_env(application, user),
        timeout=10,
    )
    return result.stdout.strip()


def start_session(application: str, user: str) -> subprocess.Popen[str]:
    """Open an interactive psql session so a transaction can remain observable."""
    return subprocess.Popen(
        psql_args(application, user)[:-2],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=connection_env(application, user),
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
    session: subprocess.Popen[str] | None, final_sql: str
) -> tuple[str, str] | None:
    """End a psql transaction and close its connection cleanly."""
    if session is None or session.poll() is not None:
        return None
    if session.stdin is None:
        raise RuntimeError("psql session stdin is unavailable")
    session.stdin.write(final_sql.rstrip() + "\n\\q\n")
    session.stdin.flush()
    try:
        return session.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        session.terminate()
        return session.communicate(timeout=5)


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
        waiter_started = time.monotonic()
        send_sql(
            waiter,
            "UPDATE p5_ops.lock_lab SET balance = balance + 1 WHERE id = 1;",
        )
        waiter_state = wait_for_session(
            WAITER_APP,
            lambda row: row[2] == "Lock",
            waiter,
        )
        print(
            f"waiter_session pid={waiter_state[0]} state={waiter_state[1]} "
            f"wait={waiter_state[2]}/{waiter_state[3]} query={waiter_state[5]}"
        )

        chain = incident_snapshot()
        if not chain:
            raise RuntimeError("observer query did not return the expected blocker chain")
        print(f"blocker_chain={chain}")

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

        blocker_ended = datetime.now(timezone.utc)
        finish_session(blocker, "COMMIT;")
        blocker_released = True
        blocker = None

        waiter_state = wait_for_session(
            WAITER_APP,
            lambda row: row[1] == "idle",
            waiter,
        )
        waiter_elapsed = time.monotonic() - waiter_started
        waiter_output = finish_session(waiter, "")
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

        print(f"blocker_released_utc={blocker_ended.isoformat()} action=COMMIT")
        print(
            f"waiter_recovered pid={waiter_state[0]} state={waiter_state[1]} "
            f"elapsed_s={waiter_elapsed:.3f} psql_output={waiter_output!r}"
        )
        print(f"balance_before={before} balance_after_waiter_commit={after}")
        completed = True
    finally:
        # Release any held row lock first so the waiter can finish before fixture cleanup.
        if blocker is not None:
            finish_session(blocker, "ROLLBACK;")
        if waiter is not None:
            if not blocker_released:
                try:
                    wait_for_session(WAITER_APP, lambda row: row[1] == "idle", waiter)
                except (RuntimeError, TimeoutError):
                    waiter.terminate()
                    waiter.communicate(timeout=5)
            finish_session(waiter, "")
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

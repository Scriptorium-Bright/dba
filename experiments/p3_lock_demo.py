#!/usr/bin/env python3
"""Run the P3 A/B/C DDL-lock scenario against rewardp3db only."""

from __future__ import annotations

import os
import subprocess
import sys
import time


CONTAINER = os.environ.get("P3_DB_CONTAINER", "reward-p3-db-1")
DOCKER_HOST = os.environ.get(
    "DOCKER_HOST", "unix:///private/tmp/reward-colima/active-docker.sock"
)
WAIT_SECONDS = 25
POLL_SECONDS = 0.5


def psql_command(application: str, role: str, sql: str) -> list[str]:
    return [
        "docker",
        "exec",
        "-e",
        f"PGAPPNAME={application}",
        CONTAINER,
        "psql",
        "-X",
        "-A",
        "-t",
        "-F",
        "|",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        role,
        "-d",
        "rewardp3db",
        "-c",
        sql,
    ]


def run_sql(application: str, role: str, sql: str) -> str:
    result = subprocess.run(
        psql_command(application, role, sql),
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "DOCKER_HOST": DOCKER_HOST},
    )
    return result.stdout.strip()


def wait_for_app(application: str, predicate, timeout: int = 12) -> list[str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = run_sql(
            "p3_lock_observer",
            "reward_bootstrap",
            """
            SELECT application_name, pid::text, state, wait_event_type,
                   COALESCE(wait_event, ''),
                   COALESCE(array_to_string(pg_blocking_pids(pid), ','), '')
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND application_name = '""" + application + "' ORDER BY pid;")
        match = next((row.split("|") for row in rows.splitlines() if row), None)
        if match and predicate(match):
            return match
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"session {application} did not reach the expected state")


def main() -> int:
    # The database-name guard keeps this experiment away from the P2 rewarddb.
    database = run_sql("p3_lock_guard", "reward_bootstrap", "SELECT current_database();")
    if database != "rewardp3db":
        raise RuntimeError(f"refusing to run against database {database!r}")

    run_sql(
        "p3_lock_prepare",
        "reward_migration",
        "ALTER TABLE reward.wallet_ledger DROP COLUMN IF EXISTS p3_lock_probe;",
    )
    print("A: BEGIN; SELECT wallet_ledger; hold transaction for 25s")
    session_a = subprocess.Popen(
        psql_command(
            "p3_lock_a",
            "reward_observer",
            "BEGIN; SELECT count(*) FROM reward.wallet_ledger; SELECT pg_sleep(25); COMMIT;",
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "DOCKER_HOST": DOCKER_HOST},
    )
    try:
        a = wait_for_app(
            "p3_lock_a",
            lambda row: row[3] == "Timeout" and row[4] == "PgSleep",
        )
        print(f"A ready: pid={a[1]} state={a[2]} wait={a[3]}/{a[4]}")

        print("B: ALTER TABLE with lock_timeout=8s")
        b_started = time.monotonic()
        session_b = subprocess.Popen(
            psql_command(
                "p3_lock_b",
                "reward_migration",
                "SET lock_timeout='8s'; "
                "ALTER TABLE reward.wallet_ledger ADD COLUMN p3_lock_probe INTEGER;",
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "DOCKER_HOST": DOCKER_HOST},
        )
        b = wait_for_app(
            "p3_lock_b",
            lambda row: row[3] == "Lock" and row[4] == "relation",
        )
        print(f"B waiting: pid={b[1]} blockers={b[5]} wait={b[3]}/{b[4]}")

        print("C: SELECT starts after B has requested the conflicting DDL lock")
        session_c = subprocess.Popen(
            psql_command(
                "p3_lock_c",
                "reward_observer",
                "SELECT count(*) FROM reward.wallet_ledger;",
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "DOCKER_HOST": DOCKER_HOST},
        )
        c = wait_for_app(
            "p3_lock_c",
            lambda row: row[3] == "Lock" and row[4] == "relation",
        )
        print(f"C waiting: pid={c[1]} blockers={c[5]} wait={c[3]}/{c[4]}")

        lock_rows = run_sql(
            "p3_lock_snapshot",
            "reward_bootstrap",
            """
            SELECT a.application_name, l.locktype,
                   COALESCE(n.nspname || '.' || c.relname, ''),
                   l.mode, l.granted::text, COALESCE(l.waitstart::text, '')
            FROM pg_locks l
            JOIN pg_stat_activity a USING (pid)
            LEFT JOIN pg_class c ON c.oid = l.relation
            LEFT JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE a.application_name IN ('p3_lock_a', 'p3_lock_b', 'p3_lock_c')
              AND l.relation = 'reward.wallet_ledger'::regclass
            ORDER BY a.application_name, l.granted;
            """,
        )
        print("pg_locks snapshot: app|type|relation|mode|granted|waitstart")
        print(lock_rows or "<no relation locks returned>")

        _b_out, b_err = session_b.communicate(timeout=WAIT_SECONDS)
        b_elapsed = time.monotonic() - b_started
        print(f"B exit={session_b.returncode} elapsed={b_elapsed:.2f}s")
        print(b_err.strip() or "<no stderr>")
        if session_b.returncode == 0 or "lock timeout" not in b_err.lower():
            raise RuntimeError("expected DDL lock_timeout failure was not observed")

        c_out, c_err = session_c.communicate(timeout=WAIT_SECONDS)
        print(f"C exit={session_c.returncode} result={c_out.strip()!r} error={c_err.strip()!r}")
        if session_c.returncode != 0:
            raise RuntimeError("later SELECT did not resume after B timed out")

        a_out, a_err = session_a.communicate(timeout=WAIT_SECONDS)
        print(f"A exit={session_a.returncode} result={a_out.strip()!r} error={a_err.strip()!r}")
        if session_a.returncode != 0:
            raise RuntimeError("long reader transaction did not finish cleanly")

        retry_started = time.monotonic()
        run_sql(
            "p3_lock_retry",
            "reward_migration",
            "SET lock_timeout='4s'; "
            "ALTER TABLE reward.wallet_ledger ADD COLUMN p3_lock_probe INTEGER;",
        )
        retry_elapsed = time.monotonic() - retry_started
        print(f"B retry after A COMMIT: ALTER succeeded in {retry_elapsed:.3f}s")

        cleanup_started = time.monotonic()
        run_sql(
            "p3_lock_cleanup",
            "reward_migration",
            "ALTER TABLE reward.wallet_ledger DROP COLUMN p3_lock_probe;",
        )
        print(f"probe column cleanup completed in {time.monotonic() - cleanup_started:.3f}s")
        return 0
    finally:
        # If an observer/DDL command fails unexpectedly, close its session so the lab leaves no locks.
        for process_name in ("session_a", "session_b", "session_c"):
            process = locals().get(process_name)
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
        run_sql(
            "p3_lock_final_cleanup",
            "reward_migration",
            "ALTER TABLE reward.wallet_ledger DROP COLUMN IF EXISTS p3_lock_probe;",
        )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # The lab prints a concise failure cause for the execution log.
        print(f"P3 lock experiment failed: {error}", file=sys.stderr)
        raise SystemExit(1)

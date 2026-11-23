#!/usr/bin/env python3
"""Compare blocking CREATE INDEX with CREATE INDEX CONCURRENTLY on rewardp4db."""

from __future__ import annotations

import os
import subprocess
import sys
import time


CONTAINER = os.environ.get("P4_DB_CONTAINER", "reward-p4-db-1")
DOCKER_HOST = os.environ.get(
    "DOCKER_HOST", "unix:///private/tmp/reward-colima/active-docker.sock"
)
INDEX_NAME = "p4_index_method_probe_idx"
WAIT_SECONDS = 30
POLL_SECONDS = 0.1


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
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        role,
        "-d",
        "rewardp4db",
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
            "p4_index_observer",
            "reward_bootstrap",
            """
            SELECT application_name, pid::text, state,
                   COALESCE(wait_event_type, ''), COALESCE(wait_event, ''),
                   COALESCE(array_to_string(pg_blocking_pids(pid), ','), '')
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND application_name = '""" + application + "' ORDER BY pid;")
        match = next((line.split("|") for line in rows.splitlines() if line), None)
        if match and predicate(match):
            return match
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"session {application} did not reach the expected state")


def start_reader(application: str) -> subprocess.Popen[str]:
    # A no-op UPDATE holds RowExclusiveLock until ROLLBACK and leaves the fixture values unchanged.
    return subprocess.Popen(
        psql_command(
            application,
            "reward_migration",
            "BEGIN; UPDATE reward.wallet_ledger SET amount=amount WHERE id=1; "
            "SELECT pg_sleep(25); ROLLBACK;",
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "DOCKER_HOST": DOCKER_HOST},
    )


def start_writer(application: str, row_id: int) -> tuple[subprocess.Popen[str], float]:
    started = time.monotonic()
    process = subprocess.Popen(
        psql_command(
            application,
            "reward_migration",
            f"UPDATE reward.wallet_ledger SET amount=amount WHERE id={row_id};",
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "DOCKER_HOST": DOCKER_HOST},
    )
    return process, started


def main() -> int:
    database = run_sql("p4_index_guard", "reward_bootstrap", "SELECT current_database();")
    if database != "rewardp4db":
        raise RuntimeError(f"refusing to run against database {database!r}")

    run_sql(
        "p4_index_prepare",
        "reward_migration",
        f"DROP INDEX IF EXISTS reward.{INDEX_NAME};",
    )
    reader = blocker = writer = None
    try:
        print("NORMAL INDEX: long DML transaction holds RowExclusiveLock")
        reader = start_reader("p4_normal_reader")
        a = wait_for_app(
            "p4_normal_reader",
            lambda row: row[3] == "Timeout" and row[4] == "PgSleep",
        )
        print(f"A active pid={a[1]} wait={a[3]}/{a[4]}")

        ddl_started = time.monotonic()
        blocker = subprocess.Popen(
            psql_command(
                "p4_normal_ddl",
                "reward_migration",
                "SET lock_timeout='8s'; "
                f"CREATE INDEX {INDEX_NAME} ON reward.wallet_ledger (source_type, id DESC);",
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "DOCKER_HOST": DOCKER_HOST},
        )
        b = wait_for_app(
            "p4_normal_ddl",
            lambda row: row[3] == "Lock" and row[4] == "relation",
        )
        print(f"B blocked pid={b[1]} blocker={b[5]} wait={b[3]}/{b[4]}")

        writer, writer_started = start_writer("p4_normal_writer_c", 2)
        c = wait_for_app(
            "p4_normal_writer_c",
            lambda row: row[3] == "Lock" and row[4] == "relation",
        )
        print(f"C blocked behind queued DDL pid={c[1]} blockers={c[5]}")

        _b_out, b_err = blocker.communicate(timeout=WAIT_SECONDS)
        blocker_elapsed = time.monotonic() - ddl_started
        print(f"B result=lock-timeout elapsed={blocker_elapsed:.2f}s error={b_err.strip()!r}")
        if blocker.returncode == 0 or "lock timeout" not in b_err.lower():
            raise RuntimeError("normal CREATE INDEX did not show the expected DML lock wait")

        c_out, c_err = writer.communicate(timeout=WAIT_SECONDS)
        print(
            f"C resumed before A ended elapsed={time.monotonic() - writer_started:.2f}s "
            f"result={c_out.strip()!r} error={c_err.strip()!r}"
        )
        if writer.returncode != 0:
            raise RuntimeError("DML did not resume after the normal index lock timeout")

        a_out, a_err = reader.communicate(timeout=WAIT_SECONDS)
        print(f"A finished result={a_out.strip()!r} error={a_err.strip()!r}")
        if reader.returncode != 0:
            raise RuntimeError("the DML lock holder did not finish cleanly")

        normal_started = time.monotonic()
        run_sql(
            "p4_normal_build",
            "reward_migration",
            f"CREATE INDEX {INDEX_NAME} ON reward.wallet_ledger (source_type, id DESC);",
        )
        print(f"normal build without competing DML: {time.monotonic() - normal_started:.3f}s")
        run_sql("p4_normal_drop", "reward_migration", f"DROP INDEX reward.{INDEX_NAME};")

        print("CONCURRENT INDEX: writers remain compatible with the index build lock")
        reader = start_reader("p4_concurrent_reader")
        a2 = wait_for_app(
            "p4_concurrent_reader",
            lambda row: row[3] == "Timeout" and row[4] == "PgSleep",
        )
        print(f"A2 active pid={a2[1]} wait={a2[3]}/{a2[4]}")

        concurrent_started = time.monotonic()
        blocker = subprocess.Popen(
            psql_command(
                "p4_concurrent_ddl",
                "reward_migration",
                f"CREATE INDEX CONCURRENTLY {INDEX_NAME} "
                "ON reward.wallet_ledger (source_type, id DESC);",
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "DOCKER_HOST": DOCKER_HOST},
        )
        b2 = wait_for_app(
            "p4_concurrent_ddl",
            lambda row: row[2] == "active" and row[3] == "Lock" and row[4] == "virtualxid",
            timeout=20,
        )
        print(f"B2 waiting on old snapshot pid={b2[1]} wait={b2[3]}/{b2[4]}")

        writer, writer_started = start_writer("p4_concurrent_writer_c", 3)
        c_out, c_err = writer.communicate(timeout=WAIT_SECONDS)
        c_elapsed = time.monotonic() - writer_started
        print(f"C2 DML during concurrent build elapsed={c_elapsed:.3f}s "
              f"result={c_out.strip()!r} error={c_err.strip()!r}")
        if writer.returncode != 0:
            raise RuntimeError("DML failed while CREATE INDEX CONCURRENTLY was active")

        a_out, a_err = reader.communicate(timeout=WAIT_SECONDS)
        print(f"A2 finished result={a_out.strip()!r} error={a_err.strip()!r}")
        if reader.returncode != 0:
            raise RuntimeError("the concurrent-build lock holder did not finish cleanly")

        b_out, b_err = blocker.communicate(timeout=WAIT_SECONDS)
        concurrent_elapsed = time.monotonic() - concurrent_started
        print(f"B2 result=success elapsed={concurrent_elapsed:.3f}s "
              f"stdout={b_out.strip()!r} stderr={b_err.strip()!r}")
        if blocker.returncode != 0:
            raise RuntimeError("CREATE INDEX CONCURRENTLY failed")

        valid = run_sql(
            "p4_index_validity",
            "reward_observer",
            f"SELECT indisready::text || '|' || indisvalid::text "
            f"FROM pg_index WHERE indexrelid='reward.{INDEX_NAME}'::regclass;",
        )
        print(f"concurrent index ready|valid={valid}")
        if valid not in {"t|t", "true|true"}:
            raise RuntimeError(f"concurrent index validity is {valid!r}")
        return 0
    finally:
        for process in (blocker, writer, reader):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
        run_sql(
            "p4_index_cleanup",
            "reward_migration",
            f"DROP INDEX IF EXISTS reward.{INDEX_NAME};",
        )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"P4 index build comparison failed: {error}", file=sys.stderr)
        raise SystemExit(1)

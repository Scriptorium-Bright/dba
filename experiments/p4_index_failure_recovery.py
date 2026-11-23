#!/usr/bin/env python3
"""Exercise PostgreSQL concurrent-index failure recovery on a P4-only scratch table."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


# Pin every database call to P4 so this DDL exercise cannot be redirected to another phase.
PROJECT = "reward-p4"
DATABASE = "rewardp4db"
COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", "compose.p4.yaml"]
DOCKER_HOST = os.environ.get(
    "DOCKER_HOST", "unix:///private/tmp/reward-colima/active-docker.sock"
)
TABLE = "reward.p4_index_failure_probe"
SEQUENCE_INDEX = "reward.p4_index_failure_probe_sequence_idx"
UNIQUE_INDEX = "reward.p4_index_failure_probe_unique_idx"
OUTPUT = Path("experiments/results/p4_index_failure_recovery_2026-11-23.json")


def psql(sql: str, *, expect_success: bool = True) -> subprocess.CompletedProcess[str]:
    """Run one autocommit psql command against the fixed disposable P4 database."""
    completed = subprocess.run(
        [
            *COMPOSE, "exec", "-T", "db", "psql", "-X", "-A", "-t", "-F", "|",
            "-v", "ON_ERROR_STOP=1", "-U", "reward_bootstrap", "-d", DATABASE,
            "-c", sql,
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "DOCKER_HOST": DOCKER_HOST, "LC_ALL": "C"},
        timeout=60,
    )
    if expect_success and completed.returncode != 0:
        raise RuntimeError(f"P4 psql command failed: {completed.stderr.strip()}")
    if not expect_success and completed.returncode == 0:
        raise RuntimeError("the duplicate-key unique index build unexpectedly succeeded")
    return completed


def index_state() -> list[str]:
    """Return catalog validity for only the two indexes created by this probe."""
    output = psql(f"""
        SELECT indexrelid::regclass::TEXT || '|' || indisready || '|' || indisvalid
        FROM pg_catalog.pg_index
        WHERE indrelid = '{TABLE}'::regclass
          AND indexrelid::regclass::TEXT IN ('{SEQUENCE_INDEX}', '{UNIQUE_INDEX}')
        ORDER BY indexrelid::regclass::TEXT;
    """).stdout.strip()
    return [line for line in output.splitlines() if line]


def main() -> None:
    """Fail a second concurrent build, inspect the INVALID object, then repair and verify."""
    psql(f"""
        DO $guard$
        BEGIN
            IF current_database() <> '{DATABASE}' THEN
                RAISE EXCEPTION 'index recovery probe is restricted to {DATABASE}';
            END IF;
            IF to_regclass('{TABLE}') IS NOT NULL THEN
                RAISE EXCEPTION 'scratch table already exists; inspect it before cleanup';
            END IF;
        END;
        $guard$;
    """)
    psql(f"""
        CREATE TABLE {TABLE} (
            sequence_key INTEGER NOT NULL,
            duplicate_key INTEGER NOT NULL
        );
    """)
    table_created = True
    try:
        psql(f"INSERT INTO {TABLE} VALUES (1, 7), (2, 7), (3, 9);")
        # The first of two indexes succeeds, matching a partially completed rollout.
        psql(f"CREATE INDEX CONCURRENTLY p4_index_failure_probe_sequence_idx "
             f"ON {TABLE} (sequence_key);")

        # Deliberate duplicate data makes the second unique concurrent build fail.
        failed = psql(
            f"CREATE UNIQUE INDEX CONCURRENTLY p4_index_failure_probe_unique_idx "
            f"ON {TABLE} (duplicate_key);",
            expect_success=False,
        )
        failure_text = (failed.stdout + failed.stderr).strip()
        if "could not create unique index" not in failure_text:
            raise RuntimeError(f"unexpected concurrent build failure: {failure_text}")

        failed_state = index_state()
        if f"{SEQUENCE_INDEX}|true|true" not in failed_state:
            raise RuntimeError(f"the first successfully built index is not valid: {failed_state}")
        invalid_lines = [line for line in failed_state if line.startswith(UNIQUE_INDEX + "|")]
        if len(invalid_lines) != 1 or invalid_lines[0].endswith("|true"):
            raise RuntimeError(f"the failed index was not recorded INVALID: {failed_state}")

        # Drop only the known failed object concurrently; keep the valid first index.
        psql(f"DROP INDEX CONCURRENTLY {UNIQUE_INDEX};")
        psql(f"UPDATE {TABLE} SET duplicate_key = 8 WHERE sequence_key = 2;")
        psql(f"CREATE UNIQUE INDEX CONCURRENTLY p4_index_failure_probe_unique_idx "
             f"ON {TABLE} (duplicate_key);")

        repaired_state = index_state()
        expected = [f"{SEQUENCE_INDEX}|true|true", f"{UNIQUE_INDEX}|true|true"]
        if repaired_state != expected:
            raise RuntimeError(f"recovered indexes are not ready and valid: {repaired_state}")

        payload = {
            "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
            "composeProject": PROJECT,
            "database": DATABASE,
            "scratchTable": TABLE,
            "firstIndexAfterBuild": f"{SEQUENCE_INDEX}|true|true",
            "expectedSecondBuildFailure": failure_text,
            "catalogAfterFailure": failed_state,
            "recovery": [
                f"DROP INDEX CONCURRENTLY {UNIQUE_INDEX}",
                "remove one duplicate key from the disposable table",
                f"CREATE UNIQUE INDEX CONCURRENTLY {UNIQUE_INDEX}",
            ],
            "catalogAfterRepair": repaired_state,
        }
    finally:
        if table_created:
            # The table is uniquely named and guarded above; dropping it removes only probe objects.
            psql(f"DROP TABLE IF EXISTS {TABLE};")

    cleanup_state = psql(
        f"SELECT CASE WHEN to_regclass('{TABLE}') IS NULL THEN 'removed' ELSE 'present' END;"
    ).stdout.strip()
    if cleanup_state != "removed":
        raise RuntimeError(f"scratch table cleanup was not confirmed: {cleanup_state}")
    payload["scratchTableCleanup"] = cleanup_state
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

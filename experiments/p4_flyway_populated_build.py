#!/usr/bin/env python3
"""Measure the P4 Flyway V15 build against the named 1M-row disposable ledger probe."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]
DATABASE = "rewardp4_flyway_populated_probe"
CONTAINER = os.environ.get("P4_DB_CONTAINER", "reward-p4-db-1")
DOCKER_HOST = os.environ.get(
    "DOCKER_HOST", "unix:///private/tmp/reward-colima/active-docker.sock"
)
HELPER_DIR = Path(os.environ.get("P4_FLYWAY_HELPER_DIR", "/tmp/p4-flyway-helper"))
JAVA_CP = os.environ.get("P4_FLYWAY_JAVA_CLASSPATH", "")
FLYWAY_URL = os.environ.get(
    "P4_FLYWAY_URL",
    "jdbc:postgresql://127.0.0.1:55433/"
    f"{DATABASE}?currentSchema=reward&ApplicationName=p4_v15_flyway_probe",
)
FLYWAY_APP = "p4_v15_flyway_probe"
SNAPSHOT_APP = "p4_v15_old_snapshot"
RESULT = ROOT / "experiments/results/p4_flyway_populated_concurrency_2026-11-23.json"


def psql_command(application: str, role: str, sql: str) -> list[str]:
    """Run a short SQL statement in the selected P4 container and named probe database."""
    return [
        "docker", "exec", "-e", f"PGAPPNAME={application}", CONTAINER,
        "psql", "-X", "-A", "-t", "-F", "|", "-v", "ON_ERROR_STOP=1",
        "-U", role, "-d", DATABASE, "-c", sql,
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


def wait_for_snapshot(timeout_seconds: float = 20.0) -> list[str]:
    """Require the deliberately open read transaction to own an older snapshot."""
    deadline = time.monotonic() + timeout_seconds
    query = f"""
        SELECT pid::text, state, COALESCE(wait_event_type, ''), COALESCE(wait_event, ''),
               xact_start IS NOT NULL
        FROM pg_stat_activity
        WHERE datname = current_database() AND application_name = '{SNAPSHOT_APP}'
    """
    while time.monotonic() < deadline:
        rows = run_sql("p4_v15_observer", "reward_bootstrap", query)
        fields = rows.split("|") if rows else []
        if len(fields) == 5 and fields[1] == "active" and fields[4] == "t":
            return fields
        time.sleep(0.2)
    raise TimeoutError("the disposable old-snapshot session did not become active")


def wait_for_concurrent_build(snapshot_pid: int, timeout_seconds: float = 90.0) -> list[str]:
    """Wait for the all-source created_at index build to link to the open snapshot PID."""
    deadline = time.monotonic() + timeout_seconds
    last_activity: list[list[str]] = []
    query = f"""
        SELECT a.pid::text, a.application_name, a.state,
               COALESCE(a.wait_event_type, ''), COALESCE(a.wait_event, ''),
               c.relname, p.phase,
               regexp_replace(left(a.query, 1000), E'[\\r\\n|]+', ' ', 'g'),
               array_to_string(pg_blocking_pids(a.pid), ',')
        FROM pg_stat_activity a
        JOIN pg_stat_progress_create_index p ON p.pid = a.pid
        JOIN pg_catalog.pg_class c ON c.oid = p.index_relid
        WHERE a.datname = current_database()
          AND a.pid <> pg_backend_pid()
          AND a.application_name = '{FLYWAY_APP}'
          AND c.relname = 'wallet_ledger_any_source_created_idx'
    """
    while time.monotonic() < deadline:
        rows = run_sql("p4_v15_observer", "reward_bootstrap", query)
        activities = [line.split("|", 8) for line in rows.splitlines() if line]
        if activities:
            last_activity = activities
        for fields in activities:
            if (
                len(fields) == 9
                and fields[2] == "active"
                and fields[3:5] == ["Lock", "virtualxid"]
                and fields[5] == "wallet_ledger_any_source_created_idx"
                and "old snapshots" in fields[6]
                and str(snapshot_pid) in fields[8].split(",")
            ):
                return fields
        time.sleep(0.2)
    raise TimeoutError(
        "Flyway V15 did not show the open snapshot PID in pg_blocking_pids() "
        "during the all-source created_at CREATE INDEX CONCURRENTLY / Lock virtualxid; "
        f"last observed sessions={last_activity!r}"
    )


def main() -> int:
    if not JAVA_CP:
        raise RuntimeError("set P4_FLYWAY_JAVA_CLASSPATH to the Maven/Flyway runtime classpath")
    if not (HELPER_DIR / "P4FlywayMaintenance.class").exists():
        raise RuntimeError("compile experiments/P4FlywayMaintenance.java into P4_FLYWAY_HELPER_DIR first")
    if f"/{DATABASE}?" not in FLYWAY_URL:
        raise RuntimeError(f"Flyway URL must point to the named disposable database {DATABASE}")

    preflight = run_sql(
        "p4_v15_preflight",
        "reward_bootstrap",
        "SELECT current_database(), (SELECT count(*) FROM reward.wallet_ledger), "
        "(SELECT count(*) FROM reward.wallet_ledger WHERE source_type='EVENT'), "
        "(SELECT count(*) FROM reward.wallet_ledger WHERE source_type='COMPENSATION'), "
        "(SELECT count(*) FROM reward.flyway_schema_history WHERE version='15'), "
        "(SELECT count(*) FROM pg_catalog.pg_index i "
        " JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid "
        " WHERE c.relname IN ('wallet_ledger_compensation_cursor_idx', "
        "'wallet_ledger_compensation_created_idx', 'wallet_ledger_any_source_created_idx')), "
        "(SELECT count(*) FROM reward.wallets w "
        " LEFT JOIN (SELECT user_id, "
        "SUM(amount) FILTER (WHERE currency_type='MONEY') AS money, "
        "SUM(amount) FILTER (WHERE currency_type='POINT') AS points "
        "FROM reward.wallet_ledger GROUP BY user_id) l USING (user_id) "
        "WHERE w.money_balance <> COALESCE(l.money, 0) "
        "OR w.point_balance <> COALESCE(l.points, 0)), "
        "(SELECT count(*) FROM (SELECT DISTINCT l.user_id "
        "FROM reward.wallet_ledger l LEFT JOIN reward.wallets w USING (user_id) "
        "WHERE w.user_id IS NULL) missing_wallets)",
    ).split("|")
    expected_preflight = [DATABASE, "1000000", "990000", "10000", "0", "0", "0", "0"]
    if preflight != expected_preflight:
        raise RuntimeError(
            "probe requires 1M ledger rows (990K EVENT, 10K COMPENSATION), "
            f"zero wallet-ledger mismatches, and no V15 objects: {preflight}"
        )

    # Build the first two V15 indexes before opening the long snapshot. This stages
    # Flyway so the experiment can isolate its final all-source index build.
    staged_indexes = []
    for index_name, create_sql in (
        (
            "wallet_ledger_compensation_cursor_idx",
            "CREATE INDEX CONCURRENTLY wallet_ledger_compensation_cursor_idx "
            "ON reward.wallet_ledger (id DESC) WHERE source_type='COMPENSATION'",
        ),
        (
            "wallet_ledger_compensation_created_idx",
            "CREATE INDEX CONCURRENTLY wallet_ledger_compensation_created_idx "
            "ON reward.wallet_ledger (created_at DESC, id DESC) WHERE source_type='COMPENSATION'",
        ),
    ):
        result = run_sql(
            f"p4_v15_stage_{index_name}",
            "reward_migration",
            create_sql,
        )
        if result != "CREATE INDEX":
            raise RuntimeError(f"could not stage {index_name} before Flyway: {result!r}")
        staged_indexes.append({"name": index_name, "result": result})

    staged_catalog = run_sql(
        "p4_v15_verify_staged_indexes",
        "reward_bootstrap",
        "SELECT c.relname||'|'||i.indisready::text||'|'||i.indisvalid::text "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_index i ON i.indexrelid=c.oid "
        "WHERE c.relnamespace='reward'::regnamespace AND c.relname IN "
        "('wallet_ledger_compensation_cursor_idx','wallet_ledger_compensation_created_idx') "
        "ORDER BY c.relname",
    )
    staged_rows = staged_catalog.splitlines()
    if len(staged_rows) != 2 or any("|true|true" not in row for row in staged_rows):
        raise RuntimeError(f"staged V15 indexes are not ready/valid: {staged_rows!r}")

    env = {**os.environ, "DOCKER_HOST": DOCKER_HOST}
    snapshot = subprocess.Popen(
        psql_command(
            SNAPSHOT_APP,
            "reward_migration",
            "BEGIN; SELECT pg_sleep(180); ROLLBACK;",
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    migration = None
    test_ledger_id = None
    original_created_at = None
    test_row_updated = False
    try:
        snapshot_state = wait_for_snapshot()
        migration_env = {
            **env,
            "P4_FLYWAY_URL": FLYWAY_URL,
            "P4_FLYWAY_HELPER_DIR": str(HELPER_DIR),
            "P4_FLYWAY_JAVA_CLASSPATH": JAVA_CP,
        }
        started = time.monotonic()
        migration = subprocess.Popen(
            ["java", "-cp", f"{HELPER_DIR}:{JAVA_CP}", "P4FlywayMaintenance", "migrate"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=migration_env,
        )

        snapshot_pid = int(snapshot_state[0])
        build_wait = wait_for_concurrent_build(snapshot_pid)
        test_row = run_sql(
            "p4_v15_select_compensation_ledger_row",
            "reward_bootstrap",
            "SELECT id||'|'||created_at::text FROM reward.wallet_ledger "
            "WHERE user_id=100001 AND source_type='COMPENSATION'",
        )
        test_row_fields = test_row.split("|", 1)
        if len(test_row_fields) != 2 or not test_row_fields[0].isdigit():
            raise RuntimeError(f"could not select one seeded COMPENSATION ledger row: {test_row!r}")
        test_ledger_id = int(test_row_fields[0])
        original_created_at = test_row_fields[1]
        dml_started = time.monotonic()
        dml_output = run_sql(
            "p4_v15_writer_during_build",
            "reward_migration",
            "UPDATE reward.wallet_ledger "
            "SET created_at=TIMESTAMPTZ '2100-01-01 00:00:00+00' "
            f"WHERE id={test_ledger_id} AND source_type='COMPENSATION'",
        )
        dml_elapsed = time.monotonic() - dml_started
        if dml_output.strip() != "UPDATE 1":
            raise RuntimeError(f"writer did not update the indexed COMPENSATION row: {dml_output!r}")
        test_row_updated = True

        blocker_pid = snapshot_pid
        blocker_terminated = run_sql(
            "p4_v15_release_snapshot",
            "reward_bootstrap",
            f"SELECT pg_terminate_backend({blocker_pid})",
        )
        snapshot.communicate(timeout=10)

        flyway_stdout, flyway_stderr = migration.communicate(timeout=180)
        migration_elapsed = time.monotonic() - started
        if migration.returncode != 0:
            raise RuntimeError(
                f"Flyway migrate failed with {migration.returncode}: "
                f"stdout={flyway_stdout!r}; stderr={flyway_stderr!r}"
            )

        history = run_sql(
            "p4_v15_history",
            "reward_bootstrap",
            "SELECT version||'|'||success||'|'||execution_time "
            "FROM reward.flyway_schema_history WHERE version='15'",
        )
        indexes = run_sql(
            "p4_v15_catalog",
            "reward_bootstrap",
            "WITH expected(name, definition) AS (VALUES "
            "('wallet_ledger_compensation_cursor_idx', "
            "'CREATE INDEX wallet_ledger_compensation_cursor_idx ON reward.wallet_ledger USING btree (id DESC) WHERE (source_type = ''COMPENSATION''::text)'), "
            "('wallet_ledger_compensation_created_idx', "
            "'CREATE INDEX wallet_ledger_compensation_created_idx ON reward.wallet_ledger USING btree (created_at DESC, id DESC) WHERE (source_type = ''COMPENSATION''::text)'), "
            "('wallet_ledger_any_source_created_idx', "
            "'CREATE INDEX wallet_ledger_any_source_created_idx ON reward.wallet_ledger USING btree (created_at DESC, id DESC)')) "
            "SELECT e.name||'|'||i.indisready::text||'|'||i.indisvalid::text||'|'||"
            "(pg_catalog.pg_get_indexdef(i.indexrelid)=e.definition)||'|'||"
            "pg_catalog.pg_relation_size(i.indexrelid) "
            "FROM expected e JOIN pg_catalog.pg_class c ON c.relname=e.name "
            "JOIN pg_catalog.pg_index i ON i.indexrelid=c.oid "
            "WHERE c.relnamespace='reward'::regnamespace ORDER BY e.name",
        )
        if not history.startswith("15|true|"):
            raise RuntimeError(f"V15 history row is not successful: {history!r}")
        index_rows = indexes.splitlines()
        if len(index_rows) != 3 or any("|true|true|true|" not in row for row in index_rows):
            raise RuntimeError(f"V15 index definitions or catalog flags differ: {index_rows!r}")

        updated_timestamp_plan = run_sql(
            "p4_v15_updated_timestamp_index_plan",
            "reward_bootstrap",
            "SET enable_seqscan=off; SET enable_bitmapscan=off; "
            "EXPLAIN (COSTS OFF) SELECT id FROM reward.wallet_ledger "
            "WHERE created_at=TIMESTAMPTZ '2100-01-01 00:00:00+00' "
            "ORDER BY created_at DESC,id DESC LIMIT 1",
        )
        updated_timestamp_result = run_sql(
            "p4_v15_updated_timestamp_index_result",
            "reward_bootstrap",
            "SET enable_seqscan=off; SET enable_bitmapscan=off; "
            "SELECT id FROM reward.wallet_ledger "
            "WHERE created_at=TIMESTAMPTZ '2100-01-01 00:00:00+00' "
            "ORDER BY created_at DESC,id DESC LIMIT 1",
        )
        updated_timestamp_id = updated_timestamp_result.splitlines()[-1].strip()
        if "wallet_ledger_any_source_created_idx" not in updated_timestamp_plan:
            raise RuntimeError(
                "updated created_at lookup did not use the V15 all-source date index: "
                f"{updated_timestamp_plan!r}"
            )
        if updated_timestamp_id != str(test_ledger_id):
            raise RuntimeError(
                "the index-backed lookup did not return the row updated during index build: "
                f"expected={test_ledger_id}, actual={updated_timestamp_id!r}"
            )

        restore_output = run_sql(
            "p4_v15_restore_concurrent_writer_row",
            "reward_migration",
            f"UPDATE reward.wallet_ledger SET created_at=TIMESTAMPTZ '{original_created_at}' "
            f"WHERE id={test_ledger_id} AND source_type='COMPENSATION'",
        )
        if restore_output.strip() != "UPDATE 1":
            raise RuntimeError(f"could not restore the probe fixture after the index check: {restore_output!r}")
        test_row_updated = False
        cleanup_state = run_sql(
            "p4_v15_verify_writer_cleanup",
            "reward_bootstrap",
            f"SELECT created_at::text FROM reward.wallet_ledger WHERE id={test_ledger_id}",
        )
        if cleanup_state != original_created_at:
            raise RuntimeError(f"concurrent-writer fixture timestamp differs after restore: {cleanup_state!r}")
        post_cleanup_reconciliation = run_sql(
            "p4_v15_post_cleanup_reconciliation",
            "reward_bootstrap",
            "SELECT (SELECT count(*) FROM reward.wallets w "
            "LEFT JOIN (SELECT user_id, "
            "SUM(amount) FILTER (WHERE currency_type='MONEY') AS money, "
            "SUM(amount) FILTER (WHERE currency_type='POINT') AS points "
            "FROM reward.wallet_ledger GROUP BY user_id) l USING (user_id) "
            "WHERE w.money_balance <> COALESCE(l.money, 0) "
            "OR w.point_balance <> COALESCE(l.points, 0))||'|'||"
            "(SELECT count(*) FROM (SELECT DISTINCT l.user_id "
            "FROM reward.wallet_ledger l LEFT JOIN reward.wallets w USING (user_id) "
            "WHERE w.user_id IS NULL) missing_wallets)",
        ).split("|")
        if post_cleanup_reconciliation != ["0", "0"]:
            raise RuntimeError(
                "post-cleanup wallet-ledger reconciliation differs: "
                f"{post_cleanup_reconciliation!r}"
            )

        record = {
            "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
            "database": DATABASE,
            "ledgerRows": int(preflight[1]),
            "sourceRows": {"EVENT": int(preflight[2]), "COMPENSATION": int(preflight[3])},
            "walletLedgerMismatches": int(preflight[6]),
            "ledgerUsersWithoutWallet": int(preflight[7]),
            "postCleanupReconciliation": {
                "walletLedgerMismatches": int(post_cleanup_reconciliation[0]),
                "ledgerUsersWithoutWallet": int(post_cleanup_reconciliation[1]),
            },
            "stagedIndexesBeforeFlyway": {
                "builds": staged_indexes,
                "catalog": staged_rows,
            },
            "flywayApplication": FLYWAY_APP,
            "oldSnapshotSession": {
                "application": SNAPSHOT_APP,
                "pid": blocker_pid,
                "state": snapshot_state[1],
                "waitEventType": snapshot_state[2],
                "waitEvent": snapshot_state[3],
            },
            "flywayConcurrentBuildWait": {
                "pid": int(build_wait[0]),
                "state": build_wait[2],
                "waitEventType": build_wait[3],
                "waitEvent": build_wait[4],
                "indexName": build_wait[5],
                "progressPhase": build_wait[6],
                "query": build_wait[7],
                "blockingPids": [int(pid) for pid in build_wait[8].split(",") if pid],
                "snapshotPid": snapshot_pid,
                "snapshotIsDirectBlocker": True,
            },
            "writerDuringBuild": {
                "sql": "UPDATE reward.wallet_ledger SET created_at='2100-01-01 00:00:00+00' WHERE id=<seeded COMPENSATION row>",
                "result": dml_output,
                "clientElapsedSeconds": round(dml_elapsed, 6),
                "completedWhileFlywayWaitedOnVirtualxid": True,
                "updatedIndexedKey": "wallet_ledger_any_source_created_idx.created_at",
                "updatedLedgerId": test_ledger_id,
                "temporaryCreatedAt": "2100-01-01 00:00:00+00",
                "originalCreatedAt": original_created_at,
                "fixtureCleanup": restore_output,
                "fixtureStateAfterCleanup": cleanup_state,
            },
            "snapshotRelease": blocker_terminated,
            "flywayMigrate": {
                "exitCode": migration.returncode,
                "migrationsExecuted": 1,
                "clientElapsedSeconds": round(migration_elapsed, 3),
                "stdout": flyway_stdout.strip(),
                "stderr": flyway_stderr.strip(),
            },
            "schemaHistoryV15": history,
            "indexes": index_rows,
            "indexBackedUpdatedTimestampLookup": {
                "plan": updated_timestamp_plan,
                "returnedId": int(updated_timestamp_id),
                "matchesUpdatedRow": True,
            },
        }
        RESULT.parent.mkdir(parents=True, exist_ok=True)
        RESULT.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0
    finally:
        if snapshot.poll() is None:
            try:
                run_sql(
                    "p4_v15_emergency_release",
                    "reward_bootstrap",
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    f"WHERE datname=current_database() AND application_name='{SNAPSHOT_APP}'",
                )
            except Exception:
                snapshot.terminate()
            snapshot.communicate(timeout=10)
        if migration is not None and migration.poll() is None:
            migration.terminate()
            try:
                migration.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                migration.kill()
                migration.communicate()
        if test_row_updated and test_ledger_id is not None and original_created_at is not None:
            try:
                run_sql(
                    "p4_v15_restore_writer_timestamp_after_failure",
                    "reward_migration",
                    f"UPDATE reward.wallet_ledger SET created_at=TIMESTAMPTZ '{original_created_at}' "
                    f"WHERE id={test_ledger_id} AND source_type='COMPENSATION'",
                )
            except Exception as cleanup_error:
                print(f"could not restore the disposable ledger timestamp: {cleanup_error}", file=sys.stderr)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"populated Flyway build probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)

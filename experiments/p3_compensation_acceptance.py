#!/usr/bin/env python3
"""rewardp3db에서 CS 보상 procedure 권한과 재시도 결과를 확인한다."""

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid


# 기본값은 loopback에 공개된 P3 실습 스택이며 다른 phase DB와 공유하지 않는다.
API_BASE = os.environ.get("P3_API_BASE", "http://127.0.0.1:8182")
DOCKER_HOST = os.environ.get(
    "DOCKER_HOST", "unix:///private/tmp/reward-colima/active-docker.sock"
)
COMPOSE = [
    "docker", "compose", "-p", "reward-p3", "-f", "compose.p3.yaml",
    "exec", "-T", "db", "psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1",
    "-U", "reward_bootstrap", "-d", "rewardp3db",
]
OPERATOR_USER_ID = 101
EVENT_ID = 5
MONEY_AMOUNT = 2500
POINT_AMOUNT = 500


def psql(sql: str) -> str:
    """P3 bootstrap role runs guarded setup and read-only acceptance queries."""
    completed = subprocess.run(
        [*COMPOSE, "-c", sql],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "DOCKER_HOST": DOCKER_HOST},
    )
    return completed.stdout.strip()


def compensate(payload: dict, operator_user_id: int = OPERATOR_USER_ID) -> tuple[int, dict]:
    """Call the same HTTP path used by the admin UI and preserve non-2xx bodies."""
    request = urllib.request.Request(
        f"{API_BASE}/api/admin/compensations",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "X-User-Id": str(operator_user_id),
            "X-Demo-Role": "ADMIN",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def claim(user_id: int, event_id: int, request_id: str) -> tuple[int, dict]:
    """Call the public CLAIM route with an explicit request UUID."""
    request = urllib.request.Request(
        f"{API_BASE}/api/events/{event_id}/claims",
        headers={"X-User-Id": str(user_id), "Idempotency-Key": request_id},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def reset_user_wallet(user_id: int) -> tuple[int, dict]:
    """Exercise the local single-user reset route through the same reward_app API role."""
    request = urllib.request.Request(
        f"{API_BASE}/api/me/wallet/reset",
        headers={"X-User-Id": str(user_id)},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def verify_app_cannot_set_migration_role() -> bool:
    """Attempt SET ROLE through a real reward_app database login without printing its password."""
    command = [
        "docker", "compose", "-p", "reward-p3", "-f", "compose.p3.yaml",
        "exec", "-T", "db", "sh", "-eu", "-c",
        "PGPASSWORD=\"$DB_APP_PASSWORD\" psql -X -v ON_ERROR_STOP=1 "
        "-h 127.0.0.1 -U reward_app -d rewardp3db "
        "-c 'SET ROLE reward_migration'",
    ]
    attempt = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env={**os.environ, "DOCKER_HOST": DOCKER_HOST},
    )
    if attempt.returncode == 0 or "permission denied to set role" not in attempt.stderr:
        raise RuntimeError(
            "reward_app SET ROLE check returned an unexpected result: "
            f"exit={attempt.returncode}, stderr={attempt.stderr.strip()}"
        )
    return True


def psql_transaction(application_name: str, sql: str) -> subprocess.Popen:
    """Keep a real reward_app CALL uncommitted so a second unique-key insert must wait."""
    process = subprocess.Popen(
        [*COMPOSE],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env={**os.environ, "DOCKER_HOST": DOCKER_HOST},
    )
    process.stdin.write(
        f"SET application_name = '{application_name}';\n"
        f"BEGIN;\nSET LOCAL ROLE reward_app;\n{sql}\n"
    )
    process.stdin.flush()
    return process


def finish_psql_transaction(process: subprocess.Popen, command: str = "COMMIT") -> str:
    """Finish the held transaction and surface any psql error instead of hiding it."""
    if process.poll() is None:
        process.stdin.write(f"{command};\n\\q\n")
        process.stdin.flush()
        process.stdin.close()
    output = process.stdout.read()
    return_code = process.wait(timeout=15)
    if return_code != 0:
        raise RuntimeError(f"concurrent compensation SQL failed: {output.strip()}")
    return output.strip()


def wait_for_condition(sql: str, description: str, timeout: float = 12) -> str:
    """Poll PostgreSQL's live activity view until the intended wait is observable."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        observed = psql(sql)
        if observed == "1":
            return observed
        time.sleep(0.05)
    raise TimeoutError(f"PostgreSQL did not expose {description}")


def concurrent_compensation_race() -> dict:
    """Verify different UUIDs for one incident/user rely safely on the business UNIQUE key."""
    user_id = 1_700_000_000_000 + time.time_ns() % 1_000_000_000
    incident_reference = f"P3-RACE-{uuid.uuid4().hex}"
    request_a = str(uuid.uuid4())
    request_b = str(uuid.uuid4())
    money_amount = 3300
    point_amount = 700
    psql(f"""
        DO $guard$
        BEGIN
            IF EXISTS (SELECT 1 FROM reward.users WHERE id = {user_id}) THEN
                RAISE EXCEPTION 'race user id already exists';
            END IF;
        END;
        $guard$;
        INSERT INTO reward.users(id, nickname) VALUES ({user_id}, 'p3-v14-comp-race');
        INSERT INTO reward.wallets(user_id) VALUES ({user_id});
    """)

    call_a = f"""
        CALL reward.grant_compensation(
            {user_id}, {EVENT_ID}, '{request_a}', '{incident_reference}',
            'same incident concurrent writer', {OPERATOR_USER_ID},
            {money_amount}, {point_amount}, NULL::text, NULL::bigint
        );
    """
    held = psql_transaction("p3_compensation_race_a", call_a)
    waiter = None
    try:
        wait_for_condition("""
            SELECT CASE WHEN count(*) = 1 THEN 1 ELSE 0 END
            FROM pg_stat_activity
            WHERE application_name = 'p3_compensation_race_a'
              AND state = 'idle in transaction' AND xact_start IS NOT NULL
        """, "first compensation CALL held before COMMIT")

        payload = {
            "userId": user_id,
            "eventId": EVENT_ID,
            "requestId": request_b,
            "incidentReference": incident_reference,
            "reason": "same incident concurrent writer",
            "moneyAmount": money_amount,
            "pointAmount": point_amount,
        }
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            waiter = executor.submit(compensate, payload)
            wait_for_condition("""
                SELECT CASE WHEN count(*) = 1 THEN 1 ELSE 0 END
                FROM pg_stat_activity w
                WHERE w.datname = current_database()
                  AND w.wait_event_type = 'Lock'
                  AND w.wait_event = 'transactionid'
                  AND w.query ILIKE '%grant_compensation%'
                  AND EXISTS (
                      SELECT 1 FROM pg_stat_activity a
                      WHERE a.application_name = 'p3_compensation_race_a'
                        AND a.pid = ANY(pg_blocking_pids(w.pid))
                  )
            """, "second CS request waiting on the unique-key transaction")
            finish_psql_transaction(held)
            status, response = waiter.result(timeout=15)

        if status != 200 or response.get("result") != "ALREADY_COMPENSATED":
            raise RuntimeError(
                "different request IDs for one concurrent incident did not resolve "
                f"to ALREADY_COMPENSATED: HTTP {status}, {response}"
            )

    finally:
        if held.poll() is None:
            try:
                finish_psql_transaction(held, "ROLLBACK")
            except Exception:
                held.kill()
                held.wait(timeout=5)

    reconciliation = psql(f"""
        SELECT
            (SELECT count(*) FROM reward.compensations
             WHERE user_id = {user_id} AND incident_reference = '{incident_reference}'),
            (SELECT count(*) FROM reward.wallet_ledger l
             JOIN reward.compensations c ON c.id = l.compensation_id
             WHERE c.user_id = {user_id} AND c.incident_reference = '{incident_reference}'),
            w.money_balance, w.point_balance,
            COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'MONEY'), 0),
            COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'POINT'), 0)
        FROM reward.wallets w
        LEFT JOIN reward.wallet_ledger l ON l.user_id = w.user_id
        WHERE w.user_id = {user_id}
        GROUP BY w.user_id, w.money_balance, w.point_balance;
    """)
    row = reconciliation.splitlines()[-1].split("|")
    expected = ["1", "2", str(money_amount), str(point_amount),
                str(money_amount), str(point_amount)]
    if row != expected:
        raise RuntimeError(f"concurrent compensation does not reconcile: {row}")

    cleanup_status, cleanup = reset_user_wallet(user_id)
    if cleanup_status != 200 or cleanup.get("usersReset") != 1 \
            or cleanup.get("compensationsDeleted") != 1 \
            or cleanup.get("ledgerRowsDeleted") != 2:
        raise RuntimeError(f"concurrent compensation test cleanup failed: {cleanup}")

    return {
        "waitEvent": "transactionid",
        "winner": "PAID",
        "secondRequest": response,
        "httpStatus": status,
        "reconciliation": row,
        "cleanup": cleanup,
    }


def same_request_id_conflict_race() -> dict:
    """Verify one request UUID cannot pay a different user while the winner is uncommitted."""
    user_a = 1_700_000_000_000 + time.time_ns() % 1_000_000_000
    user_b = user_a + 1
    request_id = str(uuid.uuid4())
    incident_a = f"P3-KEY-A-{uuid.uuid4().hex}"
    incident_b = f"P3-KEY-B-{uuid.uuid4().hex}"
    amount_a = (4400, 300)
    amount_b = (9900, 900)
    psql(f"""
        DO $guard$
        BEGIN
            IF EXISTS (SELECT 1 FROM reward.users WHERE id IN ({user_a}, {user_b})) THEN
                RAISE EXCEPTION 'same-key race user id already exists';
            END IF;
        END;
        $guard$;
        INSERT INTO reward.users(id, nickname)
        VALUES ({user_a}, 'p3-v14-key-race-a'), ({user_b}, 'p3-v14-key-race-b');
        INSERT INTO reward.wallets(user_id) VALUES ({user_a}), ({user_b});
    """)

    call_a = f"""
        CALL reward.grant_compensation(
            {user_a}, {EVENT_ID}, '{request_id}', '{incident_a}',
            'request UUID owner payload', {OPERATOR_USER_ID},
            {amount_a[0]}, {amount_a[1]}, NULL::text, NULL::bigint
        );
    """
    held = psql_transaction("p3_compensation_key_race_a", call_a)
    try:
        wait_for_condition("""
            SELECT CASE WHEN count(*) = 1 THEN 1 ELSE 0 END
            FROM pg_stat_activity
            WHERE application_name = 'p3_compensation_key_race_a'
              AND state = 'idle in transaction' AND xact_start IS NOT NULL
        """, "first request UUID owner held before COMMIT")

        payload = {
            "userId": user_b,
            "eventId": EVENT_ID,
            "requestId": request_id,
            "incidentReference": incident_b,
            "reason": "conflicting user and incident payload",
            "moneyAmount": amount_b[0],
            "pointAmount": amount_b[1],
        }
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            waiter = executor.submit(compensate, payload)
            wait_for_condition("""
                SELECT CASE WHEN count(*) = 1 THEN 1 ELSE 0 END
                FROM pg_stat_activity w
                WHERE w.datname = current_database()
                  AND w.wait_event_type = 'Lock'
                  AND w.wait_event = 'advisory'
                  AND w.query ILIKE '%grant_compensation%'
                  AND EXISTS (
                      SELECT 1 FROM pg_stat_activity a
                      WHERE a.application_name = 'p3_compensation_key_race_a'
                        AND a.pid = ANY(pg_blocking_pids(w.pid))
                  )
            """, "same request UUID waiting on transaction advisory lock")
            finish_psql_transaction(held)
            status, response = waiter.result(timeout=15)

        if status != 409:
            raise RuntimeError(
                f"same request UUID reuse should be HTTP 409, got HTTP {status}: {response}"
            )

    finally:
        if held.poll() is None:
            try:
                finish_psql_transaction(held, "ROLLBACK")
            except Exception:
                held.kill()
                held.wait(timeout=5)

    reconciliation = psql(f"""
        SELECT
            (SELECT count(*) FROM reward.compensations WHERE request_id = '{request_id}'),
            (SELECT count(*) FROM reward.compensations
             WHERE user_id = {user_a} AND incident_reference = '{incident_a}'),
            (SELECT count(*) FROM reward.compensations
             WHERE user_id = {user_b} AND incident_reference = '{incident_b}'),
            (SELECT count(*) FROM reward.wallet_ledger l
             JOIN reward.compensations c ON c.id = l.compensation_id
             WHERE c.user_id = {user_a} AND c.incident_reference = '{incident_a}'),
            (SELECT count(*) FROM reward.wallet_ledger l
             JOIN reward.compensations c ON c.id = l.compensation_id
             WHERE c.user_id = {user_b} AND c.incident_reference = '{incident_b}'),
            (SELECT money_balance FROM reward.wallets WHERE user_id = {user_a}),
            (SELECT point_balance FROM reward.wallets WHERE user_id = {user_a}),
            (SELECT money_balance FROM reward.wallets WHERE user_id = {user_b}),
            (SELECT point_balance FROM reward.wallets WHERE user_id = {user_b});
    """)
    row = reconciliation.splitlines()[-1].split("|")
    expected = ["1", "1", "0", "2", "0", str(amount_a[0]),
                str(amount_a[1]), "0", "0"]
    if row != expected:
        raise RuntimeError(f"same request UUID conflict changed unexpected data: {row}")

    cleanup_results = []
    for test_user_id in (user_a, user_b):
        cleanup_status, cleanup = reset_user_wallet(test_user_id)
        if cleanup_status != 200 or cleanup.get("usersReset") != 1:
            raise RuntimeError(f"same-key race test cleanup failed: {cleanup}")
        cleanup_results.append(cleanup)

    return {
        "waitEvent": "advisory",
        "winner": "PAID",
        "conflictingRequestHttpStatus": status,
        "reconciliation": row,
        "cleanup": cleanup_results,
    }


def cross_operation_request_id_conflict() -> dict:
    """A UUID can identify one payout operation across CLAIM and compensation."""
    user_id = 1_700_000_000_000 + time.time_ns() % 1_000_000_000
    reverse_user_id = user_id + 1
    claim_request_id = str(uuid.uuid4())
    compensation_request_id = str(uuid.uuid4())
    claim_event_id = int(psql("""
        SELECT id FROM reward.events
        WHERE mode = 'CLAIM' AND status = 'OPEN'
          AND starts_at <= clock_timestamp() AND ends_at > clock_timestamp()
        ORDER BY id LIMIT 1;
    """))
    psql(f"""
        DO $guard$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM reward.users WHERE id IN ({user_id}, {reverse_user_id})
            ) THEN
                RAISE EXCEPTION 'cross-operation test user id already exists';
            END IF;
        END;
        $guard$;
        INSERT INTO reward.users(id, nickname) VALUES
            ({user_id}, 'p3-claim-first-key'),
            ({reverse_user_id}, 'p3-compensation-first-key');
        INSERT INTO reward.wallets(user_id) VALUES ({user_id}), ({reverse_user_id});
    """)

    claim_status, claim_response = claim(user_id, claim_event_id, claim_request_id)
    if claim_status != 200 or claim_response.get("result") != "PAID":
        raise RuntimeError(f"first CLAIM should pay once, got HTTP {claim_status}: {claim_response}")

    compensation_payload = {
        "userId": user_id,
        "eventId": EVENT_ID,
        "requestId": claim_request_id,
        "incidentReference": f"P3-CROSS-{uuid.uuid4().hex}",
        "reason": "전역 요청 UUID 범위 충돌 확인",
        "moneyAmount": 1700,
        "pointAmount": 300,
    }
    compensation_status, compensation_response = compensate(compensation_payload)
    if compensation_status != 409:
        raise RuntimeError(
            "a CLAIM request UUID reused for compensation must return HTTP 409, "
            f"got HTTP {compensation_status}: {compensation_response}"
        )

    reconciliation = psql(f"""
        SELECT
            (SELECT count(*) FROM reward.reward_grants WHERE request_id = '{claim_request_id}'),
            (SELECT count(*) FROM reward.compensations WHERE request_id = '{claim_request_id}'),
            (SELECT count(*) FROM reward.wallet_ledger WHERE user_id = {user_id}),
            (SELECT money_balance FROM reward.wallets WHERE user_id = {user_id}),
            (SELECT point_balance FROM reward.wallets WHERE user_id = {user_id}),
            COALESCE((SELECT SUM(amount) FROM reward.wallet_ledger
                      WHERE user_id = {user_id} AND currency_type = 'MONEY'), 0),
            COALESCE((SELECT SUM(amount) FROM reward.wallet_ledger
                      WHERE user_id = {user_id} AND currency_type = 'POINT'), 0);
    """).splitlines()[-1].split("|")
    grant = psql(f"""
        SELECT money_awarded, points_awarded
        FROM reward.reward_grants WHERE request_id = '{claim_request_id}';
    """).splitlines()[-1].split("|")
    expected = ["1", "0", "2", *grant, *grant]
    if len(grant) != 2 or reconciliation != expected:
        raise RuntimeError(f"cross-operation request UUID changed payout data: {reconciliation}")

    reverse_payload = {
        "userId": reverse_user_id,
        "eventId": EVENT_ID,
        "requestId": compensation_request_id,
        "incidentReference": f"P3-CROSS-REVERSE-{uuid.uuid4().hex}",
        "reason": "전역 요청 UUID 역방향 충돌 확인",
        "moneyAmount": 1800,
        "pointAmount": 350,
    }
    reverse_compensation_status, reverse_compensation = compensate(reverse_payload)
    reverse_claim_status, reverse_claim = claim(
        reverse_user_id, claim_event_id, compensation_request_id
    )
    if reverse_compensation_status != 200 or reverse_compensation.get("result") != "PAID":
        raise RuntimeError(
            "first compensation should pay once, got "
            f"HTTP {reverse_compensation_status}: {reverse_compensation}"
        )
    if reverse_claim_status != 409:
        raise RuntimeError(
            "a compensation request UUID reused for CLAIM must return HTTP 409, "
            f"got HTTP {reverse_claim_status}: {reverse_claim}"
        )

    reverse_reconciliation = psql(f"""
        SELECT
            (SELECT count(*) FROM reward.reward_grants
             WHERE request_id = '{compensation_request_id}'),
            (SELECT count(*) FROM reward.compensations
             WHERE request_id = '{compensation_request_id}'),
            (SELECT count(*) FROM reward.wallet_ledger WHERE user_id = {reverse_user_id}),
            (SELECT money_balance FROM reward.wallets WHERE user_id = {reverse_user_id}),
            (SELECT point_balance FROM reward.wallets WHERE user_id = {reverse_user_id}),
            COALESCE((SELECT SUM(amount) FROM reward.wallet_ledger
                      WHERE user_id = {reverse_user_id} AND currency_type = 'MONEY'), 0),
            COALESCE((SELECT SUM(amount) FROM reward.wallet_ledger
                      WHERE user_id = {reverse_user_id} AND currency_type = 'POINT'), 0);
    """).splitlines()[-1].split("|")
    reverse_expected = ["0", "1", "2", "1800", "350", "1800", "350"]
    if reverse_reconciliation != reverse_expected:
        raise RuntimeError(
            f"reverse cross-operation request UUID changed payout data: {reverse_reconciliation}"
        )

    cleanup = []
    for test_user_id, expected_grants, expected_compensations in (
        (user_id, 1, 0), (reverse_user_id, 0, 1)
    ):
        cleanup_status, cleanup_response = reset_user_wallet(test_user_id)
        if cleanup_status != 200 or cleanup_response.get("usersReset") != 1 \
                or cleanup_response.get("grantsDeleted") != expected_grants \
                or cleanup_response.get("compensationsDeleted") != expected_compensations \
                or cleanup_response.get("ledgerRowsDeleted") != 2:
            raise RuntimeError(f"cross-operation acceptance cleanup failed: {cleanup_response}")
        cleanup.append(cleanup_response)

    return {
        "claimThenCompensation": {
            "requestId": claim_request_id,
            "claimHttpStatus": claim_status,
            "claimResult": claim_response.get("result"),
            "compensationHttpStatus": compensation_status,
            "compensationResponse": compensation_response,
            "reconciliationBeforeCleanup": reconciliation,
        },
        "compensationThenClaim": {
            "requestId": compensation_request_id,
            "compensationHttpStatus": reverse_compensation_status,
            "compensationResult": reverse_compensation.get("result"),
            "claimHttpStatus": reverse_claim_status,
            "claimResponse": reverse_claim,
            "reconciliationBeforeCleanup": reverse_reconciliation,
        },
        "cleanup": cleanup,
    }


def mixed_cross_operation_request_id_race(first_operation: str) -> dict:
    """Hold one payout transaction while the opposite API procedure requests the same UUID."""
    if first_operation not in {"CLAIM", "COMPENSATION"}:
        raise ValueError(f"unsupported first operation: {first_operation}")

    user_id = 1_700_000_000_000 + time.time_ns() % 1_000_000_000
    request_id = str(uuid.uuid4())
    incident_reference = f"P3-MIXED-{first_operation}-{uuid.uuid4().hex}"
    application_name = f"p3_mixed_{first_operation.lower()}_owner"
    claim_event_id = int(psql("""
        SELECT id FROM reward.events
        WHERE mode = 'CLAIM' AND status = 'OPEN'
          AND starts_at <= clock_timestamp() AND ends_at > clock_timestamp()
        ORDER BY id LIMIT 1;
    """))
    psql(f"""
        DO $guard$
        BEGIN
            IF EXISTS (SELECT 1 FROM reward.users WHERE id = {user_id}) THEN
                RAISE EXCEPTION 'mixed-operation race user id already exists';
            END IF;
        END;
        $guard$;
        INSERT INTO reward.users(id, nickname)
        VALUES ({user_id}, 'p3-mixed-{first_operation.lower()}');
        INSERT INTO reward.wallets(user_id) VALUES ({user_id});
    """)

    if first_operation == "CLAIM":
        held_call = f"""
            CALL reward.grant_event_reward(
                {claim_event_id}, {user_id}, '{request_id}', 'CLAIM',
                NULL::text, NULL::bigint
            );
        """
        opposite_query = "%grant_compensation%"
        first_money = None
        first_points = None
    else:
        first_money = 1800
        first_points = 350
        held_call = f"""
            CALL reward.grant_compensation(
                {user_id}, {EVENT_ID}, '{request_id}', '{incident_reference}',
                'mixed payout request lock', {OPERATOR_USER_ID},
                {first_money}, {first_points}, NULL::text, NULL::bigint
            );
        """
        opposite_query = "%grant_event_reward%"

    held = psql_transaction(application_name, held_call)
    waiter = None
    try:
        wait_for_condition(f"""
            SELECT CASE WHEN count(*) = 1 THEN 1 ELSE 0 END
            FROM pg_stat_activity
            WHERE application_name = '{application_name}'
              AND state = 'idle in transaction' AND xact_start IS NOT NULL
        """, f"{first_operation} procedure held before COMMIT")

        def opposing_call():
            if first_operation == "CLAIM":
                return compensate({
                    "userId": user_id,
                    "eventId": EVENT_ID,
                    "requestId": request_id,
                    "incidentReference": incident_reference,
                    "reason": "mixed payout request lock",
                    "moneyAmount": 1800,
                    "pointAmount": 350,
                })
            return claim(user_id, claim_event_id, request_id)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            waiter = executor.submit(opposing_call)
            wait_for_condition(f"""
                SELECT CASE WHEN count(*) = 1 THEN 1 ELSE 0 END
                FROM pg_stat_activity w
                WHERE w.datname = current_database()
                  AND w.wait_event_type = 'Lock'
                  AND w.wait_event = 'advisory'
                  AND w.query ILIKE '{opposite_query}'
                  AND EXISTS (
                      SELECT 1 FROM pg_stat_activity a
                      WHERE a.application_name = '{application_name}'
                        AND a.pid = ANY(pg_blocking_pids(w.pid))
                  )
            """, f"opposite {first_operation} payout blocked on the shared advisory lock")
            blocker_pair = psql(f"""
                SELECT w.pid || '<-' || a.pid
                FROM pg_stat_activity w
                JOIN pg_stat_activity a ON a.application_name = '{application_name}'
                  AND a.pid = ANY(pg_blocking_pids(w.pid))
                WHERE w.datname = current_database()
                  AND w.wait_event_type = 'Lock'
                  AND w.wait_event = 'advisory'
                  AND w.query ILIKE '{opposite_query}';
            """).splitlines()[-1]
            if "<-" not in blocker_pair:
                raise RuntimeError(f"mixed payout lock blocker PIDs were not captured: {blocker_pair}")

            finish_psql_transaction(held)
            response_status, response = waiter.result(timeout=15)

        if response_status != 409:
            raise RuntimeError(
                "opposite payout must return HTTP 409 after the first operation commits, "
                f"got HTTP {response_status}: {response}"
            )

    finally:
        if held.poll() is None:
            try:
                finish_psql_transaction(held, "ROLLBACK")
            except Exception:
                held.kill()
                held.wait(timeout=5)

    reconciliation = psql(f"""
        SELECT
            (SELECT count(*) FROM reward.reward_grants WHERE request_id = '{request_id}'),
            (SELECT count(*) FROM reward.compensations WHERE request_id = '{request_id}'),
            (SELECT count(*) FROM reward.wallet_ledger WHERE user_id = {user_id}),
            w.money_balance,
            w.point_balance,
            COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'MONEY'), 0),
            COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'POINT'), 0),
            COALESCE(
                (SELECT g.money_awarded FROM reward.reward_grants g WHERE g.request_id = '{request_id}'),
                (SELECT c.money_awarded FROM reward.compensations c WHERE c.request_id = '{request_id}'),
                0
            ),
            COALESCE(
                (SELECT g.points_awarded FROM reward.reward_grants g WHERE g.request_id = '{request_id}'),
                (SELECT c.points_awarded FROM reward.compensations c WHERE c.request_id = '{request_id}'),
                0
            )
        FROM reward.wallets w
        LEFT JOIN reward.wallet_ledger l ON l.user_id = w.user_id
        WHERE w.user_id = {user_id}
        GROUP BY w.user_id, w.money_balance, w.point_balance;
    """).splitlines()[-1].split("|")
    expected_counts = ["1", "0"] if first_operation == "CLAIM" else ["0", "1"]
    amount_pair = reconciliation[7:9]
    if reconciliation[:3] != [*expected_counts, "2"] \
            or reconciliation[3:5] != amount_pair \
            or reconciliation[5:7] != amount_pair \
            or amount_pair == ["0", "0"]:
        raise RuntimeError(f"mixed payout request race did not reconcile: {reconciliation}")

    cleanup_status, cleanup = reset_user_wallet(user_id)
    expected_grants = 1 if first_operation == "CLAIM" else 0
    expected_compensations = 1 if first_operation == "COMPENSATION" else 0
    if cleanup_status != 200 or cleanup.get("usersReset") != 1 \
            or cleanup.get("grantsDeleted") != expected_grants \
            or cleanup.get("compensationsDeleted") != expected_compensations \
            or cleanup.get("ledgerRowsDeleted") != 2:
        raise RuntimeError(f"mixed-operation race cleanup failed: {cleanup}")

    return {
        "firstOperation": first_operation,
        "requestId": request_id,
        "waitEvent": "advisory",
        "waiterAndBlockerPids": blocker_pair,
        "firstResult": "PAID",
        "oppositeRequestHttpStatus": response_status,
        "oppositeResponse": response,
        "reconciliationBeforeCleanup": reconciliation,
        "cleanup": cleanup,
    }


def main() -> int:
    # P3 is a separate volume; the guard prevents an accidental call against rewarddb.
    if psql("SELECT current_database();") != "rewardp3db":
        raise RuntimeError("refusing to run compensation acceptance outside rewardp3db")

    user_id = 1_700_000_000_000 + time.time_ns() % 1_000_000_000
    psql(f"""
        DO $guard$
        BEGIN
            IF EXISTS (SELECT 1 FROM reward.users WHERE id = {user_id}) THEN
                RAISE EXCEPTION 'acceptance user id already exists';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM reward.events WHERE id = {EVENT_ID}) THEN
                RAISE EXCEPTION 'acceptance event {EVENT_ID} does not exist';
            END IF;
        END;
        $guard$;
        INSERT INTO reward.users(id, nickname) VALUES ({user_id}, 'p3-v14-compensation');
        INSERT INTO reward.wallets(user_id) VALUES ({user_id});
    """)

    request_id = str(uuid.uuid4())
    incident_reference = f"P3-V16-{uuid.uuid4().hex}"
    payload = {
        "userId": user_id,
        "eventId": EVENT_ID,
        "requestId": request_id,
        "incidentReference": incident_reference,
        "reason": "격리 실습에서 보상 procedure 재시도와 권한 경계를 확인",
        "moneyAmount": MONEY_AMOUNT,
        "pointAmount": POINT_AMOUNT,
    }

    # 세 응답은 서로 다른 정책 결과다: 새 지급, 동일 요청 재생, 장애 건 중복 보상.
    first_status, first = compensate(payload)
    replay_status, replay = compensate(payload)
    second_request = {**payload, "requestId": str(uuid.uuid4())}
    duplicate_status, duplicate = compensate(second_request)
    expected = [(first_status, first, "PAID"),
                (replay_status, replay, "REPLAYED"),
                (duplicate_status, duplicate, "ALREADY_COMPENSATED")]
    for status, body, expected_result in expected:
        if status != 200 or body.get("result") != expected_result:
            raise RuntimeError(
                f"expected {expected_result}, got HTTP {status}: {body}"
            )
    if first.get("compensationId") != replay.get("compensationId"):
        raise RuntimeError("same request replay returned a different compensation id")
    if first.get("compensationId") != duplicate.get("compensationId"):
        raise RuntimeError("incident duplicate returned a different compensation id")

    # A different request UUID is not enough to call a business-key conflict a replay.
    changed_payloads = [
        ("reason", {**payload, "requestId": str(uuid.uuid4()), "reason": "변경된 장애 설명"},
         OPERATOR_USER_ID),
        ("operator", {**payload, "requestId": str(uuid.uuid4())}, OPERATOR_USER_ID + 1),
        ("money", {**payload, "requestId": str(uuid.uuid4()), "moneyAmount": MONEY_AMOUNT + 1},
         OPERATOR_USER_ID),
        ("points", {**payload, "requestId": str(uuid.uuid4()), "pointAmount": POINT_AMOUNT + 1},
         OPERATOR_USER_ID),
    ]
    changed_payload_results = []
    for changed_field, changed_payload, operator_id in changed_payloads:
        status, body = compensate(changed_payload, operator_id)
        if status != 409:
            raise RuntimeError(
                f"changed compensation {changed_field} should be HTTP 409, got {status}: {body}"
            )
        changed_payload_results.append({"field": changed_field, "httpStatus": status})

    # API 실행은 reward_app의 EXECUTE 권한도 실제로 사용한다. 여기에 카탈로그·DML을 대조한다.
    reconciliation = psql(f"""
        SELECT
            (SELECT count(*) FROM reward.compensations
             WHERE user_id = {user_id} AND incident_reference = '{incident_reference}') AS compensations,
            (SELECT count(*) FROM reward.wallet_ledger l
             JOIN reward.compensations c ON c.id = l.compensation_id
             WHERE c.user_id = {user_id} AND c.incident_reference = '{incident_reference}') AS ledger_rows,
            w.money_balance,
            w.point_balance,
            COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'MONEY'), 0) AS ledger_money,
            COALESCE(SUM(l.amount) FILTER (WHERE l.currency_type = 'POINT'), 0) AS ledger_points
        FROM reward.wallets w
        LEFT JOIN reward.wallet_ledger l ON l.user_id = w.user_id
        WHERE w.user_id = {user_id}
        GROUP BY w.user_id, w.money_balance, w.point_balance;
    """)
    row = reconciliation.splitlines()[-1].split("|")
    if row != ["1", "2", str(MONEY_AMOUNT), str(POINT_AMOUNT),
               str(MONEY_AMOUNT), str(POINT_AMOUNT)]:
        raise RuntimeError(f"compensation and wallet ledger do not reconcile: {row}")

    # Try the prohibited writes as reward_app in a rolled-back transaction, not only in ACL metadata.
    direct_dml = psql(f"""
        BEGIN;
        SET LOCAL ROLE reward_app;
        DO $direct_dml$
        DECLARE
            wallet_update_denied BOOLEAN := FALSE;
            compensation_insert_denied BOOLEAN := FALSE;
            ledger_delete_denied BOOLEAN := FALSE;
            grant_delete_denied BOOLEAN := FALSE;
        BEGIN
            BEGIN
                UPDATE reward.wallets SET money_balance = money_balance WHERE user_id = {user_id};
            EXCEPTION WHEN insufficient_privilege THEN
                wallet_update_denied := TRUE;
            END;
            BEGIN
                INSERT INTO reward.compensations (
                    event_id, user_id, request_id, incident_reference, reason,
                    operator_user_id, money_awarded, points_awarded
                ) VALUES (
                    {EVENT_ID}, {user_id}, '{uuid.uuid4()}', 'direct-write-check',
                    'permission verification', {OPERATOR_USER_ID}, 1, 0
                );
            EXCEPTION WHEN insufficient_privilege THEN
                compensation_insert_denied := TRUE;
            END;
            BEGIN
                DELETE FROM reward.wallet_ledger WHERE id = -1;
            EXCEPTION WHEN insufficient_privilege THEN
                ledger_delete_denied := TRUE;
            END;
            BEGIN
                DELETE FROM reward.reward_grants WHERE id = -1;
            EXCEPTION WHEN insufficient_privilege THEN
                grant_delete_denied := TRUE;
            END;
            IF NOT wallet_update_denied OR NOT compensation_insert_denied
               OR NOT ledger_delete_denied OR NOT grant_delete_denied THEN
                RAISE EXCEPTION 'reward_app direct DML was not denied';
            END IF;
        END;
        $direct_dml$;
        ROLLBACK;
    """)
    if "DO" not in direct_dml:
        raise RuntimeError(f"direct DML check did not complete: {direct_dml}")

    privilege_check = psql(f"""
        SELECT has_table_privilege('reward_app', 'reward.wallets', 'UPDATE') AS wallet_update,
               has_table_privilege('reward_app', 'reward.compensations', 'INSERT') AS compensation_insert,
               has_function_privilege(
                   'reward_app',
                   'reward.grant_compensation(bigint,bigint,uuid,text,text,bigint,bigint,bigint,text,bigint)',
                   'EXECUTE') AS procedure_execute,
               p.prosecdef,
               pg_catalog.pg_get_userbyid(p.proowner) AS owner_name,
               owner_role.rolsuper AS owner_superuser,
               owner_role.rolcreaterole AS owner_create_role,
               owner_role.rolcreatedb AS owner_create_db,
               owner_role.rolreplication AS owner_replication,
               owner_role.rolbypassrls AS owner_bypass_rls,
               pg_catalog.pg_get_userbyid(n.nspowner) AS schema_owner,
               pg_catalog.pg_has_role('reward_app', p.proowner, 'MEMBER') AS app_owner_member,
               pg_catalog.pg_has_role('reward_app', p.proowner, 'USAGE') AS app_owner_inherit,
               pg_catalog.pg_has_role('reward_app', p.proowner, 'SET') AS app_can_set_owner,
               has_table_privilege(p.proowner, 'reward.wallets', 'UPDATE') AS owner_wallet_update,
               has_table_privilege(p.proowner, 'reward.compensations', 'INSERT') AS owner_comp_insert,
               EXISTS (
                   SELECT 1 FROM pg_catalog.aclexplode(p.proacl) acl
                   WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
               ) AS public_execute,
               (SELECT count(*) FROM pg_catalog.pg_auth_members m
                WHERE m.member = p.proowner) AS owner_memberships,
               app_role.rolsuper AS app_superuser,
               p.proconfig,
               (SELECT count(*) FROM pg_catalog.pg_proc d
                JOIN pg_catalog.pg_namespace dn ON dn.oid = d.pronamespace
                WHERE dn.nspname = 'reward' AND d.prosecdef
                  AND (d.proconfig IS NULL OR NOT (
                      d.proconfig @> ARRAY['search_path=pg_catalog, reward, pg_temp']
                  ))) AS definer_paths_without_pg_temp,
               (SELECT count(*) FROM pg_catalog.pg_proc d
                JOIN pg_catalog.pg_namespace dn ON dn.oid = d.pronamespace
                WHERE dn.nspname = 'reward' AND d.prosecdef
                  AND EXISTS (
                      SELECT 1 FROM pg_catalog.aclexplode(d.proacl) acl
                      WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
                  )) AS public_definer_executes,
               (SELECT string_agg(d.proname || '/' || d.prokind::text, ',' ORDER BY d.proname)
                FROM pg_catalog.pg_proc d
                JOIN pg_catalog.pg_namespace dn ON dn.oid = d.pronamespace
                WHERE dn.nspname = 'reward'
                  AND has_function_privilege('reward_app', d.oid, 'EXECUTE')) AS app_execute_routines,
               (SELECT count(*) FROM pg_catalog.pg_proc d
                JOIN pg_catalog.pg_namespace dn ON dn.oid = d.pronamespace
                WHERE dn.nspname = 'reward' AND d.prosecdef
                  AND pg_catalog.pg_get_userbyid(d.proowner) <> 'reward_migration') AS definer_wrong_owners,
               owner_role.rolcanlogin AS owner_can_login,
               (SELECT count(*) FROM pg_catalog.pg_tables t
                WHERE t.schemaname = 'reward' AND t.tableowner = owner_role.rolname
                  AND t.tablename <> 'p3_tx_isolation_observation') AS owner_table_count,
               (SELECT count(*) FROM pg_catalog.pg_tables t
                WHERE t.schemaname = 'reward' AND t.tableowner <> owner_role.rolname) AS other_table_owner_count,
               (SELECT count(*) FROM pg_catalog.pg_auth_members m
                WHERE m.member = app_role.oid) AS app_role_memberships
        FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
        JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = p.proowner
        JOIN pg_catalog.pg_roles app_role ON app_role.rolname = 'reward_app'
        WHERE n.nspname = 'reward' AND p.proname = 'grant_compensation' AND p.prokind = 'p';
    """)
    privilege_row = privilege_check.splitlines()[-1].split("|")
    expected_privileges = [
        "f", "f", "t", "t", "reward_migration", "f", "f", "f", "f", "f",
        "reward_migration", "f", "f", "f", "t", "t", "f", "0", "f",
    ]
    if privilege_row[:19] != expected_privileges or \
            "search_path=pg_catalog, reward, pg_temp" not in privilege_row[19] or \
            privilege_row[20:22] != ["0", "0"] or \
            privilege_row[22] != (
                "grant_compensation/p,grant_event_reward/p,"
                "reset_all_local_wallets/f,reset_local_user_wallet/f"
            ) or privilege_row[23:] != ["0", "t", "11", "0", "0"]:
        raise RuntimeError(f"unexpected procedure privilege configuration: {privilege_row}")

    # Optional temporary trigger captures the isolation setting from the real API transaction.
    isolation_observer_installed = psql(
        "SELECT to_regclass('reward.p3_tx_isolation_observation') IS NOT NULL;"
    ).splitlines()[-1] == "t"
    isolation_observation = None
    if isolation_observer_installed:
        isolation_observation = psql("""
            SELECT count(*),
                   string_agg(DISTINCT transaction_isolation, ',' ORDER BY transaction_isolation),
                   string_agg(DISTINCT session_user_name, ',' ORDER BY session_user_name),
                   string_agg(DISTINCT current_user_name, ',' ORDER BY current_user_name)
            FROM reward.p3_tx_isolation_observation
            WHERE source_table = 'compensations';
        """).splitlines()[-1].split("|")
        if isolation_observation[0] == "0" or isolation_observation[1:] != [
            "read committed", "reward_app", "reward_migration"
        ]:
            raise RuntimeError(
                "API compensation transaction used unexpected isolation/roles: "
                f"{isolation_observation}"
            )

    set_role_denied = verify_app_cannot_set_migration_role()

    race_result = concurrent_compensation_race()
    key_conflict_race = same_request_id_conflict_race()
    cross_operation_key = cross_operation_request_id_conflict()
    mixed_claim_first = mixed_cross_operation_request_id_race("CLAIM")
    mixed_compensation_first = mixed_cross_operation_request_id_race("COMPENSATION")

    # Reset only the disposable primary user after all payout assertions have been captured.
    reset_status, reset_response = reset_user_wallet(user_id)
    if reset_status != 200 or reset_response.get("usersReset") != 1 \
            or reset_response.get("compensationsDeleted") != 1 \
            or reset_response.get("ledgerRowsDeleted") != 2:
        raise RuntimeError(f"single-user reset did not clear only the test activity: {reset_response}")
    reset_reconciliation = psql(f"""
        SELECT (SELECT count(*) FROM reward.users WHERE id = {user_id}),
               (SELECT money_balance FROM reward.wallets WHERE user_id = {user_id}),
               (SELECT point_balance FROM reward.wallets WHERE user_id = {user_id}),
               (SELECT count(*) FROM reward.compensations WHERE user_id = {user_id}),
               (SELECT count(*) FROM reward.wallet_ledger WHERE user_id = {user_id});
    """).splitlines()[-1].split("|")
    if reset_reconciliation != ["1", "0", "0", "0", "0"]:
        raise RuntimeError(f"single-user reset postcondition failed: {reset_reconciliation}")

    print(json.dumps({
        "database": "rewardp3db",
        "userId": user_id,
        "eventId": EVENT_ID,
        "incidentReference": incident_reference,
        "requestResults": [first, replay, duplicate],
        "changedBusinessPayloads": changed_payload_results,
        "reconciliation": row,
        "directDml": "wallet UPDATE and compensation INSERT denied as reward_app",
        "rewardAppPrivileges": privilege_row,
        "appCannotSetMigrationRole": set_role_denied,
        "transactionIsolationObservation": isolation_observation,
        "resetResponse": reset_response,
        "resetReconciliation": reset_reconciliation,
        "concurrentDifferentRequestIds": race_result,
        "concurrentSameRequestIdDifferentPayload": key_conflict_race,
        "crossOperationRequestIdConflict": cross_operation_key,
        "mixedClaimThenCompensationRace": mixed_claim_first,
        "mixedCompensationThenClaimRace": mixed_compensation_first,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

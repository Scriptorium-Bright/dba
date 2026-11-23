#!/usr/bin/env python3
"""P2a 동시 요청 및 P2b 1,000명 배치/재개를 로컬 데모 API로 재현한다."""

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_BASE = os.environ.get("REWARD_API", "http://127.0.0.1:8082")
DOCKER_HOST = os.environ.get(
    "DOCKER_HOST", "unix:///private/tmp/reward-colima/active-docker.sock"
)
ADMIN_HEADERS = {"X-User-Id": "101", "X-Demo-Role": "ADMIN"}


def request(method, path, payload=None, user_id=101, timeout=60):
    """API 응답 JSON을 읽고 HTTP 오류 본문을 함께 출력 가능한 예외로 바꾼다."""
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {**ADMIN_HEADERS, "X-User-Id": str(user_id)}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API_BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        message = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} returned HTTP {error.code}: {message}") from error
    return json.loads(raw) if raw else None


def docker(args, stdin=None, check=True):
    """화면과 같은 Colima 엔진의 프로젝트 PostgreSQL을 조회/중단/재시작한다."""
    env = os.environ.copy()
    env["DOCKER_HOST"] = DOCKER_HOST
    result = subprocess.run(
        ["docker", "compose", *args],
        cwd=ROOT,
        env=env,
        input=stdin,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stdout.strip() or f"docker compose {args} failed")
    return result


def psql_file(filename, variables=None):
    """named volume의 로컬 DB 안에서 SQL 실험/검증 파일을 실행한다."""
    command = [
        "exec", "-T", "db", "psql", "-U", "reward_bootstrap", "-d", "rewarddb",
        "-v", "ON_ERROR_STOP=1", "-A", "-t", "-F", "|",
    ]
    for key, value in (variables or {}).items():
        command.extend(["-v", f"{key}={value}"])
    command.extend(["-f", "-"])
    sql = (ROOT / filename).read_text(encoding="utf-8")
    return docker(command, stdin=sql)


def transaction_isolation_observation(event_id, expected_grants):
    """Check the trigger's transaction-local setting for this event's real grant rows."""
    query = f"""
        SELECT count(*),
               string_agg(DISTINCT transaction_isolation, ',' ORDER BY transaction_isolation),
               string_agg(DISTINCT session_user_name, ',' ORDER BY session_user_name),
               string_agg(DISTINCT current_user_name, ',' ORDER BY current_user_name)
        FROM reward.p2_tx_isolation_observation
        WHERE event_id = {int(event_id)};
    """
    result = docker([
        "exec", "-T", "db", "psql", "-X", "-U", "reward_bootstrap", "-d", "rewarddb",
        "-A", "-t", "-F", "|", "-c", query,
    ])
    values = result.stdout.strip().split("|")
    if len(values) != 4 or values != [
        str(expected_grants), "read committed", "reward_app", "reward_migration"
    ]:
        raise RuntimeError(
            "actual reward grant transaction isolation/roles differ from the explicit contract: "
            f"{values}; expected grant count {expected_grants}, READ COMMITTED, "
            "reward_app session and reward_migration definer"
        )
    return {
        "grantRows": int(values[0]),
        "transactionIsolation": values[1],
        "sessionUser": values[2],
        "currentUser": values[3],
    }


def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def create_event(mode, reward_money=7, reward_point=3):
    now = datetime.now(timezone.utc)
    payload = {
        "name": f"P2 {mode} acceptance {uuid.uuid4().hex[:8]}",
        "mode": mode,
        "startsAt": iso(now - timedelta(hours=1)),
        "endsAt": iso(now + timedelta(days=7)),
        "scheduledAt": iso(now - timedelta(minutes=1)) if mode == "BATCH" else None,
        "rewardMoney": reward_money,
        "rewardPoint": reward_point,
    }
    created = request("POST", "/api/admin/events", payload)
    return created["id"]


def open_event(event_id):
    return request("POST", f"/api/admin/events/{event_id}/open")


def p2a():
    """한 사용자의 100 동일 요청, 다른 키 중복, 트랜잭션 롤백을 확인한다."""
    event_id = create_event("CLAIM")
    open_event(event_id)
    request_id = str(uuid.uuid4())

    def claim_with_key(target_event_id, key, target_user_id=101):
        # 같은 Idempotency-Key를 100개 worker에 나눠 동시 전송한다.
        req = urllib.request.Request(
            API_BASE + f"/api/events/{target_event_id}/claims",
            headers={**ADMIN_HEADERS, "X-User-Id": str(target_user_id), "Idempotency-Key": key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            return {"httpStatus": error.code, **json.loads(error.read())}

    def claim_once(_):
        return claim_with_key(event_id, request_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=100) as executor:
        results = list(executor.map(claim_once, range(100)))
    result_counts = {name: sum(row["result"] == name for row in results)
                     for name in ("PAID", "REPLAYED", "ALREADY_GRANTED", "NOT_AVAILABLE")}
    if result_counts["PAID"] != 1 or result_counts["REPLAYED"] != 99:
        raise RuntimeError(f"100개 동시요청 결과가 예상과 다릅니다: {result_counts}")

    duplicate = claim_with_key(event_id, str(uuid.uuid4()))
    if duplicate["result"] != "ALREADY_GRANTED":
        raise RuntimeError(f"다른 요청 ID 재요청 결과가 예상과 다릅니다: {duplicate}")
    reused_request_id = claim_with_key(event_id, request_id, target_user_id=102)
    if reused_request_id.get("httpStatus") != 409:
        raise RuntimeError(f"다른 사용자 request_id 재사용이 거부되지 않았습니다: {reused_request_id}")
    sql_result = psql_file("experiments/sql/verify_p2a_event.sql", {"event_id": event_id})
    expected = "1|2|0"
    if expected not in sql_result.stdout:
        raise RuntimeError(f"지급/원장 SQL 검증이 예상과 다릅니다: {sql_result.stdout.strip()}")

    rollback_event = create_event("CLAIM")
    open_event(rollback_event)
    before = request("GET", "/api/me/wallet", user_id=102)
    failure_request_id = str(uuid.uuid4())
    rollback_request_id = str(uuid.uuid4())
    rollback_sql = psql_file(
        "experiments/sql/rollback_after_ledger_failure.sql",
        {
            "event_id": rollback_event,
            "user_id": 102,
            "failure_request_id": failure_request_id,
            "rollback_request_id": rollback_request_id,
        },
    )
    after = request("GET", "/api/me/wallet", user_id=102)
    if before != after or "0|f" not in rollback_sql.stdout:
        raise RuntimeError(
            "원장 실패/호출자 ROLLBACK 뒤 잔액 또는 grant가 남았습니다. "
            f"before={before}, after={after}, sql={rollback_sql.stdout.strip()}"
        )

    request_id_race = p2a_request_id_race(claim_with_key)
    isolation_observation = transaction_isolation_observation(event_id, 1)

    print(json.dumps({
        "eventId": event_id,
        "sameKeyResults": result_counts,
        "differentKeyResult": duplicate["result"],
        "requestIdReusedByOtherUser": reused_request_id["httpStatus"],
        "grantLedgerSql": sql_result.stdout.strip(),
        "rollbackEventId": rollback_event,
        "rollbackWalletUnchanged": before == after,
        "rollbackSql": rollback_sql.stdout.strip().splitlines()[-1],
        "concurrentRequestIdRace": request_id_race,
        "transactionIsolationObservation": isolation_observation,
    }, ensure_ascii=False, indent=2))


def psql_transaction(application_name, sql):
    """지정 SQL을 미커밋 상태로 열어 두는 별도 PostgreSQL 세션을 시작한다."""
    env = os.environ.copy()
    env["DOCKER_HOST"] = DOCKER_HOST
    process = subprocess.Popen(
        [
            "docker", "compose", "exec", "-T", "db", "psql", "-X", "-q", "-A", "-t",
            "-v", "ON_ERROR_STOP=1", "-U", "reward_bootstrap", "-d", "rewarddb",
        ],
        cwd=ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    process.stdin.write(f"SET application_name = '{application_name}';\nBEGIN;\n{sql}\n")
    process.stdin.flush()
    return process


def finish_psql_transaction(process, command="COMMIT"):
    """대기 세션의 transaction을 끝내고 psql 종료 코드·응답을 확인한다."""
    if process.poll() is None:
        process.stdin.write(f"{command};\n\\q\n")
        process.stdin.flush()
        process.stdin.close()
    output = process.stdout.read()
    return_code = process.wait(timeout=15)
    if return_code != 0:
        raise RuntimeError(f"경합 재현 SQL transaction 실패: {output.strip()}")
    return output.strip()


def pg_condition(sql, timeout, description):
    """pg_stat_activity에서 세션 단계가 실제로 관찰될 때까지 기다린다."""
    deadline = time.monotonic() + timeout
    command = [
        "exec", "-T", "db", "psql", "-X", "-A", "-t", "-U", "reward_bootstrap",
        "-d", "rewarddb", "-c", sql,
    ]
    while time.monotonic() < deadline:
        observed = docker(command, check=False)
        if observed.returncode == 0 and observed.stdout.strip() == "1":
            return
        time.sleep(0.05)
    raise RuntimeError(f"PostgreSQL에서 {description} 상태를 관찰하지 못했습니다.")


def p2a_request_id_race(claim_with_key=None):
    """동일 UUID waiter와 별도 pair 지급이 겹치는 경합을 재현해 409와 정합성을 확인한다."""
    if claim_with_key is None:
        def claim_with_key(target_event_id, key, target_user_id=101):
            req = urllib.request.Request(
                API_BASE + f"/api/events/{target_event_id}/claims",
                headers={**ADMIN_HEADERS, "X-User-Id": str(target_user_id), "Idempotency-Key": key},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as error:
                return {"httpStatus": error.code, **json.loads(error.read())}

    request_id = str(uuid.uuid4())
    second_request_id = str(uuid.uuid4())
    event_a = create_event("CLAIM", 7, 3)
    event_b = create_event("CLAIM", 7, 3)
    open_event(event_a)
    open_event(event_b)

    # 전용 사용자로 기존 데모 지갑에 실험 지급을 섞지 않는다.
    user_a = 1_700_000_000_000 + time.time_ns() % 1_000_000_000
    user_b = user_a + 1
    seed_users = docker([
        "exec", "-T", "db", "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U",
        "reward_bootstrap", "-d", "rewarddb", "-c",
        f"INSERT INTO reward.users(id,nickname) VALUES ({user_a},'request-id-race-a'),"
        f"({user_b},'request-id-race-b'); "
        f"INSERT INTO reward.wallets(user_id) VALUES ({user_a}),({user_b});",
    ])
    if seed_users.returncode != 0:
        raise RuntimeError(f"경합 실험 사용자 생성 실패: {seed_users.stdout.strip()}")

    winner_a = psql_transaction(
        "request_id_race_winner_a",
        f"CALL reward.grant_event_reward({event_a}, {user_a}, '{request_id}', 'CLAIM', NULL, NULL);",
    )
    api_waiter = None
    try:
        pg_condition(
            "SELECT CASE WHEN count(*) = 1 THEN 1 ELSE 0 END "
            "FROM pg_stat_activity WHERE application_name='request_id_race_winner_a' "
            "AND state='idle in transaction' AND xact_start IS NOT NULL",
            10,
            "request_id winner의 열린 transaction",
        )

        # 다른 사용자·이벤트의 B 요청은 UUID advisory lock에서 기다려야 한다.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            api_waiter = executor.submit(claim_with_key, event_b, request_id, user_b)
            pg_condition(
                "SELECT CASE WHEN count(*) = 1 THEN 1 ELSE 0 END "
                "FROM pg_stat_activity WHERE wait_event_type='Lock' AND wait_event='advisory' "
                "AND state='active' AND query ILIKE '%grant_event_reward%'",
                10,
                "동일 request_id의 transaction-level advisory wait",
            )

            # B가 ID와 event/user 사전 조회 전에 대기하는 동안, 같은 pair에 다른 key를 지급한다.
            winner_c = claim_with_key(event_b, second_request_id, user_b)
            if winner_c.get("result") != "PAID":
                raise RuntimeError(f"별도 pair의 지급 결과가 예상과 다릅니다: {winner_c}")

            winner_a_output = finish_psql_transaction(winner_a)
            result_b = api_waiter.result(timeout=15)
            if result_b.get("httpStatus") != 409:
                raise RuntimeError(f"동시 request_id 재사용이 409로 처리되지 않았습니다: {result_b}")

    finally:
        if winner_a.poll() is None:
            try:
                finish_psql_transaction(winner_a, "ROLLBACK")
            except Exception:
                winner_a.kill()
                winner_a.wait(timeout=5)

    integrity = psql_file(
        "experiments/sql/verify_request_id_lock_race.sql",
        {
            "event_a": event_a,
            "event_b": event_b,
            "user_a": user_a,
            "user_b": user_b,
            "request_id": request_id,
            "second_request_id": second_request_id,
        },
    )
    expected = "1|1|1|1|7|3|7|3|4|0|0"
    if expected not in integrity.stdout:
        raise RuntimeError(f"동시 request_id 정합성 대사가 실패했습니다: {integrity.stdout.strip()}")

    request("POST", f"/api/admin/events/{event_a}/close")
    request("POST", f"/api/admin/events/{event_b}/close")
    privilege_result = psql_file(
        "experiments/sql/verify_reward_app_permissions.sql",
        {"event_id": event_a, "user_id": user_a, "request_id": request_id},
    )
    if "REPLAYED|" not in privilege_result.stdout:
        raise RuntimeError(
            "앱 역할의 직접 DML 차단, definer procedure 호출 또는 pg_temp 경로 검증이 실패했습니다: "
            f"{privilege_result.stdout.strip()}"
        )
    return {
        "requestId": request_id,
        "winnerA": winner_a_output,
        "winnerC": winner_c["result"],
        "sameRequestDifferentPair": result_b["httpStatus"],
        "databaseReconciliation": integrity.stdout.strip(),
        "expected": "eventA/eventB grants=1/1, shared request owner=1, wallets=7/3 each, ledger rows=4, grant/wallet mismatches=0/0",
        "appPrivilegeAndTempShadowCheck": privilege_result.stdout.strip(),
    }


def wait_for_database():
    """SIGKILL 재시작 후 PostgreSQL readiness와 API 재연결을 기다린다."""
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        ready = docker(["exec", "-T", "db", "pg_isready", "-U", "reward_bootstrap", "-d", "rewarddb"], check=False)
        if ready.returncode == 0:
            try:
                request("GET", "/actuator/health", timeout=5)
                return
            except (RuntimeError, OSError, urllib.error.URLError):
                # SSH port-forward/API 재기동 중 연결이 닫히면 다음 주기에 다시 확인한다.
                pass
        time.sleep(2)
    raise RuntimeError("PostgreSQL/API가 90초 안에 복구되지 않았습니다.")


def p2b(crash_after_first_page=False):
    """1,000명 배치를 일부 커밋하고, 선택 시 DB crash 중단 뒤 미지급자를 재개한다."""
    seeded = psql_file("experiments/sql/seed_p2b_1000_users.sql")
    if "1000" not in seeded.stdout:
        raise RuntimeError(f"P2b 사용자 seed가 완료되지 않았습니다: {seeded.stdout.strip()}")

    event_id = create_event("BATCH")
    recipient_ids = list(range(910001, 911001))
    updated = request("PUT", f"/api/admin/events/{event_id}/recipients", {"userIds": recipient_ids})
    if updated["recipientCount"] != 1000:
        raise RuntimeError(f"대상 1,000명 등록 결과가 예상과 다릅니다: {updated}")
    open_event(event_id)

    first_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    first_future = first_executor.submit(
        request, "POST", f"/api/admin/events/{event_id}/run", {"maxRecipients": 600}, timeout=180
    )
    deadline = time.monotonic() + 90
    duplicate_worker_result = None
    while time.monotonic() < deadline:
        if first_future.done():
            raise RuntimeError(f"첫 배치 페이지가 advisory lock 관찰 전에 끝났습니다: {first_future.result()}")
        progress = request("GET", f"/api/admin/events/{event_id}/distribution", timeout=10)
        if progress["grantedCount"] >= 5:
            try:
                request("POST", f"/api/admin/events/{event_id}/run", {"maxRecipients": 1}, timeout=15)
            except RuntimeError as error:
                duplicate_worker_result = str(error)
            break
        time.sleep(0.05)
    if duplicate_worker_result is None or "HTTP 409" not in duplicate_worker_result:
        raise RuntimeError(f"동일 이벤트의 중복 worker가 advisory lock으로 차단되지 않았습니다: {duplicate_worker_result}")
    first_page = first_future.result(timeout=180)
    first_executor.shutdown(wait=True)
    if first_page["paidCount"] != 600 or first_page["failedCount"] != 0:
        raise RuntimeError(f"첫 600명 배치 결과가 예상과 다릅니다: {first_page}")

    interrupted_run_result = {"error": None}
    if crash_after_first_page:
        # 두 번째 페이지를 실제 실행 중인 상태에서 진행률을 관찰하고 PostgreSQL을 강제 종료한다.
        background = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = background.submit(
            request, "POST", f"/api/admin/events/{event_id}/run", {"maxRecipients": 1000}, timeout=30
        )
        crash_count = None
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if future.done():
                try:
                    completed_early = future.result()
                except Exception as error:
                    raise RuntimeError(f"DB를 중단하기 전 두 번째 페이지가 실패했습니다: {error}") from error
                raise RuntimeError(f"진행률을 관찰하기 전에 두 번째 페이지가 끝났습니다: {completed_early}")
            current = request("GET", f"/api/admin/events/{event_id}/distribution", timeout=10)
            if current["grantedCount"] >= 650 and current["pendingCount"] > 0:
                crash_count = current["grantedCount"]
                break
            time.sleep(0.05)
        if crash_count is None:
            raise RuntimeError("두 번째 페이지에서 부분 커밋 진행률을 90초 안에 관찰하지 못했습니다.")

        print(f"P2b: 실행 중 지급 {crash_count}건 커밋을 관찰했습니다. PostgreSQL에 SIGKILL을 보냅니다.")
        docker(["kill", "-s", "SIGKILL", "db"])
        # API 프로세스도 종료해 DB 재기동 뒤 기존 워커가 자동으로 이어달리지 않게 한다.
        docker(["kill", "-s", "SIGKILL", "api"])
        docker(["up", "-d", "db", "api"])
        wait_for_database()
        try:
            future.result(timeout=35)
        except Exception as error:
            interrupted_run_result["error"] = str(error)
        background.shutdown(wait=True, cancel_futures=True)
        if interrupted_run_result["error"] is None:
            raise RuntimeError("강제 종료한 워커가 오류 없이 완료되어 중단 상태를 재현하지 못했습니다.")
        recovery_log = docker(["logs", "--since=3m", "--no-color", "db"], check=False)
        print("--- PostgreSQL 재시작 로그 ---")
        print(recovery_log.stdout[-8000:])
        recovery_markers = ("database system was interrupted", "automatic recovery in progress", "redo done")
        if not all(marker in recovery_log.stdout.lower() for marker in recovery_markers):
            raise RuntimeError("PostgreSQL 로그에서 Crash Recovery/REDO 완료 근거를 확인하지 못했습니다.")

    recovered_distribution = request("GET", f"/api/admin/events/{event_id}/distribution")
    if crash_after_first_page and recovered_distribution["pendingCount"] == 0:
        raise RuntimeError("Crash Recovery 뒤 미지급 대상이 없어 워커 재개 검증을 수행할 수 없습니다.")
    expected_remaining = recovered_distribution["pendingCount"]
    second_page = request("POST", f"/api/admin/events/{event_id}/run", {"maxRecipients": 1000}, timeout=180)
    if second_page["paidCount"] != expected_remaining or second_page["failedCount"] != 0:
        raise RuntimeError(f"미지급자 재개 결과가 예상과 다릅니다: {second_page}")

    distribution = request("GET", f"/api/admin/events/{event_id}/distribution")
    if distribution["recipientCount"] != 1000 or distribution["grantedCount"] != 1000 or distribution["pendingCount"] != 0:
        raise RuntimeError(f"최종 배포 집계가 예상과 다릅니다: {distribution}")
    if crash_after_first_page and not any(run["status"] == "FAILED" for run in distribution["recentRuns"]):
        raise RuntimeError("중단된 RUNNING 실행이 재개 시 FAILED로 정리되지 않았습니다.")

    integrity = docker([
        "exec", "-T", "db", "psql", "-U", "reward_bootstrap", "-d", "rewarddb",
        "-v", f"event_id={event_id}", "-A", "-t", "-F", "|", "-f", "-",
    ], stdin=(ROOT / "experiments/sql/verify_p2b_event.sql").read_text(encoding="utf-8"))
    expected = "1000|1000|0|0|0"
    if expected not in integrity.stdout:
        raise RuntimeError(f"지급/원장/지갑 SQL 검증이 예상과 다릅니다: {integrity.stdout.strip()}")
    isolation_observation = transaction_isolation_observation(event_id, 1000)

    print(json.dumps({
        "eventId": event_id,
        "firstRun": first_page,
        "duplicateWorkerRejected": duplicate_worker_result,
        "grantsAtCrashObservation": crash_count if crash_after_first_page else None,
        "grantsAfterCrashRecovery": 1000 - expected_remaining if crash_after_first_page else None,
        "interruptedRunHttpResult": interrupted_run_result["error"],
        "resumedRun": second_page,
        "distribution": {
            "recipients": distribution["recipientCount"],
            "granted": distribution["grantedCount"],
            "pending": distribution["pendingCount"],
        },
        "integritySql": integrity.stdout.strip(),
        "transactionIsolationObservation": isolation_observation,
        "crashRecoveryExecuted": crash_after_first_page,
    }, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("p2a", "p2a-request-id-race", "p2b"))
    parser.add_argument(
        "--crash-after-first-page",
        action="store_true",
        help="P2b 600명 commit 뒤 프로젝트 DB에 SIGKILL하고 recovery 로그를 확인합니다.",
    )
    options = parser.parse_args()
    if options.phase == "p2a":
        p2a()
    elif options.phase == "p2a-request-id-race":
        print(json.dumps(p2a_request_id_race(), ensure_ascii=False, indent=2))
    else:
        p2b(options.crash_after_first_page)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"P2 acceptance failed: {error}", file=sys.stderr)
        sys.exit(1)

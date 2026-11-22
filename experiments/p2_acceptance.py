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

    print(json.dumps({
        "eventId": event_id,
        "sameKeyResults": result_counts,
        "differentKeyResult": duplicate["result"],
        "requestIdReusedByOtherUser": reused_request_id["httpStatus"],
        "grantLedgerSql": sql_result.stdout.strip(),
        "rollbackEventId": rollback_event,
        "rollbackWalletUnchanged": before == after,
        "rollbackSql": rollback_sql.stdout.strip().splitlines()[-1],
    }, ensure_ascii=False, indent=2))


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
        "crashRecoveryExecuted": crash_after_first_page,
    }, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("p2a", "p2b"))
    parser.add_argument(
        "--crash-after-first-page",
        action="store_true",
        help="P2b 600명 commit 뒤 프로젝트 DB에 SIGKILL하고 recovery 로그를 확인합니다.",
    )
    options = parser.parse_args()
    if options.phase == "p2a":
        p2a()
    else:
        p2b(options.crash_after_first_page)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"P2 acceptance failed: {error}", file=sys.stderr)
        sys.exit(1)

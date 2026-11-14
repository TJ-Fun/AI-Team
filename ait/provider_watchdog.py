"""Watchdog pass: attempt timeouts, retry scheduling, and capacity recovery."""

from __future__ import annotations

from ait.common import *


def run_watchdog_pass() -> int:
    changed = 0
    now_epoch = time.time()
    with STATE_LOCK:
        for task in read_tasks():
            status = task.get("status")
            if status == "In progress":
                attempts = task.setdefault("attempts", [])
                active = next((a for a in reversed(attempts) if a.get("state") == "in_progress"), None)
                started_ts = parse_iso_epoch((active or {}).get("started_at") or task.get("updated_at"))
                if started_ts is None:
                    continue
                timeout_sec = int(task.get("attempt_timeout_seconds") or DEFAULT_ATTEMPT_TIMEOUT_SECONDS)
                if now_epoch - started_ts < timeout_sec:
                    continue
                if active:
                    active["state"] = "timed_out"
                    active["finished_at"] = now_iso()
                    active["error"] = "No response before the attempt timeout."
                routed = task.get("workflow_phase") == "qa" or str(task.get("provider") or "") in (
                    "Automatic",
                    "Auto",
                    "Antigravity",
                )
                if routed:
                    qa_active = task.get("workflow_phase") == "qa"
                    role = "QA" if qa_active else str(task.get("assigned_role") or "PM/PO")
                    cycle_key = "qa_routing_cycle_start" if qa_active else "routing_cycle_start"
                    nxt = (
                        route_entry_for_role(task, role, cycle_key)
                        if qa_active
                        else task_route_entry(task)
                    )
                    failed_key = (
                        str((active or {}).get("provider") or ""),
                        str((active or {}).get("route_model") or "auto"),
                    )
                    if nxt is None:
                        task["status"] = "Blocked"
                        reason = "All route combinations exhausted after an agent timeout."
                        task["block_reason"] = reason
                        if qa_active:
                            task["assigned_role"] = str(task.pop("qa_return_role", "Unknown"))
                            task.pop("workflow_phase", None)
                            task.pop("qa_attempt_active", None)
                    elif route_combo_key(nxt) == failed_key:
                        delay = int(
                            nxt.get("retry_delay_seconds")
                            if nxt.get("retry_delay_seconds") is not None
                            else DEFAULT_ROUTE_RETRY_DELAY_SECONDS
                        )
                        task["status"] = "Waiting for capacity"
                        task["retry_after"] = (
                            datetime.now(timezone.utc) + timedelta(seconds=delay)
                        ).strftime("%Y-%m-%dT%H:%M:%SZ")
                        task["waiting_reason"] = "Agent attempt timed out."
                        reason = f"Agent attempt timed out; retrying in {delay}s."
                        if qa_active:
                            task.pop("qa_attempt_active", None)
                    else:
                        task["status"] = "Ready"
                        task["dispatch_note"] = "Attempt timed out; trying next provider/model."
                        reason = "Agent attempt timed out; trying the next route."
                else:
                    retries = int(task.get("automatic_retries") or 0)
                    if retries >= MAX_AUTOMATIC_RETRIES:
                        task["status"] = "Blocked"
                        reason = "Automatic retry limit reached after an agent timeout."
                        task["block_reason"] = reason
                    else:
                        wait_h = min(2 * (retries + 1), 6)
                        task["status"] = "Waiting for capacity"
                        task["retry_after"] = (datetime.now(timezone.utc) + timedelta(hours=wait_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
                        task["waiting_reason"] = "Agent attempt timed out."
                        reason = f"Agent attempt timed out; retry scheduled after {wait_h} hours."
                ts = now_iso()
                task["updated_at"] = ts
                task.setdefault("activity", []).append(
                    {"at": ts, "type": "watchdog_recovery", "from": "In progress", "to": task["status"], "message": reason}
                )
                write_task(task)
                if task["status"] == "Blocked":
                    esc_id = maybe_create_escalation(task, reason)
                    if esc_id:
                        task.setdefault("activity", []).append(
                            {"at": now_iso(), "type": "escalation", "message": f"Auto-escalated to PM/PO as {esc_id}."}
                        )
                        write_task(task)
                changed += 1
            elif status == "Waiting for capacity" and task.get("retry_after"):
                retry_ts = parse_iso_epoch(task.get("retry_after"))
                if retry_ts is None or now_epoch < retry_ts:
                    continue
                ts = now_iso()
                routed = task.get("workflow_phase") == "qa" or str(task.get("provider") or "") in (
                    "Automatic",
                    "Auto",
                    "Antigravity",
                )
                if not routed:
                    task["automatic_retries"] = int(task.get("automatic_retries") or 0) + 1
                task["status"] = "Ready"
                task.pop("retry_after", None)
                task.pop("waiting_reason", None)
                task["dispatch_note"] = "Retry window reached; queued for dispatch."
                task["updated_at"] = ts
                task.setdefault("activity", []).append(
                    {
                        "at": ts,
                        "type": "watchdog_recovery",
                        "from": "Waiting for capacity",
                        "to": "Ready",
                        "message": "Retry time reached; returned to Ready queue.",
                    }
                )
                write_task(task)
                changed += 1
    if changed:
        DISPATCH_EVENT.set()
    return changed


def watchdog_worker_loop() -> None:
    while True:
        try:
            changed = run_watchdog_pass()
            WATCHDOG_STATUS["last_run"] = now_iso()
            WATCHDOG_STATUS["changed"] = changed
            WATCHDOG_STATUS["error"] = None
        except Exception as exc:
            WATCHDOG_STATUS["last_run"] = now_iso()
            WATCHDOG_STATUS["error"] = str(exc)[:300]
        time.sleep(WATCHDOG_INTERVAL_SECONDS)

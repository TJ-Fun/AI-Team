"""Operational task API routes: status, provider, validation, local sync, merge approval."""

from __future__ import annotations

from ait.common import *


def handle_tasks_ops_post(handler, path, body) -> bool:
    m_status = re.fullmatch(r"/api/tasks/(T-\d{4,})/status", path)
    if m_status:
        tid = m_status.group(1)
        target = str(body.get("status", ""))
        note = str(body.get("note", "")).strip()[:500]
        if target not in ("Ready", "Blocked", "Canceled", "Draft"):
            handler.send_json({"error": "Invalid manual status transition."}, HTTPStatus.BAD_REQUEST)
            return True
        with STATE_LOCK:
            task = read_task(tid)
            if not task:
                handler.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                return True
            cur = str(task.get("status", ""))
            if target not in TRANSITIONS.get(cur, set()) or target not in USER_TRANSITIONS.get(cur, set()):
                handler.send_json({"error": f"Cannot move a {cur} task to {target}."}, HTTPStatus.CONFLICT)
                return True
            if target == "Ready" and task.get("produces_deliverable") and not (task.get("acceptance_checks") or []):
                handler.send_json(
                    {"error": "Add at least one acceptance check before marking a deliverable task Ready."},
                    HTTPStatus.BAD_REQUEST,
                )
                return True
            ts = now_iso()
            if target == "Ready":
                task.pop("retry_after", None)
                task.pop("waiting_reason", None)
                was_qa_blocked = cur == "Blocked" and int(task.get("qa_failures") or 0) > 0
                if cur in ("Waiting for capacity", "Blocked") and (not task.get("provider") or task.get("provider") == "Automatic"):
                    task["routing_cycle_start"] = len(task.get("attempts") or [])
                if cur == "Rework needed" and task.get("provider") == "Automatic":
                    task["routing_cycle_start"] = len(task.get("attempts") or [])
                if cur == "Blocked":
                    task["qa_failures"] = 0
                    task.pop("block_reason", None)
                sel = task_provider(task)
                if cur == "Rework needed":
                    msg = "Task returned to the agent queue with its failed validation results included."
                elif was_qa_blocked:
                    msg = "Task manually unblocked; QA failure count reset and the task queued for dispatch."
                elif cur == "Waiting for capacity":
                    msg = "Manual retry requested; task queued for immediate dispatch."
                else:
                    msg = "Task is clear and queued for automatic dispatch."
                task["dispatch_note"] = (
                    f"Waiting for {sel} to respond."
                    if sel
                    else "No provider in this task's route is connected. Add a provider key in Connections & routing."
                )
            elif target == "Blocked":
                task.pop("retry_after", None)
                task.pop("waiting_reason", None)
                msg = note or "Task blocked by the user."
                task["block_reason"] = msg
            elif target == "Canceled":
                task.pop("retry_after", None)
                task.pop("waiting_reason", None)
                msg = note or "Task canceled by the user."
            else:
                msg = note or "Task returned to Draft."
                task.pop("block_reason", None)
            task["status"] = target
            task["updated_at"] = ts
            task.setdefault("activity", []).append(
                {"at": ts, "type": "status_changed", "from": cur, "to": target, "message": msg}
            )
            write_task(task)
        if target == "Ready":
            DISPATCH_EVENT.set()
        handler.send_json({"task": task})
        return True

    m_prov = re.fullmatch(r"/api/tasks/(T-\d{4,})/provider", path)
    if m_prov:
        tid = m_prov.group(1)
        provider = str(body.get("provider", "")).strip()
        if provider not in task_provider_options():
            handler.send_json({"error": "Choose Automatic, Auto, or a valid AI provider."}, HTTPStatus.BAD_REQUEST)
            return True
        with STATE_LOCK:
            task = read_task(tid)
            if not task:
                handler.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                return True
            if task.get("status") in ("In progress", "Done", "Canceled"):
                handler.send_json(
                    {"error": f"Cannot change AI provider while a task is {task.get('status')}."},
                    HTTPStatus.CONFLICT,
                )
                return True
            prev_prov = str(task.get("provider") or "Automatic")
            ts = now_iso()
            task["provider"] = provider
            task["routing_cycle_start"] = len(task.get("attempts") or [])
            if task.get("status") == "Ready":
                sel = task_provider(task)
                task["dispatch_note"] = (
                    f"Waiting for {sel} to respond."
                    if sel
                    else "No provider in this task's route is connected. Add a provider key in Connections & routing."
                )
            task["updated_at"] = ts
            if prev_prov != provider:
                task.setdefault("activity", []).append(
                    {
                        "at": ts,
                        "type": "provider_changed",
                        "message": f"AI provider changed from {prev_prov} to {provider}.",
                    }
                )
            write_task(task)
            append_event({"at": ts, "task_id": tid, "type": "provider_changed", "from": prev_prov, "to": provider})
            is_ready = task.get("status") == "Ready"
        if is_ready:
            DISPATCH_EVENT.set()
        handler.send_json({"task": task})
        return True

    m_val = re.fullmatch(r"/api/tasks/(T-\d{4,})/validation", path)
    if m_val:
        tid = m_val.group(1)
        task = read_task(tid)
        if not task:
            handler.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
            return True
        handler.send_json(
            {"error": "Deliverable validation is performed automatically by the QA role."},
            HTTPStatus.CONFLICT,
        )
        return True

    m_local_sync = re.fullmatch(r"/api/tasks/(T-\d{4,})/sync-local", path)
    if m_local_sync:
        tid = m_local_sync.group(1)
        task = read_task(tid)
        if not task:
            handler.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
            return True
        pull = task.get("pull_request")
        if (
            task.get("status") != "Done"
            or (task.get("validation") or {}).get("status") != "passed"
            or not isinstance(pull, dict)
            or pull.get("state") != "merged"
        ):
            handler.send_json(
                {"error": "Local sync can only be retried after QA passes and the pull request is merged."},
                HTTPStatus.CONFLICT,
            )
            return True
        if not sync_local_checkout_after_merge(tid):
            latest = read_task(tid) or task
            handler.send_json(
                {"error": latest.get("local_sync_error") or "Local checkout sync failed."},
                HTTPStatus.BAD_REQUEST,
            )
            return True
        handler.send_json({"task": read_task(tid)})
        return True

    m_merge = re.fullmatch(r"/api/tasks/(T-\d{4,})/approve-merge", path)
    if m_merge:
        tid = m_merge.group(1)
        with STATE_LOCK:
            task = read_task(tid)
            if not task:
                handler.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                return True
            if task.get("status") != "Needs approval" or not task.get("merge_approval_required"):
                handler.send_json({"error": "This task is not waiting for merge approval."}, HTTPStatus.CONFLICT)
                return True
            ts = now_iso()
            task["status"] = "Done"
            task["merge_approved"] = True
            task["merge_approval_required"] = False
            task["merge_approved_at"] = ts
            task["updated_at"] = ts
            task.setdefault("activity", []).append(
                {
                    "at": ts,
                    "type": "merge_approved",
                    "from": "Needs approval",
                    "to": "Done",
                    "message": "User approved the pull request merge.",
                }
            )
            write_task(task)
        github_postprocess_task(tid)
        handler.send_json({"task": read_task(tid)})
        return True

    return False

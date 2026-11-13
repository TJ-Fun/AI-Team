"""Manual environment setup tasks for user-executed host/account steps."""

from __future__ import annotations

from ait.common import *

ENV_TASK_TYPE = "env_setup"
ENV_STEP_RESULTS = {"confirmed", "problem", "change", "skipped"}
ENV_DONE_STEP_STATES = {"confirmed", "skipped"}


def clean_env_steps(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not (1 <= len(raw) <= 10):
        raise ValueError("Environment tasks need between 1 and 10 steps.")
    steps: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError("Each environment step must be an object.")
        sid = re.sub(r"[^a-z0-9-]+", "-", str(item.get("id") or f"s{i}").lower()).strip("-")[:40] or f"s{i}"
        if sid in seen:
            raise ValueError("Environment step IDs must be unique.")
        seen.add(sid)
        title = str(item.get("title", "")).strip()[:160]
        instructions = str(item.get("instructions", "")).strip()[:12000]
        expected = str(item.get("expected", "")).strip()[:2000]
        if not title or not instructions:
            raise ValueError("Each environment step needs a title and detailed instructions.")
        steps.append(
            {
                "id": sid,
                "title": title,
                "instructions": instructions,
                "expected": expected,
                "status": "pending",
                "output": "",
                "note": "",
            }
        )
    return steps


def _env_task(tid: str) -> dict[str, Any] | None:
    task = read_task(tid)
    if not task or task.get("task_type") != ENV_TASK_TYPE:
        return None
    return task


def create_env_task(
    *,
    task_id: str | None = None,
    title: str,
    request: str,
    project_id: str = "default",
    module_id: str = "",
    steps: Any,
    status: str = "Ready",
    source_plan_task_id: str | None = None,
    planning_root_id: str | None = None,
    phase: int | None = None,
) -> dict[str, Any]:
    proj = next((p for p in read_projects() if p.get("id") == project_id), None)
    if not proj:
        raise ValueError("Choose a valid project.")
    clean_steps = clean_env_steps(steps)
    ts = now_iso()
    tid = task_id or next_task_id()
    task = {
        "id": tid,
        "task_type": ENV_TASK_TYPE,
        "title": title.strip()[:160],
        "request": request.strip()[:12000],
        "assigned_role": "User",
        "provider": "Manual",
        "project_id": project_id,
        "module_id": module_id,
        "phase": phase or 1,
        "status": status if status in ("Draft", "Ready") else "Ready",
        "produces_deliverable": False,
        "acceptance_checks": [],
        "env_steps": clean_steps,
        "env_state": {"current_step": 0, "started_at": None, "completed_at": None},
        "created_at": ts,
        "updated_at": ts,
        "attempts": [],
        "validation": None,
        "source_plan_task_id": source_plan_task_id,
        "planning_root_id": planning_root_id,
        "activity": [{"at": ts, "type": "created", "message": "Manual environment task created for the user."}],
    }
    write_task(task)
    return task


def start_env_task(tid: str) -> dict[str, Any]:
    with STATE_LOCK:
        task = _env_task(tid)
        if not task:
            raise ValueError("Environment task not found.")
        if task.get("status") in ("Done", "Canceled"):
            raise ValueError(f"Cannot start a {task.get('status')} environment task.")
        if task.get("status") == "In progress":
            return task
        if task.get("status") not in ("Draft", "Ready"):
            raise ValueError("Only Draft or Ready environment tasks can be started.")
        ts = now_iso()
        steps = task.get("env_steps") or []
        current = next((i for i, s in enumerate(steps) if s.get("status") not in ENV_DONE_STEP_STATES), 0)
        task["status"] = "In progress"
        task["env_state"] = {
            "current_step": current,
            "started_at": ts,
            "completed_at": None,
        }
        task["updated_at"] = ts
        task.pop("block_reason", None)
        task.pop("env_issue", None)
        task.setdefault("activity", []).append({"at": ts, "type": "env_started", "message": "User started the environment setup task."})
        write_task(task)
        return task


def submit_env_step_feedback(tid: str, step_id: str, result: str, output: str, note: str) -> dict[str, Any]:
    if result not in ENV_STEP_RESULTS:
        raise ValueError("Feedback result must be confirmed, problem, change, or skipped.")
    with STATE_LOCK:
        task = _env_task(tid)
        if not task:
            raise ValueError("Environment task not found.")
        if task.get("status") in ("Done", "Canceled"):
            raise ValueError(f"Cannot update a {task.get('status')} environment task.")
        steps = task.get("env_steps") or []
        idx = next((i for i, s in enumerate(steps) if s.get("id") == step_id), None)
        if idx is None:
            raise ValueError("Environment step not found.")
        ts = now_iso()
        step = steps[idx]
        step["output"] = str(output or "")[:20000]
        step["note"] = str(note or "")[:2000]
        step["feedback_result"] = result
        if result == "confirmed":
            step["status"] = "confirmed"
        elif result == "skipped":
            step["status"] = "skipped"
        else:
            step["status"] = "problem"

        if task.get("status") in ("Draft", "Ready"):
            task["status"] = "In progress"
            task.setdefault("env_state", {})["started_at"] = task["env_state"].get("started_at") or ts
        task["env_state"]["current_step"] = next(
            (i for i, s in enumerate(steps) if s.get("status") not in ENV_DONE_STEP_STATES),
            len(steps),
        )

        done = all(s.get("status") in ENV_DONE_STEP_STATES for s in steps)
        if result in ("problem", "change"):
            task["status"] = "Blocked"
            task["block_reason"] = str(note or output or "Environment step reported a problem.")[:500]
            task["env_issue"] = {
                "step_id": step_id,
                "step_title": step.get("title"),
                "result": result,
                "output": step["output"],
                "note": step["note"],
                "at": ts,
            }
            task.setdefault("activity", []).append(
                {"at": ts, "type": "env_step_issue", "message": f"Environment step {step_id} reported {result}: {task['block_reason']}"}
            )
        elif done:
            task["status"] = "Done"
            task["env_state"]["completed_at"] = ts
            task.pop("block_reason", None)
            task.pop("env_issue", None)
            task.setdefault("activity", []).append({"at": ts, "type": "env_completed", "message": "All environment setup steps confirmed."})
        else:
            task["status"] = "In progress"
            task.pop("block_reason", None)
            task.pop("env_issue", None)
            task.setdefault("activity", []).append(
                {"at": ts, "type": "env_step_feedback", "message": f"Environment step {step_id} marked {result}."}
            )
        task["updated_at"] = ts
        write_task(task)
        return task


def escalate_env_task(tid: str, note: str = "") -> tuple[dict[str, Any], str]:
    with STATE_LOCK:
        task = _env_task(tid)
        if not task:
            raise ValueError("Environment task not found.")
        issue = task.get("env_issue") or {}
        ts = now_iso()
        esc_id = next_task_id()
        steps_txt = "\n".join(
            f"- {s.get('id')}: {s.get('title')} [{s.get('status')}]\n  Instructions: {s.get('instructions')}\n  Expected: {s.get('expected') or 'n/a'}"
            for s in (task.get("env_steps") or [])
        )
        request = (
            f"Environment setup task {tid} ('{task.get('title', '')}') needs PM/PO troubleshooting or a decision change.\n"
            f"Project: {task.get('project_id') or 'default'}\n"
            f"Reported step: {issue.get('step_id') or 'unknown'} — {issue.get('step_title') or ''}\n"
            f"Result: {issue.get('result') or 'problem'}\n"
            f"User note: {note or issue.get('note') or ''}\n"
            f"Command output:\n{issue.get('output') or ''}\n\n"
            f"Steps:\n{steps_txt}\n\n"
            "Decide whether to revise the environment steps, provide corrected instructions, or escalate to the user with exact manual commands."
        )
        write_task(
            {
                "id": esc_id,
                "task_type": "pm_planning",
                "title": f"PM/PO env troubleshoot: {tid}",
                "request": request,
                "original_request": request,
                "assigned_role": "PM/PO",
                "provider": "Automatic",
                "status": "Ready",
                "project_id": task.get("project_id") or "default",
                "module_id": "",
                "produces_deliverable": False,
                "acceptance_checks": [],
                "env_for_task_id": tid,
                "created_at": ts,
                "updated_at": ts,
                "attempts": [],
                "validation": None,
                "activity": [{"at": ts, "type": "created", "message": f"PM/PO troubleshoot request for environment task {tid}."}],
            }
        )
        task.setdefault("activity", []).append({"at": ts, "type": "env_escalated", "message": f"Escalated to PM/PO as {esc_id}."})
        task["updated_at"] = ts
        write_task(task)
        append_event({"at": ts, "task_id": esc_id, "type": "env_escalation_created", "for_task": tid})
        DISPATCH_EVENT.set()
        return task, esc_id

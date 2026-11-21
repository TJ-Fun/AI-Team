"""Task JSON storage, reply lookup, and repository key resolution."""

from __future__ import annotations

from ait.common import *


# --- Tasks ---
def read_tasks() -> list[dict[str, Any]]:
    ensure_data()
    tasks: list[dict[str, Any]] = []
    for p in (DATA / "tasks").glob("*.json"):
        try:
            tasks.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    tasks.sort(key=lambda t: str(t.get("created_at", "")), reverse=True)
    return tasks


def read_task(task_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"T-\d{4,}", task_id):
        return None
    p = DATA / "tasks" / f"{task_id}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_task(task: dict[str, Any]) -> None:
    ensure_data()
    with STATE_LOCK:
        task_path = DATA / "tasks" / f"{task['id']}.json"
        previous = read_task(str(task["id"]))
        atomic_json(task_path, task)
        timestamp = str(task.get("updated_at") or now_iso())
        if previous is None:
            status = str(task.get("status") or "Unknown")
            append_event(
                {
                    "at": timestamp,
                    "type": "task_created",
                    "task_id": task["id"],
                    "status": status,
                    "project_id": task.get("project_id") or "default",
                    "task_type": task.get("task_type") or "work",
                }
            )
            logger.info(
                "Task created task_id=%s status=%s project_id=%s",
                task["id"],
                status,
                task.get("project_id") or "default",
            )
        else:
            old_status = str(previous.get("status") or "Unknown")
            new_status = str(task.get("status") or "Unknown")
            if old_status != new_status:
                append_event(
                    {
                        "at": timestamp,
                        "type": "task_status_changed",
                        "task_id": task["id"],
                        "from": old_status,
                        "to": new_status,
                        "project_id": task.get("project_id") or "default",
                    }
                )
                logger.info(
                    "Task status changed task_id=%s from=%s to=%s project_id=%s",
                    task["id"],
                    old_status,
                    new_status,
                    task.get("project_id") or "default",
                )


def next_task_id() -> str:
    with STATE_LOCK:
        nums = []
        for t in read_tasks():
            m = re.fullmatch(r"T-(\d{4,})", str(t.get("id", "")))
            if m:
                nums.append(int(m.group(1)))
        nxt = (max(nums) if nums else 0) + 1
        return f"T-{nxt:04d}"


def read_task_replies(task_id: str) -> list[dict[str, Any]]:
    task = read_task(task_id)
    if not task:
        return []
    replies: list[dict[str, Any]] = []
    for att in task.get("attempts", []) or []:
        folder = str(att.get("provider", "")).lower().replace(" ", "")
        num = att.get("number")
        if not folder or not isinstance(num, int) or num < 1:
            continue
        rp = DATA / "replies" / task_id / folder / f"attempt-{num:03d}.json"
        if rp.is_file():
            try:
                replies.append(json.loads(rp.read_text(encoding="utf-8")))
            except Exception:
                continue
    return replies


def task_repository_key(task: dict[str, Any]) -> str:
    if task.get("task_type") in ("pm_planning", "pm_escalation", "project_overview", "clarification_question", "design_review", "env_setup"):
        return ""
    role = str(task.get("assigned_role") or "")
    if role == "QA":
        role = str(task.get("qa_return_role") or "")
    if role not in ("Frontend developer", "Backend developer", "Documentation", "QA"):
        return ""
    project_id = str(task.get("project_id") or "default")
    project = next((p for p in read_projects() if p.get("id") == project_id), None)
    if not project or not project.get("repository"):
        return ""
    owner, repo = repository_parts(str(project["repository"]))
    return f"{owner.casefold()}/{repo.casefold()}"

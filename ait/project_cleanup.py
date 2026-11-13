"""Per-project destructive cleanup operations."""

from __future__ import annotations

from ait.common import *


class ProjectBusyError(ValueError):
    """Raised when a project has active work that must finish first."""


def project_or_raise(project_id: str) -> dict[str, Any]:
    pid = str(project_id or "").strip()
    project = next((p for p in read_projects() if p.get("id") == pid), None)
    if not project:
        raise ValueError("Project not found.")
    return project


def task_project_id(task: dict[str, Any]) -> str:
    return str(task.get("project_id") or "default")


def project_task_ids(project_id: str) -> list[str]:
    pid = str(project_id or "default")
    ids: list[str] = []
    for task in read_tasks():
        if task_project_id(task) != pid:
            continue
        tid = str(task.get("id") or "").strip()
        if tid:
            ids.append(tid)
    return ids


def active_project_task_ids(project_id: str) -> list[str]:
    pid = str(project_id or "default")
    active: list[str] = []
    for task in read_tasks():
        if task_project_id(task) != pid:
            continue
        if str(task.get("status") or "") == "In progress":
            tid = str(task.get("id") or "").strip()
            if tid:
                active.append(tid)
    return active


def running_clone_for_project(project_id: str) -> bool:
    pid = str(project_id or "")
    with CLONE_JOBS_LOCK:
        return any(
            str(job.get("project_id") or "") == pid and job.get("status") == "running"
            for job in CLONE_JOBS.values()
        )


def assert_no_active_project_work(project_id: str) -> None:
    active = active_project_task_ids(project_id)
    if active:
        raise ProjectBusyError(
            "Project has In progress tasks: " + ", ".join(active) + ". Wait for them to finish first."
        )
    if running_clone_for_project(project_id):
        raise ProjectBusyError("A Git clone is running for this project. Wait for it to finish first.")


def clear_project_tasks(project_id: str) -> dict[str, Any]:
    project = project_or_raise(project_id)
    pid = str(project["id"])
    with STATE_LOCK:
        assert_no_active_project_work(pid)
        task_ids = project_task_ids(pid)
        id_set = set(task_ids)
        deleted_tasks = 0
        deleted_replies = 0
        for tid in task_ids:
            tpath = DATA / "tasks" / f"{tid}.json"
            artifacts = [tpath, tpath.with_suffix(".json.bak"), tpath.with_suffix(".json.tmp")]
            if any(p.exists() for p in artifacts):
                deleted_tasks += 1
            for path in artifacts:
                path.unlink(missing_ok=True)
            replies_dir = DATA / "replies" / tid
            if replies_dir.exists():
                shutil.rmtree(replies_dir, ignore_errors=True)
                deleted_replies += 1

        log_path = DATA / "logs" / "activity.jsonl"
        if log_path.is_file() and id_set:
            kept: list[str] = []
            for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    if str(json.loads(line).get("task_id") or "") in id_set:
                        continue
                except Exception:
                    pass
                kept.append(line + "\n")
            atomic_text(log_path, "".join(kept))

        append_event(
            {
                "at": now_iso(),
                "type": "project_tasks_cleared",
                "project_id": pid,
                "tasks": deleted_tasks,
                "replies": deleted_replies,
            }
        )
    return {"project_id": pid, "deleted_tasks": deleted_tasks, "deleted_replies": deleted_replies}

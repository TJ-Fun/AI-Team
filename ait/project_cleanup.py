"""Per-project destructive cleanup operations."""

from __future__ import annotations

from ait.common import *


PROJECT_MAINTENANCE: set[str] = set()


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


def _safe_local_clone_path(project: dict[str, Any]) -> Path | None:
    local_path = str(project.get("local_path") or "").strip()
    if not local_path:
        return None
    try:
        path = Path(local_path).expanduser().resolve()
    except Exception as exc:
        raise ValueError(f"Invalid local path: {exc}") from exc
    if path == path.parent:
        raise ValueError("Refusing to delete the filesystem root.")
    if path == Path.home().resolve():
        raise ValueError("Refusing to delete the home directory.")
    for protected in (ROOT, DATA):
        if path == protected or protected in path.parents or path in protected.parents:
            raise ValueError("Refusing to delete a protected application path.")
    return path


def _local_clone_ready(project: dict[str, Any]) -> Path | None:
    path = _safe_local_clone_path(project)
    if path is None or not path.exists():
        return None
    if not (path / ".git").exists():
        raise ValueError("Configured local path is not a git clone; refusing to delete it.")
    return path


def _assert_local_clone_clean(project: dict[str, Any]) -> None:
    if _local_clone_ready(project) is None:
        return
    status = git_status(str(project.get("id") or ""))
    if int(status.get("modified") or 0) or bool(status.get("ahead")):
        raise ProjectBusyError(
            "Local clone has uncommitted changes or unpushed commits. Commit, push, or clean it before wiping."
        )


def _delete_local_clone(project: dict[str, Any]) -> dict[str, Any]:
    path = _local_clone_ready(project)
    if path is None:
        return {"deleted": False, "path": ""}
    shutil.rmtree(path, ignore_errors=True)
    return {"deleted": True, "path": str(path)}


def _same_repository(a: str, b: str) -> bool:
    try:
        return canonical_github_repository(a) == canonical_github_repository(b)
    except Exception:
        return False


def assert_unique_project_repository(project: dict[str, Any]) -> None:
    pid = str(project.get("id") or "")
    repository = str(project.get("repository") or "").strip()
    if not repository:
        raise ValueError("Project does not have a GitHub repository configured.")
    for other in read_projects():
        if str(other.get("id") or "") == pid:
            continue
        other_repo = str(other.get("repository") or "").strip()
        if other_repo and _same_repository(repository, other_repo):
            raise ValueError("Another project uses this GitHub repository; refusing to wipe it.")


def wipe_project_github(project_id: str, confirm_text: str) -> dict[str, Any]:
    project = project_or_raise(project_id)
    pid = str(project["id"])
    repository = str(project.get("repository") or "").strip()
    if not repository:
        raise ValueError("Project does not have a GitHub repository configured.")
    owner, repo = canonical_github_repository(repository)
    expected = f"{owner}/{repo}"
    if str(confirm_text or "").strip().casefold() != expected.casefold():
        raise ValueError(f"Type {expected} to confirm the GitHub wipe.")
    assert_unique_project_repository(project)
    _assert_local_clone_clean(project)

    with STATE_LOCK:
        PROJECT_MAINTENANCE.add(pid)
    try:
        with STATE_LOCK:
            assert_no_active_project_work(pid)
        remote = github_wipe_repository_contents(repository)
        if remote.get("errors"):
            details = "; ".join(str(item) for item in remote["errors"][:5])
            raise ValueError(f"GitHub wipe incomplete; local clone was not deleted. {details}")
        local = _delete_local_clone(project)
        append_event(
            {
                "at": now_iso(),
                "type": "project_github_wiped",
                "project_id": pid,
                "repository": f"{owner}/{repo}",
                "commit": remote.get("commit"),
                "deleted_releases": remote.get("deleted_releases", 0),
                "deleted_branches": remote.get("deleted_branches", 0),
                "deleted_tags": remote.get("deleted_tags", 0),
                "local_clone_deleted": bool(local.get("deleted")),
            }
        )
    finally:
        with STATE_LOCK:
            PROJECT_MAINTENANCE.discard(pid)
    return {
        "project_id": pid,
        "repository": f"{owner}/{repo}",
        "default_branch": remote.get("default_branch"),
        "commit": remote.get("commit"),
        "deleted_releases": remote.get("deleted_releases", 0),
        "deleted_branches": remote.get("deleted_branches", 0),
        "deleted_tags": remote.get("deleted_tags", 0),
        "local_clone": local,
    }

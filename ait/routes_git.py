"""Git operation API routes."""

from __future__ import annotations

from ait.common import *


def handle_git_get(handler, path, query) -> bool:
    m_clone_job = re.fullmatch(r"/api/git/([a-z0-9-]+)/clone/([a-f0-9]{32})", path)
    if m_clone_job:
        try:
            handler.send_json(get_git_clone_job(m_clone_job.group(1), m_clone_job.group(2)))
            return True
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return True

    m_git_status = re.fullmatch(r"/api/git/([a-z0-9-]+)/status", path)
    if m_git_status:
        project = next((p for p in read_projects() if p.get("id") == m_git_status.group(1)), None)
        if not project:
            handler.send_json({"error": "Project not found."}, HTTPStatus.NOT_FOUND)
            return True
        local_path = str(project.get("local_path", "")).strip()
        if not local_path or not Path(local_path).expanduser().is_dir():
            handler.send_json({"available": False, "message": "Not cloned yet."})
            return True
        try:
            handler.send_json({"available": True, "status": git_status(m_git_status.group(1))})
            return True
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True

    return False


def handle_git_post(handler, path, body) -> bool:
    # Git operation endpoints
    m_git_status = re.fullmatch(r"/api/git/([a-z0-9-]+)/status", path)
    if m_git_status:
        pid = m_git_status.group(1)
        try:
            status = git_status(pid)
            handler.send_json({"status": status})
            return True
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True

    m_git_pull = re.fullmatch(r"/api/git/([a-z0-9-]+)/pull", path)
    if m_git_pull:
        pid = m_git_pull.group(1)
        try:
            result = git_pull(pid)
            append_event({"at": now_iso(), "type": "git_pull", "project_id": pid, "success": True})
            handler.send_json(result)
            return True
        except ValueError as exc:
            logger.exception("Git pull request failed (project=%s)", pid)
            append_event({"at": now_iso(), "type": "git_pull", "project_id": pid, "success": False, "error": str(exc)[:300]})
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True

    m_git_push = re.fullmatch(r"/api/git/([a-z0-9-]+)/push", path)
    if m_git_push:
        pid = m_git_push.group(1)
        try:
            result = git_push(pid)
            append_event({"at": now_iso(), "type": "git_push", "project_id": pid, "success": True})
            handler.send_json(result)
            return True
        except ValueError as exc:
            logger.exception("Git push request failed (project=%s)", pid)
            append_event({"at": now_iso(), "type": "git_push", "project_id": pid, "success": False, "error": str(exc)[:300]})
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True

    m_git_clone = re.fullmatch(r"/api/git/([a-z0-9-]+)/clone", path)
    if m_git_clone:
        pid = m_git_clone.group(1)
        try:
            job = start_git_clone(pid)
            logger.info("Git clone job accepted (project=%s, job=%s)", pid, job["job_id"])
            append_event({"at": now_iso(), "type": "git_clone", "project_id": pid, "job_id": job["job_id"]})
            handler.send_json(job, HTTPStatus.ACCEPTED)
            return True
        except ValueError as exc:
            logger.error("Git clone request rejected (project=%s): %s", pid, exc)
            append_event({"at": now_iso(), "type": "git_clone", "project_id": pid, "success": False, "error": str(exc)[:300]})
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True
        except Exception as exc:
            logger.exception("Could not start Git clone job (project=%s)", pid)
            handler.send_json({"error": f"Could not start Git clone: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return True

    return False


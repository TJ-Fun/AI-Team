"""API routes for manual environment setup tasks."""

from __future__ import annotations

from ait.common import *


def handle_env_tasks_post(handler, path, body) -> bool:
    if path == "/api/env-tasks":
        title = str(body.get("title", "")).strip()
        request = str(body.get("request", "")).strip()
        pid = str(body.get("project_id") or "default")
        mid = str(body.get("module_id") or "")
        status = str(body.get("status") or "Ready")
        if not title or not request:
            handler.send_json({"error": "Title and request are required."}, HTTPStatus.BAD_REQUEST)
            return True
        proj = next((p for p in read_projects() if p.get("id") == pid), None)
        if not proj or (mid and not any(m.get("id") == mid for m in proj.get("modules", []))):
            handler.send_json({"error": "Choose a valid project and sub-project."}, HTTPStatus.BAD_REQUEST)
            return True
        try:
            task = create_env_task(
                title=title,
                request=request,
                project_id=pid,
                module_id=mid,
                steps=body.get("steps"),
                status=status,
            )
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True
        handler.send_json({"task": task}, HTTPStatus.CREATED)
        return True

    m_env = re.fullmatch(r"/api/tasks/(T-\d{4,})/(env-start|env-feedback|env-escalate)", path)
    if not m_env:
        return False
    tid, action = m_env.group(1), m_env.group(2)
    try:
        if action == "env-start":
            task = start_env_task(tid)
            handler.send_json({"task": task})
            return True
        if action == "env-feedback":
            task = submit_env_step_feedback(
                tid,
                str(body.get("step_id", "")),
                str(body.get("result", "")),
                str(body.get("output", "")),
                str(body.get("note", "")),
            )
            handler.send_json({"task": task})
            return True
        task, esc_id = escalate_env_task(tid, str(body.get("note", "")))
        handler.send_json({"task": task, "escalation_task_id": esc_id}, HTTPStatus.CREATED)
        return True
    except ValueError as exc:
        handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        return True

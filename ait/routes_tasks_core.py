"""Core task API routes: list, replies, create, edit, and delete."""

from __future__ import annotations

from ait.common import *


def handle_tasks_core_get(handler, path, query) -> bool:
    if path == "/api/tasks":
        handler.send_json({"tasks": read_tasks()})
        return True

    m_replies = re.fullmatch(r"/api/tasks/(T-\d{4,})/replies", path)
    if m_replies:
        tid = m_replies.group(1)
        if not read_task(tid):
            handler.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
            return True
        handler.send_json({"replies": read_task_replies(tid)})
        return True

    return False


def handle_tasks_core_post(handler, path, body) -> bool:
    if path == "/api/tasks":
        title = str(body.get("title", "")).strip()
        req_text = str(body.get("request", "")).strip()
        role = str(body.get("role", "PM/PO")).strip()
        provider = str(body.get("provider", "Automatic")).strip()
        raw_checks = body.get("acceptance_checks", [])
        produces = bool(body.get("produces_deliverable"))
        logger.info(f"Creating task: title='{title}', role={role}, provider={provider}")
        if not title or not req_text or len(title) > 160 or len(req_text) > 12000:
            handler.send_json({"error": "Valid title and request are required."}, HTTPStatus.BAD_REQUEST)
            return True
        if role not in WORK_ROLES or provider not in task_provider_options():
            handler.send_json({"error": "Choose a valid work role and AI provider."}, HTTPStatus.BAD_REQUEST)
            return True
        pid = str(body.get("project_id") or "default")
        mid = str(body.get("module_id") or "")
        proj = next((p for p in read_projects() if p.get("id") == pid), None)
        if not proj or (mid and not any(m.get("id") == mid for m in proj.get("modules", []))):
            handler.send_json({"error": "Choose a valid project and sub-project."}, HTTPStatus.BAD_REQUEST)
            return True
        if not isinstance(raw_checks, list):
            handler.send_json({"error": "Acceptance checks must be a list."}, HTTPStatus.BAD_REQUEST)
            return True
        checks = [str(c).strip()[:500] for c in raw_checks if str(c).strip()][:20]
        if (
            proj.get("repository")
            and role in ("Frontend developer", "Backend developer", "Documentation")
            and (not produces or not checks)
        ):
            handler.send_json(
                {"error": "Repository work must be marked as a deliverable and include at least one acceptance check."},
                HTTPStatus.BAD_REQUEST,
            )
            return True
        ts = now_iso()
        tid = next_task_id()
        task = {
            "id": tid,
            "task_type": "work",
            "title": title,
            "request": req_text,
            "assigned_role": role,
            "provider": provider,
            "project_id": pid,
            "module_id": mid,
            "acceptance_checks": checks,
            "produces_deliverable": produces,
            "status": "Draft",
            "created_at": ts,
            "updated_at": ts,
            "attempts": [],
            "validation": None,
            "activity": [{"at": ts, "type": "created", "message": "Task created as Draft."}],
        }
        write_task(task)
        handler.send_json({"task": task}, HTTPStatus.CREATED)
        return True

    return False


def handle_tasks_core_put(handler, path, body) -> bool:
    m_ov = re.fullmatch(r"/api/tasks/(T-\d{4,})/overview", path)
    if m_ov:
        tid = m_ov.group(1)
        try:
            documents = clean_overview_documents(body)
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True
        with STATE_LOCK:
            task = read_task(tid)
            if not task:
                handler.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                return True
            if task.get("task_type") != "project_overview" or task.get("status") != "Needs approval":
                handler.send_json({"error": "Overview is no longer awaiting approval."}, HTTPStatus.CONFLICT)
                return True
            ts = now_iso()
            task["overview_documents"] = documents
            task["updated_at"] = ts
            task.setdefault("activity", []).append(
                {"at": ts, "type": "overview_edited", "message": "User edited proposed project overview."}
            )
            write_task(task)
        handler.send_json({"task": task})
        return True

    m_task = re.fullmatch(r"/api/tasks/(T-\d{4,})", path)
    if m_task:
        tid = m_task.group(1)
        title = str(body.get("title", "")).strip()
        req_text = str(body.get("request", "")).strip()
        role = str(body.get("role", "")).strip()
        provider = str(body.get("provider", "Automatic")).strip()
        raw_checks = body.get("acceptance_checks", [])
        produces = bool(body.get("produces_deliverable"))
        if not title or not req_text or len(title) > 160 or len(req_text) > 12000:
            handler.send_json({"error": "Enter a valid title and request."}, HTTPStatus.BAD_REQUEST)
            return True
        if role not in WORK_ROLES or provider not in task_provider_options() or not isinstance(raw_checks, list):
            handler.send_json({"error": "Invalid role, provider, or acceptance checks."}, HTTPStatus.BAD_REQUEST)
            return True
        pid = str(body.get("project_id") or "default")
        mid = str(body.get("module_id") or "")
        proj = next((p for p in read_projects() if p.get("id") == pid), None)
        if not proj or (mid and not any(m.get("id") == mid for m in proj.get("modules", []))):
            handler.send_json({"error": "Choose a valid project and sub-project."}, HTTPStatus.BAD_REQUEST)
            return True
        checks = [str(c).strip()[:500] for c in raw_checks if str(c).strip()][:20]
        with STATE_LOCK:
            task = read_task(tid)
            if not task:
                handler.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                return True
            if task.get("status") not in ("Draft", "Blocked", "Rework needed", "Waiting for capacity"):
                handler.send_json(
                    {"error": "Only Draft, Blocked, Rework needed, or Waiting for capacity tasks can be edited."},
                    HTTPStatus.CONFLICT,
                )
                return True
            ts = now_iso()
            task.update(
                {
                    "title": title,
                    "request": req_text,
                    "assigned_role": role,
                    "provider": provider,
                    "project_id": pid,
                    "module_id": mid,
                    "acceptance_checks": checks,
                    "produces_deliverable": produces,
                    "updated_at": ts,
                }
            )
            task.setdefault("activity", []).append({"at": ts, "type": "edited", "message": "Task details updated."})
            write_task(task)
        handler.send_json({"task": task})
        return True

    return False


def handle_tasks_core_delete(handler, path) -> bool:
    m_task = re.fullmatch(r"/api/tasks/(T-\d{4,})", path)
    if m_task:
        tid = m_task.group(1)
        with STATE_LOCK:
            tpath = DATA / "tasks" / f"{tid}.json"
            rdir = DATA / "replies" / tid
            lpath = DATA / "logs" / "activity.jsonl"
            artifacts = [tpath, tpath.with_suffix(".json.bak"), tpath.with_suffix(".json.tmp")]
            existed = any(p.exists() for p in artifacts) or rdir.exists()
            if not existed:
                handler.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                return True
            if lpath.is_file():
                kept = []
                for line in lpath.read_text(encoding="utf-8", errors="replace").splitlines():
                    if not line.strip():
                        continue
                    try:
                        if json.loads(line).get("task_id") == tid:
                            continue
                    except Exception:
                        pass
                    kept.append(line + "\n")
                atomic_text(lpath, "".join(kept))
            for p in artifacts:
                p.unlink(missing_ok=True)
            if rdir.exists():
                shutil.rmtree(rdir, ignore_errors=True)
        handler.send_json({"deleted": True, "task_id": tid})
        return True

    return False

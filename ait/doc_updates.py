"""PM/PO project-document and roadmap update task handling."""

from __future__ import annotations

from ait.common import *
from ait.format_retry import maybe_format_retry


DOC_UPDATE_SLUGS = ("project_brief", "technical_overview", "decisions", "outcomes")
DOC_UPDATE_TASK_TYPES = ("doc_update", "roadmap_update", "vision_update", "board_delta", "doc_edit")
DOC_EDIT_CORE_SLUGS = ("project_brief", "technical_overview", "decisions", "outcomes")
DOC_EDIT_STATIC_SLUGS = frozenset(DOC_EDIT_CORE_SLUGS) | {ROADMAP_DOC_SLUG}
MAX_DOC_CHARS = 30000
MAX_DOC_EDIT_REQUEST_CHARS = 8000


def is_doc_edit_slug(slug: str) -> bool:
    """True for PM/PO-editable docs: 4 core docs, roadmap, phase_N. Excludes vision/trackers."""
    return slug in DOC_EDIT_STATIC_SLUGS or bool(PHASE_DOC_RE.fullmatch(slug))


def _parse_reply_json(reply: str) -> dict[str, Any]:
    clean = reply.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean, flags=re.IGNORECASE).strip()
    data = json.loads(clean)
    if not isinstance(data, dict):
        raise ValueError("PM/PO update reply must be a JSON object.")
    return data


def _clean_doc_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} must contain non-empty text.")
    if len(text) > MAX_DOC_CHARS:
        raise ValueError(f"{label} must be under {MAX_DOC_CHARS} characters.")
    return text


def parse_doc_update_response(reply: str, slug: str | None = None) -> dict[str, str]:
    data = _parse_reply_json(reply)
    if data.get("type") != "doc_update":
        raise ValueError("Document update reply must have type 'doc_update'.")
    docs = data.get("documents")
    if not isinstance(docs, dict):
        raise ValueError("Document update reply needs a 'documents' object.")
    key = str(slug or "").strip()
    if key:
        if key not in DOC_UPDATE_SLUGS:
            raise ValueError(f"Unknown document slug: {key}.")
        return {key: _clean_doc_text(docs.get(key), project_doc_title(key))}
    cleaned: dict[str, str] = {}
    for slug in DOC_UPDATE_SLUGS:
        cleaned[slug] = _clean_doc_text(docs.get(slug), project_doc_title(slug))
    return cleaned


def parse_roadmap_update_response(reply: str) -> dict[str, Any]:
    data = _parse_reply_json(reply)
    if data.get("type") != "roadmap_update":
        raise ValueError("Roadmap update reply must have type 'roadmap_update'.")
    roadmap = _clean_doc_text(data.get("roadmap") or data.get("master_plan"), "Roadmap")
    phases: dict[str, str] = {}
    raw_phases = data.get("phases")
    if raw_phases is not None:
        if not isinstance(raw_phases, dict):
            raise ValueError("Roadmap update 'phases' must be an object.")
        for slug, value in raw_phases.items():
            key = str(slug).strip()
            if not PHASE_DOC_RE.fullmatch(key):
                raise ValueError(f"Invalid phase document slug: {key or '(empty)'}.")
            phases[key] = _clean_doc_text(value, project_doc_title(key))
    active_phase = data.get("active_phase")
    if active_phase is not None:
        try:
            active_phase = int(active_phase)
        except (TypeError, ValueError):
            raise ValueError("active_phase must be a phase number or null.") from None
        if not 1 <= active_phase <= 9:
            raise ValueError("active_phase must be between 1 and 9.")
    return {"roadmap": roadmap, "phases": phases, "active_phase": active_phase}


def parse_doc_edit_response(reply: str) -> dict[str, Any]:
    data = _parse_reply_json(reply)
    if data.get("type") != "doc_edit":
        raise ValueError("Document edit reply must have type 'doc_edit'.")
    docs = data.get("documents")
    if not isinstance(docs, dict) or not docs:
        raise ValueError("Document edit reply needs a non-empty 'documents' object.")
    cleaned: dict[str, str] = {}
    for slug, value in docs.items():
        key = str(slug).strip()
        if not is_doc_edit_slug(key):
            raise ValueError(f"Document '{key or '(empty)'}' cannot be edited by PM/PO.")
        cleaned[key] = _clean_doc_text(value, project_doc_title(key))
    active_phase = data.get("active_phase")
    if active_phase is not None:
        try:
            active_phase = int(active_phase)
        except (TypeError, ValueError):
            raise ValueError("active_phase must be a phase number or null.") from None
        if not 1 <= active_phase <= 9:
            raise ValueError("active_phase must be between 1 and 9.")
    return {"documents": cleaned, "active_phase": active_phase}


def _finish_update_task(task: dict[str, Any], reply: str, provider: str, parser, label: str) -> tuple[str, str]:
    try:
        payload = parser(reply)
    except Exception as exc:
        msg = str(exc)
        logger.warning("PM/PO %s rejected task_id=%s provider=%s: %s", label, task.get("id"), provider, msg)
        append_event(
            {
                "at": now_iso(),
                "type": "pm_update_rejected",
                "task_id": task.get("id"),
                "provider": provider,
                "error": msg[:1000],
            }
        )
        task["validation"] = {
            "status": "failed",
            "checks": [{"check": "PM/PO update structure", "kind": "schema", "passed": False, "message": msg}],
            "validated_at": now_iso(),
        }
        retried, retry_msg = maybe_format_retry(task, provider, msg)
        if retried:
            return "Ready", retry_msg
        task["planning_error"] = msg
        return "Rework needed", f"{provider} replied, but the {label} could not be logged: {msg}"
    ts = now_iso()
    task["update_payload"] = payload
    task.pop("format_retry_feedback", None)
    task["validation"] = {
        "status": "passed",
        "checks": [{"check": "PM/PO update structure", "kind": "schema", "passed": True, "message": "Response parsed and saved."}],
        "validated_at": ts,
    }
    return "Needs approval", f"PM/PO prepared a {label}; review the proposed changes and approve or reject them."


def finish_doc_update_task(task: dict[str, Any], reply: str, provider: str) -> tuple[str, str]:
    slug = str(task.get("doc_slug") or "").strip() or None
    return _finish_update_task(
        task, reply, provider, lambda r: parse_doc_update_response(r, slug), "project document update"
    )


def finish_roadmap_update_task(task: dict[str, Any], reply: str, provider: str) -> tuple[str, str]:
    return _finish_update_task(task, reply, provider, parse_roadmap_update_response, "roadmap update")


def finish_doc_edit_task(task: dict[str, Any], reply: str, provider: str) -> tuple[str, str]:
    return _finish_update_task(task, reply, provider, parse_doc_edit_response, "project document edit")


def _apply_active_phase(pid: str, active_phase: Any) -> None:
    if active_phase is None:
        return
    projects = read_projects()
    for proj in projects:
        if proj.get("id") == pid:
            proj["active_phase"] = active_phase
    save_projects(projects)


def apply_doc_update(task: dict[str, Any]) -> dict[str, Any]:
    """Write an approved update payload to project documents. Returns snapshot info."""
    pid = str(task.get("project_id") or "default")
    payload = task.get("update_payload") or {}
    snapshots: dict[str, str | None] = {}
    if task.get("task_type") == "doc_update":
        for slug, content in payload.items():
            if slug in DOC_UPDATE_SLUGS:
                snapshots[slug] = save_project_context_document(pid, slug, str(content))
    elif task.get("task_type") == "vision_update":
        snapshots["project_vision"] = save_project_context_document(pid, "project_vision", str(payload.get("project_vision") or ""))
    elif task.get("task_type") == "board_delta":
        return apply_board_delta_update(task)
    elif task.get("task_type") == "roadmap_update":
        snapshots[ROADMAP_DOC_SLUG] = save_project_context_document(pid, ROADMAP_DOC_SLUG, str(payload.get("roadmap") or payload.get("master_plan") or ""))
        for slug, content in (payload.get("phases") or {}).items():
            snapshots[slug] = save_project_context_document(pid, slug, str(content))
        _apply_active_phase(pid, payload.get("active_phase"))
    elif task.get("task_type") == "doc_edit":
        for slug, content in (payload.get("documents") or {}).items():
            key = str(slug).strip()
            if is_doc_edit_slug(key):
                snapshots[key] = save_project_context_document(pid, key, str(content))
        _apply_active_phase(pid, payload.get("active_phase"))
    return {"snapshots": snapshots}


def create_doc_edit_task(project_id: str, request: str, documents: list[str] | None = None) -> dict[str, Any]:
    """Create a Ready doc_edit task from an agreed chat change. Returns the task."""
    pid = str(project_id or "default")
    req = str(request or "").strip()
    if not req:
        raise ValueError("A document edit request is required.")
    if len(req) > MAX_DOC_EDIT_REQUEST_CHARS:
        raise ValueError("Document edit request is too long.")
    targets = [str(s).strip() for s in (documents or []) if is_doc_edit_slug(str(s).strip())]
    target_text = ", ".join(targets) if targets else "the documents discussed in the conversation"
    ts = now_iso()
    tid = next_task_id()
    task = {
        "id": tid,
        "task_type": "doc_edit",
        "title": "PM/PO document edit: " + re.sub(r"\s+", " ", req)[:110],
        "request": (
            "Apply this agreed project-document change:\n\n" + req
            + f"\n\nTarget documents: {target_text}. "
            "Request the current versions you need via info_requests, then return the full updated text "
            "for each document you change under the doc_edit contract. Only include documents that actually change."
        ),
        "project_id": pid,
        "module_id": "",
        "assigned_role": "PM/PO",
        "provider": "Automatic",
        "status": "Ready",
        "produces_deliverable": False,
        "acceptance_checks": [],
        "attempts": [],
        "validation": None,
        "created_at": ts,
        "updated_at": ts,
        "dispatch_note": "Document edit queued for the PM/PO agent.",
        "activity": [{"at": ts, "type": "created", "message": "Document edit requested from PM/PO chat."}],
    }
    write_task(task)
    DISPATCH_EVENT.set()
    return task


def decide_doc_update(task_id: str, decision: str) -> dict[str, Any]:
    """Approve or reject a pending document/roadmap update. Returns the updated task."""
    tid = str(task_id)
    choice = str(decision or "").strip().lower()
    if choice not in ("approve", "reject"):
        raise ValueError("Decision must be approve or reject.")
    with STATE_LOCK:
        task = read_task(tid)
        if not task:
            raise KeyError("Update task not found.")
        if task.get("task_type") not in DOC_UPDATE_TASK_TYPES or task.get("status") != "Needs approval":
            raise ValueError("This update is no longer awaiting a decision.")
        ts = now_iso()
        if choice == "approve":
            result = apply_doc_update(task)
            task["status"] = "Done"
            task["approved_at"] = ts
            task["validation"] = {
                "status": "passed",
                "method": "user_approval",
                "reviewed_by": "User",
                "notes": "Approved and applied to project documents.",
                "snapshots": result["snapshots"],
                "validated_at": ts,
            }
            task.setdefault("activity", []).append(
                {"at": ts, "type": "update_approved", "from": "Needs approval", "to": "Done", "message": "User approved the proposed project document update."}
            )
        else:
            task["status"] = "Canceled"
            task["validation"] = {
                "status": "rejected",
                "method": "user_approval",
                "reviewed_by": "User",
                "notes": "Rejected by user; project documents unchanged.",
                "validated_at": ts,
            }
            task.setdefault("activity", []).append(
                {"at": ts, "type": "update_rejected", "from": "Needs approval", "to": "Canceled", "message": "User rejected the proposed project document update."}
            )
        task["updated_at"] = ts
        write_task(task)
        append_event({"at": ts, "task_id": tid, "type": "doc_update_decision", "decision": choice})
        return task

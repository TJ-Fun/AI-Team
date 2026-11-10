"""PM/PO project-document and roadmap update task handling."""

from __future__ import annotations

from ait.common import *


DOC_UPDATE_SLUGS = ("project_brief", "technical_overview", "decisions", "outcomes")
DOC_UPDATE_TASK_TYPES = ("doc_update", "roadmap_update")
MAX_DOC_CHARS = 20000


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


def parse_doc_update_response(reply: str) -> dict[str, str]:
    data = _parse_reply_json(reply)
    if data.get("type") != "doc_update":
        raise ValueError("Document update reply must have type 'doc_update'.")
    docs = data.get("documents")
    if not isinstance(docs, dict):
        raise ValueError("Document update reply needs a 'documents' object.")
    cleaned: dict[str, str] = {}
    for slug in DOC_UPDATE_SLUGS:
        cleaned[slug] = _clean_doc_text(docs.get(slug), project_doc_title(slug))
    return cleaned


def parse_roadmap_update_response(reply: str) -> dict[str, Any]:
    data = _parse_reply_json(reply)
    if data.get("type") != "roadmap_update":
        raise ValueError("Roadmap update reply must have type 'roadmap_update'.")
    master_plan = _clean_doc_text(data.get("master_plan"), "Master plan")
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
    return {"master_plan": master_plan, "phases": phases, "active_phase": active_phase}


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
        task["planning_error"] = msg
        return "Rework needed", f"{provider} replied, but the {label} could not be logged: {msg}"
    ts = now_iso()
    task["update_payload"] = payload
    task["validation"] = {
        "status": "passed",
        "checks": [{"check": "PM/PO update structure", "kind": "schema", "passed": True, "message": "Response parsed and saved."}],
        "validated_at": ts,
    }
    return "Needs approval", f"PM/PO prepared a {label}; review the proposed changes and approve or reject them."


def finish_doc_update_task(task: dict[str, Any], reply: str, provider: str) -> tuple[str, str]:
    return _finish_update_task(task, reply, provider, parse_doc_update_response, "project document update")


def finish_roadmap_update_task(task: dict[str, Any], reply: str, provider: str) -> tuple[str, str]:
    return _finish_update_task(task, reply, provider, parse_roadmap_update_response, "roadmap update")


def apply_doc_update(task: dict[str, Any]) -> dict[str, Any]:
    """Write an approved update payload to project documents. Returns snapshot info."""
    pid = str(task.get("project_id") or "default")
    payload = task.get("update_payload") or {}
    snapshots: dict[str, str | None] = {}
    if task.get("task_type") == "doc_update":
        for slug, content in payload.items():
            if slug in DOC_UPDATE_SLUGS:
                snapshots[slug] = save_project_context_document(pid, slug, str(content))
    elif task.get("task_type") == "roadmap_update":
        snapshots[ROADMAP_DOC_SLUG] = save_project_context_document(pid, ROADMAP_DOC_SLUG, str(payload.get("master_plan") or ""))
        for slug, content in (payload.get("phases") or {}).items():
            snapshots[slug] = save_project_context_document(pid, slug, str(content))
        if payload.get("active_phase") is not None:
            projects = read_projects()
            for proj in projects:
                if proj.get("id") == pid:
                    proj["active_phase"] = payload["active_phase"]
            save_projects(projects)
    return {"snapshots": snapshots}


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

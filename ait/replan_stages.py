"""Replan chain stage subtask handlers: doc sync and vision proposals."""

from __future__ import annotations

from ait.common import *
from ait.doc_updates import _finish_update_task, _parse_reply_json

CHAIN_DOC_SLUGS = ("project_brief", "technical_overview", "decisions", "outcomes")


def parse_chain_doc_response(reply: str) -> dict[str, str]:
    data = _parse_reply_json(reply)
    if data.get("type") != "chain_doc":
        raise ValueError("Chain doc reply must have type 'chain_doc'.")
    docs = data.get("documents")
    if not isinstance(docs, dict) or not docs:
        raise ValueError("chain_doc needs a non-empty 'documents' object.")
    cleaned: dict[str, str] = {}
    for slug, value in docs.items():
        key = str(slug).strip()
        if key not in CHAIN_DOC_SLUGS:
            raise ValueError(f"chain_doc cannot update '{key}'; allowed: {', '.join(CHAIN_DOC_SLUGS)}.")
        text = str(value or "").strip()
        if not text or len(text) > MAX_DOC_CHARS:
            raise ValueError(f"Document '{key}' must be non-empty and under 20,000 characters.")
        cleaned[key] = text
    return cleaned


def finish_chain_doc_task(task: dict[str, Any], reply: str, provider: str) -> tuple[str, str]:
    """Auto-apply chain doc updates inside an approved stage; no user approval per subtask."""
    try:
        docs = parse_chain_doc_response(reply)
    except Exception as exc:
        msg = str(exc)
        logger.warning("Chain doc rejected task_id=%s provider=%s: %s", task.get("id"), provider, msg)
        append_event(
            {"at": now_iso(), "type": "chain_doc_rejected", "task_id": task.get("id"), "provider": provider, "error": msg[:1000]}
        )
        task["validation"] = {
            "status": "failed",
            "checks": [{"check": "chain doc structure", "kind": "schema", "passed": False, "message": msg}],
            "validated_at": now_iso(),
        }
        retried, retry_msg = maybe_format_retry(task, provider, msg)
        if retried:
            return "Ready", retry_msg
        task["planning_error"] = msg
        return "Rework needed", f"{provider} replied, but the chain doc update could not be logged: {msg}"
    ts = now_iso()
    pid = str(task.get("project_id") or "default")
    applied: list[str] = []
    for slug, content in docs.items():
        save_project_context_document(pid, slug, content)
        applied.append(slug)
    task["chain_doc_applied"] = applied
    task.pop("format_retry_feedback", None)
    task["validation"] = {
        "status": "passed",
        "method": "auto_apply",
        "checks": [{"check": "chain doc structure", "kind": "schema", "passed": True, "message": "Documents applied automatically."}],
        "validated_at": ts,
    }
    return "Done", f"Chain doc subtask applied updates to: {', '.join(applied)}."


def parse_vision_update_response(reply: str) -> dict[str, str]:
    data = _parse_reply_json(reply)
    if data.get("type") != "vision_update":
        raise ValueError("Vision update reply must have type 'vision_update'.")
    text = str(data.get("project_vision") or "").strip()
    if not text or len(text) > MAX_DOC_CHARS:
        raise ValueError("project_vision must be non-empty and under 20,000 characters.")
    return {"project_vision": text}


def finish_vision_update_task(task: dict[str, Any], reply: str, provider: str) -> tuple[str, str]:
    return _finish_update_task(task, reply, provider, parse_vision_update_response, "project vision update")


BOARD_DELTA_OPS = ("cancel", "supersede", "create")


def parse_board_delta_response(reply: str) -> dict[str, Any]:
    data = _parse_reply_json(reply)
    if data.get("type") != "board_delta":
        raise ValueError("Board delta reply must have type 'board_delta'.")
    summary = str(data.get("summary") or "").strip()[:500]
    raw = data.get("mutations")
    if not isinstance(raw, list) or not raw:
        raise ValueError("board_delta needs a non-empty 'mutations' array.")
    if len(raw) > 50:
        raise ValueError("board_delta supports at most 50 mutations.")
    mutations: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Each board mutation must be an object.")
        op = str(item.get("op") or "").strip()
        if op not in BOARD_DELTA_OPS:
            raise ValueError(f"Invalid board mutation op '{op}'; allowed: {', '.join(BOARD_DELTA_OPS)}.")
        mut: dict[str, Any] = {"op": op, "reason": str(item.get("reason") or "")[:500]}
        if op in ("cancel", "supersede"):
            mut["task_id"] = str(item.get("task_id") or "").strip()
            if not mut["task_id"]:
                raise ValueError(f"{op} mutation needs a task_id.")
        if op in ("supersede", "create"):
            mut["title"] = str(item.get("title") or "").strip()[:200]
            mut["request"] = str(item.get("request") or "").strip()[:12000]
            mut["role"] = str(item.get("role") or "PM/PO").strip()
            if not mut["title"] or not mut["request"]:
                raise ValueError(f"{op} mutation needs a title and request.")
            if mut["role"] not in WORK_ROLES:
                raise ValueError(f"Invalid role '{mut['role']}' for board mutation.")
        mutations.append(mut)
    return {"summary": summary, "mutations": mutations}


def finish_board_delta_task(task: dict[str, Any], reply: str, provider: str) -> tuple[str, str]:
    return _finish_update_task(task, reply, provider, parse_board_delta_response, "board delta proposal")


def _apply_board_cancel(target: dict[str, Any], reason: str, ts: str) -> str:
    status = target.get("status")
    if status == "Done":
        return "skipped: Done tasks cannot be canceled; use supersede instead."
    if status == "In progress":
        return "skipped: task is In progress; wait for it to finish or cancel it manually."
    if status == "Canceled":
        return "skipped: already canceled."
    target["status"] = "Canceled"
    target["updated_at"] = ts
    target.setdefault("activity", []).append({"at": ts, "type": "board_delta", "message": f"Canceled by approved replan board delta. {reason}".strip()})
    write_task(target)
    return "applied"


def _apply_board_supersede(target: dict[str, Any], mut: dict[str, Any], reason: str, ts: str) -> str:
    status = target.get("status")
    if status == "In progress":
        return "skipped: task is In progress; cannot supersede while running."
    if status != "Canceled":
        target["status"] = "Canceled"
        target["updated_at"] = ts
        target.setdefault("activity", []).append(
            {"at": ts, "type": "board_delta", "message": f"Superseded by approved replan board delta. {reason}".strip()}
        )
        write_task(target)
    new_id = next_task_id()
    write_task(
        {
            "id": new_id,
            "task_type": "work",
            "title": mut["title"],
            "request": mut["request"],
            "assigned_role": mut["role"],
            "provider": "Automatic",
            "status": "Ready",
            "produces_deliverable": False,
            "acceptance_checks": [],
            "project_id": target.get("project_id") or "default",
            "module_id": target.get("module_id") or "",
            "phase": target.get("phase") or 1,
            "supersedes_task_id": target.get("id"),
            "created_at": ts,
            "updated_at": ts,
            "attempts": [],
            "validation": None,
            "activity": [{"at": ts, "type": "created", "message": f"Created by approved replan board delta, superseding {target.get('id')}."}],
        }
    )
    return f"applied (replacement {new_id})"


def _apply_board_create(mut: dict[str, Any], pid: str, ts: str) -> str:
    new_id = next_task_id()
    write_task(
        {
            "id": new_id,
            "task_type": "work",
            "title": mut["title"],
            "request": mut["request"],
            "assigned_role": mut["role"],
            "provider": "Automatic",
            "status": "Ready",
            "produces_deliverable": False,
            "acceptance_checks": [],
            "project_id": pid,
            "module_id": "",
            "phase": 1,
            "created_at": ts,
            "updated_at": ts,
            "attempts": [],
            "validation": None,
            "activity": [{"at": ts, "type": "created", "message": "Created by approved replan board delta."}],
        }
    )
    return f"applied (created {new_id})"


def apply_board_delta_update(task: dict[str, Any]) -> dict[str, Any]:
    """Apply an approved board delta payload. Returns per-mutation results."""
    payload = task.get("update_payload") or {}
    pid = str(task.get("project_id") or "default")
    ts = now_iso()
    reason = str(payload.get("summary") or "")
    results: list[dict[str, str]] = []
    for mut in payload.get("mutations") or []:
        op = mut.get("op")
        entry = {"op": op, "task_id": str(mut.get("task_id") or ""), "result": "", "message": ""}
        if op == "create":
            entry["result"] = "applied"
            entry["message"] = _apply_board_create(mut, pid, ts)
        else:
            target = read_task(str(mut.get("task_id") or ""))
            if not target:
                entry["result"] = "error"
                entry["message"] = "task not found"
            elif str(target.get("project_id") or "default") != pid:
                entry["result"] = "error"
                entry["message"] = "task belongs to a different project"
            elif op == "cancel":
                res = _apply_board_cancel(target, reason, ts)
                if res.startswith("skipped"):
                    entry["result"], entry["message"] = "skipped", res.split(": ", 1)[-1]
                else:
                    entry["result"], entry["message"] = "applied", res
            elif op == "supersede":
                res = _apply_board_supersede(target, mut, reason, ts)
                if res.startswith("skipped"):
                    entry["result"], entry["message"] = "skipped", res.split(": ", 1)[-1]
                else:
                    entry["result"], entry["message"] = "applied", res
        results.append(entry)
    return {"snapshots": {}, "results": results}

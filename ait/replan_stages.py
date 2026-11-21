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

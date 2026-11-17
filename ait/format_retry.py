"""Automatic format-correction retry for contract-based PM/PO task replies."""

from __future__ import annotations

from ait.common import *

MAX_FORMAT_RETRIES = 2


def maybe_format_retry(task: dict[str, Any], provider: str, error_msg: str) -> tuple[bool, str]:
    """Requeue a contract task for an automatic format-correction retry.

    Returns (True, message) when the task was requeued as Ready; returns
    (False, message) when the retry budget is exhausted and the caller should
    mark the task Rework needed instead.
    """
    retries = int(task.get("format_retries") or 0)
    if retries >= MAX_FORMAT_RETRIES:
        return False, error_msg
    ts = now_iso()
    task["format_retries"] = retries + 1
    task["format_retry_feedback"] = (
        f"Your previous reply was rejected: {error_msg}. "
        "Reply again with ONLY the required JSON object in exactly the format described - "
        "no markdown, no code fences, no extra text."
    )
    task["status"] = "Ready"
    task["updated_at"] = ts
    task.pop("planning_error", None)
    task.pop("block_reason", None)
    task.pop("retry_after", None)
    task.pop("waiting_reason", None)
    if str(task.get("provider") or "") in ("Automatic", "Auto", "Antigravity"):
        task["routing_cycle_start"] = len(task.get("attempts") or [])
    task.setdefault("activity", []).append(
        {
            "at": ts,
            "type": "format_retry",
            "from": "In progress",
            "to": "Ready",
            "message": f"{provider} reply failed format validation; auto-retry {retries + 1} of {MAX_FORMAT_RETRIES} queued.",
        }
    )
    write_task(task)
    append_event(
        {
            "at": ts,
            "task_id": task.get("id"),
            "type": "format_retry",
            "provider": provider,
            "retry": retries + 1,
        }
    )
    DISPATCH_EVENT.set()
    return True, f"{provider} reply failed format validation; auto-retry {retries + 1} of {MAX_FORMAT_RETRIES} queued."

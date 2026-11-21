"""Auto-append confirmed outcomes when deliverable tasks complete."""

from __future__ import annotations

from ait.common import *


def maybe_log_outcome(task: dict[str, Any]) -> None:
    """Append a one-line outcome entry the first time a deliverable task reaches Done."""
    if task.get("outcome_logged"):
        return
    if not task.get("produces_deliverable"):
        return
    if task.get("status") != "Done":
        return
    pid = str(task.get("project_id") or "default")
    ts = str(task.get("updated_at") or now_iso())
    title = str(task.get("title") or task.get("id") or "task")
    tid = str(task.get("id") or "")
    val = task.get("validation") or {}
    verdict = str(val.get("status") or "done")
    note = f"- {ts[:10]}: {title} ({tid}) — {verdict}."
    try:
        prev = load_project_context(pid, "outcomes")
        body = prev.rstrip()
        if note.strip() not in body:
            save_project_context_document(pid, "outcomes", (body + "\n" + note + "\n").strip() + "\n")
    except Exception:
        return
    task["outcome_logged"] = True
    task["updated_at"] = now_iso()
    write_task(task)

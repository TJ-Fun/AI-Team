"""PM planning helpers."""

from __future__ import annotations

from ait.common import *


# --- PM escalation creation ---

def maybe_create_escalation(blocked_task: dict[str, Any], reason: str) -> str:
    """Create a PM/PO escalation task for a system-blocked work task. Returns new task id or ''."""
    tid = str(blocked_task.get("id") or "")
    if not tid or blocked_task.get("task_type") not in ("work", None):
        return ""
    pid = str(blocked_task.get("project_id") or "default")
    for t in read_tasks():
        if (
            t.get("task_type") == "pm_escalation"
            and t.get("escalation_for_task_id") == tid
            and t.get("status") not in ("Done", "Canceled", "Blocked")
        ):
            return ""
    ts = now_iso()
    esc_id = next_task_id()
    attempts = blocked_task.get("attempts") or []
    last_errors = [
        str(a.get("error") or "")[:300]
        for a in attempts[-3:]
        if a.get("error")
    ]
    request = (
        f"Task {tid} ('{blocked_task.get('title', '')}') became Blocked.\n"
        f"Role: {blocked_task.get('assigned_role', '')}\n"
        f"Module: {blocked_task.get('module_id') or 'whole project'}\n"
        f"Phase: {blocked_task.get('phase') or 1}\n"
        f"Reason: {reason}\n"
        f"Request:\n{str(blocked_task.get('request', ''))[:4000]}\n\n"
        f"Acceptance checks:\n" + ("\n".join(f"- {c}" for c in (blocked_task.get("acceptance_checks") or [])) or "- None")
        + (f"\n\nRecent attempt errors:\n" + "\n".join(f"- {e}" for e in last_errors) if last_errors else "")
        + "\n\nDecide whether a design change is needed. If yes, propose small reversible team tasks plus alternatives for user review. "
        "If the block is purely technical (provider outage, bad instructions), propose corrected team tasks or explain why no change is needed."
    )
    write_task(
        {
            "id": esc_id,
            "task_type": "pm_escalation",
            "title": f"PM/PO escalation: {tid} blocked",
            "request": request,
            "escalation_for_task_id": tid,
            "escalation_round": int(blocked_task.get("escalation_round") or 0) + 1,
            "assigned_role": "PM/PO",
            "provider": "Automatic",
            "status": "Ready",
            "project_id": pid,
            "module_id": "",
            "produces_deliverable": False,
            "acceptance_checks": [],
            "created_at": ts,
            "updated_at": ts,
            "attempts": [],
            "validation": None,
            "dispatch_note": "Escalation queued for the PM/PO agent.",
            "activity": [{"at": ts, "type": "created", "message": f"Auto-escalation for blocked task {tid}."}],
        }
    )
    append_event({"at": ts, "task_id": esc_id, "type": "escalation_created", "for_task": tid})
    DISPATCH_EVENT.set()
    return esc_id

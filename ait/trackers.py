"""Auto-generated roadmap and phase tracker documents.

Trackers are system-owned: they are regenerated deterministically from the
task board whenever tracked state changes. Users and agents should treat them
as read-only references.
"""

from __future__ import annotations

from ait.common import *
from ait.releases import release_sort_key

TRACKER_NOTE = "_Auto-generated from the task board — do not edit; regenerated automatically._"

# PM/PO meta task types never count toward phase progress.
TRACKER_EXCLUDED_TYPES = {
    "pm_planning",
    "pm_escalation",
    "project_overview",
    "clarification_question",
    "design_review",
    "doc_update",
    "roadmap_update",
    "doc_edit",
    "chain_plan",
    "chain_review",
    "chain_doc",
    "vision_update",
    "board_delta",
}

TERMINAL_STATUSES = {"Done", "Canceled"}


def _task_phase(task: dict[str, Any]) -> int | None:
    try:
        phase = int(task.get("phase") or 0)
    except (TypeError, ValueError):
        return None
    return phase if 1 <= phase <= 9 else None


def _tracked_tasks(tasks: list[dict[str, Any]], project_id: str) -> dict[int, list[dict[str, Any]]]:
    by_phase: dict[int, list[dict[str, Any]]] = {}
    for task in tasks:
        if str(task.get("project_id") or "default") != project_id:
            continue
        if task.get("task_type") in TRACKER_EXCLUDED_TYPES:
            continue
        phase = _task_phase(task)
        if phase is None:
            continue
        by_phase.setdefault(phase, []).append(task)
    return by_phase


def _phase_state(open_count: int, total: int, earlier_open: bool) -> str:
    if total == 0:
        return "Pending"
    if open_count == 0:
        return "Done"
    return "Pending" if earlier_open else "In Progress"


def _roadmap_tracker_text(by_phase: dict[int, list[dict[str, Any]]], doc_phases: set[int], active_phase: int | None) -> str:
    lines = ["# Roadmap tracker", "", TRACKER_NOTE, ""]
    if active_phase:
        lines.append(f"Active phase: **{active_phase}**")
        lines.append("")
    lines.append("| Phase | State | Progress |")
    lines.append("| --- | --- | --- |")
    phases = sorted(set(by_phase) | doc_phases)
    earlier_open = False
    for phase in phases:
        items = by_phase.get(phase, [])
        total = len(items)
        open_count = sum(1 for t in items if t.get("status") not in TERMINAL_STATUSES)
        state = _phase_state(open_count, total, earlier_open)
        lines.append(f"| Phase {phase} | {state} | {total - open_count}/{total} done |")
        if open_count:
            earlier_open = True
    if not phases:
        lines.append("| — | Pending | No phased tasks yet |")
    return "\n".join(lines).strip() + "\n"


def _phase_tracker_text(phase: int, items: list[dict[str, Any]]) -> str:
    lines = [f"# Phase {phase} tracker", "", TRACKER_NOTE, ""]
    lines.append("| Task | Role | Release | Status |")
    lines.append("| --- | --- | --- | --- |")

    def sort_key(task: dict[str, Any]) -> tuple:
        rid = str(task.get("release") or "")
        try:
            rel_key = release_sort_key(rid) if rid else (0,)
        except Exception:
            rel_key = (0,)
        try:
            order = int(task.get("release_order") or 0)
        except (TypeError, ValueError):
            order = 0
        return (rel_key, order, str(task.get("id") or ""))

    for task in sorted(items, key=sort_key):
        rid = str(task.get("release") or "—")
        role = str(task.get("assigned_role") or task.get("role") or "—")
        status = str(task.get("status") or "—")
        lines.append(f"| {task.get('id', '')} | {role} | {rid} | {status} |")
    if not items:
        lines.append("| — | — | — | No tasks in this phase yet |")
    return "\n".join(lines).strip() + "\n"


def _save_if_changed(project_id: str, slug: str, text: str) -> None:
    try:
        if load_project_context(project_id, slug) == text.strip():
            return
        save_project_context_document(project_id, slug, text)
    except Exception as exc:
        logger.warning("Tracker sync failed for %s/%s: %s", project_id, slug, exc)


def sync_project_trackers(project_id: str) -> None:
    """Regenerate roadmap_tracker and phase_N_tracker docs from the task board."""
    pid = str(project_id or "default")
    try:
        proj = next((p for p in read_projects() if p.get("id") == pid), None)
        if not proj:
            return
        by_phase = _tracked_tasks(read_tasks(), pid)
        doc_phases = {
            int(m.group(1))
            for slug in project_phase_doc_slugs(pid)
            if (m := re.fullmatch(r"phase_([1-9])", slug))
        }
        active_phase = proj.get("active_phase")
        try:
            active_phase = int(active_phase) if active_phase else None
        except (TypeError, ValueError):
            active_phase = None
        _save_if_changed(pid, ROADMAP_TRACKER_DOC_SLUG, _roadmap_tracker_text(by_phase, doc_phases, active_phase))
        for phase in sorted(set(by_phase) | doc_phases):
            _save_if_changed(pid, f"phase_{phase}_tracker", _phase_tracker_text(phase, by_phase.get(phase, [])))
    except Exception as exc:
        logger.warning("Tracker sync failed for project %s: %s", pid, exc)

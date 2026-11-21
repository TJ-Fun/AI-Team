"""Release-driven project workflow: roadmap parsing and release gating."""

from __future__ import annotations

from ait.common import *

RELEASE_ID_RE = re.compile(r"^v\d+\.\d+(?:\.\d+)?$")
RELEASE_PLAN_FENCE_RE = re.compile(r"```(?:json)?\s*\n(\{.*?\})\s*```", re.DOTALL)
OPEN_TASK_STATUSES = {"Draft", "Ready", "In progress", "Needs validation", "Needs answer", "Needs approval", "Rework needed", "Blocked", "Waiting for capacity"}
DONE_TASK_STATUSES = {"Done", "Canceled"}


def normalize_release_id(value: Any) -> str:
    """Return a normalized release id like 'v0.1.2', or '' when absent/invalid."""
    text = str(value or "").strip()
    return text if RELEASE_ID_RE.fullmatch(text) else ""


def release_sort_key(release_id: str) -> tuple[int, ...]:
    parts = [int(p) for p in release_id.lstrip("v").split(".")]
    return tuple(parts)


def _clean_release_entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    rid = normalize_release_id(raw.get("id"))
    if not rid:
        return None
    status = str(raw.get("status") or "planned").strip().lower()
    if status not in ("planned", "in_progress", "done"):
        status = "planned"
    task_ids = [str(t) for t in (raw.get("task_ids") or []) if str(t).strip()][:200]
    return {
        "id": rid,
        "title": str(raw.get("title") or "").strip()[:200],
        "status": status,
        "task_ids": task_ids,
    }


def parse_release_plan(doc_text: str) -> dict[str, Any] | None:
    """Extract the release-plan JSON block embedded in the roadmap document."""
    text = str(doc_text or "")
    for match in RELEASE_PLAN_FENCE_RE.finditer(text):
        try:
            data = json.loads(match.group(1))
        except Exception:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("phases"), list):
            continue
        phases: list[dict[str, Any]] = []
        for ph in data["phases"][:20]:
            if not isinstance(ph, dict):
                continue
            pid = normalize_release_id(ph.get("id"))
            if not pid:
                continue
            releases = [r for r in (_clean_release_entry(x) for x in (ph.get("releases") or [])[:50]) if r]
            phases.append(
                {
                    "id": pid,
                    "title": str(ph.get("title") or "").strip()[:200],
                    "status": str(ph.get("status") or "planned").strip().lower() or "planned",
                    "releases": releases,
                }
            )
        if not phases:
            continue
        current_phase = normalize_release_id(data.get("current_phase")) or None
        current_release = normalize_release_id(data.get("current_release")) or None
        return {
            "current_phase": current_phase,
            "current_release": current_release,
            "phases": phases,
        }
    return None


def load_release_plan(project_id: str) -> dict[str, Any] | None:
    try:
        return parse_release_plan(load_project_context(project_id, ROADMAP_DOC_SLUG))
    except Exception:
        return None


def plan_release_ids(plan: dict[str, Any] | None) -> set[str]:
    ids: set[str] = set()
    for ph in (plan or {}).get("phases") or []:
        for rel in ph.get("releases") or []:
            ids.add(str(rel.get("id")))
    return ids


def find_release(plan: dict[str, Any] | None, release_id: str) -> dict[str, Any] | None:
    for ph in (plan or {}).get("phases") or []:
        for rel in ph.get("releases") or []:
            if rel.get("id") == release_id:
                return rel
    return None


def release_env_blocked(task: dict[str, Any], all_tasks: list[dict[str, Any]]) -> bool:
    """True when a work task's release still has unfinished env_setup tasks."""
    rid = str(task.get("release") or "")
    if not rid or task.get("task_type") == "env_setup":
        return False
    pid = str(task.get("project_id") or "default")
    for other in all_tasks:
        if (
            other.get("task_type") == "env_setup"
            and str(other.get("release") or "") == rid
            and str(other.get("project_id") or "default") == pid
            and other.get("status") not in DONE_TASK_STATUSES
        ):
            return True
    return False


def derived_current_release(all_tasks: list[dict[str, Any]], project_id: str) -> str:
    """Earliest open release id for a project, used when the roadmap has no plan block."""
    ids = {
        str(t.get("release") or "")
        for t in all_tasks
        if str(t.get("project_id") or "default") == project_id
        and str(t.get("release") or "")
        and t.get("status") not in DONE_TASK_STATUSES
    }
    ids.discard("")
    return min(ids, key=release_sort_key) if ids else ""


def task_release_gated(task: dict[str, Any], all_tasks: list[dict[str, Any]], plan: dict[str, Any] | None) -> bool:
    """True when a Ready task must wait because its release is not current."""
    rid = str(task.get("release") or "")
    if not rid:
        return False
    current = str((plan or {}).get("current_release") or "")
    if not current:
        current = derived_current_release(all_tasks, str(task.get("project_id") or "default"))
    if not current:
        return False
    if rid != current:
        return True
    return release_env_blocked(task, all_tasks)


def render_release_plan_block(plan: dict[str, Any]) -> str:
    return "```json\n" + json.dumps(plan, indent=2) + "\n```"


def update_release_plan_doc(project_id: str, plan: dict[str, Any]) -> None:
    """Write the release-plan JSON block back into the roadmap document."""
    block = render_release_plan_block(plan)
    doc = load_project_context(project_id, ROADMAP_DOC_SLUG)
    if RELEASE_PLAN_FENCE_RE.search(doc):
        new_doc = RELEASE_PLAN_FENCE_RE.sub(lambda _m: block, doc, count=1)
    else:
        header = "## Release plan\n\n"
        new_doc = (doc.rstrip() + "\n\n" + header + block + "\n") if doc.strip() else (header + block + "\n")
    save_project_context_document(project_id, ROADMAP_DOC_SLUG, new_doc)


def _all_plan_releases(plan: dict[str, Any] | None) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for ph in (plan or {}).get("phases") or []:
        for rel in ph.get("releases") or []:
            rows.append((str(ph.get("id") or ""), rel))
    rows.sort(key=lambda row: release_sort_key(str(row[1].get("id") or "v0.0.0")))
    return rows


def advance_release_plan(plan: dict[str, Any], completed_id: str) -> str:
    """Mark a release done and move current_release/current_phase to the next planned one.

    Returns the new current release id ('' when the plan is exhausted).
    """
    completed = find_release(plan, completed_id)
    if completed is not None:
        completed["status"] = "done"
        completed["completed_at"] = now_iso()
    rows = _all_plan_releases(plan)
    nxt = ""
    nxt_phase = ""
    seen_completed = False
    for phase_id, rel in rows:
        if rel.get("id") == completed_id:
            seen_completed = True
            continue
        if not seen_completed and rel.get("status") == "done":
            continue
        if rel.get("status") == "done":
            continue
        nxt = str(rel.get("id") or "")
        nxt_phase = phase_id
        break
    if nxt:
        rel = find_release(plan, nxt)
        if rel is not None and rel.get("status") == "planned":
            rel["status"] = "in_progress"
        plan["current_release"] = nxt
        plan["current_phase"] = nxt_phase or plan.get("current_phase")
    else:
        plan["current_release"] = ""
        plan["current_phase"] = ""
    return nxt

"""GitHub Actions check-run evidence for QA and project checks."""

from __future__ import annotations

from ait.common import *


def _normalize_check_run(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(item.get("name") or ""),
        "status": str(item.get("status") or ""),
        "conclusion": str(item.get("conclusion") or ""),
        "url": str(item.get("html_url") or item.get("details_url") or ""),
        "started_at": str(item.get("started_at") or ""),
        "completed_at": str(item.get("completed_at") or ""),
    }


def github_check_runs(project_id: str, ref: str, per_page: int = 50) -> list[dict[str, Any]]:
    project = github_project(project_id)
    owner, repo = repository_parts(project["repository"])
    has_token = bool(get_provider_key("github"))
    safe_ref = urllib.parse.quote(str(ref).strip(), safe="")
    data = github_api(
        f"/repos/{owner}/{repo}/commits/{safe_ref}/check-runs?per_page={int(per_page)}",
        require_auth=has_token,
    )
    runs = data.get("check_runs") if isinstance(data, dict) else []
    return [_normalize_check_run(r) for r in (runs or []) if isinstance(r, dict)]


def github_checks_summary(checks: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"success": 0, "failure": 0, "pending": 0, "other": 0}
    for run in checks:
        conclusion = str(run.get("conclusion") or "").lower()
        status = str(run.get("status") or "").lower()
        if conclusion == "success":
            counts["success"] += 1
        elif conclusion in ("failure", "timed_out", "action_required", "cancelled", "stale"):
            counts["failure"] += 1
        elif status in ("queued", "in_progress", "waiting", "requested", "pending") or not conclusion:
            counts["pending"] += 1
        else:
            counts["other"] += 1
    return counts


def github_checks_summary_text(checks: list[dict[str, Any]], ref: str = "") -> str:
    if not checks:
        return "GitHub check runs: none reported."
    counts = github_checks_summary(checks)
    lines = [
        f"GitHub check runs{f' for {ref}' if ref else ''}: {len(checks)} total — "
        f"{counts['success']} success, {counts['failure']} failed/cancelled, {counts['pending']} pending, {counts['other']} other."
    ]
    for run in checks[:20]:
        label = run.get("name") or "check"
        state = run.get("conclusion") or run.get("status") or "unknown"
        url = run.get("url") or ""
        lines.append(f"- {label}: {state}" + (f" — {url}" if url else ""))
    return "\n".join(lines)


def github_checks_for_task(task: dict[str, Any]) -> dict[str, Any]:
    project_id = str(task.get("project_id") or "default")
    pull = task.get("pull_request") if isinstance(task.get("pull_request"), dict) else {}
    head = pull.get("head") if isinstance(pull.get("head"), dict) else {}
    ref = str(head.get("sha") or task.get("qa_reviewed_head_sha") or task.get("github_ref") or "").strip()
    if not ref:
        return {"available": False, "reason": "No pull request head SHA or git ref is linked to this task."}
    try:
        checks = github_check_runs(project_id, ref)
    except Exception as exc:
        return {"available": False, "reason": str(exc)}
    return {"available": True, "ref": ref, "checks": checks, "summary": github_checks_summary(checks)}

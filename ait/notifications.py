"""Notifications, provider quality, usage resets, and CPU sampling."""

from __future__ import annotations

from ait.common import *


# --- Notifications, Quality, Usage, CPU ---
def get_notifications() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    all_tasks = sorted(
        read_tasks(),
        key=lambda t: str(t.get("updated_at") or t.get("created_at") or ""),
        reverse=True,
    )
    for t in all_tasks:
        tid = t.get("id", "")
        upd = t.get("updated_at", "")
        if t.get("status") == "Blocked":
            items.append(
                {
                    "id": f"task:{tid}:blocked:{upd}",
                    "kind": "blocked",
                    "severity": "high",
                    "title": f"{tid} is blocked",
                    "message": t.get("block_reason") or "Task needs attention.",
                    "task_id": tid,
                }
            )
        if t.get("status") == "Rework needed":
            validation = t.get("validation") or {}
            validation_checks = validation.get("checks", [])
            if not isinstance(validation_checks, list):
                validation_checks = []
            failed_check = next(
                (
                    check
                    for check in validation_checks
                    if isinstance(check, dict) and check.get("passed") is False
                ),
                None,
            )
            reason = (
                t.get("planning_error")
                or (failed_check or {}).get("message")
                or "Task needs changes before it can be completed."
            )
            items.append(
                {
                    "id": f"task:{tid}:rework:{upd}",
                    "kind": "rework",
                    "severity": "high",
                    "title": f"{tid} needs rework",
                    "message": str(reason),
                    "task_id": tid,
                }
            )
        if t.get("status") == "Waiting for capacity":
            atts = t.get("attempts") or []
            prov = atts[-1].get("provider", "Provider") if atts else "Provider"
            items.append(
                {
                    "id": f"task:{tid}:capacity:{upd}",
                    "kind": "capacity",
                    "severity": "medium",
                    "title": f"{prov} is unavailable",
                    "message": f"{tid} is waiting for capacity.",
                    "task_id": tid,
                }
            )
        if t.get("ci_status") == "failed":
            items.append(
                {
                    "id": f"task:{tid}:ci:{upd}",
                    "kind": "ci",
                    "severity": "high",
                    "title": f"CI checks failed for {tid}",
                    "message": str(t.get("ci_error") or "GitHub Actions checks failed on the pull request."),
                    "task_id": tid,
                }
            )
        if t.get("github_error"):
            items.append(
                {
                    "id": f"task:{tid}:github:{upd}",
                    "kind": "github",
                    "severity": "high",
                    "title": f"GitHub needs attention for {tid}",
                    "message": str(t["github_error"]),
                    "task_id": tid,
                }
            )
        if t.get("local_sync_error"):
            items.append(
                {
                    "id": f"task:{tid}:local-sync:{upd}",
                    "kind": "local_sync",
                    "severity": "high",
                    "title": f"Local checkout needs attention for {tid}",
                    "message": str(t["local_sync_error"]),
                    "task_id": tid,
                }
            )
        if t.get("status") == "Needs approval" and t.get("merge_approval_required"):
            items.append(
                {
                    "id": f"task:{tid}:merge-approval:{upd}",
                    "kind": "decision",
                    "severity": "high",
                    "title": f"Merge approval needed for {tid}",
                    "message": "QA passed; approve the pull request merge in task details.",
                    "task_id": tid,
                }
            )
        task_type = t.get("task_type")
        if t.get("status") == "Needs answer" and task_type == "clarification_question":
            question = str((t.get("question_data") or {}).get("question") or t.get("request") or "")
            items.append(
                {
                    "id": f"task:{tid}:decision:{upd}",
                    "kind": "decision",
                    "severity": "high",
                    "title": f"PM/PO needs an answer: {t.get('title') or tid}",
                    "message": question[:500] or "PM/PO is waiting for your answer before planning can continue.",
                    "task_id": tid,
                }
            )
        if t.get("status") == "Needs approval" and task_type in ("project_overview", "design_review"):
            if task_type == "project_overview":
                decision_title = "Project overview needs approval"
                decision_message = "PM/PO proposed project brief, technical overview, and decisions for your review."
            else:
                review = t.get("review_data") or {}
                design_change = review.get("design_change") or {}
                decision_title = "Design review needs approval"
                decision_message = str(
                    design_change.get("description")
                    or review.get("summary")
                    or t.get("request")
                    or "PM/PO proposed a design change for your review."
                )
            items.append(
                {
                    "id": f"task:{tid}:decision:{upd}",
                    "kind": "decision",
                    "severity": "medium",
                    "title": decision_title,
                    "message": decision_message[:500],
                    "task_id": tid,
                }
            )

        if t.get("status") == "Needs validation" and not task_provider({**t, "workflow_phase": "qa"}):
            items.append(
                {
                    "id": f"task:{tid}:qa-provider:{upd}",
                    "kind": "provider",
                    "severity": "high",
                    "title": f"{tid} is waiting for QA",
                    "message": "No provider in the QA routing order is connected. Configure a QA provider to continue validation.",
                    "task_id": tid,
                }
            )

    avail = set(configured_providers())
    if not avail and any(t.get("status") == "Ready" for t in all_tasks):
        items.append(
            {
                "id": "providers:none:configured",
                "kind": "provider",
                "severity": "high",
                "title": "No AI provider is connected",
                "message": "Ready tasks are queued. Add a provider key in Connections & routing.",
            }
        )
    for t in all_tasks:
        chosen = str(t.get("provider", ""))
        if t.get("status") == "Ready" and chosen in PROVIDERS and chosen not in avail:
            items.append(
                {
                    "id": f"task:{t['id']}:provider-missing:{chosen}",
                    "kind": "provider",
                    "severity": "medium",
                    "title": f"{t['id']} is waiting for {chosen}",
                    "message": f"This task explicitly selected {chosen}. Connect it or edit the task's provider.",
                    "task_id": t["id"],
                }
            )
    if not get_provider_key("github"):
        items.append(
            {
                "id": "connection:github:missing",
                "kind": "connection",
                "severity": "low",
                "title": "GitHub is not connected",
                "message": "Add a GitHub token in Connections & routing to enable repository workflows.",
            }
        )

    upd = app_update_status()
    if upd.get("update_available"):
        items.append(
            {
                "id": f"app:update:{upd.get('latest')}",
                "kind": "app_update",
                "severity": "low",
                "title": f"App update available: v{upd.get('latest')}",
                "message": f"Version {upd.get('latest')} is available (current: {upd.get('current') or 'unknown'}).",
                "action": "update",
            }
        )

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for proj in read_projects():
        for m in proj.get("milestones") or []:
            if not isinstance(m, dict):
                continue
            completed_at = str(m.get("completed_at") or "")
            if completed_at >= cutoff:
                items.append(
                    {
                        "id": f"milestone:{proj.get('id')}:{m.get('phase')}:{m.get('version')}",
                        "kind": "milestone",
                        "severity": "low",
                        "title": f"{proj.get('name', 'Project')} reached v{m.get('version')}",
                        "message": f"Phase {m.get('phase')} milestone completed.",
                    }
                )

    dismissed_path = DATA / "settings" / "notifications_dismissed.json"
    dismissed: set[str] = set()
    if dismissed_path.is_file():
        try:
            arr = json.loads(dismissed_path.read_text(encoding="utf-8"))
            if isinstance(arr, list):
                dismissed = {str(x) for x in arr}
        except Exception:
            pass
    return [i for i in items if i["id"] not in dismissed]


def dismiss_notification(nid: str) -> None:
    ensure_data()
    p = DATA / "settings" / "notifications_dismissed.json"
    items: list[str] = []
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                items = [str(x) for x in raw]
        except Exception:
            items = []
    if nid not in items:
        items.append(nid)
    atomic_json(p, items)


def get_provider_quality() -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for t in read_tasks():
        for att in t.get("attempts") or []:
            role = str(att.get("role") or t.get("assigned_role") or "Unknown")
            prov = str(att.get("provider", ""))
            if prov not in PROVIDERS:
                continue
            key = (role, prov)
            row = grouped.setdefault(
                key,
                {
                    "role": role,
                    "provider": prov,
                    "requests": 0,
                    "replies": 0,
                    "quota_or_service_errors": 0,
                    "measured": 0,
                    "passed": 0,
                    "failed": 0,
                    "total_seconds": 0.0,
                    "timed": 0,
                },
            )
            row["requests"] += 1
            if att.get("state") == "succeeded":
                row["replies"] += 1
            if att.get("state") in ("provider_unavailable", "waiting_for_capacity", "timed_out", "failed"):
                row["quota_or_service_errors"] += 1
            qo = att.get("quality_outcome")
            if qo in ("passed", "failed"):
                row["measured"] += 1
                row[qo] += 1
            s_ts = parse_iso_epoch(att.get("started_at"))
            f_ts = parse_iso_epoch(att.get("finished_at"))
            if s_ts is not None and f_ts is not None and f_ts >= s_ts:
                row["total_seconds"] += f_ts - s_ts
                row["timed"] += 1

    out: list[dict[str, Any]] = []
    for r in grouped.values():
        out.append(
            {
                "role": r["role"],
                "provider": r["provider"],
                "requests": r["requests"],
                "replies": r["replies"],
                "quota_or_service_errors": r["quota_or_service_errors"],
                "measured": r["measured"],
                "passed": r["passed"],
                "failed": r["failed"],
                "qa_pass_rate": round(100 * r["passed"] / r["measured"]) if r["measured"] else None,
                "average_seconds": round(r["total_seconds"] / r["timed"], 1) if r["timed"] else None,
            }
        )
    out.sort(key=lambda x: (x["role"], x["provider"]))
    return out


def read_ai_stat_resets() -> list[dict[str, Any]]:
    p = DATA / "logs" / "ai_stats_resets.json"
    if not p.is_file():
        return []
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
        return v if isinstance(v, list) else []
    except Exception:
        return []


def append_ai_stat_reset(reset: dict[str, Any]) -> None:
    resets = read_ai_stat_resets()
    resets.append(reset)
    atomic_json(DATA / "logs" / "ai_stats_resets.json", resets)


def get_cpu_percent() -> int | None:
    global PREV_CPU_SAMPLE
    stat_file = Path("/proc/stat")
    if stat_file.is_file():
        try:
            first = stat_file.read_text(encoding="utf-8").splitlines()[0]
            nums = [int(x) for x in first.split()[1:]]
            idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
            total = sum(nums)
            with CPU_SAMPLE_LOCK:
                if PREV_CPU_SAMPLE is None:
                    PREV_CPU_SAMPLE = (total, idle)
                    return 0
                prev_total, prev_idle = PREV_CPU_SAMPLE
                PREV_CPU_SAMPLE = (total, idle)
            dt = total - prev_total
            di = idle - prev_idle
            if dt <= 0:
                return 0
            return max(0, min(100, round(100 * (1.0 - di / dt))))
        except Exception:
            return None
    return None

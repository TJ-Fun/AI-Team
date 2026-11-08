"""PM planning, provider calls, dispatch, and background worker loops."""

from __future__ import annotations

from ait.common import *


# --- PM Planning, Deterministic Validation & Provider Calls ---
def parse_pm_plan_response(reply: str, project_id: str = "default") -> dict[str, Any]:
    text = reply.strip()
    if text.startswith("```"):
        nl = text.find("\n")
        text = (text[nl + 1 :] if nl >= 0 else text).rstrip("`").strip()
    s = text.find("{")
    e = text.rfind("}")
    if s < 0 or e <= s:
        raise ValueError("PM/PO response did not contain a JSON object.")
    data = json.loads(text[s : e + 1])
    if not isinstance(data, dict):
        raise ValueError("PM/PO response must be a JSON object.")
    kind = data.get("type")
    summary = str(data.get("summary", "")).strip()[:2000]

    if kind == "questions":
        questions = data.get("questions")
        if not isinstance(questions, list) or not (1 <= len(questions) <= 8):
            raise ValueError("A clarification response must contain between 1 and 8 questions.")
        norm_q = []
        for q in questions:
            if not isinstance(q, dict):
                raise ValueError("Each clarification question must be an object.")
            title = str(q.get("title", "")).strip()[:120]
            question = str(q.get("question", "")).strip()[:2000]
            opts = q.get("options")
            if not title or not question or not isinstance(opts, list) or not (2 <= len(opts) <= 8):
                raise ValueError("Each question needs a title, question, and 2 to 8 answer options.")
            clean_opts = []
            for o in opts:
                if isinstance(o, str) and o.strip():
                    clean_opts.append({"label": o.strip()[:160], "description": ""})
                elif isinstance(o, dict) and str(o.get("label", "")).strip():
                    clean_opts.append(
                        {
                            "label": str(o["label"]).strip()[:160],
                            "description": str(o.get("description", "")).strip()[:500],
                        }
                    )
            if len(clean_opts) < 2:
                raise ValueError("Each question must have at least two usable options.")
            norm_q.append(
                {
                    "title": title,
                    "question": question,
                    "why": str(q.get("why", "")).strip()[:1000],
                    "options": clean_opts,
                    "allow_free_text": q.get("allow_free_text") is not False,
                }
            )
        return {"type": "questions", "summary": summary, "questions": norm_q}

    if kind == "plan":
        tasks = data.get("tasks")
        if not isinstance(tasks, list) or not (1 <= len(tasks) <= 12):
            raise ValueError("A plan response must contain between 1 and 12 tasks.")
        proj = next((p for p in read_projects() if p.get("id") == project_id), None)
        valid_mods = {m["id"] for m in (proj or {}).get("modules", [])}
        norm_t = []
        warnings: list[str] = []
        for t in tasks:
            if not isinstance(t, dict):
                raise ValueError("Each proposed task must be an object.")
            title = str(t.get("title", "")).strip()[:160]
            req = str(t.get("request", "")).strip()[:12000]
            role = str(t.get("role", "")).strip()
            mid = str(t.get("module_id", "")).strip()
            checks = t.get("acceptance_checks", [])
            if not title:
                raise ValueError("Each proposed task needs a non-empty title.")
            if not req:
                raise ValueError(f"Proposed task '{title}' needs a non-empty request.")
            if role not in WORK_ROLES:
                raise ValueError(f"Proposed task '{title}' has an invalid work role: {role or '(empty)'}.")
            if not isinstance(checks, list):
                raise ValueError(f"Proposed task '{title}' acceptance_checks must be a list.")
            if mid and mid not in valid_mods:
                warnings.append(
                    f"Task '{title}' referenced unknown sub-project '{mid}' and was assigned to the whole project."
                )
                logger.warning(
                    "PM/PO proposed unknown sub-project task_title=%s module_id=%s; assigning task to whole project",
                    title,
                    mid,
                )
                mid = ""
            clean_checks = [str(c).strip()[:500] for c in checks if str(c).strip()][:20]
            produces = t["produces_deliverable"] if isinstance(t.get("produces_deliverable"), bool) else (role != "PM/PO")
            if produces and not clean_checks:
                clean_checks.append("Manually review the resulting deliverable against the task request.")
            try:
                phase = int(t.get("phase") or 1)
            except (TypeError, ValueError):
                phase = 1
            phase = min(max(phase, 1), 9)
            norm_t.append(
                {
                    "title": title,
                    "request": req,
                    "role": role,
                    "module_id": mid,
                    "acceptance_checks": clean_checks,
                    "produces_deliverable": produces,
                    "phase": phase,
                }
            )
        ov = data.get("project_overview")
        if not isinstance(ov, dict):
            raise ValueError("A final plan must include a project_overview for user approval.")
        ov_docs: dict[str, str] = {}
        for slug in ("project_brief", "technical_overview", "decisions"):
            val = ov.get(slug)
            if not isinstance(val, str) or not val.strip() or len(val) > 20000:
                raise ValueError(f"The project overview needs non-empty {slug} text under 20,000 characters.")
            ov_docs[slug] = val.strip()
        return {
            "type": "plan",
            "summary": summary,
            "tasks": norm_t,
            "project_overview": ov_docs,
            "warnings": warnings,
        }

    raise ValueError("PM/PO response type must be 'questions' or 'plan'.")


def finish_pm_planning_task(task: dict[str, Any], reply: str, provider: str) -> tuple[str, str]:
    try:
        res = parse_pm_plan_response(reply, str(task.get("project_id") or "default"))
        ts = now_iso()
        ids: list[str] = []
        if res["type"] == "questions":
            for item in res["questions"]:
                cid = next_task_id()
                ids.append(cid)
                child = {
                    "id": cid,
                    "task_type": "clarification_question",
                    "title": item["title"],
                    "request": item["question"],
                    "question_data": {
                        "why": item["why"],
                        "options": item["options"],
                        "allow_free_text": item["allow_free_text"],
                    },
                    "assigned_role": "PM/PO",
                    "provider": "Automatic",
                    "status": "Needs answer",
                    "planning_root_id": task.get("planning_root_id"),
                    "planning_task_id": task["id"],
                    "planning_round": task.get("planning_round") or 1,
                    "project_id": task.get("project_id") or "default",
                    "module_id": "",
                    "created_at": ts,
                    "updated_at": ts,
                    "attempts": [],
                    "validation": None,
                    "activity": [{"at": ts, "type": "created", "message": "PM/PO clarification question; awaiting your answer."}],
                }
                write_task(child)
            task["child_task_ids"] = ids
            task["planning_result_type"] = "questions"
            task["planning_summary"] = res["summary"]
            task["validation"] = {
                "status": "passed",
                "checks": [{"check": "PM/PO response structure", "kind": "schema", "passed": True, "message": "Response parsed and saved."}],
                "validated_at": ts,
            }
            return "Done", f"PM/PO clarification questions logged as {', '.join(ids)}. Waiting for your answers."

        ov_id = next_task_id()
        ids.append(ov_id)
        write_task(
            {
                "id": ov_id,
                "task_type": "project_overview",
                "title": "Project overview — review and approve",
                "request": "Review the PM/PO's proposed project brief, technical overview, and decisions. Edit any text before approval.",
                "overview_documents": res["project_overview"],
                "assigned_role": "PM/PO",
                "provider": "Automatic",
                "status": "Needs approval",
                "planning_root_id": task.get("planning_root_id"),
                "source_plan_task_id": task["id"],
                "project_id": task.get("project_id") or "default",
                "module_id": "",
                "created_at": ts,
                "updated_at": ts,
                "attempts": [],
                "validation": None,
                "activity": [{"at": ts, "type": "created", "message": "PM/PO prepared a project overview; waiting for your approval."}],
            }
        )
        plan_status = "Draft" if get_autonomy().get("plan_requires_approval") else "Ready"
        work_ids: list[str] = []
        for item in res["tasks"]:
            wid = next_task_id()
            ids.append(wid)
            work_ids.append(wid)
            write_task(
                {
                    "id": wid,
                    "task_type": "work",
                    "title": item["title"],
                    "request": item["request"],
                    "assigned_role": item["role"],
                    "provider": "Automatic",
                    "acceptance_checks": item["acceptance_checks"],
                    "produces_deliverable": item["produces_deliverable"],
                    "phase": item.get("phase") or 1,
                    "status": plan_status,
                    "planning_root_id": task.get("planning_root_id"),
                    "source_plan_task_id": task["id"],
                    "project_id": task.get("project_id") or "default",
                    "module_id": item["module_id"],
                    "created_at": ts,
                    "updated_at": ts,
                    "attempts": [],
                    "validation": None,
                    "activity": [{"at": ts, "type": "created", "message": f"Proposed by PM/PO in {task['id']}; {'created as draft for manual release' if plan_status == 'Draft' else 'automatically queued for execution'}."}],
                }
            )
        task["project_overview_task_id"] = ov_id
        task["work_task_ids"] = work_ids
        task["child_task_ids"] = ids
        task["planning_result_type"] = "plan"
        task["planning_summary"] = res["summary"]
        task["planning_warnings"] = res["warnings"]
        task["validation"] = {
            "status": "passed",
            "checks": [{"check": "PM/PO response structure", "kind": "schema", "passed": True, "message": "Response parsed and saved."}],
            "validated_at": ts,
        }
        DISPATCH_EVENT.set()
        message = f"PM/PO created project overview {ov_id} and {'queued' if plan_status == 'Ready' else 'created draft'} {len(work_ids)} work task(s): {', '.join(work_ids)}."
        if res["warnings"]:
            message += " " + " ".join(res["warnings"])
        return "Done", message
    except Exception as exc:
        msg = str(exc)
        logger.warning("PM/PO result rejected task_id=%s provider=%s: %s", task.get("id"), provider, msg)
        append_event(
            {
                "at": now_iso(),
                "type": "pm_plan_rejected",
                "task_id": task.get("id"),
                "provider": provider,
                "error": msg[:1000],
            }
        )
        task["validation"] = {
            "status": "failed",
            "checks": [{"check": "PM/PO response structure", "kind": "schema", "passed": False, "message": msg}],
            "validated_at": now_iso(),
        }
        task["planning_error"] = msg
        return "Rework needed", f"{provider} replied, but the PM/PO result could not be logged: {msg}"


def parse_escalation_response(reply: str, project_id: str) -> dict[str, Any]:
    clean = reply.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean, flags=re.IGNORECASE).strip()
    try:
        data = json.loads(clean)
    except Exception as exc:
        raise ValueError("PM/PO escalation reply must be a valid JSON object.") from exc
    if not isinstance(data, dict) or data.get("type") != "escalation":
        raise ValueError("PM/PO escalation reply must have type 'escalation'.")
    summary = str(data.get("summary") or "").strip()[:500]

    dc = data.get("design_change")
    design_change = None
    if isinstance(dc, dict):
        desc = str(dc.get("description") or "").strip()[:4000]
        reason = str(dc.get("reason") or "").strip()[:2000]
        if desc:
            design_change = {"description": desc, "reason": reason}

    proj = next((p for p in read_projects() if p.get("id") == project_id), None)
    valid_mods = {m["id"] for m in (proj or {}).get("modules", [])}
    team_tasks: list[dict[str, Any]] = []
    raw_tasks = data.get("team_tasks") or []
    if not isinstance(raw_tasks, list) or len(raw_tasks) > 8:
        raise ValueError("escalation team_tasks must be a list of at most 8 tasks.")
    for t in raw_tasks:
        if not isinstance(t, dict):
            raise ValueError("Each escalation team task must be an object.")
        title = str(t.get("title", "")).strip()[:160]
        req = str(t.get("request", "")).strip()[:12000]
        role = str(t.get("role", "")).strip()
        mid = str(t.get("module_id", "")).strip()
        checks = t.get("acceptance_checks") or []
        if not title or not req:
            raise ValueError("Each escalation team task needs a title and request.")
        if role not in WORK_ROLES:
            raise ValueError(f"Escalation team task '{title}' has an invalid work role.")
        if not isinstance(checks, list):
            raise ValueError(f"Escalation team task '{title}' acceptance_checks must be a list.")
        if mid and mid not in valid_mods:
            mid = ""
        clean_checks = [str(c).strip()[:500] for c in checks if str(c).strip()][:20]
        produces = t["produces_deliverable"] if isinstance(t.get("produces_deliverable"), bool) else (role != "PM/PO")
        if produces and not clean_checks:
            clean_checks.append("Manually review the resulting deliverable against the task request.")
        phase = None
        if t.get("phase") is not None:
            try:
                phase = int(t.get("phase"))
            except (TypeError, ValueError):
                phase = None
        if phase is not None:
            phase = min(max(phase, 1), 9)
        team_tasks.append(
            {
                "title": title,
                "request": req,
                "role": role,
                "module_id": mid,
                "acceptance_checks": clean_checks,
                "produces_deliverable": produces,
                "phase": phase,
            }
        )

    alternatives: list[dict[str, str]] = []
    raw_alts = data.get("alternatives") or []
    if isinstance(raw_alts, list):
        for a in raw_alts[:4]:
            if isinstance(a, dict):
                at = str(a.get("title", "")).strip()[:160]
                ad = str(a.get("description", "")).strip()[:2000]
                if at:
                    alternatives.append({"title": at, "description": ad})
            elif isinstance(a, str) and a.strip():
                alternatives.append({"title": a.strip()[:160], "description": ""})

    if design_change:
        if not team_tasks:
            raise ValueError("A design change must include at least one provisional team task.")
        if not alternatives:
            raise ValueError("A design change must include at least one alternative.")

    return {
        "type": "escalation",
        "summary": summary,
        "design_change": design_change,
        "team_tasks": team_tasks,
        "alternatives": alternatives,
        "recommendation": str(data.get("recommendation") or "").strip()[:2000],
    }


def finish_pm_escalation_task(task: dict[str, Any], reply: str, provider: str) -> tuple[str, str]:
    try:
        res = parse_escalation_response(reply, str(task.get("project_id") or "default"))
        ts = now_iso()
        pid = str(task.get("project_id") or "default")
        blocked_id = str(task.get("escalation_for_task_id") or "")
        blocked = read_task(blocked_id) if blocked_id else None
        autonomy = get_autonomy()
        provisional_status = "Ready" if autonomy.get("escalation_runs_provisional", True) else "Draft"
        try:
            blocked_phase = min(max(int((blocked or {}).get("phase") or 1), 1), 9)
        except (TypeError, ValueError):
            blocked_phase = 1

        review_id = ""
        team_ids: list[str] = []
        if res["design_change"] or res["team_tasks"]:
            review_id = next_task_id()
            for item in res["team_tasks"]:
                wid = next_task_id()
                team_ids.append(wid)
                write_task(
                    {
                        "id": wid,
                        "task_type": "work",
                        "title": item["title"],
                        "request": item["request"],
                        "assigned_role": item["role"],
                        "provider": "Automatic",
                        "acceptance_checks": item["acceptance_checks"],
                        "produces_deliverable": item["produces_deliverable"],
                        "phase": item.get("phase") or blocked_phase,
                        "status": provisional_status,
                        "pending_design_review": True,
                        "design_review_task_id": review_id,
                        "escalation_task_id": task["id"],
                        "escalation_for_task_id": blocked_id,
                        "project_id": pid,
                        "module_id": item["module_id"] or str((blocked or {}).get("module_id") or ""),
                        "planning_root_id": (blocked or {}).get("planning_root_id") or task.get("planning_root_id"),
                        "source_plan_task_id": (blocked or {}).get("source_plan_task_id") or task.get("source_plan_task_id"),
                        "created_at": ts,
                        "updated_at": ts,
                        "attempts": [],
                        "validation": None,
                        "activity": [
                            {
                                "at": ts,
                                "type": "created",
                                "message": f"Provisional task from PM/PO escalation {task['id']}; {'runs' if provisional_status == 'Ready' else 'is drafted'} while design review {review_id} awaits approval.",
                            }
                        ],
                    }
                )
            write_task(
                {
                    "id": review_id,
                    "task_type": "design_review",
                    "title": f"Design review: {res['summary'] or 'PM/PO proposed change'}",
                    "request": res["design_change"]["description"] if res["design_change"] else res["summary"],
                    "review_data": {
                        "summary": res["summary"],
                        "design_change": res["design_change"],
                        "alternatives": res["alternatives"],
                        "recommendation": res["recommendation"],
                        "team_task_ids": team_ids,
                        "source_task_id": blocked_id,
                        "escalation_task_id": task["id"],
                    },
                    "assigned_role": "PM/PO",
                    "provider": "Automatic",
                    "status": "Needs approval",
                    "project_id": pid,
                    "module_id": "",
                    "produces_deliverable": False,
                    "acceptance_checks": [],
                    "created_at": ts,
                    "updated_at": ts,
                    "attempts": [],
                    "validation": None,
                    "activity": [
                        {
                            "at": ts,
                            "type": "created",
                            "message": f"PM/PO proposed a design change; provisional team tasks are {'running' if provisional_status == 'Ready' else 'drafted'} pending your review.",
                        }
                    ],
                }
            )
            if blocked and blocked.get("status") == "Blocked":
                blocked.setdefault("activity", []).append(
                    {
                        "at": ts,
                        "type": "design_review_created",
                        "message": f"PM/PO escalation {task['id']} created design review {review_id}; provisional tasks {'running' if provisional_status == 'Ready' else 'drafted'}.",
                    }
                )
                blocked["updated_at"] = ts
                write_task(blocked)
            DISPATCH_EVENT.set()

        task["validation"] = {
            "status": "passed",
            "checks": [{"check": "PM/PO escalation structure", "kind": "schema", "passed": True, "message": "Response parsed and saved."}],
            "validated_at": ts,
        }
        if review_id:
            msg = (
                f"PM/PO proposed a design change. Queued {len(team_ids)} provisional task(s) "
                f"({', '.join(team_ids)}) and created design review {review_id} for your approval."
            )
        else:
            if blocked and blocked.get("status") == "Blocked":
                blocked.setdefault("activity", []).append(
                    {
                        "at": ts,
                        "type": "escalation_reviewed",
                        "message": f"PM/PO escalation {task['id']} found no design change needed: {res['summary']}",
                    }
                )
                blocked["updated_at"] = ts
                write_task(blocked)
            msg = f"PM/PO reviewed the escalation and decided no design change is needed: {res['summary']}"
        return "Done", msg
    except Exception as exc:
        msg = str(exc)
        logger.warning("PM/PO escalation rejected task_id=%s provider=%s: %s", task.get("id"), provider, msg)
        task["validation"] = {
            "status": "failed",
            "checks": [{"check": "PM/PO escalation structure", "kind": "schema", "passed": False, "message": msg}],
            "validated_at": now_iso(),
        }
        task["planning_error"] = msg
        return "Rework needed", f"{provider} replied, but the PM/PO escalation could not be logged: {msg}"


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


def project_min_open_phase(tasks: list[dict[str, Any]], project_id: str) -> int | None:
    phases = [
        int(t["phase"])
        for t in tasks
        if t.get("project_id", "default") == project_id
        and "phase" in t
        and t.get("status") not in ("Done", "Canceled", "Draft")
    ]
    return min(phases) if phases else None


def task_phase_gated(task: dict[str, Any], all_tasks: list[dict[str, Any]]) -> bool:
    if "phase" not in task:
        return False
    pid = str(task.get("project_id") or "default")
    min_phase = project_min_open_phase(all_tasks, pid)
    return min_phase is not None and int(task["phase"]) > min_phase


def check_project_milestones() -> list[str]:
    """Detect completed phases per project, bump version, log decision, notify."""
    completed: list[str] = []
    projects = read_projects()
    tasks = read_tasks()
    for proj in projects:
        pid = str(proj.get("id") or "default")
        done_phases = set(proj.get("completed_phases") or [])
        phased = [t for t in tasks if t.get("project_id", "default") == pid and "phase" in t]
        by_phase: dict[int, list[dict[str, Any]]] = {}
        for t in phased:
            by_phase.setdefault(int(t["phase"]), []).append(t)
        newly: list[int] = []
        for phase, items in sorted(by_phase.items()):
            if phase in done_phases:
                continue
            if items and all(t.get("status") in ("Done", "Canceled") for t in items) and any(
                t.get("status") == "Done" for t in items
            ):
                newly.append(phase)
        if not newly:
            continue
        ts = now_iso()
        proj["completed_phases"] = sorted(done_phases | set(newly))
        version = int(proj.get("version") or 1) + len(newly)
        proj["version"] = version
        proj.setdefault("milestones", []).extend(
            {"phase": p, "version": version - len(newly) + i + 1, "completed_at": ts} for i, p in enumerate(newly)
        )
        save_projects([dict(proj) if p.get("id") == pid else p for p in projects])
        for p in newly:
            note = f"- {ts[:10]}: Milestone reached — phase {p} completed; project version {version - len(newly) + newly.index(p) + 1}."
            try:
                prev = load_project_context(pid, "decisions")
                save_project_context_document(pid, "decisions", (prev.rstrip() + "\n" + note + "\n").strip() + "\n")
            except Exception:
                pass
            completed.append(f"{proj.get('name', pid)} v{version}: phase {p} milestone completed.")
            append_event({"at": ts, "type": "milestone", "project_id": pid, "phase": p, "version": version})
    return completed


def deterministic_validation(checks: list[str], reply: str) -> dict[str, Any]:
    results = []
    for raw_check in checks:
        raw = str(raw_check).strip()
        cmd, sep, expected = raw.partition(":")
        cmd_lower = cmd.strip().lower()
        exp = expected.strip()
        if not sep or cmd_lower not in ("exact", "contains", "not_contains"):
            results.append({"check": raw, "kind": "qa", "passed": None, "message": "Requires independent QA review."})
            continue
        if cmd_lower == "exact":
            passed = reply.strip() == exp
            desc = "Reply exactly matches expected text." if passed else "Reply did not exactly match expected text."
        elif cmd_lower == "contains":
            passed = exp in reply
            desc = "Required text is present." if passed else "Required text is missing."
        else:
            passed = exp not in reply
            desc = "Forbidden text is absent." if passed else "Forbidden text was found."
        results.append({"check": raw, "kind": cmd_lower, "passed": passed, "message": desc})

    failed = any(r["passed"] is False for r in results)
    all_auto = bool(results) and all(r["passed"] is not None for r in results)
    status = "failed" if failed else ("passed" if all_auto else "pending_qa")
    return {"status": status, "checks": results, "validated_at": now_iso()}


def build_qa_review_instructions(task: dict[str, Any]) -> str:
    rel = str(task.get("qa_subject_reply_file") or "")
    reply_path = (DATA / rel).resolve() if rel else None
    if not reply_path or not str(reply_path).startswith(str(DATA)) or not reply_path.is_file():
        agent_reply = "The original agent reply could not be loaded."
    else:
        try:
            saved = json.loads(reply_path.read_text(encoding="utf-8"))
            agent_reply = str(saved.get("text") or saved.get("error") or "")[:30000]
        except (OSError, ValueError, TypeError) as exc:
            agent_reply = f"The original agent reply could not be loaded: {exc}"

    repository_context = ""
    pull = task.get("pull_request")
    project = next((p for p in read_projects() if p.get("id") == (task.get("project_id") or "default")), None)
    repository_required = bool(
        project
        and project.get("repository")
        and task.get("qa_return_role") in ("Frontend developer", "Backend developer", "Documentation")
    )
    if isinstance(pull, dict) and pull.get("number") and project and project.get("repository"):
        owner, repo = repository_parts(project["repository"])
        pull_data = github_api(f"/repos/{owner}/{repo}/pulls/{int(pull['number'])}")
        head = pull_data.get("head") if isinstance(pull_data, dict) else {}
        branch = str((head or {}).get("ref") or pull.get("branch") or "")
        head_sha = str((head or {}).get("sha") or "")
        if not branch or not head_sha:
            raise ValueError(f"GitHub did not provide the head branch and commit for {task['id']}'s pull request.")
        latest = read_task(str(task["id"]))
        if latest:
            latest["qa_reviewed_head_sha"] = head_sha
            latest["qa_reviewed_at"] = now_iso()
            write_task(latest)
            task["qa_reviewed_head_sha"] = head_sha
        repository_context = (
            f"Pull request branch: {branch}\nReviewed commit: {head_sha}\n"
            + github_task_context(task, head_sha, max_files=32, max_bytes=48000)
        )
    elif repository_required:
        repository_context = "No GitHub pull request is linked to this repository-changing task yet."

    checks = task.get("acceptance_checks") or []
    return (
        "\n\nQA review task:\n"
        + f"Original work role: {task.get('qa_return_role', 'Unknown')}\n"
        + f"Task request:\n{task.get('request', '')}\n\nAcceptance checks:\n"
        + ("\n".join(f"- {check}" for check in checks) if checks else "- No acceptance checks were provided.")
        + f"\n\nOriginal agent reply:\n{agent_reply}"
        + (f"\n\nCurrent pull-request branch source snapshot:\n{repository_context}" if repository_context else "")
        + "\n\nReview the provided evidence and criteria. Do not claim to run tests or inspect files beyond what is included. "
        + "Return only JSON with this shape: "
        + '{"decision":"pass" or "fail","summary":"brief rationale","checks":[{"check":"criterion or finding",'
        + '"passed":true or false,"message":"specific evidence or required correction"}]}. '
        + "Use decision pass only when the evidence supports every acceptance check. If repository changes are required but no PR and branch snapshot are supplied, fail the review for missing evidence. List actionable, reproducible issues as failed checks."
    )


def parse_qa_result(reply: str) -> dict[str, Any]:
    clean = reply.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean, flags=re.IGNORECASE)
    try:
        result = json.loads(clean)
    except (ValueError, TypeError) as exc:
        raise ValueError("QA response must be a JSON object with a pass/fail decision and checks.") from exc
    if not isinstance(result, dict) or result.get("decision") not in ("pass", "fail"):
        raise ValueError("QA response decision must be 'pass' or 'fail'.")
    raw_checks = result.get("checks")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise ValueError("QA response must include at least one check.")
    checks: list[dict[str, Any]] = []
    for item in raw_checks[:30]:
        if not isinstance(item, dict) or not str(item.get("check", "")).strip():
            raise ValueError("Each QA check must include a description.")
        if not isinstance(item.get("passed"), bool):
            raise ValueError("Each QA check must have a boolean passed value.")
        checks.append(
            {
                "check": str(item["check"]).strip()[:500],
                "kind": "qa",
                "passed": item["passed"],
                "message": str(item.get("message", "")).strip()[:2000],
            }
        )
    passed = result["decision"] == "pass" and all(item["passed"] for item in checks)
    if result["decision"] == "fail" and all(item["passed"] for item in checks):
        checks.append(
            {
                "check": "QA decision",
                "kind": "qa",
                "passed": False,
                "message": str(result.get("summary") or "QA requested changes.")[:2000],
            }
        )
    return {
        "status": "passed" if passed else "failed",
        "method": "agent_qa",
        "summary": str(result.get("summary") or "")[:2000],
        "checks": checks,
        "validated_at": now_iso(),
    }


def planning_contract_for_task(task: dict[str, Any]) -> str:
    tt = task.get("task_type")
    if tt == "pm_planning":
        return PM_PLANNING_CONTRACT
    if tt == "pm_escalation":
        return ESCALATION_CONTRACT
    return ""


def needs_work_reply_envelope(task: dict[str, Any]) -> bool:
    if task.get("task_type") in ("pm_planning", "pm_escalation") or task.get("qa_attempt_active"):
        return False
    role = str(task.get("assigned_role") or "")
    if role in ("Frontend developer", "Backend developer", "Documentation"):
        proj = next((p for p in read_projects() if p.get("id") == (task.get("project_id") or "default")), None)
        if proj and proj.get("repository"):
            return False
    return True


def parse_work_reply_envelope(reply: str) -> str:
    clean = reply.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean, flags=re.IGNORECASE).strip()
    try:
        obj = json.loads(clean)
    except Exception as exc:
        raise ValueError("Agent reply must be a JSON envelope with 'summary' and 'details' fields.") from exc
    if not isinstance(obj, dict):
        raise ValueError("Agent reply envelope must be a JSON object.")
    summary = str(obj.get("summary") or "").strip()
    details = str(obj.get("details") or "").strip()
    if not summary and not details:
        raise ValueError("Agent reply envelope must include a non-empty 'summary' or 'details'.")
    if summary and details:
        return f"{summary}\n\n{details}"
    return summary or details


def build_repo_agent_instructions(task: dict[str, Any], provider: str) -> str:
    if task.get("qa_attempt_active"):
        return build_qa_review_instructions(task)
    if (
        task.get("task_type") == "pm_planning"
        or task.get("assigned_role") not in ("Frontend developer", "Backend developer", "Documentation")
    ):
        return ""
    proj = next((p for p in read_projects() if p.get("id") == (task.get("project_id") or "default")), None)
    if not proj or not proj.get("repository"):
        return ""
    snapshot = github_task_context(task)
    return (
        "\n\nRepository snapshot (read-only context):\n"
        + snapshot
        + '\n\nReturn only valid JSON, no markdown, with this shape: '
        + '{"summary":"brief change summary","files":[{"path":"relative/path","content":"complete replacement file text"}],"checks":["checks performed"],"feedback":"optional blockers or scope concerns for PM/PO"}. '
        + "Include complete contents for each changed or new text file. Do not return diffs. "
        + "Return at most 20 files. Only text files with these extensions are accepted: "
        + ".md .txt .html .css .js .ts .tsx .jsx .py .json .toml .yml .yaml .xml .sh. "
        + "Lock files (package-lock.json, poetry.lock, etc.) and files outside the editable scope are rejected; "
        + "one invalid entry fails the whole task. Each file content must be complete replacement text under 50,000 bytes. "
        + "Use the optional feedback field for blockers or scope concerns instead of a PM/PO feedback section."
    )


def call_gemini_task(task: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    api_key = get_provider_key("gemini")
    if not api_key:
        raise ProviderFailure("Gemini", 401, "Gemini API key is not configured.")
    role = str(task.get("assigned_role") or "PM/PO")
    checks = task.get("acceptance_checks") or []
    ctx = project_context_for_role(role, task)
    prompt = (
        (f"Project context (confirmed reference information):\n{ctx}\n\n" if ctx else "")
        + f"Task ID: {task['id']}\nAssigned role: {role}\nProvider: Gemini\nRequest:\n{task.get('request', '')}\n\n"
        + "Acceptance checks:\n"
        + ("\n".join(f"- {c}" for c in checks) if checks else "- None specified")
    )
    prompt += build_repo_agent_instructions(task, "Gemini")
    if needs_work_reply_envelope(task):
        prompt += "\n\n" + WORK_REPLY_CONTRACT
    contract = planning_contract_for_task(task)
    if contract:
        prompt += "\n\n" + contract
    if (task.get("validation") or {}).get("status") == "failed":
        prompt += "\n\nPrevious attempt failed validation checks:\n" + json.dumps(task["validation"].get("checks", []), indent=2)

    sys_inst = combined_agent_prompt(role) + (("\n\n" + contract) if contract else "")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(GEMINI_MODEL, safe='')}:generateContent?key={urllib.parse.quote(api_key, safe='')}"
    payload = {
        "systemInstruction": {"parts": [{"text": sys_inst}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": HTTP_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:1500]
        raise ProviderFailure("Gemini", exc.code, body, exc.headers.get("Retry-After")) from exc
    except Exception as exc:
        raise ProviderFailure("Gemini", 503, str(exc)[:1000]) from exc

    candidates = result.get("candidates") or []
    parts = (((candidates[0] if candidates else {}).get("content") or {}).get("parts")) or []
    text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict)).strip()
    if not text:
        raise ProviderFailure("Gemini", 400, "Gemini returned no text.")
    return text, {"usage_metadata": result.get("usageMetadata")}


def normalize_github_repo_url(raw_repo: str) -> str:
    cleaned = (raw_repo or "").strip().rstrip("/")
    if not cleaned:
        return ""
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", cleaned):
        return f"https://github.com/{cleaned}"
    return cleaned


def call_openai_compatible_task(task: dict[str, Any], provider: str) -> tuple[str, str, dict[str, Any]]:
    cfg = openai_compat_config(provider)
    if not cfg:
        raise ProviderFailure(provider, 400, f"Unsupported provider: {provider}")
    conn_id = cfg["conn_id"]
    api_key = get_provider_key(conn_id)
    if not api_key:
        raise ProviderFailure(provider, 401, f"{provider} API key is not configured.")
    model = cfg["model"]
    endpoint = cfg["endpoint"]
    role = str(task.get("assigned_role") or "PM/PO")
    checks = task.get("acceptance_checks") or []
    ctx = project_context_for_role(role, task)
    prompt = (
        (f"Project context (confirmed reference information):\n{ctx}\n\n" if ctx else "")
        + f"Task ID: {task['id']}\nAssigned role: {role}\nProvider: {provider}\nRequest:\n{task.get('request', '')}\n\n"
        + "Acceptance checks:\n"
        + ("\n".join(f"- {c}" for c in checks) if checks else "- None specified")
    )
    prompt += build_repo_agent_instructions(task, provider)
    if needs_work_reply_envelope(task):
        prompt += "\n\n" + WORK_REPLY_CONTRACT
    contract = planning_contract_for_task(task)
    if contract:
        prompt += "\n\n" + contract
    if (task.get("validation") or {}).get("status") == "failed":
        prompt += "\n\nPrevious attempt failed validation checks:\n" + json.dumps(task["validation"].get("checks", []), indent=2)

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": combined_agent_prompt(role) + (("\n\n" + contract) if contract else ""),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    if cfg["max_tokens_key"] == "max_completion_tokens":
        payload["max_completion_tokens"] = int(cfg["fixed_max_tokens"])
    else:
        payload["max_tokens"] = 4096 if task.get("task_type") == "pm_planning" else 2048

    req_headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": HTTP_USER_AGENT,
    }
    if cfg.get("openrouter"):
        req_headers["X-Title"] = "Personal AI Team"

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=req_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=150) as resp:
            result = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw).get("error", {}).get("message") or raw
        except Exception:
            detail = raw
        detail_str = str(detail).strip()
        if exc.code == 403 and ("1010" in raw or "1020" in raw):
            detail_str = f"{detail_str} (Cloudflare blocked the request signature or VM VPN/proxy IP)"
        raise ProviderFailure(provider, exc.code, detail_str[:1500], exc.headers.get("Retry-After")) from exc
    except Exception as exc:
        raise ProviderFailure(provider, 503, str(exc)[:1000]) from exc

    choices = result.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    msg = choice.get("message") or {}
    content = msg.get("content")
    if content is None:
        content = ""
    elif isinstance(content, list):
        content = "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    text = str(content).strip()
    if not text:
        finish_hint = (
            " Token limit was reached before visible text was emitted."
            if choice.get("finish_reason") == "length"
            else ""
        )
        raise ProviderFailure(provider, 400, f"{provider} returned no text.{finish_hint}")
    return text, str(result.get("model") or model), {"usage": result.get("usage"), "finish_reason": choice.get("finish_reason")}


def test_provider_connection(conn_id: str) -> dict[str, Any]:
    if conn_id == "github":
        user = github_api("/user")
        login = str(user.get("login", ""))
        return {
            "connected": True,
            "provider": "GitHub",
            "login": login,
            "message": f"GitHub connected as {login}.",
        }

    record = provider_record_by_id(conn_id)
    if not record:
        raise ValueError("Unknown connection.")
    name = str(record.get("name", ""))
    api_key = get_provider_key(conn_id)
    if not api_key:
        raise ValueError(f"{name} key is not configured.")

    if record.get("kind") == "gemini":
        model = provider_model(name)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}?key={urllib.parse.quote(api_key, safe='')}"
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": HTTP_USER_AGENT},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")[:400]
            raise ValueError(f"{name} HTTP {exc.code}: {raw}") from exc
        except Exception as exc:
            raise ValueError(f"{name} request failed: {exc}") from exc
        return {
            "connected": True,
            "provider": name,
            "model": model,
            "message": f"{name} connected (model: {model}).",
        }

    if conn_id == "openrouter":
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/auth/key",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": HTTP_USER_AGENT,
                "X-Title": "Personal AI Team",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")[:400]
            raise ValueError(f"{name} HTTP {exc.code}: {raw}") from exc
        except Exception as exc:
            raise ValueError(f"{name} request failed: {exc}") from exc
        model = provider_model(name)
        return {
            "connected": True,
            "provider": name,
            "model": model,
            "message": f"{name} connected (default model: {model}).",
        }

    base = str(record.get("base_url", "")).strip().rstrip("/")
    model = provider_model(name)
    req = urllib.request.Request(
        f"{base}/models",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": HTTP_USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            parsed = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")[:400]
        raise ValueError(f"{name} HTTP {exc.code}: {raw}") from exc
    except Exception as exc:
        raise ValueError(f"{name} request failed: {exc}") from exc
    models = [str(m.get("id", "")) for m in (parsed.get("data") or []) if isinstance(m, dict)]
    return {
        "connected": True,
        "provider": name,
        "model": model,
        "model_available": (model in models) if models else None,
        "message": f"{name} connected ({len(models)} models listed; configured model '{model}').",
    }


def retry_after_time(header: str | None, fallback_hours: int = 2) -> str:
    if header:
        try:
            sec = min(max(0, int(float(header))), 48 * 3600)
            return (datetime.now(timezone.utc) + timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    return (datetime.now(timezone.utc) + timedelta(hours=fallback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


AUTO_ORDER_CACHE: dict[str, Any] = {"at": 0.0, "order": []}
AUTO_ORDER_TTL_SECONDS = 60.0


def auto_provider_order() -> list[str]:
    """Configured providers ordered by audit pass rate, ties broken by Connections order."""
    now = time.time()
    cached = AUTO_ORDER_CACHE.get("order") or []
    if cached and now - float(AUTO_ORDER_CACHE.get("at") or 0) < AUTO_ORDER_TTL_SECONDS:
        return list(cached)
    configured = configured_providers()
    scores: dict[str, dict[str, int]] = {}
    for row in get_provider_quality():
        prov = str(row.get("provider", ""))
        if prov not in configured:
            continue
        agg = scores.setdefault(prov, {"measured": 0, "passed": 0})
        agg["measured"] += int(row.get("measured") or 0)
        agg["passed"] += int(row.get("passed") or 0)
    registry_order = provider_names()

    def sort_key(name: str) -> tuple[float, int]:
        agg = scores.get(name)
        score = (100.0 * agg["passed"] / agg["measured"]) if agg and agg["measured"] else 0.0
        try:
            idx = registry_order.index(name)
        except ValueError:
            idx = len(registry_order)
        return (-score, idx)

    order = sorted(configured, key=sort_key)
    AUTO_ORDER_CACHE["at"] = now
    AUTO_ORDER_CACHE["order"] = order
    return list(order)


def select_auto_provider(tried: set[str] | None = None) -> str:
    skip = tried or set()
    for name in auto_provider_order():
        if name not in skip:
            return name
    return ""


def claim_task_for_provider(provider: str) -> dict[str, Any] | None:
    if provider not in configured_providers():
        return None
    model = provider_model(provider)
    with STATE_LOCK:
        summaries = reversed(read_tasks())
        qa_candidates = [
            item for item in summaries
            if item.get("produces_deliverable")
            and (
                item.get("status") == "Needs validation"
                or (item.get("status") == "Ready" and item.get("workflow_phase") == "qa")
            )
        ]
        all_tasks = read_tasks()
        for summary in qa_candidates:
            task = read_task(str(summary.get("id", "")))
            if not task:
                continue
            if task.get("status") == "Needs validation":
                task["qa_routing_cycle_start"] = len(task.get("attempts") or [])
                task["qa_return_role"] = str(task.get("assigned_role") or "Unknown")
                task["assigned_role"] = "QA"
                task["workflow_phase"] = "qa"
            selected_provider = provider_for_role(task, "QA", "qa_routing_cycle_start")
            if selected_provider != provider:
                continue
            repo_key = task_repository_key(task)
            if repo_key and repo_key in ACTIVE_REPOSITORY_TASKS:
                continue
            if repo_key:
                ACTIVE_REPOSITORY_TASKS.add(repo_key)
                task["_repository_key"] = repo_key
            task["qa_attempt_active"] = True
            task.pop("qa_subject_reply_file", None)
            if task.get("latest_reply_file"):
                task["qa_subject_reply_file"] = task["latest_reply_file"]
            break
        else:
            task = None

        if task is None:
            for summary in reversed(all_tasks):
                if summary.get("status") != "Ready":
                    continue
                candidate = read_task(str(summary.get("id", "")))
                if not candidate or candidate.get("status") != "Ready":
                    continue
                if candidate.get("workflow_phase") == "qa":
                    continue
                if task_provider(candidate) != provider:
                    continue
                if task_phase_gated(candidate, all_tasks):
                    continue
                repo_key = task_repository_key(candidate)
                if repo_key and repo_key in ACTIVE_REPOSITORY_TASKS:
                    continue
                if repo_key and any(
                    (
                        other.get("status") == "Needs validation"
                        or (other.get("status") == "Ready" and other.get("workflow_phase") == "qa")
                    )
                    and task_repository_key(other) == repo_key
                    for other in all_tasks
                ):
                    continue
                if repo_key and any(
                    other.get("status") == "Ready"
                    and other.get("workflow_phase") != "qa"
                    and task_repository_key(other) == repo_key
                    and str(other.get("created_at") or "") < str(candidate.get("created_at") or "")
                    and task_provider(other)
                    for other in all_tasks
                ):
                    continue
                task = candidate
                if repo_key:
                    ACTIVE_REPOSITORY_TASKS.add(repo_key)
                    task["_repository_key"] = repo_key
                break
        if task is None:
            return None

        attempts = task.setdefault("attempts", [])
        attempt_num = max((int(a.get("number", 0)) for a in attempts), default=0) + 1
        ts = now_iso()
        attempts.append(
            {
                "number": attempt_num,
                "provider": provider,
                "model": model,
                "role": str(task.get("assigned_role") or "PM/PO"),
                "state": "in_progress",
                "started_at": ts,
            }
        )
        task["status"] = "In progress"
        task["updated_at"] = ts
        task["attempt_timeout_seconds"] = DEFAULT_ATTEMPT_TIMEOUT_SECONDS
        task["dispatch_note"] = f"{provider} is working on this task."
        task.setdefault("activity", []).append(
            {
                "at": ts,
                "type": "qa_dispatched" if task.get("qa_attempt_active") else "dispatched",
                "message": (
                    f"Task sent to QA ({provider}, {model}), attempt {attempt_num}."
                    if task.get("qa_attempt_active")
                    else f"Task sent to {provider} ({model}), attempt {attempt_num}."
                ),
            }
        )
        repo_key = task.get("_repository_key") or task_repository_key(task)
        task.pop("_repository_key", None)
        write_task(task)
        append_event(
            {
                "at": ts,
                "task_id": task["id"],
                "type": "qa_dispatched" if task.get("qa_attempt_active") else "dispatched",
                "provider": provider,
                "attempt": attempt_num,
            }
        )
        if repo_key:
            task["_repository_key"] = repo_key
        return task


ACTIVE_PROVIDER_COUNTS: dict[str, int] = {}


def bump_provider_activity(provider: str, delta: int) -> None:
    ACTIVE_PROVIDER_COUNTS[provider] = max(0, ACTIVE_PROVIDER_COUNTS.get(provider, 0) + delta)


def dispatch_one_for_provider(provider: str) -> bool:
    task = claim_task_for_provider(provider)
    if not task:
        return False
    tid = task["id"]
    repository_key = str(task.pop("_repository_key", ""))
    attempt_num = int(task["attempts"][-1]["number"])
    qa_active = bool(task.get("qa_attempt_active"))
    bump_provider_activity(provider, 1)

    try:
        if provider_kind(provider) == "gemini":
            reply, meta = call_gemini_task(task)
            actual_model = provider_model(provider)
        else:
            reply, actual_model, meta = call_openai_compatible_task(task, provider)

        if needs_work_reply_envelope(task):
            try:
                reply = parse_work_reply_envelope(reply)
            except ValueError as exc:
                raise ProviderFailure(provider, 400, str(exc)) from exc

        with STATE_LOCK:
            current = read_task(tid)
            active_attempt = next(
                (item for item in (current or {}).get("attempts", []) if int(item.get("number", 0)) == attempt_num),
                None,
            )
            if not current or current.get("status") != "In progress" or not active_attempt or active_attempt.get("state") != "in_progress":
                ts = now_iso()
                folder = provider.lower().replace(" ", "")
                rel = f"replies/{tid}/{folder}/attempt-{attempt_num:03d}.json"
                atomic_json(
                    DATA / rel,
                    {
                        "task_id": tid,
                        "provider": provider,
                        "model": actual_model,
                        "attempt": attempt_num,
                        "at": ts,
                        "state": "stale",
                        "text": reply,
                        **meta,
                    },
                )
                logger.info("Late agent reply ignored task_id=%s provider=%s attempt=%s", tid, provider, attempt_num)
                append_event(
                    {"at": ts, "task_id": tid, "type": "stale_agent_reply_ignored", "provider": provider, "attempt": attempt_num}
                )
                if current:
                    for item in current.get("attempts") or []:
                        if int(item.get("number", 0)) == attempt_num:
                            item["stale_reply_file"] = rel
                            if item.get("state") == "in_progress":
                                item["state"] = "ignored"
                                item["finished_at"] = ts
                                item["error"] = "Task was no longer active when the response arrived."
                    current.setdefault("activity", []).append(
                        {
                            "at": ts,
                            "type": "stale_agent_reply_ignored",
                            "message": f"Late {provider} response from attempt {attempt_num} was saved but not applied.",
                        }
                    )
                    current["updated_at"] = ts
                    write_task(current)
                DISPATCH_EVENT.set()
                return True

        proj = next((p for p in read_projects() if p.get("id") == (task.get("project_id") or "default")), None)
        if (
            proj
            and proj.get("repository")
            and not qa_active
            and task.get("assigned_role") in ("Frontend developer", "Backend developer", "Documentation")
        ):
            apply_repository_manifest(task, reply, provider, attempt_num)

        with STATE_LOCK:
            fresh = read_task(tid)
            if fresh:
                ts = now_iso()
                folder = provider.lower().replace(" ", "")
                rel = f"replies/{tid}/{folder}/attempt-{attempt_num:03d}.json"
                atomic_json(
                    DATA / rel,
                    {
                        "task_id": tid,
                        "provider": provider,
                        "model": actual_model,
                        "attempt": attempt_num,
                        "at": ts,
                        "state": "success",
                        "text": reply,
                        **meta,
                    },
                )
                logger.info("Agent reply stored task_id=%s provider=%s attempt=%s file=%s", tid, provider, attempt_num, rel)
                for att in fresh.get("attempts") or []:
                    if att.get("number") == attempt_num:
                        att["state"] = "succeeded"
                        att["model"] = actual_model
                        att["finished_at"] = ts
                        att["reply_file"] = rel
                fresh["latest_reply_file"] = rel
                fresh["automatic_retries"] = 0
                fresh["updated_at"] = ts

                if qa_active:
                    try:
                        val = parse_qa_result(reply)
                    except ValueError as exc:
                        val = {
                            "status": "failed",
                            "method": "agent_qa",
                            "summary": "QA returned an invalid review result.",
                            "checks": [
                                {
                                    "check": "QA response format",
                                    "kind": "qa",
                                    "passed": False,
                                    "message": str(exc),
                                }
                            ],
                            "validated_at": ts,
                        }
                    fresh["validation"] = val
                    if val["status"] == "passed":
                        next_status = "Done"
                        msg = f"QA approved the deliverable after reviewing acceptance checks and available evidence."
                        fresh["qa_routing_cycle_start"] = len(fresh.get("attempts") or [])
                    else:
                        failures = int(fresh.get("qa_failures") or 0) + 1
                        fresh["qa_failures"] = failures
                        val["qa_failures"] = failures
                        if failures >= MAX_QA_FAILURES:
                            next_status = "Blocked"
                            msg = f"QA failed {failures} times; user intervention is required."
                            fresh["block_reason"] = msg
                        else:
                            next_status = "Ready"
                            msg = f"QA requested changes (failure {failures} of {MAX_QA_FAILURES}); returned to the original work role."
                            if fresh.get("provider") == "Automatic":
                                fresh["routing_cycle_start"] = len(fresh.get("attempts") or [])
                        fresh["assigned_role"] = str(fresh.pop("qa_return_role", "Unknown"))
                        fresh.pop("workflow_phase", None)
                    if next_status == "Done":
                        fresh["assigned_role"] = str(fresh.pop("qa_return_role", "Unknown"))
                        fresh.pop("workflow_phase", None)
                    fresh.pop("qa_attempt_active", None)
                elif fresh.get("task_type") == "pm_planning":
                    next_status, msg = finish_pm_planning_task(fresh, reply, provider)
                elif fresh.get("task_type") == "pm_escalation":
                    next_status, msg = finish_pm_escalation_task(fresh, reply, provider)
                elif fresh.get("produces_deliverable"):
                    val = deterministic_validation(fresh.get("acceptance_checks") or [], reply)
                    fresh["validation"] = val
                    if val["status"] == "failed":
                        next_status, msg = "Rework needed", f"{provider} replied, but deterministic acceptance checks failed."
                    else:
                        fresh["qa_routing_cycle_start"] = len(fresh.get("attempts") or [])
                        fresh["qa_subject_reply_file"] = rel
                        next_status, msg = "Needs validation", (
                            f"{provider} replied and deterministic checks passed; this deliverable is queued for QA review."
                            if val["status"] == "passed"
                            else f"{provider} replied; this deliverable is queued for QA review."
                        )
                else:
                    fresh["validation"] = {"status": "not_required", "checks": [], "validated_at": ts}
                    next_status, msg = "Done", f"{provider} replied; no deliverable validation was required."

                for att in fresh.get("attempts") or []:
                    if att.get("number") == attempt_num:
                        if qa_active:
                            att["quality_outcome"] = "passed" if val.get("status") == "passed" else "failed"
                        else:
                            att["quality_outcome"] = (
                                "passed"
                                if next_status == "Done" and fresh.get("produces_deliverable")
                                else ("failed" if next_status in ("Rework needed", "Blocked") else "unknown")
                            )
                fresh["status"] = next_status
                fresh.pop("dispatch_note", None)
                if qa_active and next_status == "Ready":
                    fresh["dispatch_note"] = msg
                fresh.setdefault("activity", []).append(
                    {
                        "at": ts,
                        "type": "qa_review" if qa_active else "agent_replied",
                        "from": "In progress",
                        "to": next_status,
                        "message": msg,
                    }
                )
                write_task(fresh)
                append_event(
                    {
                        "at": ts,
                        "task_id": tid,
                        "type": "qa_review" if qa_active else "agent_replied",
                        "provider": provider,
                        "to": next_status,
                    }
                )
                if next_status == "Blocked":
                    esc_id = maybe_create_escalation(fresh, msg)
                    if esc_id:
                        fresh.setdefault("activity", []).append(
                            {"at": now_iso(), "type": "escalation", "message": f"Auto-escalated to PM/PO as {esc_id}."}
                        )
                        write_task(fresh)
                if next_status in ("Needs validation", "Ready"):
                    DISPATCH_EVENT.set()

        fresh_after = read_task(tid)
        if fresh_after and fresh_after.get("pull_request"):
            github_postprocess_task(tid)

    except Exception as exc:
        failure = exc if isinstance(exc, ProviderFailure) else ProviderFailure(provider, 503, str(exc)[:1000])
        with STATE_LOCK:
            fresh = read_task(tid)
            if fresh:
                ts = now_iso()
                retryable = failure.status in (408, 429) or failure.status >= 500
                fallbackable = retryable or failure.status in (400, 401, 403)
                fallback = (
                    fallbackable
                    and has_untried_alternate(fresh, provider)
                    and (qa_active or fresh.get("provider") == "Automatic")
                )
                next_status = "Ready" if fallback else ("Waiting for capacity" if retryable else "Blocked")
                folder = provider.lower().replace(" ", "")
                rel = f"replies/{tid}/{folder}/attempt-{attempt_num:03d}.json"
                atomic_json(
                    DATA / rel,
                    {
                        "task_id": tid,
                        "provider": provider,
                        "attempt": attempt_num,
                        "at": ts,
                        "state": "error",
                        "error_status": failure.status,
                        "error": str(failure),
                    },
                )
                for att in fresh.get("attempts") or []:
                    if att.get("number") == attempt_num:
                        att["state"] = "provider_unavailable" if fallback else ("waiting_for_capacity" if retryable else "failed")
                        att["finished_at"] = ts
                        att["reply_file"] = rel
                msg = f"{provider} API error {failure.status}: {failure}."
                if fallback:
                    nxt = provider_for_role(fresh, "QA", "qa_routing_cycle_start") if qa_active else task_provider(fresh)
                    fresh["dispatch_note"] = f"{provider} unavailable; switching to {nxt}."
                    msg = f"{msg} Trying {nxt} automatically."
                elif retryable:
                    fresh["retry_after"] = retry_after_time(failure.retry_after, 24 if failure.status == 429 else 2)
                    fresh["waiting_reason"] = msg
                else:
                    fresh["block_reason"] = msg
                fresh["status"] = next_status
                fresh["updated_at"] = ts
                fresh["latest_reply_file"] = rel
                if qa_active:
                    fresh.pop("qa_attempt_active", None)
                    if not fallback and not retryable:
                        fresh["assigned_role"] = str(fresh.pop("qa_return_role", "Unknown"))
                        fresh.pop("workflow_phase", None)
                fresh.setdefault("activity", []).append(
                    {"at": ts, "type": "agent_error", "from": "In progress", "to": next_status, "message": msg}
                )
                write_task(fresh)
                append_event({"at": ts, "task_id": tid, "type": "agent_error", "provider": provider, "to": next_status})
                if next_status == "Blocked":
                    esc_id = maybe_create_escalation(fresh, msg)
                    if esc_id:
                        fresh.setdefault("activity", []).append(
                            {"at": now_iso(), "type": "escalation", "message": f"Auto-escalated to PM/PO as {esc_id}."}
                        )
                        write_task(fresh)
        if fallback:
            DISPATCH_EVENT.set()
    finally:
        bump_provider_activity(provider, -1)
        if repository_key:
            with STATE_LOCK:
                ACTIVE_REPOSITORY_TASKS.discard(repository_key)
            DISPATCH_EVENT.set()
    return True


PROVIDER_WORKER_LOCK = threading.Lock()
PROVIDER_WORKERS: dict[str, threading.Event] = {}


def provider_worker_loop(provider: str, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            worked = dispatch_one_for_provider(provider)
            if not worked:
                stop_event.wait(timeout=3.0)
                DISPATCH_EVENT.clear()
        except Exception:
            logger.exception("Provider worker %s dispatch failed", provider)
            stop_event.wait(timeout=3.0)


def reconcile_provider_workers() -> None:
    with PROVIDER_WORKER_LOCK:
        wanted = set(configured_providers())
        for name in list(PROVIDER_WORKERS):
            if name not in wanted:
                PROVIDER_WORKERS[name].set()
                del PROVIDER_WORKERS[name]
        for name in wanted:
            if name not in PROVIDER_WORKERS:
                stop_event = threading.Event()
                PROVIDER_WORKERS[name] = stop_event
                threading.Thread(target=provider_worker_loop, args=(name, stop_event), daemon=True).start()


def run_watchdog_pass() -> int:
    changed = 0
    now_epoch = time.time()
    with STATE_LOCK:
        for task in read_tasks():
            status = task.get("status")
            if status == "In progress":
                attempts = task.setdefault("attempts", [])
                active = next((a for a in reversed(attempts) if a.get("state") == "in_progress"), None)
                started_ts = parse_iso_epoch((active or {}).get("started_at") or task.get("updated_at"))
                if started_ts is None:
                    continue
                timeout_sec = int(task.get("attempt_timeout_seconds") or DEFAULT_ATTEMPT_TIMEOUT_SECONDS)
                if now_epoch - started_ts < timeout_sec:
                    continue
                if active:
                    active["state"] = "timed_out"
                    active["finished_at"] = now_iso()
                    active["error"] = "No response before the attempt timeout."
                retries = int(task.get("automatic_retries") or 0)
                if retries >= MAX_AUTOMATIC_RETRIES:
                    task["status"] = "Blocked"
                    reason = "Automatic retry limit reached after an agent timeout."
                    task["block_reason"] = reason
                    if task.get("workflow_phase") == "qa":
                        task["assigned_role"] = str(task.pop("qa_return_role", "Unknown"))
                        task.pop("workflow_phase", None)
                        task.pop("qa_attempt_active", None)
                else:
                    wait_h = min(2 * (retries + 1), 6)
                    task["status"] = "Waiting for capacity"
                    task["retry_after"] = (datetime.now(timezone.utc) + timedelta(hours=wait_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    task["waiting_reason"] = "Agent attempt timed out."
                    reason = f"Agent attempt timed out; retry scheduled after {wait_h} hours."
                    if task.get("workflow_phase") == "qa":
                        task.pop("qa_attempt_active", None)
                ts = now_iso()
                task["updated_at"] = ts
                task.setdefault("activity", []).append(
                    {"at": ts, "type": "watchdog_recovery", "from": "In progress", "to": task["status"], "message": reason}
                )
                write_task(task)
                if task["status"] == "Blocked":
                    esc_id = maybe_create_escalation(task, reason)
                    if esc_id:
                        task.setdefault("activity", []).append(
                            {"at": now_iso(), "type": "escalation", "message": f"Auto-escalated to PM/PO as {esc_id}."}
                        )
                        write_task(task)
                changed += 1
            elif status == "Waiting for capacity" and task.get("retry_after"):
                retry_ts = parse_iso_epoch(task.get("retry_after"))
                if retry_ts is None or now_epoch < retry_ts:
                    continue
                ts = now_iso()
                task["automatic_retries"] = int(task.get("automatic_retries") or 0) + 1
                if task.get("provider") == "Automatic" or task.get("workflow_phase") == "qa":
                    role = str(task.get("assigned_role") or "PM/PO")
                    qa_active = task.get("workflow_phase") == "qa"
                    cycle_key = "qa_routing_cycle_start" if qa_active else "routing_cycle_start"
                    start = max(0, int(task.get(cycle_key) or 0))
                    tried = {
                        str(a.get("provider", ""))
                        for a in (task.get("attempts") or [])[start:]
                        if str(a.get("role") or role) == role
                    }
                    order = selection_order_for_role("QA" if qa_active else role)
                    configured = configured_providers()
                    if not any(p in configured and p not in tried for p in order):
                        task[cycle_key] = len(task.get("attempts") or [])
                task["status"] = "Ready"
                task.pop("retry_after", None)
                task.pop("waiting_reason", None)
                task["dispatch_note"] = "Retry window reached; queued for dispatch."
                task["updated_at"] = ts
                task.setdefault("activity", []).append(
                    {
                        "at": ts,
                        "type": "watchdog_recovery",
                        "from": "Waiting for capacity",
                        "to": "Ready",
                        "message": "Retry time reached; returned to Ready queue.",
                    }
                )
                write_task(task)
                changed += 1
    if changed:
        DISPATCH_EVENT.set()
    return changed


def watchdog_worker_loop() -> None:
    while True:
        try:
            changed = run_watchdog_pass()
            WATCHDOG_STATUS["last_run"] = now_iso()
            WATCHDOG_STATUS["changed"] = changed
            WATCHDOG_STATUS["error"] = None
        except Exception as exc:
            WATCHDOG_STATUS["last_run"] = now_iso()
            WATCHDOG_STATUS["error"] = str(exc)[:300]
        time.sleep(WATCHDOG_INTERVAL_SECONDS)


CI_POLL_INTERVAL_SECONDS = 90


def run_ci_poll_pass() -> None:
    try:
        for msg in check_project_milestones():
            logger.info("Milestone: %s", msg)
    except Exception as exc:
        logger.warning("Milestone check failed: %s", str(exc)[:300])
    for task in read_tasks():
        pull = task.get("pull_request")
        if (
            task.get("status") == "Done"
            and (task.get("validation") or {}).get("status") == "passed"
            and isinstance(pull, dict)
            and pull.get("state") == "open"
            and pull.get("number")
        ):
            try:
                github_postprocess_task(str(task.get("id") or ""))
            except Exception as exc:
                logger.warning("CI poll failed for task %s: %s", task.get("id"), str(exc)[:300])


def ci_poll_worker_loop() -> None:
    while True:
        try:
            run_ci_poll_pass()
        except Exception as exc:
            logger.warning("CI poll pass failed: %s", str(exc)[:300])
        time.sleep(CI_POLL_INTERVAL_SECONDS)


def provider_activity_status() -> dict[str, Any]:
    """Return live provider busy flags keyed by provider name."""
    return {
        "provider_working": {name: ACTIVE_PROVIDER_COUNTS.get(name, 0) > 0 for name in provider_names()},
    }

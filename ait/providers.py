"""PM planning and escalation handling."""

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



"""PM planning helpers."""

from __future__ import annotations

from ait.common import *


# --- PM plan parsing ---

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

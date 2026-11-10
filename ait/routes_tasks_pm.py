"""PM/PO decision API routes: planning requests, doc updates, clarifications,
overview approval, and design reviews."""

from __future__ import annotations

from ait.common import *


def handle_tasks_pm_post(handler, path, body) -> bool:
    if path == "/api/pm-requests":
        req_text = str(body.get("request", "")).strip()
        if not req_text or len(req_text) > 12000:
            handler.send_json({"error": "Describe a project request under 12,000 characters."}, HTTPStatus.BAD_REQUEST)
            return True
        pid = str(body.get("project_id") or "default")
        if not any(p.get("id") == pid for p in read_projects()):
            handler.send_json({"error": "Choose a valid project."}, HTTPStatus.BAD_REQUEST)
            return True
        tid = next_task_id()
        ts = now_iso()
        task = {
            "id": tid,
            "task_type": "pm_planning",
            "title": "PM/PO planning: " + re.sub(r"\s+", " ", req_text)[:130],
            "request": req_text,
            "original_request": req_text,
            "project_id": pid,
            "module_id": "",
            "planning_root_id": tid,
            "planning_round": 1,
            "assigned_role": "PM/PO",
            "provider": "Automatic",
            "status": "Ready",
            "produces_deliverable": False,
            "acceptance_checks": [],
            "attempts": [],
            "validation": None,
            "created_at": ts,
            "updated_at": ts,
            "dispatch_note": "Planning request queued for the PM/PO agent.",
            "activity": [{"at": ts, "type": "created", "message": "Project request sent to PM/PO from chat."}],
        }
        write_task(task)
        hist = read_chat_history(pid)
        hist.extend(
            [
                {"role": "user", "content": req_text, "at": ts},
                {
                    "role": "assistant",
                    "content": f"I sent this request to PM/PO as {tid}. It will return clarification questions or a proposed backlog on the task board.",
                    "at": ts,
                    "task_id": tid,
                },
            ]
        )
        write_chat_history(hist, pid)
        DISPATCH_EVENT.set()
        handler.send_json({"task": task}, HTTPStatus.CREATED)
        return True

    if path == "/api/doc-updates":
        kind = str(body.get("kind", "")).strip()
        pid = str(body.get("project_id") or "default")
        proj = next((p for p in read_projects() if p.get("id") == pid), None)
        if not proj:
            handler.send_json({"error": "Choose a valid project."}, HTTPStatus.BAD_REQUEST)
            return True
        specs = {
            "docs": (
                "doc_update",
                "Update project documentation",
                "Review the project's current documents and task board, then propose updated versions of all four "
                "project documents (project_brief, technical_overview, decisions, outcomes). Request any "
                "on-request documents you need via info_requests before answering. Keep confirmed facts, mark "
                "assumptions, and keep each document under 20,000 characters.",
            ),
            "roadmap": (
                "roadmap_update",
                "Update roadmap",
                "Review the project's master plan, phase plans, and task board, then propose an updated "
                "master_plan, any changed phase plans (phase_1 through phase_9), and the current active_phase. "
                "Request any on-request documents you need via info_requests before answering. Keep each "
                "document under 20,000 characters.",
            ),
        }
        spec = specs.get(kind)
        if not spec:
            handler.send_json({"error": "kind must be 'docs' or 'roadmap'."}, HTTPStatus.BAD_REQUEST)
            return True
        task_type, title, request = spec
        tid = next_task_id()
        ts = now_iso()
        task = {
            "id": tid,
            "task_type": task_type,
            "title": f"{title}: {proj.get('name', pid)}",
            "request": request,
            "project_id": pid,
            "module_id": "",
            "assigned_role": "PM/PO",
            "provider": "Automatic",
            "status": "Ready",
            "produces_deliverable": False,
            "acceptance_checks": [],
            "attempts": [],
            "validation": None,
            "created_at": ts,
            "updated_at": ts,
            "dispatch_note": "Update request queued for the PM/PO agent.",
            "activity": [{"at": ts, "type": "created", "message": f"{title} requested by user."}],
        }
        write_task(task)
        DISPATCH_EVENT.set()
        handler.send_json({"task": task}, HTTPStatus.CREATED)
        return True

    m_doc_decision = re.fullmatch(r"/api/tasks/(T-\d{4,})/doc-update-decision", path)
    if m_doc_decision:
        try:
            task = decide_doc_update(m_doc_decision.group(1), str(body.get("decision", "")))
        except KeyError:
            handler.send_json({"error": "Update task not found."}, HTTPStatus.NOT_FOUND)
            return True
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
            return True
        handler.send_json({"task": task})
        return True

    m_ans = re.fullmatch(r"/api/tasks/(T-\d{4,})/answer", path)
    if m_ans:
        tid = m_ans.group(1)
        selected = str(body.get("selected_option", "")).strip()
        free_text = str(body.get("free_text", "")).strip()[:5000]
        with STATE_LOCK:
            question = read_task(tid)
            if not question:
                handler.send_json({"error": "Question not found."}, HTTPStatus.NOT_FOUND)
                return True
            if question.get("task_type") != "clarification_question" or question.get("status") != "Needs answer":
                handler.send_json({"error": "This question is not waiting for an answer."}, HTTPStatus.CONFLICT)
                return True
            q_data = question.get("question_data") or {}
            opts = [o.get("label") for o in (q_data.get("options") or []) if isinstance(o, dict)]
            if selected and selected not in opts:
                handler.send_json({"error": "Choose one of the listed options."}, HTTPStatus.BAD_REQUEST)
                return True
            if not selected and not free_text:
                handler.send_json({"error": "Choose an option or enter a response."}, HTTPStatus.BAD_REQUEST)
                return True
            if q_data.get("allow_free_text") is False and not selected:
                handler.send_json({"error": "Choose one of the listed options."}, HTTPStatus.BAD_REQUEST)
                return True

            ans = (f"Selected option: {selected}\n" if selected else "") + (
                f"Additional response: {free_text}" if free_text else ""
            )
            ts = now_iso()
            question["status"] = "Done"
            question["selected_option"] = selected or None
            question["free_text"] = free_text
            question["answer"] = ans
            question["updated_at"] = ts
            question.setdefault("activity", []).append(
                {"at": ts, "type": "question_answered", "message": "Clarification answered by the user."}
            )
            write_task(question)

            all_t = read_tasks()
            siblings = [
                t
                for t in all_t
                if t.get("task_type") == "clarification_question"
                and t.get("planning_task_id") == question.get("planning_task_id")
            ]
            followup = None
            if siblings and all(s.get("status") == "Done" for s in siblings):
                parent = read_task(str(question.get("planning_task_id") or ""))
                if parent and not parent.get("followup_created"):
                    parent["followup_created"] = True
                    parent.setdefault("activity", []).append(
                        {
                            "at": ts,
                            "type": "clarifications_complete",
                            "message": "All questions answered; sending answers back to PM/PO.",
                        }
                    )
                    write_task(parent)
                    root_id = question.get("planning_root_id")
                    answered_q = sorted(
                        [
                            t
                            for t in read_tasks()
                            if t.get("task_type") == "clarification_question"
                            and t.get("planning_root_id") == root_id
                            and t.get("status") == "Done"
                        ],
                        key=lambda x: (int(x.get("planning_round") or 1), str(x.get("created_at", ""))),
                    )
                    qa_txt = "\n".join(f"Q: {q.get('request')}\nA: {q.get('answer', '')}" for q in answered_q)
                    fid = next_task_id()
                    next_round = int(parent.get("planning_round") or 1) + 1
                    followup = {
                        "id": fid,
                        "task_type": "pm_planning",
                        "title": f"PM/PO planning follow-up, round {next_round}",
                        "request": (
                            f"Original project request:\n{parent.get('original_request', '')}\n\n"
                            f"Clarifications answered so far:\n{qa_txt}\n\n"
                            "Continue planning. Ask focused questions only if critical details are still missing; otherwise return the proposed task backlog."
                        ),
                        "original_request": parent.get("original_request", ""),
                        "planning_root_id": root_id,
                        "planning_round": next_round,
                        "assigned_role": "PM/PO",
                        "provider": "Automatic",
                        "status": "Ready",
                        "project_id": parent.get("project_id") or "default",
                        "module_id": "",
                        "produces_deliverable": False,
                        "acceptance_checks": [],
                        "attempts": [],
                        "validation": None,
                        "created_at": ts,
                        "updated_at": ts,
                        "dispatch_note": "Answered clarifications queued for PM/PO.",
                        "activity": [{"at": ts, "type": "created", "message": "Follow-up planning request queued."}],
                    }
                    write_task(followup)
                    append_chat_notice(
                        f"All clarification answers are in. I sent them back to PM/PO as {fid}.",
                        ts,
                        fid,
                        str(parent.get("project_id") or "default"),
                    )
                    DISPATCH_EVENT.set()
        handler.send_json({"task": question, "followup_task": followup})
        return True

    m_app_ov = re.fullmatch(r"/api/tasks/(T-\d{4,})/approve-overview", path)
    if m_app_ov:
        tid = m_app_ov.group(1)
        try:
            documents = clean_overview_documents(body)
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True
        with STATE_LOCK:
            task = read_task(tid)
            if not task:
                handler.send_json({"error": "Project overview task not found."}, HTTPStatus.NOT_FOUND)
                return True
            if task.get("task_type") != "project_overview" or task.get("status") != "Needs approval":
                handler.send_json({"error": "This project overview is no longer awaiting approval."}, HTTPStatus.CONFLICT)
                return True
            ts = now_iso()
            snapshots: dict[str, str | None] = {}
            pid = str(task.get("project_id") or "default")
            if not any(p.get("id") == pid for p in read_projects()):
                handler.send_json({"error": "Attached project no longer exists."}, HTTPStatus.CONFLICT)
                return True
            for slug, content in documents.items():
                snapshots[slug] = save_project_context_document(pid, slug, content)
            task["overview_documents"] = documents
            task["status"] = "Done"
            task["updated_at"] = ts
            task["approved_at"] = ts
            task["validation"] = {
                "status": "passed",
                "method": "user_approval",
                "reviewed_by": "User",
                "notes": "Approved and applied to Project context.",
                "snapshots": snapshots,
                "validated_at": ts,
            }
            task.setdefault("activity", []).append(
                {"at": ts, "type": "overview_approved", "from": "Needs approval", "to": "Done", "message": "User approved project overview."}
            )
            write_task(task)

            released: list[str] = []
            plan_id = str(task.get("source_plan_task_id") or "")
            if plan_id:
                for wt in read_tasks():
                    if (
                        wt.get("task_type") == "work"
                        and wt.get("status") == "Draft"
                        and not wt.get("pending_design_review")
                        and str(wt.get("source_plan_task_id") or "") == plan_id
                    ):
                        wt["status"] = "Ready"
                        wt["updated_at"] = ts
                        wt["dispatch_note"] = "Plan approved; queued for dispatch."
                        wt.pop("retry_after", None)
                        wt.pop("waiting_reason", None)
                        wt.setdefault("activity", []).append(
                            {"at": ts, "type": "plan_released", "from": "Draft", "to": "Ready", "message": f"Released by project overview approval {tid}."}
                        )
                        write_task(wt)
                        released.append(str(wt.get("id")))
                if released:
                    task.setdefault("activity", []).append(
                        {"at": ts, "type": "plan_tasks_released", "message": f"Plan approval released {len(released)} draft work task(s): {', '.join(released)}."}
                    )
                    write_task(task)
                    DISPATCH_EVENT.set()
        handler.send_json({"task": task, "snapshots": snapshots, "released_task_ids": released})
        return True

    m_design = re.fullmatch(r"/api/tasks/(T-\d{4,})/design-review", path)
    if m_design:
        tid = m_design.group(1)
        decision = str(body.get("decision", "")).strip().lower()
        free_text = str(body.get("free_text", "")).strip()[:5000]
        alt_index = body.get("alternative_index")
        if decision not in ("approve", "reject", "alternative"):
            handler.send_json({"error": "Decision must be approve, reject, or alternative."}, HTTPStatus.BAD_REQUEST)
            return True
        with STATE_LOCK:
            task = read_task(tid)
            if not task:
                handler.send_json({"error": "Design review task not found."}, HTTPStatus.NOT_FOUND)
                return True
            if task.get("task_type") != "design_review" or task.get("status") != "Needs approval":
                handler.send_json({"error": "This design review is no longer awaiting a decision."}, HTTPStatus.CONFLICT)
                return True
            review = task.get("review_data") or {}
            team_ids = [str(x) for x in (review.get("team_task_ids") or [])]
            pid = str(task.get("project_id") or "default")
            ts = now_iso()
            chosen_alt = ""
            chosen_alt_detail = ""
            if decision == "alternative":
                try:
                    idx = int(alt_index)
                except (TypeError, ValueError):
                    idx = -1
                alts = review.get("alternatives") or []
                if not (0 <= idx < len(alts)):
                    handler.send_json({"error": "Choose a listed alternative."}, HTTPStatus.BAD_REQUEST)
                    return True
                chosen_alt = str(alts[idx].get("title") or "")
                chosen_alt_detail = str(alts[idx].get("description") or "")
            elif decision == "reject" and not free_text:
                handler.send_json({"error": "Explain why the design change is rejected, or pick an alternative."}, HTTPStatus.BAD_REQUEST)
                return True

            task["status"] = "Done"
            task["updated_at"] = ts
            task["design_decision"] = decision
            task["design_free_text"] = free_text
            task["design_alternative"] = chosen_alt or None
            task["design_alternative_detail"] = chosen_alt_detail or None
            task.setdefault("activity", []).append(
                {
                    "at": ts,
                    "type": "design_review_decision",
                    "from": "Needs approval",
                    "to": "Done",
                    "message": f"User decision: {decision}" + (f" ({chosen_alt})" if chosen_alt else "") + ".",
                }
            )
            write_task(task)

            if decision == "approve":
                for wid in team_ids:
                    wt = read_task(wid)
                    if wt and wt.get("pending_design_review"):
                        was_draft = wt.get("status") == "Draft"
                        wt.pop("pending_design_review", None)
                        if was_draft:
                            wt["status"] = "Ready"
                            wt["dispatch_note"] = "Design review approved; provisional task queued."
                        wt["updated_at"] = ts
                        wt.setdefault("activity", []).append(
                            {"at": ts, "type": "design_approved", "message": f"Design review {tid} approved; provisional task {'queued' if was_draft else 'confirmed'}."}
                        )
                        write_task(wt)
                desc = str((review.get("design_change") or {}).get("description") or review.get("summary") or "")
                try:
                    prev = load_project_context(pid, "decisions")
                    note = f"- {ts[:10]}: Design change approved (review {tid}): {desc[:500]}"
                    save_project_context_document(pid, "decisions", (prev.rstrip() + "\n" + note + "\n").strip() + "\n")
                except Exception:
                    pass
                src = read_task(str(review.get("source_task_id") or ""))
                if src and src.get("status") == "Blocked":
                    src["status"] = "Canceled"
                    src["updated_at"] = ts
                    src["superseded_by_design_review"] = tid
                    src.setdefault("activity", []).append(
                        {
                            "at": ts,
                            "type": "design_review_approved",
                            "from": "Blocked",
                            "to": "Canceled",
                            "message": f"Design review {tid} approved; provisional tasks confirmed and this blocked task was canceled as superseded.",
                        }
                    )
                    write_task(src)
            else:
                reason_bits = [f"User {decision} design review {tid}."]
                if chosen_alt:
                    reason_bits.append(f"Chosen alternative: {chosen_alt}.")
                    if chosen_alt_detail:
                        reason_bits.append(f"Alternative details: {chosen_alt_detail}")
                if free_text:
                    reason_bits.append(f"User feedback: {free_text}")
                for wid in team_ids:
                    wt = read_task(wid)
                    if not wt:
                        continue
                    if wt.get("status") in ("Done", "Canceled"):
                        continue
                    wt["status"] = "Canceled"
                    wt["updated_at"] = ts
                    wt.pop("pending_design_review", None)
                    wt.setdefault("activity", []).append(
                        {"at": ts, "type": "design_rejected", "message": "Provisional task canceled after design review rejection."}
                    )
                    write_task(wt)
                esc_id = next_task_id()
                orig = read_task(str(review.get("escalation_task_id") or ""))
                blocked = read_task(str(review.get("source_task_id") or ""))
                write_task(
                    {
                        "id": esc_id,
                        "task_type": "pm_escalation",
                        "title": f"PM/PO escalation round 2: design review {tid} {decision}",
                        "request": (
                            f"The user {decision} your design change proposal (review {tid}).\n\n"
                            f"Original proposal:\n{str(review.get('summary') or '')}\n"
                            f"{str((review.get('design_change') or {}).get('description') or '')}\n\n"
                            + "\n".join(reason_bits)
                            + (f"\n\nBlocked source task {blocked.get('id') if blocked else '?'}: {blocked.get('block_reason', '')}" if blocked else "")
                            + "\n\nPropose a revised plan: new team_tasks and alternatives addressing the user's decision. "
                            "The previously proposed provisional tasks were canceled."
                        ),
                        "escalation_for_task_id": str(review.get("source_task_id") or ""),
                        "escalation_round": int((orig or {}).get("escalation_round") or 1) + 1,
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
                        "dispatch_note": "Design review rejected; revised escalation queued for PM/PO.",
                        "activity": [{"at": ts, "type": "created", "message": f"Follow-up escalation after user {decision} on {tid}."}],
                    }
                )
                if blocked and blocked.get("status") == "Blocked":
                    blocked.setdefault("activity", []).append(
                        {
                            "at": ts,
                            "type": "design_review_rejected",
                            "message": f"Design review {tid} was {decision}; follow-up escalation {esc_id} queued for PM/PO.",
                        }
                    )
                    blocked["updated_at"] = ts
                    write_task(blocked)
                DISPATCH_EVENT.set()
        handler.send_json({"task": task})
        return True

    return False

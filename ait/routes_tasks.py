"""Task and PM-request API routes."""

from __future__ import annotations

from ait.common import *


def handle_tasks_get(handler, path, query) -> bool:
    if path == "/api/tasks":
        handler.send_json({"tasks": read_tasks()})
        return True

    m_replies = re.fullmatch(r"/api/tasks/(T-\d{4,})/replies", path)
    if m_replies:
        tid = m_replies.group(1)
        if not read_task(tid):
            handler.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
            return True
        handler.send_json({"replies": read_task_replies(tid)})
        return True

    return False


def handle_tasks_post(handler, path, body) -> bool:
    if path == "/api/tasks":
        title = str(body.get("title", "")).strip()
        req_text = str(body.get("request", "")).strip()
        role = str(body.get("role", "PM/PO")).strip()
        provider = str(body.get("provider", "Automatic")).strip()
        raw_checks = body.get("acceptance_checks", [])
        produces = bool(body.get("produces_deliverable"))
        logger.info(f"Creating task: title='{title}', role={role}, provider={provider}")
        if not title or not req_text or len(title) > 160 or len(req_text) > 12000:
            handler.send_json({"error": "Valid title and request are required."}, HTTPStatus.BAD_REQUEST)
            return True
        if role not in WORK_ROLES or provider not in task_provider_options():
            handler.send_json({"error": "Choose a valid work role and AI provider."}, HTTPStatus.BAD_REQUEST)
            return True
        pid = str(body.get("project_id") or "default")
        mid = str(body.get("module_id") or "")
        proj = next((p for p in read_projects() if p.get("id") == pid), None)
        if not proj or (mid and not any(m.get("id") == mid for m in proj.get("modules", []))):
            handler.send_json({"error": "Choose a valid project and sub-project."}, HTTPStatus.BAD_REQUEST)
            return True
        if not isinstance(raw_checks, list):
            handler.send_json({"error": "Acceptance checks must be a list."}, HTTPStatus.BAD_REQUEST)
            return True
        checks = [str(c).strip()[:500] for c in raw_checks if str(c).strip()][:20]
        if (
            proj.get("repository")
            and role in ("Frontend developer", "Backend developer", "Documentation")
            and (not produces or not checks)
        ):
            handler.send_json(
                {"error": "Repository work must be marked as a deliverable and include at least one acceptance check."},
                HTTPStatus.BAD_REQUEST,
            )
            return True
        ts = now_iso()
        tid = next_task_id()
        task = {
            "id": tid,
            "task_type": "work",
            "title": title,
            "request": req_text,
            "assigned_role": role,
            "provider": provider,
            "project_id": pid,
            "module_id": mid,
            "acceptance_checks": checks,
            "produces_deliverable": produces,
            "status": "Draft",
            "created_at": ts,
            "updated_at": ts,
            "attempts": [],
            "validation": None,
            "activity": [{"at": ts, "type": "created", "message": "Task created as Draft."}],
        }
        write_task(task)
        handler.send_json({"task": task}, HTTPStatus.CREATED)
        return True

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


    m_status = re.fullmatch(r"/api/tasks/(T-\d{4,})/status", path)
    if m_status:
        tid = m_status.group(1)
        target = str(body.get("status", ""))
        note = str(body.get("note", "")).strip()[:500]
        if target not in ("Ready", "Blocked", "Canceled", "Draft"):
            handler.send_json({"error": "Invalid manual status transition."}, HTTPStatus.BAD_REQUEST)
            return True
        with STATE_LOCK:
            task = read_task(tid)
            if not task:
                handler.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                return True
            cur = str(task.get("status", ""))
            if target not in TRANSITIONS.get(cur, set()) or target not in USER_TRANSITIONS.get(cur, set()):
                handler.send_json({"error": f"Cannot move a {cur} task to {target}."}, HTTPStatus.CONFLICT)
                return True
            if target == "Ready" and task.get("produces_deliverable") and not (task.get("acceptance_checks") or []):
                handler.send_json(
                    {"error": "Add at least one acceptance check before marking a deliverable task Ready."},
                    HTTPStatus.BAD_REQUEST,
                )
                return True
            ts = now_iso()
            if target == "Ready":
                task.pop("retry_after", None)
                task.pop("waiting_reason", None)
                was_qa_blocked = cur == "Blocked" and int(task.get("qa_failures") or 0) > 0
                if cur in ("Waiting for capacity", "Blocked") and (not task.get("provider") or task.get("provider") == "Automatic"):
                    task["routing_cycle_start"] = len(task.get("attempts") or [])
                if cur == "Rework needed" and task.get("provider") == "Automatic":
                    task["routing_cycle_start"] = len(task.get("attempts") or [])
                if cur == "Blocked":
                    task["qa_failures"] = 0
                    task.pop("block_reason", None)
                sel = task_provider(task)
                if cur == "Rework needed":
                    msg = "Task returned to the agent queue with its failed validation results included."
                elif was_qa_blocked:
                    msg = "Task manually unblocked; QA failure count reset and the task queued for dispatch."
                elif cur == "Waiting for capacity":
                    msg = "Manual retry requested; task queued for immediate dispatch."
                else:
                    msg = "Task is clear and queued for automatic dispatch."
                task["dispatch_note"] = (
                    f"Waiting for {sel} to respond."
                    if sel
                    else "No provider in this task's route is connected. Add a provider key in Connections & routing."
                )
            elif target == "Blocked":
                task.pop("retry_after", None)
                task.pop("waiting_reason", None)
                msg = note or "Task blocked by the user."
                task["block_reason"] = msg
            elif target == "Canceled":
                task.pop("retry_after", None)
                task.pop("waiting_reason", None)
                msg = note or "Task canceled by the user."
            else:
                msg = note or "Task returned to Draft."
                task.pop("block_reason", None)
            task["status"] = target
            task["updated_at"] = ts
            task.setdefault("activity", []).append(
                {"at": ts, "type": "status_changed", "from": cur, "to": target, "message": msg}
            )
            write_task(task)
        if target == "Ready":
            DISPATCH_EVENT.set()
        handler.send_json({"task": task})
        return True

    m_prov = re.fullmatch(r"/api/tasks/(T-\d{4,})/provider", path)
    if m_prov:
        tid = m_prov.group(1)
        provider = str(body.get("provider", "")).strip()
        if provider not in task_provider_options():
            handler.send_json({"error": "Choose Automatic, Auto, or a valid AI provider."}, HTTPStatus.BAD_REQUEST)
            return True
        with STATE_LOCK:
            task = read_task(tid)
            if not task:
                handler.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                return True
            if task.get("status") in ("In progress", "Done", "Canceled"):
                handler.send_json(
                    {"error": f"Cannot change AI provider while a task is {task.get('status')}."},
                    HTTPStatus.CONFLICT,
                )
                return True
            prev_prov = str(task.get("provider") or "Automatic")
            ts = now_iso()
            task["provider"] = provider
            task["routing_cycle_start"] = len(task.get("attempts") or [])
            if task.get("status") == "Ready":
                sel = task_provider(task)
                task["dispatch_note"] = (
                    f"Waiting for {sel} to respond."
                    if sel
                    else "No provider in this task's route is connected. Add a provider key in Connections & routing."
                )
            task["updated_at"] = ts
            if prev_prov != provider:
                task.setdefault("activity", []).append(
                    {
                        "at": ts,
                        "type": "provider_changed",
                        "message": f"AI provider changed from {prev_prov} to {provider}.",
                    }
                )
            write_task(task)
            append_event({"at": ts, "task_id": tid, "type": "provider_changed", "from": prev_prov, "to": provider})
            is_ready = task.get("status") == "Ready"
        if is_ready:
            DISPATCH_EVENT.set()
        handler.send_json({"task": task})
        return True

    m_val = re.fullmatch(r"/api/tasks/(T-\d{4,})/validation", path)
    if m_val:
        tid = m_val.group(1)
        task = read_task(tid)
        if not task:
            handler.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
            return True
        handler.send_json(
            {"error": "Deliverable validation is performed automatically by the QA role."},
            HTTPStatus.CONFLICT,
        )
        return True

    m_local_sync = re.fullmatch(r"/api/tasks/(T-\d{4,})/sync-local", path)
    if m_local_sync:
        tid = m_local_sync.group(1)
        task = read_task(tid)
        if not task:
            handler.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
            return True
        pull = task.get("pull_request")
        if (
            task.get("status") != "Done"
            or (task.get("validation") or {}).get("status") != "passed"
            or not isinstance(pull, dict)
            or pull.get("state") != "merged"
        ):
            handler.send_json(
                {"error": "Local sync can only be retried after QA passes and the pull request is merged."},
                HTTPStatus.CONFLICT,
            )
            return True
        if not sync_local_checkout_after_merge(tid):
            latest = read_task(tid) or task
            handler.send_json(
                {"error": latest.get("local_sync_error") or "Local checkout sync failed."},
                HTTPStatus.BAD_REQUEST,
            )
            return True
        handler.send_json({"task": read_task(tid)})
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

    m_merge = re.fullmatch(r"/api/tasks/(T-\d{4,})/approve-merge", path)
    if m_merge:
        tid = m_merge.group(1)
        with STATE_LOCK:
            task = read_task(tid)
            if not task:
                handler.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                return True
            if task.get("status") != "Needs approval" or not task.get("merge_approval_required"):
                handler.send_json({"error": "This task is not waiting for merge approval."}, HTTPStatus.CONFLICT)
                return True
            ts = now_iso()
            task["status"] = "Done"
            task["merge_approved"] = True
            task["merge_approval_required"] = False
            task["merge_approved_at"] = ts
            task["updated_at"] = ts
            task.setdefault("activity", []).append(
                {
                    "at": ts,
                    "type": "merge_approved",
                    "from": "Needs approval",
                    "to": "Done",
                    "message": "User approved the pull request merge.",
                }
            )
            write_task(task)
        github_postprocess_task(tid)
        handler.send_json({"task": read_task(tid)})
        return True


    return False


def handle_tasks_put(handler, path, body) -> bool:
    m_ov = re.fullmatch(r"/api/tasks/(T-\d{4,})/overview", path)
    if m_ov:
        tid = m_ov.group(1)
        try:
            documents = clean_overview_documents(body)
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True
        with STATE_LOCK:
            task = read_task(tid)
            if not task:
                handler.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                return True
            if task.get("task_type") != "project_overview" or task.get("status") != "Needs approval":
                handler.send_json({"error": "Overview is no longer awaiting approval."}, HTTPStatus.CONFLICT)
                return True
            ts = now_iso()
            task["overview_documents"] = documents
            task["updated_at"] = ts
            task.setdefault("activity", []).append(
                {"at": ts, "type": "overview_edited", "message": "User edited proposed project overview."}
            )
            write_task(task)
        handler.send_json({"task": task})
        return True

    m_task = re.fullmatch(r"/api/tasks/(T-\d{4,})", path)
    if m_task:
        tid = m_task.group(1)
        title = str(body.get("title", "")).strip()
        req_text = str(body.get("request", "")).strip()
        role = str(body.get("role", "")).strip()
        provider = str(body.get("provider", "Automatic")).strip()
        raw_checks = body.get("acceptance_checks", [])
        produces = bool(body.get("produces_deliverable"))
        if not title or not req_text or len(title) > 160 or len(req_text) > 12000:
            handler.send_json({"error": "Enter a valid title and request."}, HTTPStatus.BAD_REQUEST)
            return True
        if role not in WORK_ROLES or provider not in task_provider_options() or not isinstance(raw_checks, list):
            handler.send_json({"error": "Invalid role, provider, or acceptance checks."}, HTTPStatus.BAD_REQUEST)
            return True
        pid = str(body.get("project_id") or "default")
        mid = str(body.get("module_id") or "")
        proj = next((p for p in read_projects() if p.get("id") == pid), None)
        if not proj or (mid and not any(m.get("id") == mid for m in proj.get("modules", []))):
            handler.send_json({"error": "Choose a valid project and sub-project."}, HTTPStatus.BAD_REQUEST)
            return True
        checks = [str(c).strip()[:500] for c in raw_checks if str(c).strip()][:20]
        with STATE_LOCK:
            task = read_task(tid)
            if not task:
                handler.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                return True
            if task.get("status") not in ("Draft", "Blocked", "Rework needed", "Waiting for capacity"):
                handler.send_json(
                    {"error": "Only Draft, Blocked, Rework needed, or Waiting for capacity tasks can be edited."},
                    HTTPStatus.CONFLICT,
                )
                return True
            ts = now_iso()
            task.update(
                {
                    "title": title,
                    "request": req_text,
                    "assigned_role": role,
                    "provider": provider,
                    "project_id": pid,
                    "module_id": mid,
                    "acceptance_checks": checks,
                    "produces_deliverable": produces,
                    "updated_at": ts,
                }
            )
            task.setdefault("activity", []).append({"at": ts, "type": "edited", "message": "Task details updated."})
            write_task(task)
        handler.send_json({"task": task})
        return True


    return False


def handle_tasks_delete(handler, path) -> bool:
    m_task = re.fullmatch(r"/api/tasks/(T-\d{4,})", path)
    if m_task:
        tid = m_task.group(1)
        with STATE_LOCK:
            tpath = DATA / "tasks" / f"{tid}.json"
            rdir = DATA / "replies" / tid
            lpath = DATA / "logs" / "activity.jsonl"
            artifacts = [tpath, tpath.with_suffix(".json.bak"), tpath.with_suffix(".json.tmp")]
            existed = any(p.exists() for p in artifacts) or rdir.exists()
            if not existed:
                handler.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                return True
            if lpath.is_file():
                kept = []
                for line in lpath.read_text(encoding="utf-8", errors="replace").splitlines():
                    if not line.strip():
                        continue
                    try:
                        if json.loads(line).get("task_id") == tid:
                            continue
                    except Exception:
                        pass
                    kept.append(line + "\n")
                atomic_text(lpath, "".join(kept))
            for p in artifacts:
                p.unlink(missing_ok=True)
            if rdir.exists():
                shutil.rmtree(rdir, ignore_errors=True)
        handler.send_json({"deleted": True, "task_id": tid})
        return True


    return False


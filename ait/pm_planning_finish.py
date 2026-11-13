"""PM planning helpers."""

from __future__ import annotations

from ait.common import *


# --- PM planning/escalation completion ---

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
            if item.get("kind") == "env_setup":
                create_env_task(
                    task_id=wid,
                    title=item["title"],
                    request=item["request"],
                    project_id=task.get("project_id") or "default",
                    module_id=item["module_id"],
                    steps=item.get("steps"),
                    status=plan_status,
                    source_plan_task_id=task["id"],
                    planning_root_id=task.get("planning_root_id"),
                    phase=item.get("phase"),
                )
                continue
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

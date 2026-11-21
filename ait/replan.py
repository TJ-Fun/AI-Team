"""Staged PM/PO replan chains: trackable multi-stage project replanning."""

from __future__ import annotations

from ait.common import *
from ait.format_retry import maybe_format_retry


CHAIN_STAGES = ["review", "vision", "docs", "roadmap", "board_delta"]
STAGE_TITLES = {
    "review": "Review",
    "vision": "Vision",
    "docs": "Docs",
    "roadmap": "Roadmap",
    "board_delta": "Board delta",
}
STAGE_KINDS = {
    "review": ["review"],
    "vision": ["vision"],
    "docs": ["doc"],
    "roadmap": ["roadmap"],
    "board_delta": ["board_delta"],
}
KIND_TASK_TYPES = {
    "review": "chain_review",
    "vision": "vision_update",
    "doc": "chain_doc",
    "roadmap": "roadmap_update",
    "board_delta": "board_delta",
}
CHAIN_PLAN_TASK_TYPE = "chain_plan"
REPLAN_WORKER_INTERVAL = 60
TERMINAL_STATES = {"Done", "Canceled"}
CHAIN_TERMINAL = {"completed", "canceled"}

STAGE_REQUESTS = {
    "review": (
        "Replan stage REVIEW: analyze the project's task board (including closed tasks), all context "
        "documents, and recent outcomes. Identify what is still valid, what is stale, and what a major "
        "replan must change. Check the decision log and do not re-ask questions that already have answers. "
        "Plan subtasks of kind 'review' that each produce a bounded analysis report."
    ),
    "vision": (
        "Replan stage VISION: based on the review findings, propose updated project_vision content that "
        "reflects the new direction. Plan subtasks of kind 'vision' (usually one) that each propose the "
        "full updated vision document for user approval."
    ),
    "docs": (
        "Replan stage DOCS: update the routine context documents (project_brief, technical_overview, "
        "decisions, outcomes) to match the approved new direction. Plan subtasks of kind 'doc'; each "
        "subtask updates specific documents and is applied automatically."
    ),
    "roadmap": (
        "Replan stage ROADMAP: propose an updated master_plan and phase plans reflecting the approved "
        "vision and docs. Plan subtasks of kind 'roadmap' (usually one) producing the roadmap update "
        "for user approval."
    ),
    "board_delta": (
        "Replan stage BOARD DELTA: propose task-board mutations needed by the new plan — cancel, "
        "supersede, or create tasks. Plan subtasks of kind 'board_delta' (usually one) producing the "
        "full mutation list for one user approval. Never delete tasks; Done tasks can only be superseded."
    ),
}


def chain_tasks(chain_id: str) -> list[dict[str, Any]]:
    return [t for t in read_tasks() if str(t.get("chain_id") or "") == chain_id]


def active_chain_for_project(project_id: str) -> dict[str, Any] | None:
    for t in read_tasks():
        if not t.get("chain_id") or t.get("chain_state") in CHAIN_TERMINAL:
            continue
        if str(t.get("project_id") or "default") == project_id:
            return t
    return None


def start_replan_chain(project_id: str) -> dict[str, Any]:
    pid = str(project_id or "default")
    if not any(p.get("id") == pid for p in read_projects()):
        raise ValueError("Choose a valid project.")
    existing = active_chain_for_project(pid)
    if existing:
        raise ValueError(f"A replan chain is already active ({existing.get('chain_id')}).")
    chain_id = next_task_id()
    task = create_chain_plan_task(chain_id, "review", pid)
    return task


def create_chain_plan_task(chain_id: str, stage: str, project_id: str) -> dict[str, Any]:
    ts = now_iso()
    title = f"Replan {STAGE_TITLES.get(stage, stage)} — plan stage"
    request = (
        f"{STAGE_REQUESTS.get(stage, '')}\n\n"
        f"Chain: {chain_id} · Stage: {stage} · Allowed subtask kinds: {', '.join(STAGE_KINDS.get(stage, []))}. "
        "Return a chain_plan with 1 to 8 small subtasks; each becomes a separate trackable PM/PO task "
        "executed one at a time after you approve the plan."
    )
    task = {
        "id": next_task_id(),
        "task_type": CHAIN_PLAN_TASK_TYPE,
        "title": title,
        "request": request,
        "assigned_role": "PM/PO",
        "provider": "Automatic",
        "status": "Ready",
        "produces_deliverable": False,
        "acceptance_checks": [],
        "project_id": project_id,
        "module_id": "",
        "chain_id": chain_id,
        "chain_stage": stage,
        "chain_seq": 0,
        "chain_kind": "plan",
        "chain_state": "active",
        "created_at": ts,
        "updated_at": ts,
        "attempts": [],
        "validation": None,
        "activity": [{"at": ts, "type": "created", "message": f"Replan chain {chain_id} stage '{stage}' plan requested."}],
    }
    write_task(task)
    DISPATCH_EVENT.set()
    return task


def parse_chain_plan_response(reply: str, stage: str) -> dict[str, Any]:
    clean = reply.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean, flags=re.IGNORECASE).strip()
    data = json.loads(clean)
    if not isinstance(data, dict) or data.get("type") != "chain_plan":
        raise ValueError("Chain stage reply must have type 'chain_plan'.")
    summary = str(data.get("summary") or "").strip()[:500]
    raw = data.get("subtasks")
    if not isinstance(raw, list) or not (1 <= len(raw) <= 8):
        raise ValueError("chain_plan needs a 'subtasks' array with 1 to 8 items.")
    allowed = set(STAGE_KINDS.get(stage, []))
    subtasks: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Each chain subtask must be an object.")
        title = str(item.get("title") or "").strip()[:160]
        req = str(item.get("request") or "").strip()[:12000]
        kind = str(item.get("kind") or "").strip()
        if not title or not req:
            raise ValueError("Each chain subtask needs a title and request.")
        if kind not in allowed:
            raise ValueError(f"Subtask kind '{kind}' is not allowed in stage '{stage}' (allowed: {', '.join(sorted(allowed))}).")
        subtasks.append({"title": title, "request": req, "kind": kind})
    return {"summary": summary, "subtasks": subtasks}


def finish_chain_plan_task(task: dict[str, Any], reply: str, provider: str) -> tuple[str, str]:
    stage = str(task.get("chain_stage") or "")
    try:
        res = parse_chain_plan_response(reply, stage)
        ts = now_iso()
        task["chain_plan"] = {"stage": stage, "summary": res["summary"], "subtasks": res["subtasks"]}
        task["status"] = "Needs approval"
        task["updated_at"] = ts
        task["validation"] = {
            "status": "passed",
            "checks": [{"check": "chain plan structure", "kind": "schema", "passed": True, "message": "Response parsed and saved."}],
            "validated_at": ts,
        }
        task.pop("format_retry_feedback", None)
        task.setdefault("activity", []).append(
            {"at": ts, "type": "chain_plan_ready", "from": "In progress", "to": "Needs approval",
             "message": f"PM/PO planned {len(res['subtasks'])} chain subtask(s) for stage '{stage}'; awaiting approval."}
        )
        return "Needs approval", f"PM/PO planned {len(res['subtasks'])} chain subtask(s) for stage '{stage}'. Approve to start them one by one."
    except Exception as exc:
        msg = str(exc)
        logger.warning("Chain plan rejected task_id=%s provider=%s: %s", task.get("id"), provider, msg)
        append_event({"at": now_iso(), "type": "chain_plan_rejected", "task_id": task.get("id"), "provider": provider, "error": msg[:1000]})
        task["validation"] = {
            "status": "failed",
            "checks": [{"check": "chain plan structure", "kind": "schema", "passed": False, "message": msg}],
            "validated_at": now_iso(),
        }
        retried, retry_msg = maybe_format_retry(task, provider, msg)
        if retried:
            return "Ready", retry_msg
        task["planning_error"] = msg
        return "Rework needed", f"{provider} replied, but the chain plan could not be logged: {msg}"


def _create_chain_subtasks(plan_task: dict[str, Any]) -> list[str]:
    plan = plan_task.get("chain_plan") or {}
    stage = str(plan.get("stage") or plan_task.get("chain_stage") or "")
    chain_id = str(plan_task.get("chain_id") or "")
    pid = str(plan_task.get("project_id") or "default")
    ts = now_iso()
    ids: list[str] = []
    for seq, item in enumerate(plan.get("subtasks") or [], 1):
        tid = next_task_id()
        ids.append(tid)
        write_task(
            {
                "id": tid,
                "task_type": KIND_TASK_TYPES.get(item["kind"], "chain_review"),
                "title": f"[{STAGE_TITLES.get(stage, stage)} {seq}] {item['title']}",
                "request": item["request"],
                "assigned_role": "PM/PO",
                "provider": "Automatic",
                "status": "Ready" if seq == 1 else "Draft",
                "produces_deliverable": False,
                "acceptance_checks": [],
                "project_id": pid,
                "module_id": "",
                "chain_id": chain_id,
                "chain_stage": stage,
                "chain_seq": seq,
                "chain_kind": "subtask",
                "chain_subtask_kind": item["kind"],
                "chain_state": "active",
                "source_plan_task_id": plan_task["id"],
                "created_at": ts,
                "updated_at": ts,
                "attempts": [],
                "validation": None,
                "activity": [
                    {
                        "at": ts,
                        "type": "created",
                        "message": f"Chain {chain_id} stage '{stage}' subtask {seq}; runs after earlier subtasks finish.",
                    }
                ],
            }
        )
    return ids


def _cancel_chain_members(members: list[dict[str, Any]], reason: str) -> int:
    ts = now_iso()
    count = 0
    for m in members:
        if m.get("chain_state") in CHAIN_TERMINAL:
            continue
        m["chain_state"] = "canceled"
        if m.get("status") in ("Draft", "Ready", "Needs approval"):
            m["status"] = "Canceled"
            m["updated_at"] = ts
            m.setdefault("activity", []).append({"at": ts, "type": "chain_canceled", "message": reason})
            write_task(m)
            count += 1
        else:
            write_task(m)
    return count


def decide_chain_plan(task_id: str, decision: str) -> dict[str, Any]:
    tid = str(task_id)
    choice = str(decision or "").strip().lower()
    if choice not in ("approve", "reject"):
        raise ValueError("Decision must be approve or reject.")
    with STATE_LOCK:
        task = read_task(tid)
        if not task:
            raise KeyError("Chain plan task not found.")
        if task.get("task_type") != CHAIN_PLAN_TASK_TYPE or task.get("status") != "Needs approval":
            raise ValueError("This chain plan is no longer awaiting a decision.")
        ts = now_iso()
        chain_id = str(task.get("chain_id") or "")
        if choice == "approve":
            ids = _create_chain_subtasks(task)
            task["status"] = "Done"
            task["chain_subtask_ids"] = ids
            task["approved_at"] = ts
            task["validation"] = {
                "status": "passed",
                "method": "user_approval",
                "reviewed_by": "User",
                "notes": "Approved; chain subtasks queued sequentially.",
                "validated_at": ts,
            }
            task.setdefault("activity", []).append(
                {"at": ts, "type": "chain_plan_approved", "from": "Needs approval", "to": "Done",
                 "message": f"User approved the stage plan; queued {len(ids)} subtask(s): {', '.join(ids)}."}
            )
        else:
            members = chain_tasks(chain_id)
            _cancel_chain_members(members, f"Chain {chain_id} canceled: stage plan rejected by user.")
            task["status"] = "Canceled"
            task["updated_at"] = ts
            task.setdefault("activity", []).append(
                {"at": ts, "type": "chain_plan_rejected", "from": "Needs approval", "to": "Canceled",
                 "message": "User rejected the stage plan; the replan chain was canceled."}
            )
        task["updated_at"] = ts
        write_task(task)
        append_event({"at": ts, "task_id": tid, "type": "chain_plan_decision", "decision": choice, "chain_id": chain_id})
        DISPATCH_EVENT.set()
        return task


def _release_next_chain_subtask(stage_tasks: list[dict[str, Any]]) -> bool:
    subtasks = sorted(
        (m for m in stage_tasks if m.get("chain_kind") == "subtask"),
        key=lambda m: int(m.get("chain_seq") or 0),
    )
    released = False
    for m in subtasks:
        status = m.get("status")
        if status in TERMINAL_STATES:
            continue
        if status == "Draft":
            m["status"] = "Ready"
            m["updated_at"] = now_iso()
            m.setdefault("activity", []).append(
                {"at": now_iso(), "type": "chain_subtask_released", "message": "Earlier chain subtasks finished; this subtask is now queued."}
            )
            write_task(m)
            released = True
        break
    return released


def advance_chain(chain_id: str, members: list[dict[str, Any]]) -> None:
    if all(m.get("chain_state") in CHAIN_TERMINAL for m in members):
        return
    if any(m.get("status") == "Canceled" for m in members):
        _cancel_chain_members(members, f"Chain {chain_id} canceled: a chain task was canceled.")
        return
    for stage in CHAIN_STAGES:
        stage_tasks = [m for m in members if str(m.get("chain_stage") or "") == stage]
        if not stage_tasks:
            continue
        if all(m.get("status") in TERMINAL_STATES for m in stage_tasks):
            continue
        _release_next_chain_subtask(stage_tasks)
        return
    ts = now_iso()
    pid = str(members[0].get("project_id") or "default") if members else "default"
    for m in members:
        if m.get("chain_state") not in CHAIN_TERMINAL:
            m["chain_state"] = "completed"
            m["updated_at"] = ts
            write_task(m)
    try:
        append_chat_notice(
            f"Replan chain {chain_id} completed all stages ({', '.join(STAGE_TITLES[s] for s in CHAIN_STAGES)}). Review the updated documents and roadmap.",
            ts,
            chain_id,
            pid,
        )
    except Exception:
        pass
    append_event({"at": ts, "type": "chain_completed", "chain_id": chain_id, "project_id": pid})


def run_replan_pass() -> None:
    chains: dict[str, list[dict[str, Any]]] = {}
    for t in read_tasks():
        cid = str(t.get("chain_id") or "")
        if cid:
            chains.setdefault(cid, []).append(t)
    for cid, members in chains.items():
        try:
            advance_chain(cid, members)
        except Exception:
            logger.exception("Replan chain %s advance failed", cid)


def replan_worker_loop() -> None:
    while True:
        try:
            run_replan_pass()
        except Exception as exc:
            logger.exception("Replan worker pass failed: %s", exc)
        time.sleep(REPLAN_WORKER_INTERVAL)

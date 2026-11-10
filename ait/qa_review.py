"""Project milestones, deterministic validation, QA review, and agent instructions."""

from __future__ import annotations

from ait.common import *


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
    if tt == "doc_update":
        return DOC_UPDATE_CONTRACT
    if tt == "roadmap_update":
        return ROADMAP_UPDATE_CONTRACT
    return ""


def needs_work_reply_envelope(task: dict[str, Any]) -> bool:
    if task.get("task_type") in ("pm_planning", "pm_escalation", "doc_update", "roadmap_update") or task.get("qa_attempt_active"):
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

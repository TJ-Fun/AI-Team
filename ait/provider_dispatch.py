"""Auto provider selection, task dispatch, and background worker loops."""

from __future__ import annotations

from ait.common import *


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


def _stash_route_settings(task: dict[str, Any], entry: dict[str, Any]) -> None:
    task["_route_model"] = str(entry.get("model") or "auto")
    task["_route_timeout"] = int(entry.get("timeout_seconds") or DEFAULT_ROUTE_TIMEOUT_SECONDS)
    task["_route_retries"] = int(
        entry.get("retries") if entry.get("retries") is not None else DEFAULT_ROUTE_RETRIES
    )
    task["_route_retry_delay"] = int(
        entry.get("retry_delay_seconds")
        if entry.get("retry_delay_seconds") is not None
        else DEFAULT_ROUTE_RETRY_DELAY_SECONDS
    )
    task["_route_stream"] = bool(entry.get("stream", True))


def claim_task_for_provider(provider: str) -> dict[str, Any] | None:
    if provider not in configured_providers():
        return None
    with STATE_LOCK:
        all_tasks = [
            item
            for item in read_tasks()
            if str(item.get("project_id") or "default") not in PROJECT_MAINTENANCE
            and item.get("task_type") != "env_setup"
        ]
        summaries = reversed(all_tasks)
        qa_candidates = [
            item for item in summaries
            if item.get("produces_deliverable")
            and (
                item.get("status") == "Needs validation"
                or (item.get("status") == "Ready" and item.get("workflow_phase") == "qa")
            )
        ]
        for summary in qa_candidates:
            task = read_task(str(summary.get("id", "")))
            if not task:
                continue
            if task.get("status") == "Needs validation":
                task["qa_routing_cycle_start"] = len(task.get("attempts") or [])
                task["qa_return_role"] = str(task.get("assigned_role") or "Unknown")
                task["assigned_role"] = "QA"
                task["workflow_phase"] = "qa"
            entry = route_entry_for_role(task, "QA", "qa_routing_cycle_start")
            if not entry or entry["provider"] != provider:
                continue
            _stash_route_settings(task, entry)
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
                entry = task_route_entry(candidate)
                if not entry or entry["provider"] != provider:
                    continue
                _stash_route_settings(candidate, entry)
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
        route_model = str(task.get("_route_model") or "auto")
        model = provider_model(provider, route_model)
        route_timeout = int(task.get("_route_timeout") or DEFAULT_ROUTE_TIMEOUT_SECONDS)
        route_stream = bool(task.get("_route_stream", True))
        route_retries = int(
            task.get("_route_retries")
            if task.get("_route_retries") is not None
            else DEFAULT_ROUTE_RETRIES
        )
        route_delay = int(
            task.get("_route_retry_delay")
            if task.get("_route_retry_delay") is not None
            else DEFAULT_ROUTE_RETRY_DELAY_SECONDS
        )
        attempts.append(
            {
                "number": attempt_num,
                "provider": provider,
                "model": model,
                "route_model": route_model,
                "role": str(task.get("assigned_role") or "PM/PO"),
                "state": "in_progress",
                "started_at": ts,
                "timeout_seconds": route_timeout,
                "stream": route_stream,
                "retries": route_retries,
                "retry_delay_seconds": route_delay,
            }
        )
        task["status"] = "In progress"
        task["updated_at"] = ts
        task["attempt_timeout_seconds"] = (
            route_timeout * STREAM_HARD_CAP_FACTOR if route_stream else route_timeout
        )
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
        reply, actual_model, meta = call_task_provider(task, provider)

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
                elif fresh.get("task_type") == "doc_update":
                    next_status, msg = finish_doc_update_task(fresh, reply, provider)
                elif fresh.get("task_type") == "roadmap_update":
                    next_status, msg = finish_roadmap_update_task(fresh, reply, provider)
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
                routed = qa_active or str(fresh.get("provider") or "") in ("Automatic", "Auto", "Antigravity")
                failed_attempt = next(
                    (a for a in fresh.get("attempts") or [] if int(a.get("number", 0)) == attempt_num),
                    {},
                )
                failed_key = (
                    provider,
                    str(failed_attempt.get("route_model") or fresh.get("_route_model") or "auto"),
                )
                nxt_entry: dict[str, Any] | None = None
                retry_same = False
                retryable = failure.status in (408, 429) or failure.status >= 500
                if routed:
                    role = "QA" if qa_active else str(fresh.get("assigned_role") or "PM/PO")
                    cycle_key = "qa_routing_cycle_start" if qa_active else "routing_cycle_start"
                    nxt_entry = (
                        route_entry_for_role(fresh, role, cycle_key)
                        if qa_active
                        else task_route_entry(fresh)
                    )
                    if nxt_entry is None:
                        next_status = "Blocked"
                        fallback = False
                    elif route_combo_key(nxt_entry) == failed_key:
                        next_status = "Waiting for capacity"
                        fallback = False
                        retry_same = True
                    else:
                        next_status = "Ready"
                        fallback = True
                else:
                    fallbackable = retryable or failure.status in (400, 401, 403)
                    fallback = (
                        fallbackable
                        and has_untried_alternate(fresh, provider)
                        and (qa_active or fresh.get("provider") == "Automatic")
                    )
                    next_status = "Ready" if fallback else ("Waiting for capacity" if retryable else "Blocked")
                waiting_capacity = retry_same or (not routed and retryable)
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
                        att["state"] = "provider_unavailable" if fallback else ("waiting_for_capacity" if waiting_capacity else "failed")
                        att["finished_at"] = ts
                        att["reply_file"] = rel
                msg = f"{provider} API error {failure.status}: {failure}."
                if fallback:
                    nxt = (
                        str(nxt_entry["provider"])
                        if nxt_entry
                        else (provider_for_role(fresh, "QA", "qa_routing_cycle_start") if qa_active else task_provider(fresh))
                    )
                    fresh["dispatch_note"] = f"{provider} unavailable; switching to {nxt}."
                    msg = f"{msg} Trying {nxt} automatically."
                elif retry_same and nxt_entry is not None:
                    delay = int(
                        nxt_entry.get("retry_delay_seconds")
                        if nxt_entry.get("retry_delay_seconds") is not None
                        else DEFAULT_ROUTE_RETRY_DELAY_SECONDS
                    )
                    retry_iso = (datetime.now(timezone.utc) + timedelta(seconds=delay)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    if failure.retry_after:
                        hdr_iso = retry_after_time(failure.retry_after, 0)
                        if (parse_iso_epoch(hdr_iso) or 0) > (parse_iso_epoch(retry_iso) or 0):
                            retry_iso = hdr_iso
                    fresh["retry_after"] = retry_iso
                    fresh["waiting_reason"] = msg
                    msg = f"{msg} Retrying {provider} in {delay}s."
                elif not routed and retryable:
                    fresh["retry_after"] = retry_after_time(failure.retry_after, 24 if failure.status == 429 else 2)
                    fresh["waiting_reason"] = msg
                else:
                    fresh["block_reason"] = msg
                fresh["status"] = next_status
                fresh["updated_at"] = ts
                fresh["latest_reply_file"] = rel
                if qa_active:
                    fresh.pop("qa_attempt_active", None)
                    if next_status == "Blocked":
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

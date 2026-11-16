"""Release completion detection, git/GitHub publishing, and plan advancement."""

from __future__ import annotations

from ait.common import *

RELEASE_STATE_DIR = "releases"
RELEASE_RETRY_SECONDS = 10 * 60
RELEASE_CHECK_INTERVAL_SECONDS = 60


def _state_path(project_id: str) -> Path:
    return DATA / RELEASE_STATE_DIR / f"{project_id}.json"


def read_release_state(project_id: str) -> dict[str, Any]:
    p = _state_path(project_id)
    if not p.is_file():
        return {"published": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"published": {}}
    except Exception:
        return {"published": {}}


def write_release_state(project_id: str, state: dict[str, Any]) -> None:
    p = _state_path(project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(p, state)


def _release_tasks(tasks: list[dict[str, Any]], project_id: str, release_id: str) -> list[dict[str, Any]]:
    return [
        t
        for t in tasks
        if str(t.get("project_id") or "default") == project_id
        and str(t.get("release") or "") == release_id
    ]


def completed_unpublished_releases(
    tasks: list[dict[str, Any]], project_id: str, plan: dict[str, Any] | None, published: dict[str, Any]
) -> list[str]:
    """Release ids whose tasks are all Done/Canceled (>=1 Done) and not yet published."""
    ids = {str(t.get("release") or "") for t in tasks if str(t.get("release") or "")}
    ids |= plan_release_ids(plan)
    done_ids: list[str] = []
    for rid in ids:
        if not rid:
            continue
        prior = published.get(rid)
        if isinstance(prior, dict) and prior.get("done"):
            continue
        members = _release_tasks(tasks, project_id, rid)
        if not members:
            continue
        if not any(t.get("status") == "Done" for t in members):
            continue
        if all(t.get("status") in DONE_TASK_STATUSES for t in members):
            done_ids.append(rid)
    return sorted(done_ids, key=release_sort_key)


def _git(repo: Path, *args: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as exc:
        return False, str(exc)[:500]
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "git command failed")[:500]
    return True, (proc.stdout or "").strip()[:500]


def publish_release(project: dict[str, Any], release_id: str, release_title: str = "") -> dict[str, Any]:
    """Tag, push, and create a GitHub release for a completed release.

    Returns {"via": "github_release"|"git_tag"|"none", "error": str|None}.
    """
    pid = str(project.get("id") or "default")
    local = str(project.get("local_path") or "").strip()
    repo = Path(local).expanduser() if local else None
    is_git = bool(repo and repo.is_dir() and (repo / ".git").exists())
    if not is_git:
        return {"via": "none", "error": None}
    ok, err = _git(repo, "tag", release_id)
    if not ok:
        # Tag may already exist; verify it points at HEAD before continuing.
        ok2, _ = _git(repo, "rev-parse", "--verify", f"refs/tags/{release_id}")
        if not ok2:
            return {"via": "none", "error": f"git tag failed: {err}"}
    ok, err = _git(repo, "push", "origin", release_id)
    if not ok:
        return {"via": "none", "error": f"git push tag failed: {err}"}
    title = release_title or f"{project.get('name', pid)} {release_id}"
    notes = f"Automated release {release_id} for {project.get('name', pid)}."
    try:
        proc = subprocess.run(
            ["gh", "release", "create", release_id, "--title", title, "--notes", notes, "--verify-tag"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError:
        return {"via": "git_tag", "error": "gh CLI not found; tag pushed only."}
    except Exception as exc:
        return {"via": "git_tag", "error": f"gh release failed: {str(exc)[:300]}"}
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "gh release create failed")[:400]
        # Release may already exist for this tag.
        if "already exists" in detail.lower() or "release already exists" in detail.lower():
            return {"via": "github_release", "error": None}
        return {"via": "git_tag", "error": f"gh release failed: {detail}"}
    return {"via": "github_release", "error": None}


def _release_title(plan: dict[str, Any] | None, release_id: str) -> str:
    rel = find_release(plan, release_id)
    if rel and rel.get("title"):
        return str(rel["title"])
    return release_id


def run_release_publish_pass() -> int:
    """Publish newly completed releases and advance each project's release plan."""
    handled = 0
    tasks = read_tasks()
    projects = read_projects()
    for proj in projects:
        pid = str(proj.get("id") or "default")
        state = read_release_state(pid)
        published = state.setdefault("published", {})
        plan = load_release_plan(pid)
        for rid in completed_unpublished_releases(tasks, pid, plan, published):
            entry = published.get(rid) or {}
            retry_at = parse_iso_epoch(entry.get("next_attempt_at") or "")
            if entry.get("error") and retry_at and time.time() < retry_at:
                continue
            result = publish_release(proj, rid, _release_title(plan, rid))
            ts = now_iso()
            # A pushed git tag is terminal even when the gh release object fails;
            # only a total publish failure (via "none" with error) stays retryable.
            done = result["via"] in ("github_release", "git_tag") or not result.get("error")
            published[rid] = {
                "at": ts,
                "via": result["via"],
                "error": result.get("error"),
                "done": done,
            }
            if not done:
                published[rid]["next_attempt_at"] = (
                    datetime.now(timezone.utc) + timedelta(seconds=RELEASE_RETRY_SECONDS)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            write_release_state(pid, state)
            append_event(
                {
                    "at": ts,
                    "type": "release_published",
                    "project_id": pid,
                    "release": rid,
                    "via": result["via"],
                    "error": result.get("error"),
                }
            )
            logger.info("Release %s published for project %s via %s", rid, pid, result["via"])
            if done and plan is not None:
                nxt = advance_release_plan(plan, rid)
                try:
                    update_release_plan_doc(pid, plan)
                except Exception as exc:
                    logger.warning("Failed to update master_plan for %s: %s", pid, exc)
                if nxt:
                    append_chat_notice(
                        f"Release {rid} is complete and published. The project advances to release {nxt}; "
                        "PM/PO will be asked to plan its tasks.",
                        ts,
                        "",
                        pid,
                    )
                else:
                    append_chat_notice(
                        f"Release {rid} is complete and published. No further releases remain in the master plan.",
                        ts,
                        "",
                        pid,
                    )
            elif not done:
                append_chat_notice(
                    f"Release {rid} completed, but publishing failed: {result['error']}. Will retry.",
                    ts,
                    "",
                    pid,
                )
            handled += 1
        fresh_tasks = read_tasks()
        fresh_plan = load_release_plan(pid)
        queued = maybe_queue_release_plan(proj, fresh_tasks, fresh_plan)
        if queued:
            handled += 1
    return handled


def maybe_queue_release_plan(
    project: dict[str, Any], tasks: list[dict[str, Any]], plan: dict[str, Any] | None
) -> str | None:
    """Queue a PM/PO planning task for the current release when the project is idle
    and that release has no tasks yet. Returns the new task id or None."""
    if plan is None:
        return None
    pid = str(project.get("id") or "default")
    cur = str(plan.get("current_release") or "")
    if not cur:
        return None
    members = _release_tasks(tasks, pid, cur)
    if members:
        return None
    open_tasks = [
        t
        for t in tasks
        if str(t.get("project_id") or "default") == pid and t.get("status") not in DONE_TASK_STATUSES
    ]
    if open_tasks:
        return None
    phase_id = str(plan.get("current_phase") or "")
    rel = find_release(plan, cur) or {}
    title = str(rel.get("title") or cur)
    ts = now_iso()
    tid = next_task_id()
    request = (
        f"Plan release {cur} (phase {phase_id or 'n/a'}) for project '{project.get('name', pid)}'.\n\n"
        f"Release title: {title}\n\n"
        "The master_plan document already marks this release as current. Propose the tasks needed to complete "
        "this release: start with env_setup tasks for any required environment/user setup (small, step-by-step), "
        "then the dev/QA/docs work tasks. Tag every task with release \"" + cur + "\" and sequential "
        "release_order values. Keep the release small and self-contained. Do not create tasks for later releases."
    )
    write_task(
        {
            "id": tid,
            "task_type": "pm_planning",
            "title": f"PM/PO plan release {cur}: {project.get('name', pid)}",
            "request": request,
            "original_request": request,
            "release_plan_for": cur,
            "auto_release_plan": True,
            "assigned_role": "PM/PO",
            "provider": "Automatic",
            "status": "Ready",
            "project_id": pid,
            "module_id": "",
            "produces_deliverable": False,
            "acceptance_checks": [],
            "attempts": [],
            "validation": None,
            "created_at": ts,
            "updated_at": ts,
            "dispatch_note": "Automatic next-release planning queued by the release watchdog.",
            "activity": [{"at": ts, "type": "created", "message": f"Auto-queued planning for release {cur}."}],
        }
    )
    append_chat_notice(
        f"Release {cur} is next in the master plan. I queued PM/PO planning ({tid}) to break it into tasks.",
        ts,
        tid,
        pid,
    )
    append_event({"at": ts, "type": "release_plan_queued", "task_id": tid, "project_id": pid, "release": cur})
    DISPATCH_EVENT.set()
    return tid


def release_worker_loop() -> None:
    while True:
        try:
            run_release_publish_pass()
        except Exception:
            logger.exception("Release publish pass failed")
        time.sleep(RELEASE_CHECK_INTERVAL_SECONDS)

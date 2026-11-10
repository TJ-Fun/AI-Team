"""GitHub task post-processing: manifest commits, PR merge, and local checkout sync."""

from __future__ import annotations

from ait.common import *


def apply_repository_manifest(task: dict[str, Any], reply: str, provider: str, attempt_number: int) -> None:
    project = github_project(str(task.get("project_id") or "default"))
    owner, repo = repository_parts(project["repository"])
    clean = reply.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean, flags=re.IGNORECASE)
    try:
        manifest = json.loads(clean)
    except Exception as exc:
        raise ValueError("Agent must return a JSON file-change manifest for a GitHub deliverable.") from exc
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list) or not (1 <= len(files) <= 20):
        raise ValueError("File-change manifest must contain 1 to 20 files.")

    mod = next((m for m in project.get("modules", []) if m.get("id") == task.get("module_id")), None)
    scope = safe_repo_path(str((mod or {}).get("path", "")))
    allowed_exts = (
        ".md", ".txt", ".html", ".css", ".js", ".ts", ".tsx", ".jsx",
        ".py", ".json", ".toml", ".yml", ".yaml", ".xml", ".sh",
    )
    protected = {
        "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
        "poetry.lock", "uv.lock", "cargo.lock", "go.sum",
    }
    clean_files: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("content"), str):
            raise ValueError("Each manifest file needs a path and text content.")
        fpath = safe_repo_path(str(item.get("path", "")))
        parts = fpath.split("/")
        fname = parts[-1].lower() if parts else ""
        if (
            not fpath
            or fname in protected
            or fpath in seen
            or (scope and not fpath.startswith(scope + "/"))
            or any(seg.startswith(".") or seg in ("node_modules", "dist", "build", "vendor") for seg in parts)
            or not fpath.lower().endswith(allowed_exts)
        ):
            raise ValueError(f"Refusing unsafe or out-of-scope repository path: {fpath[:120]}")
        content = item["content"]
        if len(content.encode("utf-8")) > 50000 or "\x00" in content:
            raise ValueError(f"File content is too large or binary: {fpath}")
        clean_files.append((fpath, content))
        seen.add(fpath)

    meta = github_api(f"/repos/{owner}/{repo}")
    base = str(meta.get("default_branch") or "main")
    base_commit = ""
    snapshot_sha = str(task.get("_snapshot_sha") or "")
    if snapshot_sha:
        try:
            github_api(f"/repos/{owner}/{repo}/git/commits/{snapshot_sha}")
            base_commit = snapshot_sha
        except Exception:
            base_commit = ""
    if not base_commit:
        base_ref = github_api(f"/repos/{owner}/{repo}/git/ref/heads/{urllib.parse.quote(base, safe='')}")
        base_commit = base_ref["object"]["sha"]
    commit_obj = github_api(f"/repos/{owner}/{repo}/git/commits/{base_commit}")
    base_tree = commit_obj["tree"]["sha"]
    branch = f"ai-team/{task['id'].lower()}-a{attempt_number:02d}"

    github_api(f"/repos/{owner}/{repo}/git/refs", "POST", {"ref": f"refs/heads/{branch}", "sha": base_commit})
    tree_items = []
    for fpath, fcontent in clean_files:
        blob = github_api(f"/repos/{owner}/{repo}/git/blobs", "POST", {"content": fcontent, "encoding": "utf-8"})
        tree_items.append({"path": fpath, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = github_api(f"/repos/{owner}/{repo}/git/trees", "POST", {"base_tree": base_tree, "tree": tree_items})
    commit = github_api(
        f"/repos/{owner}/{repo}/git/commits",
        "POST",
        {"message": f"{task['id']}: {task.get('title', '')}"[:200], "tree": tree["sha"], "parents": [base_commit]},
    )
    enc_branch = "/".join(urllib.parse.quote(seg, safe="") for seg in branch.split("/"))
    github_api(f"/repos/{owner}/{repo}/git/refs/heads/{enc_branch}", "PATCH", {"sha": commit["sha"], "force": False})
    previous_pull = task.get("pull_request")
    if isinstance(previous_pull, dict) and previous_pull.get("number") and previous_pull.get("state") == "open":
        github_api(
            f"/repos/{owner}/{repo}/pulls/{int(previous_pull['number'])}",
            "PATCH",
            {"state": "closed"},
        )
        task.setdefault("activity", []).append(
            {
                "at": now_iso(),
                "type": "pull_request_superseded",
                "message": f"Closed pull request #{previous_pull['number']} after creating a new rework attempt branch.",
            }
        )
    feedback = str(manifest.get("feedback") or "").strip()[:2000]
    pull = github_api(
        f"/repos/{owner}/{repo}/pulls",
        "POST",
        {
            "title": f"{task['id']}: {task.get('title', '')}"[:240],
            "head": branch,
            "base": base,
            "body": (
                f"Automated {provider} changes for {task['id']}.\n\n"
                f"{str(task.get('request', ''))[:4000]}\n\n"
                "Agent report and validation are recorded on the local task board."
                + (f"\n\nAgent feedback for PM/PO:\n{feedback}" if feedback else "")
            ),
        },
    )
    task["pull_request"] = {
        "number": int(pull["number"]),
        "url": str(pull.get("html_url", "")),
        "branch": branch,
        "state": str(pull.get("state", "open")),
        "created_at": now_iso(),
    }
    task["repository_change_summary"] = str(manifest.get("summary", ""))[:2000]
    if feedback:
        task["repository_agent_feedback"] = feedback
        task.setdefault("activity", []).append(
            {
                "at": now_iso(),
                "type": "agent_feedback",
                "message": f"{provider} raised feedback for PM/PO: {feedback[:300]}",
            }
        )
    write_task(task)


def sync_local_checkout_after_merge(task_id: str) -> bool:
    task = read_task(task_id)
    if not task:
        return False
    project = next((p for p in read_projects() if p.get("id") == (task.get("project_id") or "default")), None)
    if not project or not project.get("local_path"):
        return True
    repo_url = normalize_github_repo_url(str(project.get("repository") or ""))
    if not repo_url:
        return True
    owner, repo = repository_parts(repo_url)
    meta = github_api(f"/repos/{owner}/{repo}")
    branch = str(meta.get("default_branch") or "main")
    target = Path(str(project["local_path"])).expanduser().resolve()
    token = get_provider_key("github")
    git_env = git_auth_environment(token)
    staging_dir: Path | None = None

    def run_git(args: list[str], cwd: Path | None = None, timeout: int = 120) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=git_env,
        )
        if result.returncode:
            detail = redact_git_output(result.stderr or result.stdout or "", token).strip()
            raise ValueError(detail or f"git {' '.join(args)} exited with status {result.returncode}")
        return redact_git_output(result.stdout or "", token).strip()

    try:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            staging_dir = Path(tempfile.mkdtemp(prefix=f".{target.name}.sync-", dir=target.parent))
            staged_checkout = staging_dir / target.name
            run_git(
                ["clone", "--single-branch", "--branch", branch, repo_url, str(staged_checkout)],
                timeout=300,
            )
            if canonical_github_repository(run_git(["remote", "get-url", "origin"], cwd=staged_checkout)) != (
                owner.casefold(),
                repo.casefold(),
            ):
                raise ValueError("The new local checkout origin does not match the configured GitHub repository.")
            if target.exists():
                raise ValueError(f"Local checkout path appeared during sync; left it untouched: {target}")
            staged_checkout.rename(target)
            action = "cloned"
        else:
            if not target.is_dir():
                raise ValueError(f"Configured local checkout path is not a directory: {target}")
            if run_git(["rev-parse", "--is-inside-work-tree"], cwd=target) != "true":
                raise ValueError(f"Configured local path is not a Git checkout: {target}")
            origin = run_git(["remote", "get-url", "origin"], cwd=target)
            if canonical_github_repository(origin) != (owner.casefold(), repo.casefold()):
                raise ValueError("Local checkout origin does not match the configured GitHub repository.")
            current_branch = run_git(["symbolic-ref", "--short", "HEAD"], cwd=target)
            if current_branch != branch:
                raise ValueError(
                    f"Local checkout is on '{current_branch}', not the default branch '{branch}'; "
                    "switch it manually before automatic sync."
                )
            if run_git(["status", "--porcelain"], cwd=target):
                raise ValueError("Local checkout has uncommitted changes; automatic sync left it untouched.")
            run_git(
                ["fetch", "origin", f"{branch}:refs/remotes/origin/{branch}"],
                cwd=target,
                timeout=300,
            )
            ancestry = subprocess.run(
                ["git", "merge-base", "--is-ancestor", "HEAD", f"refs/remotes/origin/{branch}"],
                cwd=target,
                capture_output=True,
                text=True,
                timeout=30,
                env=git_env,
            )
            if ancestry.returncode != 0:
                raise ValueError(
                    "Local checkout has commits that are not on the GitHub default branch; "
                    "automatic sync refused to overwrite or reconcile them."
                )
            run_git(["merge", "--ff-only", f"refs/remotes/origin/{branch}"], cwd=target)
            action = "fast-forwarded"

        updated = read_task(task_id)
        if updated:
            ts = now_iso()
            updated.pop("local_sync_error", None)
            updated["local_sync"] = {"branch": branch, "at": ts, "action": action}
            updated["updated_at"] = ts
            updated.setdefault("activity", []).append(
                {
                    "at": ts,
                    "type": "local_checkout_synced",
                    "message": f"Local checkout {action} to the validated {branch} branch.",
                }
            )
            write_task(updated)
            append_event({"at": ts, "task_id": task_id, "type": "local_checkout_synced", "branch": branch})
        logger.info("Local repository checkout synced task_id=%s project_id=%s action=%s", task_id, project["id"], action)
        return True
    except Exception as exc:
        msg = str(exc)[:1000]
        updated = read_task(task_id)
        if updated:
            ts = now_iso()
            updated["local_sync_error"] = msg
            updated["updated_at"] = ts
            updated.setdefault("activity", []).append(
                {"at": ts, "type": "local_sync_error", "message": f"Local checkout sync failed: {msg[:500]}"}
            )
            write_task(updated)
            append_event({"at": ts, "task_id": task_id, "type": "local_sync_error", "error": msg[:500]})
        logger.error("Local repository checkout sync failed task_id=%s project_id=%s: %s", task_id, project.get("id"), msg)
        return False
    finally:
        if staging_dir and staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)


def github_postprocess_task(task_id: str) -> None:
    task = read_task(task_id)
    if not task:
        return

    refreshed = read_task(task_id)
    if not refreshed:
        return
    pull = refreshed.get("pull_request")
    if (
        not isinstance(pull, dict)
        or not pull.get("number")
        or refreshed.get("status") != "Done"
        or (refreshed.get("validation") or {}).get("status") != "passed"
    ):
        return
    project = next((p for p in read_projects() if p.get("id") == (refreshed.get("project_id") or "default")), None)
    if not project or not project.get("repository"):
        return
    try:
        owner, repo = repository_parts(project["repository"])
        latest_pull = github_api(f"/repos/{owner}/{repo}/pulls/{pull['number']}")
        latest_head = str(((latest_pull.get("head") or {}).get("sha") or "")) if isinstance(latest_pull, dict) else ""
        if refreshed.get("qa_reviewed_head_sha") and latest_head != refreshed["qa_reviewed_head_sha"]:
            raise ValueError("The pull request changed after QA reviewed it; rerun QA before merging.")
        checks = github_check_runs_summary(owner, repo, latest_head) if latest_head else {"state": "unknown", "runs": [], "failed": []}
        if checks.get("state") == "pending":
            if refreshed.get("ci_status") != "pending":
                refreshed["ci_status"] = "pending"
                refreshed.pop("ci_error", None)
                refreshed["updated_at"] = now_iso()
                write_task(refreshed)
                append_event({"at": now_iso(), "task_id": task_id, "type": "ci_pending"})
            return
        if checks.get("state") == "failure":
            failed_names = ", ".join(checks.get("failed") or [])[:300]
            refreshed["ci_status"] = "failed"
            refreshed["ci_error"] = f"CI checks failed on the pull request: {failed_names}"
            refreshed["updated_at"] = now_iso()
            refreshed.setdefault("activity", []).append(
                {"at": now_iso(), "type": "ci_failed", "message": refreshed["ci_error"]}
            )
            write_task(refreshed)
            append_event({"at": now_iso(), "task_id": task_id, "type": "ci_failed"})
            return
        refreshed["ci_status"] = str(checks.get("state") or "unknown")
        refreshed.pop("ci_error", None)
        if get_autonomy().get("merge_requires_approval") and not refreshed.get("merge_approved"):
            ts = now_iso()
            refreshed["status"] = "Needs approval"
            refreshed["merge_approval_required"] = True
            refreshed["updated_at"] = ts
            refreshed.setdefault("activity", []).append(
                {
                    "at": ts,
                    "type": "merge_approval_required",
                    "from": "Done",
                    "to": "Needs approval",
                    "message": "QA passed; approve the pull request merge in task details before it is merged.",
                }
            )
            write_task(refreshed)
            append_event({"at": ts, "task_id": task_id, "type": "merge_approval_required"})
            return
        merged = github_api(
            f"/repos/{owner}/{repo}/pulls/{pull['number']}/merge",
            "PUT",
            {"merge_method": "squash"},
        )
        if not merged.get("merged"):
            raise ValueError("GitHub did not confirm the merge.")
        pull["state"] = "merged"
        pull["merged_at"] = now_iso()
        refreshed["pull_request"] = pull
        refreshed["ci_status"] = "merged"
        refreshed.setdefault("activity", []).append(
            {
                "at": now_iso(),
                "type": "pull_request_merged",
                "message": f"GitHub pull request #{pull['number']} was merged after validation passed.",
            }
        )
        write_task(refreshed)
        append_event({"at": now_iso(), "task_id": task_id, "type": "pull_request_merged", "pull_request": pull["number"]})
        sync_local_checkout_after_merge(task_id)
    except Exception as exc:
        msg = str(exc)[:500]
        if "changed after QA reviewed it" in msg:
            latest = read_task(task_id)
            if latest:
                ts = now_iso()
                latest["status"] = "Needs validation"
                latest["validation"] = {
                    "status": "pending_qa",
                    "method": "agent_qa",
                    "checks": [],
                    "validated_at": ts,
                }
                latest.pop("qa_reviewed_head_sha", None)
                latest["updated_at"] = ts
                latest.setdefault("activity", []).append(
                    {
                        "at": ts,
                        "type": "qa_required",
                        "from": "Done",
                        "to": "Needs validation",
                        "message": "Pull request changed after QA; the updated commit is queued for another QA review.",
                    }
                )
                write_task(latest)
                DISPATCH_EVENT.set()
            return
        refreshed["github_error"] = msg
        refreshed.setdefault("activity", []).append(
            {"at": now_iso(), "type": "github_error", "message": f"Automatic merge failed: {msg[:350]}"}
        )
        write_task(refreshed)

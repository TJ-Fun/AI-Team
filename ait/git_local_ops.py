"""Local git operations, clone jobs, and local file viewer helpers."""

from __future__ import annotations

from ait.common import *


# --- Git Operations ---
def git_project_path(project_id: str) -> Path:
    project = next((p for p in read_projects() if p.get("id") == project_id), None)
    if not project:
        raise ValueError("Project not found.")
    local_path = str(project.get("local_path", "")).strip()
    if not local_path:
        raise ValueError("Local path is not configured for this project.")
    try:
        path = Path(local_path).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"Local path does not exist or is not a directory: {local_path}")
    except PermissionError as exc:
        raise ValueError(
            f"Permission denied accessing project path '{local_path}'. Check filesystem permissions "
            "for the service user and its systemd ReadWritePaths allowlist."
        ) from exc
    return path


def git_auth_environment(token: str = "") -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if token:
        auth = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
        try:
            config_count = max(0, int(env.get("GIT_CONFIG_COUNT", "0")))
        except ValueError:
            config_count = 0
        env[f"GIT_CONFIG_KEY_{config_count}"] = "http.https://github.com/.extraheader"
        env[f"GIT_CONFIG_VALUE_{config_count}"] = f"AUTHORIZATION: Basic {auth}"
        env["GIT_CONFIG_COUNT"] = str(config_count + 1)
    return env


def redact_git_output(output: str, token: str) -> str:
    return output.replace(token, "[redacted]") if token else output


def update_clone_job(job_id: str, **updates: Any) -> None:
    with CLONE_JOBS_LOCK:
        job = CLONE_JOBS.get(job_id)
        if job:
            job.update(updates)
            job["updated_at"] = now_iso()


def append_clone_job_output(job_id: str | None, line: str) -> None:
    if not job_id:
        return
    with CLONE_JOBS_LOCK:
        job = CLONE_JOBS.get(job_id)
        if job:
            output = job.setdefault("output", [])
            output.append(line)
            del output[:-100]
            job["progress"] = line
            job["updated_at"] = now_iso()


def git_status(project_id: str) -> dict[str, Any]:
    path = git_project_path(project_id)
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--branch"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
        branch_line = next((l for l in lines if l.startswith("##")), "## unknown")
        branch = branch_line[3:].split("...")[0] if "..." in branch_line else branch_line[3:]
        modified = [l for l in lines if not l.startswith("##")]
        ahead = "ahead" in branch_line
        behind = "behind" in branch_line
        return {
            "branch": branch,
            "modified": len(modified),
            "ahead": ahead,
            "behind": behind,
            "clean": len(modified) == 0 and not ahead and not behind,
        }
    except subprocess.TimeoutExpired:
        raise ValueError("Git status timed out.")
    except FileNotFoundError:
        raise ValueError("Git is not installed on this system.")
    except Exception as exc:
        raise ValueError(f"Git status failed: {exc}")


def git_pull(project_id: str) -> dict[str, Any]:
    path = git_project_path(project_id)
    token = get_provider_key("github")
    try:
        result = subprocess.run(
            ["git", "pull"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=120,
            env=git_auth_environment(token),
        )
        if result.returncode != 0:
            stderr = redact_git_output(result.stderr or "", token)
            stdout = redact_git_output(result.stdout or "", token)
            detail = stderr.strip() or stdout.strip() or f"git exited with status {result.returncode}"
            logger.error("Git pull failed (project=%s, exit=%s): %s", project_id, result.returncode, detail)
            raise ValueError(f"Git pull failed: {detail}")
        status = git_status(project_id)
        output = redact_git_output(result.stdout or "", token)
        logger.info("Git pull succeeded (project=%s, output_bytes=%s)", project_id, len(output.encode("utf-8")))
        return {"success": True, "output": output, "status": status}
    except subprocess.TimeoutExpired:
        raise ValueError("Git pull timed out.")
    except FileNotFoundError:
        raise ValueError("Git is not installed on this system.")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Git pull failed: {exc}")


def git_push(project_id: str) -> dict[str, Any]:
    path = git_project_path(project_id)
    token = get_provider_key("github")
    try:
        result = subprocess.run(
            ["git", "push"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=120,
            env=git_auth_environment(token),
        )
        if result.returncode != 0:
            stderr = redact_git_output(result.stderr or "", token)
            stdout = redact_git_output(result.stdout or "", token)
            detail = stderr.strip() or stdout.strip() or f"git exited with status {result.returncode}"
            logger.error("Git push failed (project=%s, exit=%s): %s", project_id, result.returncode, detail)
            raise ValueError(f"Git push failed: {detail}")
        status = git_status(project_id)
        output = redact_git_output(result.stdout or "", token)
        logger.info("Git push succeeded (project=%s, output_bytes=%s)", project_id, len(output.encode("utf-8")))
        return {"success": True, "output": output, "status": status}
    except subprocess.TimeoutExpired:
        raise ValueError("Git push timed out.")
    except FileNotFoundError:
        raise ValueError("Git is not installed on this system.")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Git push failed: {exc}")


def git_clone(project_id: str, job_id: str | None = None) -> dict[str, Any]:
    logger.info(f"Git clone requested for project: {project_id}")
    project = next((p for p in read_projects() if p.get("id") == project_id), None)
    if not project or not project.get("repository"):
        logger.error(f"Project repository not configured for {project_id}")
        raise ValueError("Project repository is not configured.")
    local_path = str(project.get("local_path", "")).strip()
    if not local_path:
        logger.error(f"Local path not configured for project {project_id}")
        raise ValueError("Local path is not configured for this project.")
    try:
        target_path = Path(local_path).expanduser().resolve()
        if target_path.exists():
            logger.error(f"Local path already exists: {local_path}")
            raise ValueError(f"Local path already exists: {local_path}")
    except PermissionError as exc:
        raise ValueError(
            f"Permission denied accessing clone destination '{local_path}'. Check filesystem permissions "
            "for the service user and its systemd ReadWritePaths allowlist."
        ) from exc

    token = get_provider_key("github")
    repo_url = normalize_github_repo_url(str(project["repository"]))
    owner, repo = repository_parts(repo_url)
    repo_url = f"https://github.com/{owner}/{repo}.git"
    logger.info("Cloning GitHub repository (project=%s, repo=%s/%s, destination=%s)",
                project_id, owner, repo, target_path)
    if token:
        logger.debug("Using the configured GitHub connection for clone authentication")
    else:
        logger.warning("GitHub connection is not configured; private repository clone may fail")

    staging_dir: Path | None = None
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        staging_dir = Path(tempfile.mkdtemp(prefix=f".{target_path.name}.clone-", dir=target_path.parent))
        staging_path = staging_dir / target_path.name
        process = subprocess.Popen(
            ["git", "clone", "--progress", repo_url, str(staging_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=git_auth_environment(token),
        )
        logger.info("Git clone process started (project=%s, pid=%s)", project_id, process.pid)
        output_lines: list[str] = []

        def capture_output() -> None:
            if process.stdout is None:
                return
            for raw_line in process.stdout:
                line = redact_git_output(raw_line.rstrip(), token)[-1500:]
                output_lines.append(line)
                del output_lines[:-100]
                logger.info("Git clone output (project=%s): %s", project_id, line[:1500])
                append_clone_job_output(job_id, line)

        output_reader = threading.Thread(target=capture_output, name=f"git-clone-output-{project_id}", daemon=True)
        output_reader.start()
        try:
            return_code = process.wait(timeout=300)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            output_reader.join(timeout=5)
            detail = "\n".join(output_lines[-20:])
            logger.error("Git clone timed out (project=%s, repo=%s/%s, output=%r)",
                         project_id, owner, repo, detail[-4000:])
            raise ValueError(f"Git clone timed out after 300 seconds.{': ' + detail if detail else ''}") from exc
        output_reader.join(timeout=5)
        stdout = "\n".join(output_lines)
        if return_code != 0:
            detail = stdout.strip() or f"git exited with status {return_code}"
            logger.error(
                "Git clone failed (project=%s, repo=%s/%s, exit=%s, output=%r)",
                project_id, owner, repo, return_code, detail[-4000:],
            )
            raise ValueError(f"Git clone failed: {detail}")
        if target_path.exists():
            raise ValueError(f"Local path appeared during clone and will not be overwritten: {target_path}")
        staging_path.replace(target_path)
    except PermissionError as exc:
        logger.error("Permission denied during Git clone (project=%s, destination=%s): %s",
                     project_id, target_path, exc)
        raise ValueError(
            f"Permission denied creating clone at '{target_path}'. Ensure the service user can write to "
            "the parent folder and that the path is allowed by the systemd ReadWritePaths setting."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(str(exc)) from exc
    except FileNotFoundError as exc:
        logger.exception("Git executable is unavailable during clone (project=%s)", project_id)
        raise ValueError("Git is not installed on this system.") from exc
    except ValueError:
        raise
    except Exception as exc:
        logger.exception("Git clone could not be started (project=%s)", project_id)
        raise ValueError(f"Git clone failed: {exc}") from exc
    finally:
        if staging_dir:
            shutil.rmtree(staging_dir, ignore_errors=True)

    logger.info("Git clone succeeded (project=%s, repo=%s/%s, output_bytes=%s)",
                project_id, owner, repo, len(stdout.encode("utf-8")))
    return {"success": True, "output": stdout}


def start_git_clone(project_id: str) -> dict[str, str]:
    project = next((p for p in read_projects() if p.get("id") == project_id), None)
    if not project or not project.get("repository"):
        raise ValueError("Project repository is not configured.")
    local_path = str(project.get("local_path", "")).strip()
    if not local_path:
        raise ValueError("Local path is not configured for this project.")
    try:
        target_path = Path(local_path).expanduser().resolve()
        if target_path.exists():
            raise ValueError(f"Local path already exists: {target_path}")
    except PermissionError as exc:
        logger.error("Permission denied checking Git clone destination (project=%s, path=%s): %s",
                     project_id, local_path, exc)
        raise ValueError(
            f"Permission denied accessing clone destination '{local_path}'. The service is sandboxed; "
            "allow this directory in systemd ReadWritePaths and ensure the service user can access it."
        ) from exc
    target_key = os.path.normcase(str(target_path))
    with CLONE_JOBS_LOCK:
        if target_key in CLONE_TARGETS:
            raise ValueError("A clone to this local path is already running.")
        active_jobs = sum(job.get("status") == "running" for job in CLONE_JOBS.values())
        if active_jobs >= 8:
            raise ValueError("Too many Git clone operations are running; try again when one finishes.")
        completed_jobs = sorted(
            (job for job in CLONE_JOBS.values() if job.get("status") != "running"),
            key=lambda job: job.get("updated_at", ""),
        )
        while len(CLONE_JOBS) >= 100 and completed_jobs:
            oldest = completed_jobs.pop(0)
            CLONE_JOBS.pop(str(oldest.get("id")), None)
        if len(CLONE_JOBS) >= 100:
            raise ValueError("Git clone job history is full; restart the application before starting another clone.")
        CLONE_TARGETS.add(target_key)
        job_id = uuid.uuid4().hex
        CLONE_JOBS[job_id] = {
            "id": job_id,
            "project_id": project_id,
            "status": "running",
            "progress": "Starting Git clone…",
            "output": [],
            "started_at": now_iso(),
            "updated_at": now_iso(),
        }

    def run_clone() -> None:
        try:
            result = git_clone(project_id, job_id)
            update_clone_job(job_id, status="succeeded", result=result, progress="Clone completed.")
        except ValueError as exc:
            logger.error("Git clone job failed (project=%s, job=%s): %s", project_id, job_id, exc)
            update_clone_job(job_id, status="failed", error=str(exc), progress="Clone failed.")
        except Exception as exc:
            logger.exception("Git clone job failed (project=%s, job=%s)", project_id, job_id)
            update_clone_job(job_id, status="failed", error=str(exc), progress="Clone failed.")
        finally:
            with CLONE_JOBS_LOCK:
                CLONE_TARGETS.discard(target_key)

    try:
        threading.Thread(target=run_clone, name=f"git-clone-{project_id}", daemon=True).start()
    except Exception:
        with CLONE_JOBS_LOCK:
            CLONE_TARGETS.discard(target_key)
            CLONE_JOBS.pop(job_id, None)
        raise
    return {"job_id": job_id, "status": "running"}


def get_git_clone_job(project_id: str, job_id: str) -> dict[str, Any]:
    with CLONE_JOBS_LOCK:
        job = CLONE_JOBS.get(job_id)
        if not job or job.get("project_id") != project_id:
            raise ValueError("Git clone job not found.")
        return {**job, "output": list(job.get("output") or [])}


def resolve_local_view_path(root: Path, relative_path: str) -> Path:
    cleaned = str(relative_path or "").strip().replace("\\", "/")
    if (
        len(cleaned) > 500
        or cleaned.startswith("/")
        or (cleaned and (Path(cleaned).is_absolute() or re.match(r"^[A-Za-z]:", cleaned)))
        or any(part in ("", ".", "..") or part.startswith(".") for part in cleaned.split("/") if cleaned)
    ):
        raise ValueError("Invalid or hidden local path.")
    cleaned = cleaned.rstrip("/")
    root_path = root.expanduser().resolve()
    target = (root_path / cleaned).resolve()
    try:
        target.relative_to(root_path)
    except ValueError as exc:
        raise ValueError("The requested path is outside the local project folder.") from exc
    return target


def local_file_listing(root: Path, relative_path: str = "") -> dict[str, Any]:
    root_path = root.expanduser().resolve()
    if not root_path.is_dir():
        raise ValueError(f"Local folder is not available: {root_path}")
    folder = resolve_local_view_path(root_path, relative_path)
    if not folder.is_dir():
        raise ValueError("The requested local path is not a folder.")
    entries = []
    for item in sorted(folder.iterdir(), key=lambda entry: (not entry.is_dir(), entry.name.casefold())):
        if item.name.startswith("."):
            continue
        try:
            resolved = item.resolve()
            resolved.relative_to(root_path)
        except (OSError, ValueError):
            continue
        if not (resolved.is_dir() or resolved.is_file()):
            continue
        rel = resolved.relative_to(root_path).as_posix()
        entries.append(
            {
                "name": item.name,
                "path": rel,
                "type": "dir" if resolved.is_dir() else "file",
                "size": resolved.stat().st_size if resolved.is_file() else 0,
            }
        )
    return {
        "path": folder.relative_to(root_path).as_posix() if folder != root_path else "",
        "entries": entries[:1000],
        "truncated": len(entries) > 1000,
    }


def read_local_text_file(root: Path, relative_path: str) -> dict[str, str]:
    root_path = root.expanduser().resolve()
    file_path = resolve_local_view_path(root_path, relative_path)
    if not file_path.is_file():
        raise ValueError("The requested local path is not a file.")
    size = file_path.stat().st_size
    if size > 300000:
        raise ValueError("This file is larger than the 300 KB viewer limit.")
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"Could not read the local file: {exc}") from exc
    if b"\x00" in raw:
        raise ValueError("Binary files are not shown in the viewer.")
    return {
        "path": file_path.relative_to(root_path).as_posix(),
        "content": raw.decode("utf-8", errors="replace"),
    }


def local_project_root(project_id: str) -> Path:
    project = next((p for p in read_projects() if p.get("id") == project_id), None)
    if not project:
        raise ValueError("Project not found.")
    local_path = str(project.get("local_path", "")).strip()
    if not local_path:
        raise ValueError("Local path is not configured for this project.")
    return Path(local_path).expanduser().resolve()

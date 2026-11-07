"""GitHub API helpers, git operations, and repository post-processing."""

from __future__ import annotations

from ait.common import *


# --- GitHub Helpers ---
def repository_parts(repository: str) -> tuple[str, str]:
    m = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?", repository)
    if not m:
        raise ValueError("The project's GitHub repository URL is invalid.")
    return m.group(1), m.group(2)


def safe_repo_path(value: str) -> str:
    cleaned = value.strip().replace("\\", "/").strip("/")
    if cleaned and (
        cleaned.startswith(".")
        or any(part in (".", "..") or part.startswith(".") for part in cleaned.split("/"))
    ):
        raise ValueError("Hidden and relative repository paths are not available in this viewer.")
    if ".." in cleaned.split("/") or len(cleaned) > 500:
        raise ValueError("Invalid repository path.")
    return cleaned


def github_api(api_path: str, method: str = "GET", payload: Any = None, require_auth: bool = True) -> Any:
    logger.debug(f"GitHub API request: {method} {api_path}")
    request_started = time.monotonic()
    token = get_provider_key("github")
    if require_auth and not token:
        logger.error("GitHub token is not configured")
        raise ValueError("GitHub token is not configured.")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": HTTP_USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
        logger.debug("Using GitHub token for authentication")
    data_bytes = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"https://api.github.com{api_path}", data=data_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            logger.debug(
                "GitHub API response: method=%s path=%s status=%s bytes=%s elapsed_ms=%s",
                method, api_path, resp.status, len(raw), round((time.monotonic() - request_started) * 1000),
            )
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        logger.error(f"GitHub API HTTP error {exc.code}: {body}")
        raise ValueError(f"GitHub returned HTTP {exc.code}: {body}") from exc
    except Exception as exc:
        logger.error(f"GitHub API request failed: {exc}")
        raise ValueError(f"GitHub request failed: {exc}") from exc


def github_project(project_id: str) -> dict[str, Any]:
    project = next((p for p in read_projects() if p.get("id") == project_id), None)
    if not project or not project.get("repository"):
        raise ValueError("This project does not have a GitHub repository configured.")
    return project


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


def canonical_github_repository(repository: str) -> tuple[str, str]:
    remote = repository.strip()
    ssh_match = re.fullmatch(r"(?:git@github\.com:|ssh://git@github\.com/)([^/]+)/([^/]+?)(?:\.git)?", remote)
    if ssh_match:
        return ssh_match.group(1).casefold(), ssh_match.group(2).casefold()
    owner, name = repository_parts(normalize_github_repo_url(remote))
    return owner.casefold(), name.casefold()


def update_app_checkout(repository_url: str, checkout_path: Path, token: str) -> str:
    """Clone the configured app repo or fast-forward its existing checkout."""
    checkout_path = checkout_path.resolve()
    git_env = git_auth_environment(token)
    staging_path: Path | None = None
    if checkout_path.exists():
        if not checkout_path.is_dir():
            raise ValueError(f"Repository checkout path is not a directory: {checkout_path}")
        probe = subprocess.run(
            ["git", "-C", str(checkout_path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=15,
        )
        if probe.returncode != 0:
            raise ValueError(
                f"Repository checkout path exists but is not a Git repository: {checkout_path}. "
                "Choose an empty directory or an existing checkout."
            )
        remote = subprocess.run(
            ["git", "-C", str(checkout_path), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=15,
        )
        if remote.returncode != 0:
            raise ValueError(f"Could not read the origin remote for checkout: {remote.stderr.strip()}")
        if canonical_github_repository(remote.stdout) != canonical_github_repository(repository_url):
            raise ValueError(
                f"The existing checkout at '{checkout_path}' belongs to a different repository. "
                "Choose its matching repository URL or another checkout directory."
            )
        operation = "pull"
        command = ["git", "-C", str(checkout_path), "pull", "--ff-only"]
    else:
        checkout_path.parent.mkdir(parents=True, exist_ok=True)
        operation = "clone"
        staging_path = Path(tempfile.mkdtemp(prefix=f".{checkout_path.name}.clone-", dir=checkout_path.parent))
        command = ["git", "clone", "--progress", repository_url, str(staging_path)]

    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=300, env=git_env,
        )
    except PermissionError as exc:
        if staging_path is not None:
            shutil.rmtree(staging_path, ignore_errors=True)
        raise ValueError(
            f"Permission denied accessing repository checkout '{checkout_path}'. "
            "Ensure the service user can write there and systemd allows the path."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        if staging_path is not None:
            shutil.rmtree(staging_path, ignore_errors=True)
        detail = redact_git_output((exc.stderr or "") if isinstance(exc.stderr, str) else "", token).strip()
        raise ValueError(f"Git {operation} timed out after 300 seconds. {detail}".strip()) from exc
    except FileNotFoundError as exc:
        if staging_path is not None:
            shutil.rmtree(staging_path, ignore_errors=True)
        raise ValueError("Git is not installed or is not available to the application service.") from exc

    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip())
    output = redact_git_output(output, token)
    if result.returncode:
        if staging_path is not None:
            shutil.rmtree(staging_path, ignore_errors=True)
        detail = output or f"git exited with status {result.returncode}"
        logger.error("App repository %s failed (exit=%s): %s", operation, result.returncode, detail)
        raise ValueError(f"Git {operation} failed: {detail}")
    if staging_path is not None:
        try:
            os.replace(staging_path, checkout_path)
        except Exception:
            shutil.rmtree(staging_path, ignore_errors=True)
            raise
    logger.info("App repository %s completed (checkout=%s): %s", operation, checkout_path, output or "no changes")
    return operation


def copy_newer_app_files(source: Path, destination: Path) -> tuple[int, int]:
    """Copy newer repository files without deleting files unique to the deployment."""
    source = source.resolve(strict=True)
    destination = destination.resolve(strict=True)
    ignored_directories = {".git", ".venv", "venv", "__pycache__", "node_modules"}
    copied = skipped = 0

    def raise_walk_error(error: OSError) -> None:
        raise error

    for current, dirs, filenames in os.walk(source, followlinks=False, onerror=raise_walk_error):
        current_path = Path(current)
        dirs[:] = [
            name for name in dirs
            if name not in ignored_directories and not (current_path / name).is_symlink()
        ]
        relative_dir = current_path.relative_to(source)
        target_dir = destination / relative_dir
        if target_dir.is_symlink():
            raise ValueError(f"Refusing to write through a symbolic link in the app directory: {target_dir}")
        target_dir.mkdir(parents=True, exist_ok=True)
        if not target_dir.resolve().is_relative_to(destination):
            raise ValueError(f"Refusing to write outside the app working directory: {target_dir}")

        for filename in filenames:
            source_file = current_path / filename
            if source_file.is_symlink():
                continue
            relative_file = source_file.relative_to(source)
            target_file = destination / relative_file
            if target_file.is_symlink():
                raise ValueError(f"Refusing to overwrite a symbolic link in the app directory: {target_file}")
            resolved_target = target_file.resolve(strict=False)
            if not resolved_target.is_relative_to(destination):
                raise ValueError(f"Refusing to write outside the app working directory: {target_file}")
            source_stat = source_file.stat()
            try:
                target_stat = target_file.stat()
            except FileNotFoundError:
                target_stat = None
            if target_stat and target_stat.st_mtime_ns >= source_stat.st_mtime_ns:
                skipped += 1
                continue
            if target_stat and not target_file.is_file():
                raise ValueError(f"Cannot replace non-file app path: {target_file}")

            fd, temporary_name = tempfile.mkstemp(prefix=f".{filename}.", dir=target_dir)
            os.close(fd)
            temporary_file = Path(temporary_name)
            try:
                shutil.copy2(source_file, temporary_file)
                os.replace(temporary_file, target_file)
            finally:
                temporary_file.unlink(missing_ok=True)
            copied += 1
    return copied, skipped


def restart_service_process() -> None:
    logger.info("Exiting app process; systemd will restart the service")
    os.kill(os.getpid(), signal.SIGTERM)


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


def github_file_listing(project_id: str, folder_path: str = "") -> dict[str, Any]:
    project = github_project(project_id)
    owner, repo = repository_parts(project["repository"])
    has_token = bool(get_provider_key("github"))
    meta = github_api(f"/repos/{owner}/{repo}", require_auth=has_token)
    branch = str(meta.get("default_branch") or "main")
    clean_path = safe_repo_path(folder_path)
    suffix = "/" + "/".join(urllib.parse.quote(seg, safe="") for seg in clean_path.split("/")) if clean_path else ""
    res = github_api(
        f"/repos/{owner}/{repo}/contents{suffix}?ref={urllib.parse.quote(branch, safe='')}",
        require_auth=has_token,
    )
    if not isinstance(res, list):
        res = [res]
    entries = [
        {
            "name": str(item.get("name", "")),
            "path": str(item.get("path", "")),
            "type": "dir" if item.get("type") == "dir" else "file",
            "size": int(item.get("size") or 0),
        }
        for item in res
        if isinstance(item, dict)
    ]
    return {"branch": branch, "path": clean_path, "entries": entries}


def github_read_text_file(project_id: str, file_path: str) -> dict[str, Any]:
    project = github_project(project_id)
    owner, repo = repository_parts(project["repository"])
    has_token = bool(get_provider_key("github"))
    meta = github_api(f"/repos/{owner}/{repo}", require_auth=has_token)
    branch = str(meta.get("default_branch") or "main")
    clean_path = safe_repo_path(file_path)
    if not clean_path or clean_path.split("/")[-1].startswith("."):
        raise ValueError("Choose a visible file path.")
    enc_path = "/".join(urllib.parse.quote(seg, safe="") for seg in clean_path.split("/"))
    item = github_api(
        f"/repos/{owner}/{repo}/contents/{enc_path}?ref={urllib.parse.quote(branch, safe='')}",
        require_auth=has_token,
    )
    if not isinstance(item, dict) or item.get("type") != "file":
        raise ValueError("That path is not a file.")
    if int(item.get("size") or 0) > 300000:
        raise ValueError("This file is larger than the 300 KB viewer limit.")
    if item.get("encoding") != "base64":
        raise ValueError("GitHub did not return text file content.")
    decoded_bytes = base64.b64decode(re.sub(r"\s+", "", str(item.get("content", ""))))
    if b"\x00" in decoded_bytes:
        raise ValueError("Binary files are not shown in the viewer.")
    return {"branch": branch, "path": clean_path, "content": decoded_bytes.decode("utf-8", errors="replace")}


def github_task_context(
    task: dict[str, Any],
    branch: str = "",
    max_files: int = 16,
    max_bytes: int = 24000,
) -> str:
    project = github_project(str(task.get("project_id") or "default"))
    owner, repo = repository_parts(project["repository"])
    has_token = bool(get_provider_key("github"))
    meta = github_api(f"/repos/{owner}/{repo}", require_auth=has_token)
    explicit_ref = bool(branch)
    branch = branch or str(meta.get("default_branch") or "main")
    if not explicit_ref:
        try:
            ref = github_api(
                f"/repos/{owner}/{repo}/git/ref/heads/{urllib.parse.quote(branch, safe='')}",
                require_auth=has_token,
            )
            sha = str(((ref.get("object") or {}).get("sha")) or "")
            if sha:
                task["_snapshot_sha"] = sha
        except Exception:
            pass
    mod = next((m for m in project.get("modules", []) if m.get("id") == task.get("module_id")), None)
    scope = safe_repo_path(str((mod or {}).get("path", "")))
    tree_resp = github_api(
        f"/repos/{owner}/{repo}/git/trees/{urllib.parse.quote(branch, safe='')}?recursive=1",
        require_auth=has_token,
    )
    entries = tree_resp.get("tree", []) if isinstance(tree_resp, dict) else []
    allowed_exts = (
        ".md", ".txt", ".html", ".css", ".js", ".ts", ".tsx", ".jsx",
        ".py", ".json", ".toml", ".yml", ".yaml", ".xml", ".sh",
    )
    skip_dirs = {"node_modules", "dist", "build", "vendor", ".git", ".venv", "venv", "coverage"}
    files = []
    for item in entries:
        if not isinstance(item, dict) or item.get("type") != "blob":
            continue
        p = str(item.get("path", ""))
        if not p.lower().endswith(allowed_exts):
            continue
        parts = p.split("/")
        if any(seg in skip_dirs or seg.startswith(".") for seg in parts):
            continue
        if scope and not p.startswith(scope + "/"):
            continue
        files.append(item)

    prio = {"README.md", "readme.md", "package.json", "pyproject.toml"}
    files.sort(key=lambda x: (0 if str(x.get("path", "")).split("/")[-1] in prio else 1, str(x.get("path", ""))))
    chunks: list[str] = []
    total = 0
    for item in files[:max_files]:
        sz = int(item.get("size") or 0)
        if sz > 16000 or total + sz > max_bytes:
            continue
        ipath = str(item.get("path", ""))
        enc_path = "/".join(urllib.parse.quote(seg, safe="") for seg in ipath.split("/"))
        try:
            raw = github_api(
                f"/repos/{owner}/{repo}/contents/{enc_path}?ref={urllib.parse.quote(branch, safe='')}",
                require_auth=has_token,
            )
            if raw.get("encoding") != "base64":
                continue
            b = base64.b64decode(re.sub(r"\s+", "", str(raw.get("content", ""))))
            if b"\x00" in b:
                continue
            txt = b.decode("utf-8", errors="replace")
            chunks.append(f"--- {ipath} ---\n{txt}")
            total += len(b)
        except Exception:
            continue
    return (
        f"Repository: {owner}/{repo}, reviewed ref: {branch}, editable scope: {scope or '/'}\n"
        + ("\n\n".join(chunks) if chunks else "No readable source files were found in the selected scope.")
    )


def github_check_runs_summary(owner: str, repo: str, sha: str) -> dict[str, Any]:
    try:
        data = github_api(f"/repos/{owner}/{repo}/commits/{sha}/check-runs?per_page=100")
    except Exception:
        return {"state": "unknown", "runs": [], "failed": []}
    runs = [r for r in (data.get("check_runs") or []) if isinstance(r, dict)] if isinstance(data, dict) else []
    if not runs:
        return {"state": "none", "runs": [], "failed": []}
    if any(str(r.get("status") or "") != "completed" for r in runs):
        return {"state": "pending", "runs": runs, "failed": []}
    ok = {"success", "neutral", "skipped"}
    failed = [str(r.get("name") or "?") for r in runs if str(r.get("conclusion") or "") not in ok]
    return {"state": "failure" if failed else "success", "runs": runs, "failed": failed}


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

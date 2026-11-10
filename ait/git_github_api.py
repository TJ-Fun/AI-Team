"""GitHub API client, repository file viewers, and task context helpers."""

from __future__ import annotations

from ait.common import *


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


def canonical_github_repository(repository: str) -> tuple[str, str]:
    remote = repository.strip()
    ssh_match = re.fullmatch(r"(?:git@github\.com:|ssh://git@github\.com/)([^/]+)/([^/]+?)(?:\.git)?", remote)
    if ssh_match:
        return ssh_match.group(1).casefold(), ssh_match.group(2).casefold()
    owner, name = repository_parts(normalize_github_repo_url(remote))
    return owner.casefold(), name.casefold()


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


"""HTTP request handler and API routes."""

from __future__ import annotations

from ait.common import *


# --- HTTP Handler ---
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        message = fmt % args
        match = re.search(r"\b([1-5]\d\d)\b", message)
        status = int(match.group(1)) if match else 200
        level = logging.ERROR if status >= 500 else (logging.WARNING if status >= 400 else logging.INFO)
        logger.log(level, "HTTP client=%s %s", self.client_address[0], message)

    def send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError) as exc:
            logger.warning(
                "HTTP response delivery failed (client=%s, status=%s): %s",
                self.client_address[0], status, exc,
            )

    def serve_file(self, file_path: Path, mime_type: str) -> None:
        if not file_path.is_file():
            self.send_json({"error": "File not found."}, HTTPStatus.NOT_FOUND)
            return
        raw = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length > 524288:
            raise ValueError("Request body is too large.")
        raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        parsed = json.loads(raw or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("JSON object body is required.")
        return parsed

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)
        logger.debug(f"GET request: {path}")

        if path in ("/", "/index.html", "/static/index.html"):
            return self.serve_file(resolve_web_asset("index.html"), "text/html; charset=utf-8")
        if path == "/static/app.js":
            return self.serve_file(resolve_web_asset("app.js"), "text/javascript; charset=utf-8")
        if path == "/static/style.css":
            return self.serve_file(resolve_web_asset("style.css"), "text/css; charset=utf-8")

        if path == "/api/config":
            chat_provider = get_chat_provider()
            return self.send_json(
                {
                    "chat_provider": chat_provider,
                    "chat_model": chat_provider_model(chat_provider),
                    "chat_configured": chat_provider in configured_providers(),
                    "providers": PROVIDERS,
                    "gemini_model": GEMINI_MODEL,
                    "gemini_configured": bool(get_provider_key("gemini")),
                    "openrouter_model": OPENROUTER_MODEL,
                    "openrouter_configured": bool(get_provider_key("openrouter")),
                    "groq_model": GROQ_MODEL,
                }
            )

        if path == "/api/status":
            activity = provider_activity_status()
            return self.send_json(
                {
                    "cpu_percent": get_cpu_percent(),
                    "chat_working": ACTIVE_CHAT_COUNT > 0,
                    "gemini_working": activity["gemini_working"],
                    "groq_working": activity["groq_working"],
                    "gemini_configured": bool(get_provider_key("gemini")),
                    "gemini_model": GEMINI_MODEL,
                    "openrouter_working": activity["openrouter_working"],
                    "openrouter_configured": bool(get_provider_key("openrouter")),
                    "openrouter_model": OPENROUTER_MODEL,
                    "groq_configured": bool(get_provider_key("groq")),
                    "github_configured": bool(get_provider_key("github")),
                    "watchdog": WATCHDOG_STATUS,
                }
            )

        if path == "/api/connections":
            conns = []
            for cid, (label, _) in CONNECTION_SPECS.items():
                val, src = get_provider_key_and_source(cid)
                conns.append({"id": cid, "name": label, "configured": bool(val), "source": src or "Not connected"})
            return self.send_json({"connections": conns, "encryption_available": secret_cipher() is not None})

        if path == "/api/routing":
            return self.send_json(
                {
                    "roles": WORK_ROLES,
                    "providers": PROVIDERS,
                    "configured": configured_providers(),
                    "routing": get_routing(),
                    "chat_provider": get_chat_provider(),
                    "autonomy": get_autonomy(),
                }
            )

        if path == "/api/projects":
            return self.send_json({"projects": read_projects()})

        m_clone_job = re.fullmatch(r"/api/git/([a-z0-9-]+)/clone/([a-f0-9]{32})", path)
        if m_clone_job:
            try:
                return self.send_json(get_git_clone_job(m_clone_job.group(1), m_clone_job.group(2)))
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)

        m_git_status = re.fullmatch(r"/api/git/([a-z0-9-]+)/status", path)
        if m_git_status:
            project = next((p for p in read_projects() if p.get("id") == m_git_status.group(1)), None)
            if not project:
                return self.send_json({"error": "Project not found."}, HTTPStatus.NOT_FOUND)
            local_path = str(project.get("local_path", "")).strip()
            if not local_path or not Path(local_path).expanduser().is_dir():
                return self.send_json({"available": False, "message": "Not cloned yet."})
            try:
                return self.send_json({"available": True, "status": git_status(m_git_status.group(1))})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_gh_files = re.fullmatch(r"/api/projects/([a-z0-9-]+)/github-files", path)
        if m_gh_files:
            pid = m_gh_files.group(1)
            try:
                f_q = query.get("file", [""])[0]
                p_q = query.get("path", [""])[0]
                if f_q:
                    return self.send_json(github_read_text_file(pid, f_q))
                return self.send_json(github_file_listing(pid, p_q))
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if path == "/api/documentation-files":
            try:
                file_path = query.get("file", [""])[0]
                folder_path = query.get("path", [""])[0]
                if file_path:
                    return self.send_json(read_local_text_file(DOCUMENTATION_DIR, file_path))
                return self.send_json(local_file_listing(DOCUMENTATION_DIR, folder_path))
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_local_files = re.fullmatch(r"/api/projects/([a-z0-9-]+)/local-files", path)
        if m_local_files:
            try:
                project_root = local_project_root(m_local_files.group(1))
                file_path = query.get("file", [""])[0]
                folder_path = query.get("path", [""])[0]
                if file_path:
                    return self.send_json(read_local_text_file(project_root, file_path))
                return self.send_json(local_file_listing(project_root, folder_path))
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_proj_hist = re.fullmatch(r"/api/projects/([a-z0-9-]+)/context-history", path)
        if m_proj_hist:
            return self.send_json({"history": project_background_history(m_proj_hist.group(1))})

        if path == "/api/tasks":
            return self.send_json({"tasks": read_tasks()})

        m_replies = re.fullmatch(r"/api/tasks/(T-\d{4,})/replies", path)
        if m_replies:
            tid = m_replies.group(1)
            if not read_task(tid):
                return self.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
            return self.send_json({"replies": read_task_replies(tid)})

        if path == "/api/prompts":
            prompts = []
            for slug, title in PROMPT_FILES.items():
                current = load_prompt(slug)
                default_txt = load_default_prompt(slug)
                prompts.append(
                    {
                        "id": slug,
                        "title": title,
                        "content": current,
                        "default_content": default_txt,
                        "is_custom": current != default_txt,
                    }
                )
            return self.send_json({"prompts": prompts})

        m_project_context = re.fullmatch(r"/api/projects/([a-z0-9-]+)/context-documents", path)
        if m_project_context:
            project_id = m_project_context.group(1)
            if not any(p.get("id") == project_id for p in read_projects()):
                return self.send_json({"error": "Project not found."}, HTTPStatus.NOT_FOUND)
            ensure_project_context(project_id)
            docs = [
                {"id": slug, "title": title, "content": load_project_context(project_id, slug)}
                for slug, title in PROJECT_CONTEXT_FILES.items()
            ]
            return self.send_json({"documents": docs})

        m_ctx_hist = re.fullmatch(r"/api/projects/([a-z0-9-]+)/context-documents/([a-z_]+)/history", path)
        if m_ctx_hist:
            project_id, slug = m_ctx_hist.groups()
            if slug not in PROJECT_CONTEXT_FILES:
                return self.send_json({"error": "Project document not found."}, HTTPStatus.NOT_FOUND)
            if not any(p.get("id") == project_id for p in read_projects()):
                return self.send_json({"error": "Project not found."}, HTTPStatus.NOT_FOUND)
            return self.send_json({"history": get_project_context_history(project_id, slug)})

        if path == "/api/notifications":
            return self.send_json({"notifications": get_notifications()})

        if path == "/api/app-update/check":
            force = str(query.get("force", [""])[0]).lower() in ("1", "true", "yes")
            return self.send_json({"update": app_update_status(force=force)})

        if path == "/api/provider-quality":
            return self.send_json({"quality": get_provider_quality()})

        if path == "/api/llm-usage":
            return self.send_json({"resets": read_ai_stat_resets()})

        if path == "/api/settings/logs":
            return self.send_json({"logs": [], "info": "Use POST to load logs with filters"})

        if path == "/api/settings/config":
            config = read_app_config()
            return self.send_json({"config": config})

        if path == "/api/chat":
            chat_pid = str((query.get("project_id") or ["default"])[0] or "default")
            return self.send_json({"messages": read_chat_history(chat_pid), "project_id": chat_pid})

        self.send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        global ACTIVE_CHAT_COUNT
        path = urllib.parse.urlparse(self.path).path
        logger.info("HTTP request started method=POST client=%s path=%s", self.client_address[0], path)
        try:
            body = self.read_json_body()
        except ValueError as exc:
            logger.warning(f"Invalid POST body for {path}: {exc}")
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if path == "/api/tasks":
            title = str(body.get("title", "")).strip()
            req_text = str(body.get("request", "")).strip()
            role = str(body.get("role", "PM/PO")).strip()
            provider = str(body.get("provider", "Automatic")).strip()
            raw_checks = body.get("acceptance_checks", [])
            produces = bool(body.get("produces_deliverable"))
            logger.info(f"Creating task: title='{title}', role={role}, provider={provider}")
            if not title or not req_text or len(title) > 160 or len(req_text) > 12000:
                return self.send_json({"error": "Valid title and request are required."}, HTTPStatus.BAD_REQUEST)
            if role not in WORK_ROLES or provider not in TASK_PROVIDERS:
                return self.send_json({"error": "Choose a valid work role and AI provider."}, HTTPStatus.BAD_REQUEST)
            pid = str(body.get("project_id") or "default")
            mid = str(body.get("module_id") or "")
            proj = next((p for p in read_projects() if p.get("id") == pid), None)
            if not proj or (mid and not any(m.get("id") == mid for m in proj.get("modules", []))):
                return self.send_json({"error": "Choose a valid project and sub-project."}, HTTPStatus.BAD_REQUEST)
            if not isinstance(raw_checks, list):
                return self.send_json({"error": "Acceptance checks must be a list."}, HTTPStatus.BAD_REQUEST)
            checks = [str(c).strip()[:500] for c in raw_checks if str(c).strip()][:20]
            if (
                proj.get("repository")
                and role in ("Frontend developer", "Backend developer", "Documentation")
                and (not produces or not checks)
            ):
                return self.send_json(
                    {"error": "Repository work must be marked as a deliverable and include at least one acceptance check."},
                    HTTPStatus.BAD_REQUEST,
                )
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
            return self.send_json({"task": task}, HTTPStatus.CREATED)

        if path == "/api/pm-requests":
            req_text = str(body.get("request", "")).strip()
            if not req_text or len(req_text) > 12000:
                return self.send_json({"error": "Describe a project request under 12,000 characters."}, HTTPStatus.BAD_REQUEST)
            pid = str(body.get("project_id") or "default")
            if not any(p.get("id") == pid for p in read_projects()):
                return self.send_json({"error": "Choose a valid project."}, HTTPStatus.BAD_REQUEST)
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
            return self.send_json({"task": task}, HTTPStatus.CREATED)

        m_prompt_reset = re.fullmatch(r"/api/prompts/([a-z_]+)/reset", path)
        if m_prompt_reset:
            slug = m_prompt_reset.group(1)
            if slug not in PROMPT_FILES:
                return self.send_json({"error": "Prompt not found."}, HTTPStatus.NOT_FOUND)
            try:
                default_txt = reset_prompt_file(slug)
                append_event({"at": now_iso(), "type": "prompt_reset", "prompt": slug})
                return self.send_json(
                    {
                        "saved": True,
                        "id": slug,
                        "title": PROMPT_FILES[slug],
                        "content": default_txt,
                        "default_content": default_txt,
                        "is_custom": False,
                    }
                )
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if path == "/api/routing":
            try:
                routing = None
                if "routing" in body:
                    routing = save_routing(body.get("routing"))
                autonomy = None
                if "autonomy" in body:
                    autonomy = save_autonomy(body.get("autonomy"))
                chat_provider = body.get("chat_provider")
                if chat_provider is not None:
                    chat_provider = str(chat_provider).strip()
                    if chat_provider not in PROVIDERS:
                        raise ValueError("Choose a valid chat provider.")
                    save_app_config({"chat_provider": chat_provider})
                append_event({"at": now_iso(), "type": "routing_updated"})
                DISPATCH_EVENT.set()
                return self.send_json(
                    {
                        "routing": routing if routing is not None else get_routing(),
                        "chat_provider": get_chat_provider(),
                        "autonomy": autonomy if autonomy is not None else get_autonomy(),
                    }
                )
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_conn_test = re.fullmatch(r"/api/connections/([a-z_]+)/test", path)
        if m_conn_test:
            cid = m_conn_test.group(1)
            if cid not in CONNECTION_SPECS:
                return self.send_json({"connected": False, "error": "Unknown connection."}, HTTPStatus.NOT_FOUND)
            try:
                result = test_provider_connection(cid)
                append_event(
                    {
                        "at": now_iso(),
                        "type": "github_connection_test" if cid == "github" else "provider_connection_test",
                        "connection": cid,
                        "success": True,
                    }
                )
                return self.send_json(result)
            except ValueError as exc:
                append_event(
                    {
                        "at": now_iso(),
                        "type": "github_connection_test" if cid == "github" else "provider_connection_test",
                        "connection": cid,
                        "success": False,
                        "error": str(exc)[:300],
                    }
                )
                return self.send_json({"connected": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if path == "/api/connections/github/repositories":
            try:
                res = github_api("/user/repos?per_page=100&sort=updated&affiliation=owner,collaborator")
                repos = [
                    {
                        "full_name": str(r.get("full_name", "")),
                        "clone_url": str(r.get("clone_url", "")),
                        "private": bool(r.get("private")),
                        "default_branch": str(r.get("default_branch") or "main"),
                    }
                    for r in (res if isinstance(res, list) else [])
                    if isinstance(r, dict)
                ]
                return self.send_json({"repositories": repos})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if path == "/api/llm-usage/reset":
            provider = str(body.get("provider", "")).strip()
            model = str(body.get("model", "")).strip()
            period = str(body.get("period", "")).strip()
            bucket = str(body.get("bucket", "")).strip()
            patterns = {"today": r"^\d{4}-\d{2}-\d{2}$", "month": r"^\d{4}-\d{2}$", "year": r"^\d{4}$", "all": r"^all$"}
            if provider not in PROVIDERS or not model or len(model) > 160:
                return self.send_json({"error": "Choose a valid provider and model."}, HTTPStatus.BAD_REQUEST)
            if period not in patterns or not re.fullmatch(patterns[period], bucket):
                return self.send_json({"error": "Choose a valid statistic period."}, HTTPStatus.BAD_REQUEST)
            reset = {"provider": provider, "model": model, "period": period, "bucket": bucket, "reset_at": now_iso()}
            append_ai_stat_reset(reset)
            append_event({"at": reset["reset_at"], "type": "ai_statistic_reset", **reset})
            return self.send_json({"reset": reset})

        # Git operation endpoints
        m_git_status = re.fullmatch(r"/api/git/([a-z0-9-]+)/status", path)
        if m_git_status:
            pid = m_git_status.group(1)
            try:
                status = git_status(pid)
                return self.send_json({"status": status})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_git_pull = re.fullmatch(r"/api/git/([a-z0-9-]+)/pull", path)
        if m_git_pull:
            pid = m_git_pull.group(1)
            try:
                result = git_pull(pid)
                append_event({"at": now_iso(), "type": "git_pull", "project_id": pid, "success": True})
                return self.send_json(result)
            except ValueError as exc:
                logger.exception("Git pull request failed (project=%s)", pid)
                append_event({"at": now_iso(), "type": "git_pull", "project_id": pid, "success": False, "error": str(exc)[:300]})
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_git_push = re.fullmatch(r"/api/git/([a-z0-9-]+)/push", path)
        if m_git_push:
            pid = m_git_push.group(1)
            try:
                result = git_push(pid)
                append_event({"at": now_iso(), "type": "git_push", "project_id": pid, "success": True})
                return self.send_json(result)
            except ValueError as exc:
                logger.exception("Git push request failed (project=%s)", pid)
                append_event({"at": now_iso(), "type": "git_push", "project_id": pid, "success": False, "error": str(exc)[:300]})
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_git_clone = re.fullmatch(r"/api/git/([a-z0-9-]+)/clone", path)
        if m_git_clone:
            pid = m_git_clone.group(1)
            try:
                job = start_git_clone(pid)
                logger.info("Git clone job accepted (project=%s, job=%s)", pid, job["job_id"])
                append_event({"at": now_iso(), "type": "git_clone", "project_id": pid, "job_id": job["job_id"]})
                return self.send_json(job, HTTPStatus.ACCEPTED)
            except ValueError as exc:
                logger.error("Git clone request rejected (project=%s): %s", pid, exc)
                append_event({"at": now_iso(), "type": "git_clone", "project_id": pid, "success": False, "error": str(exc)[:300]})
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                logger.exception("Could not start Git clone job (project=%s)", pid)
                return self.send_json({"error": f"Could not start Git clone: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        # Settings API endpoints
        if path == "/api/settings/logs":
            level = str(body.get("level", "all")).strip().upper()
            level_number = 0 if level == "ALL" else LOG_LEVELS.get(level)
            if level_number is None:
                return self.send_json({"error": "Choose a valid log display level."}, HTTPStatus.BAD_REQUEST)
            try:
                lines = int(body.get("lines", 1000))
            except (TypeError, ValueError):
                return self.send_json({"error": "Log line count must be an integer."}, HTTPStatus.BAD_REQUEST)
            if not 1 <= lines <= 5000:
                return self.send_json({"error": "Log line count must be between 1 and 5000."}, HTTPStatus.BAD_REQUEST)
            try:
                newest_lines = []
                log_files = sorted(
                    (path for path in LOG_DIR.glob("app.log*") if path.is_file()),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
                for log_file in log_files:
                    content = log_file.read_text(encoding="utf-8", errors="replace")
                    for line in reversed(content.splitlines()):
                        line_level = re.search(r"\|\s*(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*\|", line)
                        if level_number and (
                            not line_level or LOG_LEVELS[line_level.group(1)] < level_number
                        ):
                            continue
                        newest_lines.append(line)
                        if len(newest_lines) >= lines:
                            break
                    if len(newest_lines) >= lines:
                        break
                return self.send_json({"logs": list(reversed(newest_lines))})
            except Exception as exc:
                logger.exception("Failed to load application logs")
                return self.send_json({"error": f"Failed to read logs: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        if path == "/api/settings/log-level":
            level = normalize_log_level(body.get("level"))
            if not level:
                return self.send_json({"error": "Choose a valid log capture level."}, HTTPStatus.BAD_REQUEST)
            config = read_app_config()
            config["log_level"] = level
            try:
                save_app_config(config)
                configure_file_log_level(level)
                return self.send_json({"log_level": level})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

        if path == "/api/settings/logs/clear":
            try:
                file_handler.acquire()
                try:
                    file_handler.flush()
                    if file_handler.stream:
                        file_handler.stream.close()
                        file_handler.stream = None
                    for log_file in LOG_DIR.glob("app.log*"):
                        log_file.unlink(missing_ok=True)
                finally:
                    try:
                        file_handler.stream = file_handler._open()
                    finally:
                        file_handler.release()
                logger.info("Logs cleared by user")
                return self.send_json({"cleared": True})
            except Exception as exc:
                logger.exception("Failed to clear application logs")
                return self.send_json({"error": f"Failed to clear logs: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        if path == "/api/settings/restart":
            logger.info("App restart requested by user")
            self.send_json({"success": True, "message": "Service restart scheduled."})
            threading.Timer(2.0, restart_service_process).start()
            return None

        if path == "/api/settings/update":
            app_repo = str(body.get("app_repo", "")).strip()
            app_clone_dir = str(body.get("app_clone_dir", "")).strip()
            app_working_dir = str(body.get("app_working_dir", "")).strip()
            if not app_repo or not app_clone_dir or not app_working_dir:
                return self.send_json(
                    {"error": "App repository, local checkout directory, and working directory are required."},
                    HTTPStatus.BAD_REQUEST,
                )
            if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", app_repo):
                return self.send_json({"error": "Invalid GitHub repository URL."}, HTTPStatus.BAD_REQUEST)
            
            try:
                logger.info(
                    "App update requested: repo=%s checkout=%s working_dir=%s",
                    app_repo, app_clone_dir, app_working_dir,
                )
                checkout_path = Path(app_clone_dir).expanduser().resolve()
                working_path = Path(app_working_dir).expanduser().resolve()
                if not checkout_path.is_absolute() or not working_path.is_absolute():
                    return self.send_json(
                        {"error": "The local checkout and app working directory must be absolute paths."},
                        HTTPStatus.BAD_REQUEST,
                    )
                if checkout_path == Path(checkout_path.anchor) or working_path == Path(working_path.anchor):
                    return self.send_json(
                        {"error": "A filesystem root cannot be used as an app directory."},
                        HTTPStatus.BAD_REQUEST,
                    )
                if not working_path.is_dir():
                    return self.send_json({"error": f"Working directory does not exist: {app_working_dir}"}, HTTPStatus.BAD_REQUEST)
                if (
                    checkout_path == working_path
                    or checkout_path.is_relative_to(working_path)
                    or working_path.is_relative_to(checkout_path)
                ):
                    return self.send_json(
                        {"error": "The local checkout directory and app working directory must not overlap."},
                        HTTPStatus.BAD_REQUEST,
                    )
                
                token = get_provider_key("github")
                if not token:
                    return self.send_json({"error": "GitHub token is not configured."}, HTTPStatus.BAD_REQUEST)
                
                app_repo_url = normalize_github_repo_url(app_repo)
                owner, repo = repository_parts(app_repo_url)
                app_repo_url = f"https://github.com/{owner}/{repo}.git"
                operation = update_app_checkout(app_repo_url, checkout_path, token)
                copied, skipped = copy_newer_app_files(checkout_path, working_path)
                logger.info(
                    "App files copied from checkout=%s to working_dir=%s (copied=%s, kept_newer_or_equal=%s)",
                    checkout_path, working_path, copied, skipped,
                )
                response = {
                    "success": True,
                    "operation": operation,
                    "copied_files": copied,
                    "kept_files": skipped,
                    "message": f"Repository {operation} completed; copied {copied} newer file(s), kept {skipped} newer or unchanged file(s).",
                }
                self.send_json(response)
                threading.Timer(2.0, restart_service_process).start()
                return None
            except PermissionError as exc:
                logger.exception("App update permission failure")
                return self.send_json(
                    {
                        "error": (
                            f"Permission denied at '{exc.filename or app_clone_dir or app_working_dir}'. "
                            "Grant the ubuntu service user filesystem access and add the exact path "
                            "to systemd ReadWritePaths."
                        )
                    },
                    HTTPStatus.FORBIDDEN,
                )
            except OSError as exc:
                logger.exception("App update filesystem failure")
                if exc.errno == errno.EROFS:
                    detail = (
                        f"'{exc.filename or app_working_dir}' is read-only in the service's filesystem namespace. "
                        "Install the updated personal-ai-team.service with this directory in ReadWritePaths, "
                        "then run systemctl daemon-reload and restart the service before retrying."
                    )
                else:
                    detail = f"Filesystem operation failed at '{exc.filename or app_working_dir}': {exc}"
                return self.send_json({"error": detail}, HTTPStatus.INTERNAL_SERVER_ERROR)
            except subprocess.TimeoutExpired:
                logger.exception("App update command timed out")
                return self.send_json({"error": "Update command timed out."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            except ValueError as exc:
                logger.exception("App update failed")
                return self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            except Exception as exc:
                logger.exception("App update failed unexpectedly")
                return self.send_json({"error": f"Update failed: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        if path == "/api/settings/config":
            try:
                config = body
                save_app_config(config)
                return self.send_json({"success": True, "config": config})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_status = re.fullmatch(r"/api/tasks/(T-\d{4,})/status", path)
        if m_status:
            tid = m_status.group(1)
            target = str(body.get("status", ""))
            note = str(body.get("note", "")).strip()[:500]
            if target not in ("Ready", "Blocked", "Canceled", "Draft"):
                return self.send_json({"error": "Invalid manual status transition."}, HTTPStatus.BAD_REQUEST)
            with STATE_LOCK:
                task = read_task(tid)
                if not task:
                    return self.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                cur = str(task.get("status", ""))
                if target not in TRANSITIONS.get(cur, set()) or target not in USER_TRANSITIONS.get(cur, set()):
                    return self.send_json({"error": f"Cannot move a {cur} task to {target}."}, HTTPStatus.CONFLICT)
                if target == "Ready" and task.get("produces_deliverable") and not (task.get("acceptance_checks") or []):
                    return self.send_json(
                        {"error": "Add at least one acceptance check before marking a deliverable task Ready."},
                        HTTPStatus.BAD_REQUEST,
                    )
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
            return self.send_json({"task": task})

        m_prov = re.fullmatch(r"/api/tasks/(T-\d{4,})/provider", path)
        if m_prov:
            tid = m_prov.group(1)
            provider = str(body.get("provider", "")).strip()
            if provider not in TASK_PROVIDERS:
                return self.send_json({"error": "Choose Automatic or a valid AI provider."}, HTTPStatus.BAD_REQUEST)
            with STATE_LOCK:
                task = read_task(tid)
                if not task:
                    return self.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                if task.get("status") in ("In progress", "Done", "Canceled"):
                    return self.send_json(
                        {"error": f"Cannot change AI provider while a task is {task.get('status')}."},
                        HTTPStatus.CONFLICT,
                    )
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
            return self.send_json({"task": task})

        m_val = re.fullmatch(r"/api/tasks/(T-\d{4,})/validation", path)
        if m_val:
            tid = m_val.group(1)
            task = read_task(tid)
            if not task:
                return self.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
            return self.send_json(
                {"error": "Deliverable validation is performed automatically by the QA role."},
                HTTPStatus.CONFLICT,
            )

        m_local_sync = re.fullmatch(r"/api/tasks/(T-\d{4,})/sync-local", path)
        if m_local_sync:
            tid = m_local_sync.group(1)
            task = read_task(tid)
            if not task:
                return self.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
            pull = task.get("pull_request")
            if (
                task.get("status") != "Done"
                or (task.get("validation") or {}).get("status") != "passed"
                or not isinstance(pull, dict)
                or pull.get("state") != "merged"
            ):
                return self.send_json(
                    {"error": "Local sync can only be retried after QA passes and the pull request is merged."},
                    HTTPStatus.CONFLICT,
                )
            if not sync_local_checkout_after_merge(tid):
                latest = read_task(tid) or task
                return self.send_json(
                    {"error": latest.get("local_sync_error") or "Local checkout sync failed."},
                    HTTPStatus.BAD_REQUEST,
                )
            return self.send_json({"task": read_task(tid)})

        m_ans = re.fullmatch(r"/api/tasks/(T-\d{4,})/answer", path)
        if m_ans:
            tid = m_ans.group(1)
            selected = str(body.get("selected_option", "")).strip()
            free_text = str(body.get("free_text", "")).strip()[:5000]
            with STATE_LOCK:
                question = read_task(tid)
                if not question:
                    return self.send_json({"error": "Question not found."}, HTTPStatus.NOT_FOUND)
                if question.get("task_type") != "clarification_question" or question.get("status") != "Needs answer":
                    return self.send_json({"error": "This question is not waiting for an answer."}, HTTPStatus.CONFLICT)
                q_data = question.get("question_data") or {}
                opts = [o.get("label") for o in (q_data.get("options") or []) if isinstance(o, dict)]
                if selected and selected not in opts:
                    return self.send_json({"error": "Choose one of the listed options."}, HTTPStatus.BAD_REQUEST)
                if not selected and not free_text:
                    return self.send_json({"error": "Choose an option or enter a response."}, HTTPStatus.BAD_REQUEST)
                if q_data.get("allow_free_text") is False and not selected:
                    return self.send_json({"error": "Choose one of the listed options."}, HTTPStatus.BAD_REQUEST)

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
            return self.send_json({"task": question, "followup_task": followup})

        m_app_ov = re.fullmatch(r"/api/tasks/(T-\d{4,})/approve-overview", path)
        if m_app_ov:
            tid = m_app_ov.group(1)
            try:
                documents = clean_overview_documents(body)
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            with STATE_LOCK:
                task = read_task(tid)
                if not task:
                    return self.send_json({"error": "Project overview task not found."}, HTTPStatus.NOT_FOUND)
                if task.get("task_type") != "project_overview" or task.get("status") != "Needs approval":
                    return self.send_json({"error": "This project overview is no longer awaiting approval."}, HTTPStatus.CONFLICT)
                ts = now_iso()
                snapshots: dict[str, str | None] = {}
                pid = str(task.get("project_id") or "default")
                if not any(p.get("id") == pid for p in read_projects()):
                    return self.send_json({"error": "Attached project no longer exists."}, HTTPStatus.CONFLICT)
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
            return self.send_json({"task": task, "snapshots": snapshots, "released_task_ids": released})

        m_design = re.fullmatch(r"/api/tasks/(T-\d{4,})/design-review", path)
        if m_design:
            tid = m_design.group(1)
            decision = str(body.get("decision", "")).strip().lower()
            free_text = str(body.get("free_text", "")).strip()[:5000]
            alt_index = body.get("alternative_index")
            if decision not in ("approve", "reject", "alternative"):
                return self.send_json({"error": "Decision must be approve, reject, or alternative."}, HTTPStatus.BAD_REQUEST)
            with STATE_LOCK:
                task = read_task(tid)
                if not task:
                    return self.send_json({"error": "Design review task not found."}, HTTPStatus.NOT_FOUND)
                if task.get("task_type") != "design_review" or task.get("status") != "Needs approval":
                    return self.send_json({"error": "This design review is no longer awaiting a decision."}, HTTPStatus.CONFLICT)
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
                        return self.send_json({"error": "Choose a listed alternative."}, HTTPStatus.BAD_REQUEST)
                    chosen_alt = str(alts[idx].get("title") or "")
                    chosen_alt_detail = str(alts[idx].get("description") or "")
                elif decision == "reject" and not free_text:
                    return self.send_json({"error": "Explain why the design change is rejected, or pick an alternative."}, HTTPStatus.BAD_REQUEST)

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
            return self.send_json({"task": task})

        m_merge = re.fullmatch(r"/api/tasks/(T-\d{4,})/approve-merge", path)
        if m_merge:
            tid = m_merge.group(1)
            with STATE_LOCK:
                task = read_task(tid)
                if not task:
                    return self.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                if task.get("status") != "Needs approval" or not task.get("merge_approval_required"):
                    return self.send_json({"error": "This task is not waiting for merge approval."}, HTTPStatus.CONFLICT)
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
            return self.send_json({"task": read_task(tid)})

        m_ctx_res = re.fullmatch(r"/api/projects/([a-z0-9-]+)/context-documents/([a-z_]+)/restore", path)
        if m_ctx_res:
            pid, slug = m_ctx_res.groups()
            vid = str(body.get("snapshot_id", ""))
            if slug not in PROJECT_CONTEXT_FILES:
                return self.send_json({"error": "Project document not found."}, HTTPStatus.NOT_FOUND)
            if not any(p.get("id") == pid for p in read_projects()):
                return self.send_json({"error": "Project not found."}, HTTPStatus.NOT_FOUND)
            if not re.fullmatch(r"\d{8}T\d{12}Z", vid):
                return self.send_json({"error": "Choose a valid saved version."}, HTTPStatus.BAD_REQUEST)
            snap = project_context_file(pid, slug).parent / "history" / slug / f"{vid}.md"
            if not snap.is_file():
                return self.send_json({"error": "Saved version not found."}, HTTPStatus.NOT_FOUND)
            txt = snap.read_text(encoding="utf-8")
            new_snap = save_project_context_document(pid, slug, txt)
            append_event(
                {
                    "at": now_iso(),
                    "type": "project_context_restored",
                    "project_id": pid,
                    "document": slug,
                    "restored_snapshot": vid,
                    "snapshot": new_snap,
                }
            )
            return self.send_json({"saved": True, "id": slug, "title": PROJECT_CONTEXT_FILES[slug], "content": txt.strip()})

        m_proj_res = re.fullmatch(r"/api/projects/([a-z0-9-]+)/restore-context", path)
        if m_proj_res:
            pid = m_proj_res.group(1)
            vid = str(body.get("version_id", ""))
            if not re.fullmatch(r"\d{8}T\d{12}Z", vid):
                return self.send_json({"error": "Choose a valid saved project version."}, HTTPStatus.BAD_REQUEST)
            snap = DATA / "projects" / pid / "history" / f"{vid}.md"
            if not snap.is_file():
                return self.send_json({"error": "Saved project version not found."}, HTTPStatus.NOT_FOUND)
            restored = snap.read_text(encoding="utf-8")
            projs = read_projects()
            proj = next((p for p in projs if p.get("id") == pid), None)
            if not proj:
                return self.send_json({"error": "Project not found."}, HTTPStatus.NOT_FOUND)
            proj["context"] = restored
            save_projects(projs)
            return self.send_json({"restored": True, "context": restored})

        if path == "/api/chat":
            text = str(body.get("message", "")).strip()
            if not text or len(text) > 12000:
                return self.send_json({"error": "Write a message up to 12,000 characters."}, HTTPStatus.BAD_REQUEST)
            requested_project_id = str(body.get("project_id") or "")
            projects = read_projects()
            if requested_project_id:
                project = next((p for p in projects if p.get("id") == requested_project_id), None)
            else:
                project = next((p for p in projects if p.get("id") == "default"), projects[0])
            if not project:
                return self.send_json({"error": "Choose a valid project."}, HTTPStatus.BAD_REQUEST)
            project_id = str(project["id"])
            with ACTIVE_CHAT_LOCK:
                if ACTIVE_CHAT_COUNT > 0:
                    return self.send_json({"error": "Another PM/PO reply is still in progress."}, HTTPStatus.CONFLICT)
                ACTIVE_CHAT_COUNT += 1
            t0 = time.time()
            try:
                hist = read_chat_history(project_id)
                hist.append({"role": "user", "content": text, "at": now_iso()})
                write_chat_history(hist, project_id)
                append_event({"at": now_iso(), "type": "chat_request_started", "provider": get_chat_provider()})
                try:
                    reply, used_provider, used_model = call_chat_provider(hist, project_id)
                    dur = round(time.time() - t0, 2)
                    msg = {
                        "role": "assistant",
                        "content": reply,
                        "at": now_iso(),
                        "provider": used_provider,
                        "model": used_model,
                        "duration_seconds": dur,
                    }
                    hist.append(msg)
                    write_chat_history(hist, project_id)
                    append_event({"at": now_iso(), "type": "chat_reply", "provider": used_provider, "model": used_model, "duration_seconds": dur})
                    return self.send_json({"reply": msg, "messages": hist})
                except Exception as exc:
                    dur = round(time.time() - t0, 2)
                    append_event({"at": now_iso(), "type": "chat_error", "provider": get_chat_provider(), "duration_seconds": dur})
                    err_msg = f"The chat provider could not answer ({exc})."
                    hist.append({"role": "assistant", "content": err_msg, "at": now_iso(), "error": True})
                    write_chat_history(hist, project_id)
                    return self.send_json({"error": err_msg}, HTTPStatus.BAD_GATEWAY)
            finally:
                with ACTIVE_CHAT_LOCK:
                    ACTIVE_CHAT_COUNT = max(0, ACTIVE_CHAT_COUNT - 1)

        self.send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            body = self.read_json_body()
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_prompt = re.fullmatch(r"/api/prompts/([a-z_]+)", path)
        if m_prompt:
            slug = m_prompt.group(1)
            if slug not in PROMPT_FILES:
                return self.send_json({"error": "Prompt not found."}, HTTPStatus.NOT_FOUND)
            content = body.get("content")
            if not isinstance(content, str) or not content.strip():
                return self.send_json({"error": "Prompt text cannot be empty."}, HTTPStatus.BAD_REQUEST)
            if len(content) > 20000:
                return self.send_json({"error": "Prompt text must be 20,000 characters or fewer."}, HTTPStatus.BAD_REQUEST)
            saved_txt = save_prompt_file(slug, content)
            default_txt = load_default_prompt(slug)
            append_event({"at": now_iso(), "type": "prompt_saved", "prompt": slug})
            return self.send_json(
                {
                    "saved": True,
                    "id": slug,
                    "title": PROMPT_FILES[slug],
                    "content": saved_txt,
                    "default_content": default_txt,
                    "is_custom": saved_txt != default_txt,
                }
            )

        m_conn = re.fullmatch(r"/api/connections/([a-z_]+)", path)
        if m_conn:
            cid = m_conn.group(1)
            if cid not in CONNECTION_SPECS:
                return self.send_json({"error": "Unknown connection."}, HTTPStatus.NOT_FOUND)
            secret = str(body.get("secret", "")).strip()
            if not secret or len(secret) > 4000:
                return self.send_json({"error": "Provide a valid key or token."}, HTTPStatus.BAD_REQUEST)
            try:
                secrets = read_saved_secrets()
                secrets[cid] = secret
                write_saved_secrets(secrets)
                append_event({"at": now_iso(), "type": "connection_saved", "connection": cid})
                DISPATCH_EVENT.set()
                return self.send_json({"saved": True, "id": cid})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_ctx = re.fullmatch(r"/api/projects/([a-z0-9-]+)/context-documents/([a-z_]+)", path)
        if m_ctx:
            pid, slug = m_ctx.groups()
            if slug not in PROJECT_CONTEXT_FILES:
                return self.send_json({"error": "Project document not found."}, HTTPStatus.NOT_FOUND)
            if not any(p.get("id") == pid for p in read_projects()):
                return self.send_json({"error": "Project not found."}, HTTPStatus.NOT_FOUND)
            content = body.get("content")
            if not isinstance(content, str) or not content.strip() or len(content) > 20000:
                return self.send_json({"error": "Project document must be non-empty and under 20,000 characters."}, HTTPStatus.BAD_REQUEST)
            snap = save_project_context_document(pid, slug, content)
            append_event(
                {"at": now_iso(), "type": "project_context_saved", "project_id": pid, "document": slug, "snapshot": snap}
            )
            return self.send_json(
                {"saved": True, "project_id": pid, "id": slug, "title": PROJECT_CONTEXT_FILES[slug], "content": content.strip(), "snapshot": snap}
            )

        m_ov = re.fullmatch(r"/api/tasks/(T-\d{4,})/overview", path)
        if m_ov:
            tid = m_ov.group(1)
            try:
                documents = clean_overview_documents(body)
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            with STATE_LOCK:
                task = read_task(tid)
                if not task:
                    return self.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                if task.get("task_type") != "project_overview" or task.get("status") != "Needs approval":
                    return self.send_json({"error": "Overview is no longer awaiting approval."}, HTTPStatus.CONFLICT)
                ts = now_iso()
                task["overview_documents"] = documents
                task["updated_at"] = ts
                task.setdefault("activity", []).append(
                    {"at": ts, "type": "overview_edited", "message": "User edited proposed project overview."}
                )
                write_task(task)
            return self.send_json({"task": task})

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
                return self.send_json({"error": "Enter a valid title and request."}, HTTPStatus.BAD_REQUEST)
            if role not in WORK_ROLES or provider not in TASK_PROVIDERS or not isinstance(raw_checks, list):
                return self.send_json({"error": "Invalid role, provider, or acceptance checks."}, HTTPStatus.BAD_REQUEST)
            pid = str(body.get("project_id") or "default")
            mid = str(body.get("module_id") or "")
            proj = next((p for p in read_projects() if p.get("id") == pid), None)
            if not proj or (mid and not any(m.get("id") == mid for m in proj.get("modules", []))):
                return self.send_json({"error": "Choose a valid project and sub-project."}, HTTPStatus.BAD_REQUEST)
            checks = [str(c).strip()[:500] for c in raw_checks if str(c).strip()][:20]
            with STATE_LOCK:
                task = read_task(tid)
                if not task:
                    return self.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                if task.get("status") not in ("Draft", "Blocked", "Rework needed", "Waiting for capacity"):
                    return self.send_json(
                        {"error": "Only Draft, Blocked, Rework needed, or Waiting for capacity tasks can be edited."},
                        HTTPStatus.CONFLICT,
                    )
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
            return self.send_json({"task": task})

        if path == "/api/projects":
            try:
                projects = save_projects(body.get("projects"))
                append_event({"at": now_iso(), "type": "projects_updated", "count": len(projects)})
                return self.send_json({"projects": projects})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        self.send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        path = urllib.parse.urlparse(self.path).path

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
                    return self.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
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
            return self.send_json({"deleted": True, "task_id": tid})

        m_conn = re.fullmatch(r"/api/connections/([a-z_]+)", path)
        if m_conn:
            cid = m_conn.group(1)
            if cid not in CONNECTION_SPECS:
                return self.send_json({"error": "Unknown connection."}, HTTPStatus.NOT_FOUND)
            try:
                secrets = read_saved_secrets()
                secrets.pop(cid, None)
                write_saved_secrets(secrets)
                append_event({"at": now_iso(), "type": "connection_cleared", "connection": cid})
                return self.send_json({"deleted": True, "id": cid})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_ctx_del = re.fullmatch(
            r"/api/projects/([a-z0-9-]+)/context-documents/([a-z_]+)/history/(\d{8}T\d{12}Z)", path
        )
        if m_ctx_del:
            pid, slug, vid = m_ctx_del.groups()
            if slug not in PROJECT_CONTEXT_FILES or not any(p.get("id") == pid for p in read_projects()):
                return self.send_json({"error": "Saved version not found."}, HTTPStatus.NOT_FOUND)
            snap = project_context_file(pid, slug).parent / "history" / slug / f"{vid}.md"
            if not snap.is_file():
                return self.send_json({"error": "Saved version not found."}, HTTPStatus.NOT_FOUND)
            snap.unlink()
            return self.send_json({"deleted": True, "project_id": pid, "id": slug, "deleted_version": vid})

        m_proj_del = re.fullmatch(r"/api/projects/([a-z0-9-]+)/context-history/(\d{8}T\d{12}Z)", path)
        if m_proj_del:
            pid, vid = m_proj_del.group(1), m_proj_del.group(2)
            snap = DATA / "projects" / pid / "history" / f"{vid}.md"
            if not snap.is_file():
                return self.send_json({"error": "Saved project version not found."}, HTTPStatus.NOT_FOUND)
            snap.unlink()
            return self.send_json({"deleted": True, "project_id": pid, "deleted_version": vid})

        if path.startswith("/api/notifications/"):
            nid = urllib.parse.unquote(path[len("/api/notifications/") :])
            if not nid:
                return self.send_json({"error": "Missing notification ID."}, HTTPStatus.BAD_REQUEST)
            dismiss_notification(nid)
            return self.send_json({"dismissed": True, "id": nid})

        if path == "/api/chat":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            chat_pid = str((query.get("project_id") or ["default"])[0] or "default")
            write_chat_history([], chat_pid)
            append_event({"at": now_iso(), "type": "chat_cleared", "project_id": chat_pid})
            return self.send_json({"cleared": True, "project_id": chat_pid, "messages": []})

        self.send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)

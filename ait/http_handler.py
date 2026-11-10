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
        if path.startswith("/static/"):
            asset_path = urllib.parse.unquote(path[len("/static/"):])
            try:
                asset_file = resolve_static_asset(asset_path)
            except ValueError:
                return self.send_json({"error": "File not found."}, HTTPStatus.NOT_FOUND)
            return self.serve_file(asset_file, static_content_type(asset_file))

        if path == "/api/config":
            chat_provider = get_chat_provider()
            return self.send_json(
                {
                    "chat_provider": chat_provider,
                    "chat_model": chat_provider_model(chat_provider),
                    "chat_model_override": chat_model_override(chat_provider),
                    "chat_configured": bool(configured_providers()),
                    "providers": provider_names(),
                    "provider_options": task_provider_options(),
                }
            )

        if path == "/api/status":
            activity = provider_activity_status()
            working = activity.get("provider_working") or {}
            configured = configured_providers()
            providers_status = {
                name: {
                    "configured": name in configured,
                    "working": bool(working.get(name)),
                    "model": provider_model(name),
                }
                for name in provider_names()
            }
            return self.send_json(
                {
                    "cpu_percent": get_cpu_percent(),
                    "chat_working": ACTIVE_CHAT_COUNT > 0,
                    "providers": providers_status,
                    "github_configured": bool(get_provider_key("github")),
                    "watchdog": WATCHDOG_STATUS,
                }
            )

        if path == "/api/connections":
            conns = []
            for record in active_providers():
                val, src = get_provider_key_and_source(str(record["id"]))
                conns.append(
                    {
                        "id": str(record["id"]),
                        "name": str(record["name"]),
                        "configured": bool(val),
                        "source": src or "Not connected",
                        "kind": str(record.get("kind", "")),
                        "model": str(record.get("model", "")),
                        "base_url": str(record.get("base_url", "")),
                        "order": int(record.get("order", 0)),
                        "is_provider": True,
                    }
                )
            val, src = get_provider_key_and_source("github")
            conns.append(
                {
                    "id": "github",
                    "name": "GitHub repository connector",
                    "configured": bool(val),
                    "source": src or "Not connected",
                    "is_provider": False,
                }
            )
            return self.send_json(
                {
                    "connections": conns,
                    "presets": available_presets(),
                    "encryption_available": secret_cipher() is not None,
                }
            )

        if path == "/api/routing":
            return self.send_json(
                {
                    "roles": WORK_ROLES,
                    "providers": provider_names(),
                    "configured": configured_providers(),
                    "routing": get_routing(),
                    "max_preferred": MAX_PREFERRED_PROVIDERS,
                    "chat_provider": get_chat_provider(),
                    "autonomy": get_autonomy(),
                }
            )

        if handle_projects_get(self, path, query):
            return

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

        if handle_projects_get(self, path, query):
            return

        if path == "/api/documentation-files":
            try:
                file_path = query.get("file", [""])[0]
                folder_path = query.get("path", [""])[0]
                if file_path:
                    return self.send_json(read_local_text_file(DOCUMENTATION_DIR, file_path))
                return self.send_json(local_file_listing(DOCUMENTATION_DIR, folder_path))
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if handle_projects_get(self, path, query):
            return

        if handle_projects_get(self, path, query):
            return

        if handle_tasks_get(self, path, query):
            return

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

        if handle_projects_get(self, path, query):
            return

        if handle_projects_get(self, path, query):
            return

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

        m_prov_models = re.fullmatch(r"/api/providers/([a-z0-9-]+)/models", path)
        if m_prov_models:
            try:
                return self.send_json(list_provider_models(m_prov_models.group(1)))
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

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

        if handle_tasks_post(self, path, body):
            return
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
                    if chat_provider != "Auto" and chat_provider not in provider_names():
                        raise ValueError("Choose a valid chat provider.")
                    save_app_config({"chat_provider": chat_provider})
                append_event({"at": now_iso(), "type": "routing_updated"})
                DISPATCH_EVENT.set()
                return self.send_json(
                    {
                        "routing": routing if routing is not None else get_routing(),
                        "chat_provider": get_chat_provider(),
                        "chat_model": chat_provider_model(get_chat_provider()),
                        "chat_model_override": chat_model_override(get_chat_provider()),
                        "autonomy": autonomy if autonomy is not None else get_autonomy(),
                    }
                )
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_prov_test_model = re.fullmatch(r"/api/providers/([a-z0-9-]+)/test-model", path)
        if m_prov_test_model:
            try:
                result = test_provider_model(m_prov_test_model.group(1), str(body.get("model", "")).strip())
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            append_event({"at": now_iso(), "type": "provider_model_tested", "provider": result.get("provider"), "model": result.get("model")})
            return self.send_json(result)

        if path == "/api/providers":
            preset = str(body.get("preset", "")).strip()
            name = str(body.get("name", "")).strip()
            base_url = str(body.get("base_url", "")).strip()
            model = str(body.get("model", "")).strip()
            try:
                record = add_provider(preset_id=preset, name=name, base_url=base_url, model=model)
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            append_event({"at": now_iso(), "type": "provider_added", "provider": record["name"]})
            reconcile_provider_workers()
            DISPATCH_EVENT.set()
            return self.send_json({"provider": record})

        if path == "/api/providers/order":
            try:
                providers = reorder_providers(body.get("order"))
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            append_event({"at": now_iso(), "type": "provider_order_updated"})
            reconcile_provider_workers()
            DISPATCH_EVENT.set()
            return self.send_json({"providers": providers})

        m_conn_test = re.fullmatch(r"/api/connections/([a-z0-9_-]+)/test", path)
        if m_conn_test:
            cid = m_conn_test.group(1)
            if cid != "github" and cid not in connection_specs():
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
            if provider not in provider_names() or not model or len(model) > 160:
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
                copied, deleted = sync_app_files(checkout_path, working_path)
                logger.info(
                    "App files synced from checkout=%s to working_dir=%s (copied=%s, deleted=%s)",
                    checkout_path, working_path, copied, deleted,
                )
                response = {
                    "success": True,
                    "operation": operation,
                    "copied_files": copied,
                    "deleted_files": deleted,
                    "message": f"Repository {operation} completed; synced {copied} file(s), removed {deleted} stale file(s).",
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

        if handle_tasks_post(self, path, body):
            return
        if handle_projects_post(self, path, body):
            return

        if handle_projects_post(self, path, body):
            return

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

        m_provider_put = re.fullmatch(r"/api/providers/([a-z0-9-]+)", path)
        if m_provider_put:
            pid = m_provider_put.group(1)
            model = str(body.get("model", "")).strip()
            try:
                record = update_provider_model(pid, model)
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            append_event({"at": now_iso(), "type": "provider_model_updated", "provider": record.get("name"), "model": record.get("model")})
            return self.send_json({"provider": record})

        m_conn = re.fullmatch(r"/api/connections/([a-z0-9_-]+)", path)
        if m_conn:
            cid = m_conn.group(1)
            if cid != "github" and cid not in connection_specs():
                return self.send_json({"error": "Unknown connection."}, HTTPStatus.NOT_FOUND)
            secret = str(body.get("secret", "")).strip()
            if not secret or len(secret) > 4000:
                return self.send_json({"error": "Provide a valid key or token."}, HTTPStatus.BAD_REQUEST)
            try:
                secrets = read_saved_secrets()
                secrets[cid] = secret
                write_saved_secrets(secrets)
                append_event({"at": now_iso(), "type": "connection_saved", "connection": cid})
                reconcile_provider_workers()
                DISPATCH_EVENT.set()
                return self.send_json({"saved": True, "id": cid})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if handle_projects_put(self, path, body):
            return

        if handle_tasks_put(self, path, body):
            return
        if handle_projects_put(self, path, body):
            return

        self.send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        path = urllib.parse.urlparse(self.path).path

        if handle_tasks_delete(self, path):
            return
        m_provider_del = re.fullmatch(r"/api/providers/([a-z0-9-]+)", path)
        if m_provider_del:
            pid = m_provider_del.group(1)
            try:
                record = remove_provider(pid)
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            cleanup_provider_references(str(record.get("name", "")))
            if str(record.get("id", "")).startswith("custom-"):
                saved = read_saved_secrets()
                if pid in saved:
                    saved.pop(pid)
                    write_saved_secrets(saved)
            append_event({"at": now_iso(), "type": "provider_removed", "provider": record.get("name")})
            reconcile_provider_workers()
            DISPATCH_EVENT.set()
            return self.send_json({"removed": record})

        m_conn = re.fullmatch(r"/api/connections/([a-z0-9_-]+)", path)
        if m_conn:
            cid = m_conn.group(1)
            if cid != "github" and cid not in connection_specs():
                return self.send_json({"error": "Unknown connection."}, HTTPStatus.NOT_FOUND)
            try:
                secrets = read_saved_secrets()
                secrets.pop(cid, None)
                write_saved_secrets(secrets)
                append_event({"at": now_iso(), "type": "connection_cleared", "connection": cid})
                reconcile_provider_workers()
                DISPATCH_EVENT.set()
                return self.send_json({"deleted": True, "id": cid})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if handle_projects_delete(self, path):
            return

        if handle_projects_delete(self, path):
            return

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

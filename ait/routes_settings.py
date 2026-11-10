"""Settings, prompt, notification, and app-update API routes."""

from __future__ import annotations

from ait.common import *


def handle_settings_get(handler, path, query) -> bool:
    if path == "/api/documentation-files":
        try:
            file_path = query.get("file", [""])[0]
            folder_path = query.get("path", [""])[0]
            if file_path:
                handler.send_json(read_local_text_file(DOCUMENTATION_DIR, file_path))
                return True
            handler.send_json(local_file_listing(DOCUMENTATION_DIR, folder_path))
            return True
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True

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
        handler.send_json({"prompts": prompts})
        return True

    if path == "/api/notifications":
        handler.send_json({"notifications": get_notifications()})
        return True

    if path == "/api/app-update/check":
        force = str(query.get("force", [""])[0]).lower() in ("1", "true", "yes")
        handler.send_json({"update": app_update_status(force=force)})
        return True

    if path == "/api/settings/logs":
        handler.send_json({"logs": [], "info": "Use POST to load logs with filters"})
        return True

    if path == "/api/settings/config":
        config = read_app_config()
        handler.send_json({"config": config})
        return True

    return False


def handle_settings_post(handler, path, body) -> bool:
    m_prompt_reset = re.fullmatch(r"/api/prompts/([a-z_]+)/reset", path)
    if m_prompt_reset:
        slug = m_prompt_reset.group(1)
        if slug not in PROMPT_FILES:
            handler.send_json({"error": "Prompt not found."}, HTTPStatus.NOT_FOUND)
            return True
        try:
            default_txt = reset_prompt_file(slug)
            append_event({"at": now_iso(), "type": "prompt_reset", "prompt": slug})
            handler.send_json(
                {
                    "saved": True,
                    "id": slug,
                    "title": PROMPT_FILES[slug],
                    "content": default_txt,
                    "default_content": default_txt,
                    "is_custom": False,
                }
            )
            return True
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True

    if path == "/api/settings/logs":
        level = str(body.get("level", "all")).strip().upper()
        level_number = 0 if level == "ALL" else LOG_LEVELS.get(level)
        if level_number is None:
            handler.send_json({"error": "Choose a valid log display level."}, HTTPStatus.BAD_REQUEST)
            return True
        try:
            lines = int(body.get("lines", 1000))
        except (TypeError, ValueError):
            handler.send_json({"error": "Log line count must be an integer."}, HTTPStatus.BAD_REQUEST)
            return True
        if not 1 <= lines <= 5000:
            handler.send_json({"error": "Log line count must be between 1 and 5000."}, HTTPStatus.BAD_REQUEST)
            return True
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
            handler.send_json({"logs": list(reversed(newest_lines))})
            return True
        except Exception as exc:
            logger.exception("Failed to load application logs")
            handler.send_json({"error": f"Failed to read logs: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return True

    if path == "/api/settings/log-level":
        level = normalize_log_level(body.get("level"))
        if not level:
            handler.send_json({"error": "Choose a valid log capture level."}, HTTPStatus.BAD_REQUEST)
            return True
        config = read_app_config()
        config["log_level"] = level
        try:
            save_app_config(config)
            configure_file_log_level(level)
            handler.send_json({"log_level": level})
            return True
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return True

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
            handler.send_json({"cleared": True})
            return True
        except Exception as exc:
            logger.exception("Failed to clear application logs")
            handler.send_json({"error": f"Failed to clear logs: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return True

    if path == "/api/settings/restart":
        logger.info("App restart requested by user")
        handler.send_json({"success": True, "message": "Service restart scheduled."})
        threading.Timer(2.0, restart_service_process).start()
        return True

    if path == "/api/settings/update":
        app_repo = str(body.get("app_repo", "")).strip()
        app_clone_dir = str(body.get("app_clone_dir", "")).strip()
        app_working_dir = str(body.get("app_working_dir", "")).strip()
        if not app_repo or not app_clone_dir or not app_working_dir:
            handler.send_json(
                {"error": "App repository, local checkout directory, and working directory are required."},
                HTTPStatus.BAD_REQUEST,
            )
            return True
        if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", app_repo):
            handler.send_json({"error": "Invalid GitHub repository URL."}, HTTPStatus.BAD_REQUEST)
            return True

        mode = str(body.get("mode", "update")).strip().lower()
        if mode not in ("update", "repair"):
            handler.send_json({"error": "Unknown update mode."}, HTTPStatus.BAD_REQUEST)
            return True
        if mode == "update":
            status = app_update_status(force=True)
            if status.get("error") or not status.get("latest"):
                detail = str(status.get("error") or "remote version could not be determined")
                logger.error("App update blocked: version check failed: %s", detail)
                append_event({"at": now_iso(), "type": "app_update_check_failed", "error": detail[:300]})
                handler.send_json({"error": f"Could not check for updates: {detail}"}, HTTPStatus.BAD_GATEWAY)
                return True
            if not status.get("update_available"):
                handler.send_json(
                    {
                        "up_to_date": True,
                        "current": status.get("current"),
                        "latest": status.get("latest"),
                        "message": "Up to Date",
                    }
                )
                return True

        try:
            logger.info(
                "App update requested: repo=%s checkout=%s working_dir=%s",
                app_repo, app_clone_dir, app_working_dir,
            )
            checkout_path = Path(app_clone_dir).expanduser().resolve()
            working_path = Path(app_working_dir).expanduser().resolve()
            if not checkout_path.is_absolute() or not working_path.is_absolute():
                handler.send_json(
                    {"error": "The local checkout and app working directory must be absolute paths."},
                    HTTPStatus.BAD_REQUEST,
                )
                return True
            if checkout_path == Path(checkout_path.anchor) or working_path == Path(working_path.anchor):
                handler.send_json(
                    {"error": "A filesystem root cannot be used as an app directory."},
                    HTTPStatus.BAD_REQUEST,
                )
                return True
            if not working_path.is_dir():
                handler.send_json({"error": f"Working directory does not exist: {app_working_dir}"}, HTTPStatus.BAD_REQUEST)
                return True
            if (
                checkout_path == working_path
                or checkout_path.is_relative_to(working_path)
                or working_path.is_relative_to(checkout_path)
            ):
                handler.send_json(
                    {"error": "The local checkout directory and app working directory must not overlap."},
                    HTTPStatus.BAD_REQUEST,
                )
                return True
            
            token = get_provider_key("github")
            if not token:
                handler.send_json({"error": "GitHub token is not configured."}, HTTPStatus.BAD_REQUEST)
                return True
            
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
            handler.send_json(response)
            threading.Timer(2.0, restart_service_process).start()
            return True
        except PermissionError as exc:
            logger.exception("App update permission failure")
            handler.send_json(
                {
                    "error": (
                        f"Permission denied at '{exc.filename or app_clone_dir or app_working_dir}'. "
                        "Grant the ubuntu service user filesystem access and add the exact path "
                        "to systemd ReadWritePaths."
                    )
                },
                HTTPStatus.FORBIDDEN,
            )
            return True
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
            handler.send_json({"error": detail}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return True
        except subprocess.TimeoutExpired:
            logger.exception("App update command timed out")
            handler.send_json({"error": "Update command timed out."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return True
        except ValueError as exc:
            logger.exception("App update failed")
            handler.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return True
        except Exception as exc:
            logger.exception("App update failed unexpectedly")
            handler.send_json({"error": f"Update failed: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return True

    if path == "/api/settings/config":
        try:
            config = body
            save_app_config(config)
            handler.send_json({"success": True, "config": config})
            return True
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True

    return False


def handle_settings_put(handler, path, body) -> bool:
    m_prompt = re.fullmatch(r"/api/prompts/([a-z_]+)", path)
    if m_prompt:
        slug = m_prompt.group(1)
        if slug not in PROMPT_FILES:
            handler.send_json({"error": "Prompt not found."}, HTTPStatus.NOT_FOUND)
            return True
        content = body.get("content")
        if not isinstance(content, str) or not content.strip():
            handler.send_json({"error": "Prompt text cannot be empty."}, HTTPStatus.BAD_REQUEST)
            return True
        if len(content) > 20000:
            handler.send_json({"error": "Prompt text must be 20,000 characters or fewer."}, HTTPStatus.BAD_REQUEST)
            return True
        saved_txt = save_prompt_file(slug, content)
        default_txt = load_default_prompt(slug)
        append_event({"at": now_iso(), "type": "prompt_saved", "prompt": slug})
        handler.send_json(
            {
                "saved": True,
                "id": slug,
                "title": PROMPT_FILES[slug],
                "content": saved_txt,
                "default_content": default_txt,
                "is_custom": saved_txt != default_txt,
            }
        )
        return True

    return False


def handle_settings_delete(handler, path) -> bool:
    if path.startswith("/api/notifications/"):
        nid = urllib.parse.unquote(path[len("/api/notifications/") :])
        if not nid:
            handler.send_json({"error": "Missing notification ID."}, HTTPStatus.BAD_REQUEST)
            return True
        dismiss_notification(nid)
        handler.send_json({"dismissed": True, "id": nid})
        return True

    return False


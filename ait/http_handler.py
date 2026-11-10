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

        if handle_providers_get(self, path, query):
            return

        if handle_providers_get(self, path, query):
            return

        if handle_projects_get(self, path, query):
            return

        if handle_git_get(self, path, query):
            return

        if handle_git_get(self, path, query):
            return

        if handle_projects_get(self, path, query):
            return

        if handle_settings_get(self, path, query):
            return

        if handle_projects_get(self, path, query):
            return

        if handle_projects_get(self, path, query):
            return

        if handle_tasks_get(self, path, query):
            return

        if handle_settings_get(self, path, query):
            return

        if handle_projects_get(self, path, query):
            return

        if handle_projects_get(self, path, query):
            return

        if handle_settings_get(self, path, query):
            return

        if handle_settings_get(self, path, query):
            return

        if handle_providers_get(self, path, query):
            return

        if handle_providers_get(self, path, query):
            return

        if handle_settings_get(self, path, query):
            return

        if handle_settings_get(self, path, query):
            return

        if path == "/api/chat":
            chat_pid = str((query.get("project_id") or ["default"])[0] or "default")
            return self.send_json({"messages": read_chat_history(chat_pid), "project_id": chat_pid})

        if handle_providers_get(self, path, query):
            return

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
        if handle_settings_post(self, path, body):
            return

        if handle_providers_post(self, path, body):
            return

        if handle_providers_post(self, path, body):
            return

        if handle_providers_post(self, path, body):
            return

        if handle_providers_post(self, path, body):
            return

        if handle_providers_post(self, path, body):
            return

        if handle_providers_post(self, path, body):
            return

        if handle_providers_post(self, path, body):
            return

        if handle_git_post(self, path, body):
            return

        # Settings API endpoints
        if handle_settings_post(self, path, body):
            return

        if handle_settings_post(self, path, body):
            return

        if handle_settings_post(self, path, body):
            return

        if handle_settings_post(self, path, body):
            return

        if handle_settings_post(self, path, body):
            return

        if handle_settings_post(self, path, body):
            return

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

        if handle_settings_put(self, path, body):
            return

        if handle_providers_put(self, path, body):
            return

        if handle_providers_put(self, path, body):
            return

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
        if handle_providers_delete(self, path):
            return

        if handle_providers_delete(self, path):
            return

        if handle_projects_delete(self, path):
            return

        if handle_projects_delete(self, path):
            return

        if handle_settings_delete(self, path):
            return

        if path == "/api/chat":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            chat_pid = str((query.get("project_id") or ["default"])[0] or "default")
            write_chat_history([], chat_pid)
            append_event({"at": now_iso(), "type": "chat_cleared", "project_id": chat_pid})
            return self.send_json({"cleared": True, "project_id": chat_pid, "messages": []})

        self.send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)

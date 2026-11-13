"""Provider, connection, routing, and usage API routes."""

from __future__ import annotations

from ait.common import *


def handle_providers_get(handler, path, query) -> bool:
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
        handler.send_json(
            {
                "connections": conns,
                "presets": available_presets(),
                "encryption_available": secret_cipher() is not None,
            }
        )
        return True

    if path == "/api/routing":
        handler.send_json(
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
        return True

    if path == "/api/provider-quality":
        handler.send_json({"quality": get_provider_quality()})
        return True

    if path == "/api/llm-usage":
        handler.send_json({"resets": read_ai_stat_resets()})
        return True

    m_prov_models = re.fullmatch(r"/api/providers/([a-z0-9-]+)/models", path)
    if m_prov_models:
        try:
            handler.send_json(list_provider_models(m_prov_models.group(1)))
            return True
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True

    if path == "/api/providers/health":
        handler.send_json({"agents": provider_health_status(refresh=True)})
        return True

    return False


def handle_providers_post(handler, path, body) -> bool:
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
            handler.send_json(
                {
                    "routing": routing if routing is not None else get_routing(),
                    "chat_provider": get_chat_provider(),
                    "chat_model": chat_provider_model(get_chat_provider()),
                    "chat_model_override": chat_model_override(get_chat_provider()),
                    "autonomy": autonomy if autonomy is not None else get_autonomy(),
                }
            )
            return True
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True

    m_prov_test_model = re.fullmatch(r"/api/providers/([a-z0-9-]+)/test-model", path)
    if m_prov_test_model:
        try:
            result = test_provider_model(m_prov_test_model.group(1), str(body.get("model", "")).strip())
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True
        append_event({"at": now_iso(), "type": "provider_model_tested", "provider": result.get("provider"), "model": result.get("model")})
        handler.send_json(result)
        return True

    if path == "/api/providers":
        preset = str(body.get("preset", "")).strip()
        name = str(body.get("name", "")).strip()
        base_url = str(body.get("base_url", "")).strip()
        model = str(body.get("model", "")).strip()
        try:
            record = add_provider(preset_id=preset, name=name, base_url=base_url, model=model)
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True
        append_event({"at": now_iso(), "type": "provider_added", "provider": record["name"]})
        reconcile_provider_workers()
        DISPATCH_EVENT.set()
        handler.send_json({"provider": record})
        return True

    if path == "/api/providers/order":
        try:
            providers = reorder_providers(body.get("order"))
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True
        append_event({"at": now_iso(), "type": "provider_order_updated"})
        reconcile_provider_workers()
        DISPATCH_EVENT.set()
        handler.send_json({"providers": providers})
        return True

    m_conn_test = re.fullmatch(r"/api/connections/([a-z0-9_-]+)/test", path)
    if m_conn_test:
        cid = m_conn_test.group(1)
        if cid != "github" and cid not in connection_specs():
            handler.send_json({"connected": False, "error": "Unknown connection."}, HTTPStatus.NOT_FOUND)
            return True
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
            handler.send_json(result)
            return True
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
            handler.send_json({"connected": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True

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
            handler.send_json({"repositories": repos})
            return True
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True

    if path == "/api/llm-usage/reset":
        provider = str(body.get("provider", "")).strip()
        model = str(body.get("model", "")).strip()
        period = str(body.get("period", "")).strip()
        bucket = str(body.get("bucket", "")).strip()
        patterns = {"today": r"^\d{4}-\d{2}-\d{2}$", "month": r"^\d{4}-\d{2}$", "year": r"^\d{4}$", "all": r"^all$"}
        if provider not in provider_names() or not model or len(model) > 160:
            handler.send_json({"error": "Choose a valid provider and model."}, HTTPStatus.BAD_REQUEST)
            return True
        if period not in patterns or not re.fullmatch(patterns[period], bucket):
            handler.send_json({"error": "Choose a valid statistic period."}, HTTPStatus.BAD_REQUEST)
            return True
        reset = {"provider": provider, "model": model, "period": period, "bucket": bucket, "reset_at": now_iso()}
        append_ai_stat_reset(reset)
        append_event({"at": reset["reset_at"], "type": "ai_statistic_reset", **reset})
        handler.send_json({"reset": reset})
        return True

    return False


def handle_providers_put(handler, path, body) -> bool:
    m_provider_put = re.fullmatch(r"/api/providers/([a-z0-9-]+)", path)
    if m_provider_put:
        pid = m_provider_put.group(1)
        model = str(body.get("model", "")).strip()
        try:
            record = update_provider_model(pid, model)
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True
        append_event({"at": now_iso(), "type": "provider_model_updated", "provider": record.get("name"), "model": record.get("model")})
        handler.send_json({"provider": record})
        return True

    m_conn = re.fullmatch(r"/api/connections/([a-z0-9_-]+)", path)
    if m_conn:
        cid = m_conn.group(1)
        if cid != "github" and cid not in connection_specs():
            handler.send_json({"error": "Unknown connection."}, HTTPStatus.NOT_FOUND)
            return True
        secret = str(body.get("secret", "")).strip()
        if not secret or len(secret) > 4000:
            handler.send_json({"error": "Provide a valid key or token."}, HTTPStatus.BAD_REQUEST)
            return True
        try:
            secrets = read_saved_secrets()
            secrets[cid] = secret
            write_saved_secrets(secrets)
            append_event({"at": now_iso(), "type": "connection_saved", "connection": cid})
            reconcile_provider_workers()
            DISPATCH_EVENT.set()
            handler.send_json({"saved": True, "id": cid})
            return True
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True

    return False


def handle_providers_delete(handler, path) -> bool:
    m_provider_del = re.fullmatch(r"/api/providers/([a-z0-9-]+)", path)
    if m_provider_del:
        pid = m_provider_del.group(1)
        try:
            record = remove_provider(pid)
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True
        cleanup_provider_references(str(record.get("name", "")))
        if str(record.get("id", "")).startswith("custom-"):
            saved = read_saved_secrets()
            if pid in saved:
                saved.pop(pid)
                write_saved_secrets(saved)
        append_event({"at": now_iso(), "type": "provider_removed", "provider": record.get("name")})
        reconcile_provider_workers()
        DISPATCH_EVENT.set()
        handler.send_json({"removed": record})
        return True

    m_conn = re.fullmatch(r"/api/connections/([a-z0-9_-]+)", path)
    if m_conn:
        cid = m_conn.group(1)
        if cid != "github" and cid not in connection_specs():
            handler.send_json({"error": "Unknown connection."}, HTTPStatus.NOT_FOUND)
            return True
        try:
            secrets = read_saved_secrets()
            secrets.pop(cid, None)
            write_saved_secrets(secrets)
            append_event({"at": now_iso(), "type": "connection_cleared", "connection": cid})
            reconcile_provider_workers()
            DISPATCH_EVENT.set()
            handler.send_json({"deleted": True, "id": cid})
            return True
        except ValueError as exc:
            handler.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return True

    return False


"""PM/PO chat provider integration."""

from __future__ import annotations

from ait.common import *


# --- PM/PO Chat (hosted providers) ---
def get_chat_provider() -> str:
    configured = str(read_app_config().get("chat_provider") or "").strip()
    if configured == "Auto" or configured in provider_names():
        return configured or "Auto"
    return "Auto"


def get_chat_models() -> dict[str, str]:
    raw = read_app_config().get("chat_models")
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v).strip() for k, v in raw.items() if str(v).strip()}


def chat_model_override(provider: str) -> str:
    if provider == "Auto":
        return ""
    return get_chat_models().get(provider, "")


def set_chat_model(provider: str, model: str) -> None:
    models = get_chat_models()
    clean = str(model or "").strip()
    if clean:
        models[provider] = clean
    else:
        models.pop(provider, None)
    save_app_config({"chat_models": models})


def chat_provider_model(provider: str) -> str:
    if provider == "Auto":
        provider = select_auto_provider() or (configured_providers()[0] if configured_providers() else "")
    if not provider:
        return ""
    return chat_model_override(provider) or provider_model(provider)


def chat_system_prompt(project_id: str) -> str:
    board = [
        task for task in read_tasks()
        if str(task.get("project_id") or "default") == project_id
    ]
    board_text = (
        "\n".join(f"- {t['id']} | {t.get('status')} | {t.get('assigned_role')} | {t.get('title')}" for t in board[:50])
        or "(No tasks have been created.)"
    )
    return "\n\n".join(
        p
        for p in [
            combined_agent_prompt("PM/PO"),
            "Chat mode: you are talking directly with the user in a chat window, not processing a formal planning request. "
            "Answer conversationally in clear markdown. Do not return the planning JSON schema unless the user explicitly asks you to produce a plan; "
            "when they do, remind them that plans become tasks when created from the board.",
            project_context_for_role("PM/PO", {"project_id": project_id}),
            f"Current task board (read-only context):\n{board_text}",
        ]
        if p
    )


def call_chat_provider(context_messages: list[dict[str, Any]], project_id: str) -> tuple[str, str, str]:
    """Send the chat history to the configured chat provider. Returns (reply, provider, model)."""
    configured = configured_providers()
    if not configured:
        raise ProviderFailure("Chat", 401, "No AI provider is connected. Add a key in Settings → Connections.")
    preferred = get_chat_provider()
    if preferred == "Auto":
        provider = select_auto_provider() or configured[0]
    else:
        provider = preferred if preferred in configured else (select_auto_provider() or configured[0])
    model = chat_provider_model(provider)
    sys_prompt = chat_system_prompt(project_id)
    history = [
        {"role": m["role"], "content": str(m.get("content", ""))}
        for m in context_messages[-16:]
        if m.get("role") in ("user", "assistant") and str(m.get("content", "")).strip()
    ]

    if provider_kind(provider) == "gemini":
        api_key = get_provider_key("gemini")
        while history and history[0]["role"] != "user":
            history.pop(0)
        merged: list[dict[str, str]] = []
        for m in history:
            if merged and merged[-1]["role"] == m["role"]:
                merged[-1]["content"] += "\n\n" + m["content"]
            else:
                merged.append(dict(m))
        contents = [
            {
                "role": "model" if m["role"] == "assistant" else "user",
                "parts": [{"text": m["content"]}],
            }
            for m in merged
        ]
        payload = {
            "systemInstruction": {"parts": [{"text": sys_prompt}]},
            "contents": contents,
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048},
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model or GEMINI_MODEL, safe='')}:generateContent?key={urllib.parse.quote(api_key, safe='')}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": HTTP_USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:1500]
            raise ProviderFailure("Gemini", exc.code, body, exc.headers.get("Retry-After")) from exc
        except Exception as exc:
            raise ProviderFailure("Gemini", 503, str(exc)[:1000]) from exc
        candidates = result.get("candidates") or []
        parts = (((candidates[0] if candidates else {}).get("content") or {}).get("parts")) or []
        text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict)).strip()
        if not text:
            raise ProviderFailure("Gemini", 400, "Gemini returned no text.")
        return text, provider, model

    cfg = openai_compat_config(provider)
    if not cfg:
        raise ProviderFailure(provider, 400, f"Unsupported chat provider: {provider}")
    conn_id = cfg["conn_id"]
    api_key = get_provider_key(conn_id)
    endpoint = cfg["endpoint"]
    messages: list[dict[str, str]] = [{"role": "system", "content": sys_prompt}]
    messages.extend(history)
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": 2048,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": HTTP_USER_AGENT,
    }
    if cfg.get("openrouter"):
        headers["X-Title"] = "Personal AI Team"
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw).get("error", {}).get("message") or raw
        except Exception:
            detail = raw
        raise ProviderFailure(provider, exc.code, str(detail)[:1500], exc.headers.get("Retry-After")) from exc
    except Exception as exc:
        raise ProviderFailure(provider, 503, str(exc)[:1000]) from exc
    choices = result.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    msg = choice.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        content = "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    text = str(content or "").strip()
    if not text:
        raise ProviderFailure(provider, 400, f"{provider} returned no text.")
    return text, provider, model


def clean_overview_documents(body: dict[str, Any]) -> dict[str, str]:
    docs = body.get("documents")
    if not isinstance(docs, dict):
        raise ValueError("Project brief, technical overview, and decisions are required.")
    cleaned: dict[str, str] = {}
    for slug in ("project_brief", "technical_overview", "decisions"):
        val = docs.get(slug)
        if not isinstance(val, str) or not val.strip() or len(val) > 20000:
            raise ValueError(f"{PROJECT_CONTEXT_FILES[slug]} must contain text under 20,000 characters.")
        cleaned[slug] = val.strip()
    return cleaned

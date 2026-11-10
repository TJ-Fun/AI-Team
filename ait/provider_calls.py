"""Provider HTTP calls and connection/model testing."""

from __future__ import annotations

from ait.common import *


def call_gemini_task(task: dict[str, Any], extra_context: str = "") -> tuple[str, dict[str, Any]]:
    api_key = get_provider_key("gemini")
    if not api_key:
        raise ProviderFailure("Gemini", 401, "Gemini API key is not configured.")
    role = str(task.get("assigned_role") or "PM/PO")
    checks = task.get("acceptance_checks") or []
    ctx = project_context_for_role(role, task)
    prompt = (
        (f"Project context (confirmed reference information):\n{ctx}\n\n" if ctx else "")
        + f"Task ID: {task['id']}\nAssigned role: {role}\nProvider: Gemini\nRequest:\n{task.get('request', '')}\n\n"
        + "Acceptance checks:\n"
        + ("\n".join(f"- {c}" for c in checks) if checks else "- None specified")
    )
    prompt += build_repo_agent_instructions(task, "Gemini")
    if needs_work_reply_envelope(task):
        prompt += "\n\n" + WORK_REPLY_CONTRACT
    contract = planning_contract_for_task(task)
    if contract:
        prompt += "\n\n" + contract
    if (task.get("validation") or {}).get("status") == "failed":
        prompt += "\n\nPrevious attempt failed validation checks:\n" + json.dumps(task["validation"].get("checks", []), indent=2)
    if extra_context:
        prompt += "\n\n" + extra_context

    sys_inst = combined_agent_prompt(role) + (("\n\n" + contract) if contract else "")
    model = provider_model("Gemini")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:generateContent?key={urllib.parse.quote(api_key, safe='')}"
    payload = {
        "systemInstruction": {"parts": [{"text": sys_inst}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2},
    }
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
        with urllib.request.urlopen(req, timeout=180) as resp:
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
    return text, {"usage_metadata": result.get("usageMetadata")}


def normalize_github_repo_url(raw_repo: str) -> str:
    cleaned = (raw_repo or "").strip().rstrip("/")
    if not cleaned:
        return ""
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", cleaned):
        return f"https://github.com/{cleaned}"
    return cleaned


def call_openai_compatible_task(task: dict[str, Any], provider: str, extra_context: str = "") -> tuple[str, str, dict[str, Any]]:
    cfg = openai_compat_config(provider)
    if not cfg:
        raise ProviderFailure(provider, 400, f"Unsupported provider: {provider}")
    conn_id = cfg["conn_id"]
    api_key = get_provider_key(conn_id)
    if not api_key:
        raise ProviderFailure(provider, 401, f"{provider} API key is not configured.")
    model = cfg["model"]
    endpoint = cfg["endpoint"]
    role = str(task.get("assigned_role") or "PM/PO")
    checks = task.get("acceptance_checks") or []
    ctx = project_context_for_role(role, task)
    prompt = (
        (f"Project context (confirmed reference information):\n{ctx}\n\n" if ctx else "")
        + f"Task ID: {task['id']}\nAssigned role: {role}\nProvider: {provider}\nRequest:\n{task.get('request', '')}\n\n"
        + "Acceptance checks:\n"
        + ("\n".join(f"- {c}" for c in checks) if checks else "- None specified")
    )
    prompt += build_repo_agent_instructions(task, provider)
    if needs_work_reply_envelope(task):
        prompt += "\n\n" + WORK_REPLY_CONTRACT
    contract = planning_contract_for_task(task)
    if contract:
        prompt += "\n\n" + contract
    if (task.get("validation") or {}).get("status") == "failed":
        prompt += "\n\nPrevious attempt failed validation checks:\n" + json.dumps(task["validation"].get("checks", []), indent=2)
    if extra_context:
        prompt += "\n\n" + extra_context

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": combined_agent_prompt(role) + (("\n\n" + contract) if contract else ""),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    if cfg["max_tokens_key"] == "max_completion_tokens":
        payload["max_completion_tokens"] = int(cfg["fixed_max_tokens"])
    else:
        payload["max_tokens"] = 4096 if task.get("task_type") == "pm_planning" else 2048

    req_headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": HTTP_USER_AGENT,
    }
    if cfg.get("openrouter"):
        req_headers["X-Title"] = "Personal AI Team"

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=req_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=150) as resp:
            result = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw).get("error", {}).get("message") or raw
        except Exception:
            detail = raw
        detail_str = str(detail).strip()
        if exc.code == 403 and ("1010" in raw or "1020" in raw):
            detail_str = f"{detail_str} (Cloudflare blocked the request signature or VM VPN/proxy IP)"
        raise ProviderFailure(provider, exc.code, detail_str[:1500], exc.headers.get("Retry-After")) from exc
    except Exception as exc:
        raise ProviderFailure(provider, 503, str(exc)[:1000]) from exc

    choices = result.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    msg = choice.get("message") or {}
    content = msg.get("content")
    if content is None:
        content = ""
    elif isinstance(content, list):
        content = "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    text = str(content).strip()
    if not text:
        finish_hint = (
            " Token limit was reached before visible text was emitted."
            if choice.get("finish_reason") == "length"
            else ""
        )
        raise ProviderFailure(provider, 400, f"{provider} returned no text.{finish_hint}")
    return text, str(result.get("model") or model), {"usage": result.get("usage"), "finish_reason": choice.get("finish_reason")}


def test_provider_connection(conn_id: str) -> dict[str, Any]:
    if conn_id == "github":
        user = github_api("/user")
        login = str(user.get("login", ""))
        return {
            "connected": True,
            "provider": "GitHub",
            "login": login,
            "message": f"GitHub connected as {login}.",
        }

    record = provider_record_by_id(conn_id)
    if not record:
        raise ValueError("Unknown connection.")
    name = str(record.get("name", ""))
    api_key = get_provider_key(conn_id)
    if not api_key:
        raise ValueError(f"{name} key is not configured.")

    if record.get("kind") == "gemini":
        model = provider_model(name)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}?key={urllib.parse.quote(api_key, safe='')}"
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": HTTP_USER_AGENT},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")[:400]
            raise ValueError(f"{name} HTTP {exc.code}: {raw}") from exc
        except Exception as exc:
            raise ValueError(f"{name} request failed: {exc}") from exc
        return {
            "connected": True,
            "provider": name,
            "model": model,
            "message": f"{name} connected (model: {model}).",
        }

    if conn_id == "openrouter":
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/auth/key",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": HTTP_USER_AGENT,
                "X-Title": "Personal AI Team",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")[:400]
            raise ValueError(f"{name} HTTP {exc.code}: {raw}") from exc
        except Exception as exc:
            raise ValueError(f"{name} request failed: {exc}") from exc
        model = provider_model(name)
        return {
            "connected": True,
            "provider": name,
            "model": model,
            "message": f"{name} connected (default model: {model}).",
        }

    base = str(record.get("base_url", "")).strip().rstrip("/")
    model = provider_model(name)
    req = urllib.request.Request(
        f"{base}/models",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": HTTP_USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            parsed = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")[:400]
        raise ValueError(f"{name} HTTP {exc.code}: {raw}") from exc
    except Exception as exc:
        raise ValueError(f"{name} request failed: {exc}") from exc
    models = [str(m.get("id", "")) for m in (parsed.get("data") or []) if isinstance(m, dict)]
    return {
        "connected": True,
        "provider": name,
        "model": model,
        "model_available": (model in models) if models else None,
        "message": f"{name} connected ({len(models)} models listed; configured model '{model}').",
    }


def list_provider_models(conn_id: str) -> dict[str, Any]:
    """Fetch selectable models for a provider; selectable=False when the endpoint cannot list them."""
    record = provider_record_by_id(conn_id)
    if not record:
        raise ValueError("Unknown provider.")
    name = str(record.get("name", ""))
    current = provider_model(name)
    api_key = get_provider_key(str(record.get("id", "")))
    if not api_key:
        return {"provider": name, "current": current, "models": [], "selectable": False, "error": "Provider is not connected."}
    kind = str(record.get("kind", ""))
    try:
        if kind == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={urllib.parse.quote(api_key, safe='')}&pageSize=200"
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": HTTP_USER_AGENT},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                parsed = json.loads(resp.read().decode("utf-8", errors="replace"))
            models = []
            for item in parsed.get("models") or []:
                if not isinstance(item, dict):
                    continue
                methods = item.get("supportedGenerationMethods") or []
                if "generateContent" not in methods:
                    continue
                mid = str(item.get("name", "")).removeprefix("models/").strip()
                if mid:
                    models.append(mid)
            models.sort()
        else:
            base = str(record.get("base_url", "")).strip().rstrip("/")
            req = urllib.request.Request(
                f"{base}/models",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "User-Agent": HTTP_USER_AGENT,
                },
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                parsed = json.loads(resp.read().decode("utf-8", errors="replace"))
            models = sorted({str(m.get("id", "")).strip() for m in (parsed.get("data") or []) if isinstance(m, dict) and str(m.get("id", "")).strip()})
        if not models:
            return {"provider": name, "current": current, "models": [], "selectable": False, "error": "Endpoint did not list any models."}
        return {"provider": name, "current": current, "models": models, "selectable": True}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")[:200]
        return {"provider": name, "current": current, "models": [], "selectable": False, "error": f"HTTP {exc.code}: {raw}"}
    except Exception as exc:
        return {"provider": name, "current": current, "models": [], "selectable": False, "error": str(exc)[:200]}


def test_provider_model(pid: str, model: str = "") -> dict[str, Any]:
    """Send a minimal 'Hi' prompt to the selected model and return its reply."""
    record = provider_record_by_id(pid)
    if not record:
        raise ValueError("Unknown provider.")
    name = str(record.get("name", ""))
    api_key = get_provider_key(str(record.get("id", "")))
    if not api_key:
        raise ValueError(f"{name} is not connected.")
    clean_model = str(model or "").strip() or provider_model(name)
    if record.get("kind") == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(clean_model, safe='')}:generateContent?key={urllib.parse.quote(api_key, safe='')}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": "Hi"}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 64},
        }
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
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")[:300]
            raise ValueError(f"{name} HTTP {exc.code}: {raw}") from exc
        except Exception as exc:
            raise ValueError(f"{name} request failed: {exc}") from exc
        candidates = result.get("candidates") or []
        parts = (((candidates[0] if candidates else {}).get("content") or {}).get("parts")) or []
        reply = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict)).strip()
        if not reply:
            raise ValueError(f"{name} returned no text for model {clean_model}.")
        return {"provider": name, "model": clean_model, "reply": reply[:1000]}

    base = str(record.get("base_url", "")).strip().rstrip("/")
    if not base:
        raise ValueError(f"{name} has no base URL configured.")
    payload = {
        "model": clean_model,
        "messages": [{"role": "user", "content": "Hi"}],
        "temperature": 0,
        "max_tokens": 64,
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": HTTP_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")[:300]
        raise ValueError(f"{name} HTTP {exc.code}: {raw}") from exc
    except Exception as exc:
        raise ValueError(f"{name} request failed: {exc}") from exc
    choices = result.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    content = (choice.get("message") or {}).get("content")
    if isinstance(content, list):
        content = "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    reply = str(content or "").strip()
    if not reply:
        raise ValueError(f"{name} returned no text for model {clean_model}.")
    return {"provider": name, "model": clean_model, "reply": reply[:1000]}


def retry_after_time(header: str | None, fallback_hours: int = 2) -> str:
    if header:
        try:
            sec = min(max(0, int(float(header))), 48 * 3600)
            return (datetime.now(timezone.utc) + timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    return (datetime.now(timezone.utc) + timedelta(hours=fallback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

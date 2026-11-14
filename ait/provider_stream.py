"""Streaming provider task calls with idle-timeout SSE parsing.

Each provider kind adapts automatically: Gemini uses
``streamGenerateContent?alt=sse`` and every OpenAI-compatible provider uses
``chat/completions`` with ``stream: true``. The socket timeout applies per
blocking read, so every received chunk resets the idle window; only
``STREAM_IDLE_TIMEOUT_SECONDS`` of complete silence fails the attempt.
"""

from __future__ import annotations

from ait.common import *


class _StreamUnsupported(Exception):
    """Raised when an endpoint rejects streaming parameters."""


def _sse_json_events(resp) -> Any:
    """Yield parsed JSON payloads from an SSE response body."""
    for raw in resp:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        if not data:
            continue
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue


def _openai_error_detail(exc: urllib.error.HTTPError) -> str:
    raw = exc.read().decode("utf-8", errors="replace")
    try:
        detail = json.loads(raw).get("error", {}).get("message") or raw
    except Exception:
        detail = raw
    detail_str = str(detail).strip()
    if exc.code == 403 and ("1010" in raw or "1020" in raw):
        detail_str = f"{detail_str} (Cloudflare blocked the request signature or VM VPN/proxy IP)"
    return detail_str[:1500]


def _task_prompt(task: dict[str, Any], provider: str, extra_context: str) -> tuple[str, str, str]:
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
    return role, prompt, contract


def call_gemini_task(task: dict[str, Any], extra_context: str = "", model_override: str = "") -> tuple[str, dict[str, Any]]:
    api_key = get_provider_key("gemini")
    if not api_key:
        raise ProviderFailure("Gemini", 401, "Gemini API key is not configured.")
    role, prompt, contract = _task_prompt(task, "Gemini", extra_context)

    sys_inst = combined_agent_prompt(role) + (("\n\n" + contract) if contract else "")
    model = provider_model("Gemini", model_override)
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}"
        f":streamGenerateContent?alt=sse&key={urllib.parse.quote(api_key, safe='')}"
    )
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
            "Accept": "text/event-stream",
            "User-Agent": HTTP_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=STREAM_IDLE_TIMEOUT_SECONDS) as resp:
            parts: list[str] = []
            usage = None
            finish = None
            blocked = ""
            for chunk in _sse_json_events(resp):
                feedback = chunk.get("promptFeedback") or {}
                if feedback.get("blockReason") and not blocked:
                    blocked = str(feedback.get("blockReason"))
                if chunk.get("usageMetadata"):
                    usage = chunk["usageMetadata"]
                for cand in chunk.get("candidates") or []:
                    if not isinstance(cand, dict):
                        continue
                    if cand.get("finishReason"):
                        finish = str(cand["finishReason"])
                    for part in ((cand.get("content") or {}).get("parts")) or []:
                        if isinstance(part, dict) and part.get("text"):
                            parts.append(str(part["text"]))
            text = "".join(parts).strip()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:1500]
        raise ProviderFailure("Gemini", exc.code, body, exc.headers.get("Retry-After")) from exc
    except Exception as exc:
        raise ProviderFailure("Gemini", 503, f"Gemini stream stalled or failed: {exc}"[:1000]) from exc

    if not text:
        hint = f" (blocked: {blocked})" if blocked else ""
        raise ProviderFailure("Gemini", 400, f"Gemini returned no text.{hint}")
    return text, {"usage_metadata": usage, "finish_reason": finish, "streamed": True}


def _openai_payload(task: dict[str, Any], provider: str, cfg: dict[str, Any], role: str, prompt: str, contract: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": cfg["model"],
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
    return payload


def _openai_headers(provider: str, api_key: str, cfg: dict[str, Any]) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": HTTP_USER_AGENT,
    }
    if cfg.get("openrouter"):
        headers["X-Title"] = "Personal AI Team"
    return headers


def _stream_openai_compat(provider: str, endpoint: str, headers: dict[str, str], payload: dict[str, Any], include_usage: bool) -> tuple[str, str, dict[str, Any]]:
    body = dict(payload)
    body["stream"] = True
    if include_usage:
        body["stream_options"] = {"include_usage": True}
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={**headers, "Accept": "text/event-stream"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=STREAM_IDLE_TIMEOUT_SECONDS) as resp:
            parts: list[str] = []
            usage = None
            finish = None
            model_seen = ""
            for chunk in _sse_json_events(resp):
                if isinstance(chunk.get("usage"), dict):
                    usage = chunk["usage"]
                if chunk.get("model"):
                    model_seen = str(chunk["model"])
                for choice in chunk.get("choices") or []:
                    if not isinstance(choice, dict):
                        continue
                    if choice.get("finish_reason"):
                        finish = str(choice["finish_reason"])
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if isinstance(content, str):
                        parts.append(content)
                    elif isinstance(content, list):
                        parts.append("".join(str(p.get("text", "")) for p in content if isinstance(p, dict)))
            text = "".join(parts).strip()
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 404, 422):
            raise _StreamUnsupported(f"HTTP {exc.code}") from exc
        raise ProviderFailure(provider, exc.code, _openai_error_detail(exc), exc.headers.get("Retry-After")) from exc
    except _StreamUnsupported:
        raise
    except Exception as exc:
        raise ProviderFailure(provider, 503, f"{provider} stream stalled or failed: {exc}"[:1000]) from exc

    if not text:
        hint = " Token limit was reached before visible text was emitted." if finish == "length" else ""
        raise ProviderFailure(provider, 400, f"{provider} returned no text.{hint}")
    return text, model_seen or str(payload.get("model") or ""), {"usage": usage, "finish_reason": finish, "streamed": True}


def _blocking_openai_compat(provider: str, endpoint: str, headers: dict[str, str], payload: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=150) as resp:
            result = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raise ProviderFailure(provider, exc.code, _openai_error_detail(exc), exc.headers.get("Retry-After")) from exc
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
    return text, str(result.get("model") or payload.get("model") or ""), {
        "usage": result.get("usage"),
        "finish_reason": choice.get("finish_reason"),
        "streamed": False,
    }


def call_openai_compatible_task(task: dict[str, Any], provider: str, extra_context: str = "", model_override: str = "") -> tuple[str, str, dict[str, Any]]:
    cfg = openai_compat_config(provider, model_override)
    if not cfg:
        raise ProviderFailure(provider, 400, f"Unsupported provider: {provider}")
    conn_id = cfg["conn_id"]
    api_key = get_provider_key(conn_id)
    if not api_key:
        raise ProviderFailure(provider, 401, f"{provider} API key is not configured.")
    role, prompt, contract = _task_prompt(task, provider, extra_context)
    payload = _openai_payload(task, provider, cfg, role, prompt, contract)
    headers = _openai_headers(provider, api_key, cfg)
    endpoint = cfg["endpoint"]

    try:
        return _stream_openai_compat(provider, endpoint, headers, payload, include_usage=True)
    except _StreamUnsupported:
        logger.info("Provider %s rejected stream_options; retrying stream without usage", provider)
    try:
        return _stream_openai_compat(provider, endpoint, headers, payload, include_usage=False)
    except _StreamUnsupported:
        logger.info("Provider %s does not support streaming; using blocking call", provider)
    return _blocking_openai_compat(provider, endpoint, headers, payload)

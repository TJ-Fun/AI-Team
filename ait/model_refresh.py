"""Daily provider model list refresh, cache, and routing availability checks."""

from __future__ import annotations

from ait.common import *

MODEL_CACHE_FILE = DATA / "settings" / "model_cache.json"
MODEL_FLAGS_FILE = DATA / "settings" / "model_availability.json"
MODEL_REFRESH_INTERVAL_SECONDS = 24 * 60 * 60
MODEL_REFRESH_MAX_ATTEMPTS = 3
MODEL_REFRESH_BACKOFF_SECONDS = (30, 120, 300)
MODEL_CACHE_STALE_SECONDS = 24 * 60 * 60
MODEL_REFRESH_LOCK = threading.Lock()


def load_model_cache() -> dict[str, Any]:
    if MODEL_CACHE_FILE.is_file():
        try:
            data = json.loads(MODEL_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception as exc:
            logger.warning("Failed to read model cache: %s", str(exc)[:200])
    return {}


def save_model_cache(cache: dict[str, Any]) -> None:
    ensure_data()
    atomic_json(MODEL_CACHE_FILE, cache)


def load_model_flags() -> dict[str, Any]:
    if MODEL_FLAGS_FILE.is_file():
        try:
            data = json.loads(MODEL_FLAGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception as exc:
            logger.warning("Failed to read model availability flags: %s", str(exc)[:200])
    return {}


def save_model_flags(flags: dict[str, Any]) -> None:
    ensure_data()
    atomic_json(MODEL_FLAGS_FILE, flags)


def _flag_key(role: str, provider: str, model: str) -> str:
    return f"{role}|{provider}|{model}"


def _cache_age_seconds(entry: dict[str, Any]) -> float | None:
    ts = parse_iso_epoch(str(entry.get("fetched_at") or ""))
    if ts is None:
        return None
    return max(0.0, time.time() - ts)


def provider_models_response(pid: str) -> dict[str, Any]:
    """Payload for GET /api/providers/{id}/models: serve cache, live-fetch as fallback."""
    record = provider_record_by_id(pid)
    if not record:
        raise ValueError("Unknown provider.")
    pid = str(record.get("id", ""))
    name = str(record.get("name", ""))
    current = provider_model(name)
    entry = load_model_cache().get(pid)
    if entry and entry.get("selectable") and entry.get("models"):
        age = _cache_age_seconds(entry)
        return {
            "provider": name,
            "current": current,
            "models": list(entry.get("models") or []),
            "selectable": True,
            "fetched_at": entry.get("fetched_at"),
            "stale": bool(entry.get("error")) or (age is not None and age > MODEL_CACHE_STALE_SECONDS),
        }
    return list_provider_models(pid)


def refresh_provider_model_list(pid: str) -> dict[str, Any]:
    """Fetch one provider's model list with retries; update cache; keep last-good on failure."""
    with MODEL_REFRESH_LOCK:
        record = provider_record_by_id(pid)
        if not record:
            raise ValueError("Unknown provider.")
        pid = str(record.get("id", ""))
        name = str(record.get("name", ""))
        has_key = bool(get_provider_key(pid))
        result: dict[str, Any] | None = None
        last_error = ""
        attempts = 0
        max_attempts = MODEL_REFRESH_MAX_ATTEMPTS if has_key else 1
        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            result = list_provider_models(pid)
            if result.get("selectable") and result.get("models"):
                break
            last_error = str(result.get("error") or "endpoint did not list any models")
            if attempt < max_attempts:
                delay = MODEL_REFRESH_BACKOFF_SECONDS[min(attempt - 1, len(MODEL_REFRESH_BACKOFF_SECONDS) - 1)]
                logger.info(
                    "Model list refresh for %s failed (attempt %s/%s): %s; retrying in %ss",
                    name, attempt, max_attempts, last_error[:200], delay,
                )
                time.sleep(delay)
        fetched_at = now_iso()
        cache = load_model_cache()
        prior = cache.get(pid) if isinstance(cache.get(pid), dict) else {}
        if result and result.get("selectable") and result.get("models"):
            cache[pid] = {
                "provider": name,
                "current": str(result.get("current") or ""),
                "models": list(result.get("models") or []),
                "selectable": True,
                "fetched_at": fetched_at,
                "error": None,
                "attempts": attempts,
            }
            append_event({
                "at": fetched_at, "type": "model_list_refreshed",
                "provider": name, "models": len(result.get("models") or []), "attempts": attempts,
            })
            logger.info("Model list refreshed for %s: %s models (attempts=%s)", name, len(result.get("models") or []), attempts)
        else:
            entry = dict(prior)
            entry.update({
                "provider": name,
                "fetched_at": fetched_at,
                "error": last_error or "refresh failed",
                "attempts": attempts,
            })
            if not entry.get("models"):
                entry["selectable"] = False
            cache[pid] = entry
            append_event({
                "at": fetched_at, "type": "model_list_refresh_failed",
                "provider": name, "error": (last_error or "refresh failed")[:300], "attempts": attempts,
            })
            logger.warning("Model list refresh failed for %s after %s attempt(s): %s", name, attempts, (last_error or "")[:300])
        save_model_cache(cache)
        return dict(cache[pid])


def refresh_all_provider_models() -> dict[str, Any]:
    """Refresh every connected provider, then re-check routing availability."""
    results: dict[str, Any] = {}
    for record in active_providers():
        pid = str(record.get("id", ""))
        if not pid or not get_provider_key(pid):
            continue
        try:
            results[pid] = refresh_provider_model_list(pid)
        except Exception as exc:
            logger.warning("Model list refresh crashed for %s: %s", record.get("name"), str(exc)[:200])
    try:
        check_routing_model_availability()
    except Exception as exc:
        logger.warning("Routing model availability check failed: %s", str(exc)[:200])
    return results


def check_routing_model_availability() -> dict[str, Any]:
    """Flag routing entries whose explicit model is missing from a fresh model list."""
    cache = load_model_cache()
    flags = load_model_flags()
    changed = False
    current_keys: set[str] = set()
    for role, entries in get_routing().items():
        for entry in entries:
            provider = str(entry.get("provider") or "")
            model = str(entry.get("model") or "auto")
            if not provider or provider == "Auto" or model in ("", "auto"):
                continue
            key = _flag_key(role, provider, model)
            current_keys.add(key)
            record = provider_record(provider)
            pid = str(record.get("id", "")) if record else ""
            cached = cache.get(pid) if pid else None
            if not isinstance(cached, dict):
                continue
            # Only judge against a list whose last refresh succeeded.
            if not cached.get("selectable") or not cached.get("models") or cached.get("error"):
                continue
            if model in (cached.get("models") or []):
                if key in flags:
                    del flags[key]
                    changed = True
                    append_event({
                        "at": now_iso(), "type": "model_unavailable_cleared",
                        "role": role, "provider": provider, "model": model,
                    })
                    logger.info("Model %s for %s is available again; cleared flag", model, provider)
            elif key not in flags:
                flags[key] = {
                    "role": role,
                    "provider": provider,
                    "model": model,
                    "since": now_iso(),
                    "reason": "not_in_model_list",
                }
                changed = True
                append_event({
                    "at": now_iso(), "type": "model_unavailable_flagged",
                    "role": role, "provider": provider, "model": model,
                })
                logger.warning("Model %s for %s no longer listed; flagged for role %s", model, provider, role)
    for key in list(flags):
        if key not in current_keys:
            flag = flags.pop(key)
            changed = True
            append_event({
                "at": now_iso(), "type": "model_unavailable_cleared",
                "role": flag.get("role"), "provider": flag.get("provider"),
                "model": flag.get("model"), "reason": "route_removed",
            })
            logger.info("Cleared model flag for removed route %s", key)
    if changed:
        save_model_flags(flags)
    return flags


def model_refresh_worker_loop() -> None:
    while True:
        try:
            refresh_all_provider_models()
        except Exception as exc:
            logger.warning("Model refresh pass failed: %s", str(exc)[:300])
        time.sleep(MODEL_REFRESH_INTERVAL_SECONDS)

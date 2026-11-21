"""Provider routing configuration, expansion, and per-task route resolution."""

from __future__ import annotations

from ait.common import *


# --- Routing ---
def _clean_route_model(value: Any) -> str:
    model = str(value or "").strip()
    if not model or model.lower() == "auto":
        return "auto"
    return model[:160]


def _clean_route_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        num = default
    return max(lo, min(hi, num))


def default_route_settings() -> dict[str, Any]:
    return {
        "timeout_seconds": DEFAULT_ROUTE_TIMEOUT_SECONDS,
        "retries": DEFAULT_ROUTE_RETRIES,
        "retry_delay_seconds": DEFAULT_ROUTE_RETRY_DELAY_SECONDS,
        "stream": True,
    }


def _route_settings_from(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "timeout_seconds": _clean_route_int(
            item.get("timeout_seconds"), DEFAULT_ROUTE_TIMEOUT_SECONDS, 60, 86400
        ),
        "retries": _clean_route_int(item.get("retries"), DEFAULT_ROUTE_RETRIES, 0, 10),
        "retry_delay_seconds": _clean_route_int(
            item.get("retry_delay_seconds"), DEFAULT_ROUTE_RETRY_DELAY_SECONDS, 0, 86400
        ),
        "stream": bool(item.get("stream", True)),
    }


def _routing_entries(raw: Any, active: set[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    items = raw if isinstance(raw, list) else []
    for item in items:
        if isinstance(item, dict):
            provider = str(item.get("provider", "")).strip()
            model = _clean_route_model(item.get("model"))
            settings = _route_settings_from(item)
        else:
            provider = str(item).strip()
            model = "auto"
            settings = default_route_settings()
        if provider == "Auto":
            model = "auto"
        elif provider not in active:
            continue
        entries.append({"provider": provider, "model": model, **settings})
        if provider == "Auto" or len(entries) >= MAX_ROUTE_ENTRIES:
            break
    return entries


def _with_model_flags(result: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    flags = load_model_flags()
    if flags:
        for role, entries in result.items():
            for entry in entries:
                key = f"{role}|{entry.get('provider')}|{entry.get('model')}"
                if key in flags:
                    entry["model_unavailable"] = True
    return result


def get_routing() -> dict[str, list[dict[str, Any]]]:
    ensure_data()
    routing_path = DATA / "settings" / "routing.json"
    saved: dict[str, Any] = {}
    if routing_path.is_file():
        try:
            saved = json.loads(routing_path.read_text(encoding="utf-8"))
        except Exception:
            saved = {}
    active = set(provider_names())
    result: dict[str, list[dict[str, Any]]] = {}
    for role in WORK_ROLES:
        raw = saved.get(role) if isinstance(saved, dict) else None
        result[role] = _routing_entries(raw, active)
    return _with_model_flags(result)


def save_routing(value: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, dict):
        raise ValueError("Routing settings must be an object.")
    active = set(provider_names())
    cleaned: dict[str, list[dict[str, Any]]] = {}
    for role in WORK_ROLES:
        order = value.get(role, [])
        if not isinstance(order, list):
            raise ValueError(f"Provider preference for {role} must be a list.")
        cleaned[role] = _routing_entries(order, active)
    atomic_json(DATA / "settings" / "routing.json", cleaned)
    return _with_model_flags(cleaned)


def route_entries_for_role(role: str) -> list[dict[str, Any]]:
    """Expanded routing chain for a role: explicit entries plus Auto fallback.

    Each entry carries provider, model, and per-combo settings (timeout_seconds,
    retries, retry_delay_seconds, stream). Duplicate provider+model pairs are
    deduplicated, keeping the first entry's settings.
    """
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(provider: str, model: str, settings: dict[str, Any] | None = None) -> None:
        key = (provider, model or "auto")
        if key not in seen:
            seen.add(key)
            entries.append({"provider": provider, "model": key[1], **(settings or default_route_settings())})

    for entry in get_routing().get(role, []):
        provider = entry["provider"]
        if provider == "Auto":
            for name in auto_provider_order():
                add(name, "auto")
        else:
            add(
                provider,
                entry.get("model") or "auto",
                {k: entry[k] for k in ("timeout_seconds", "retries", "retry_delay_seconds", "stream")},
            )
    for name in auto_provider_order():
        add(name, "auto")
    return entries


def route_combo_key(entry: dict[str, Any]) -> tuple[str, str]:
    return (str(entry.get("provider") or ""), str(entry.get("model") or "auto"))


def _combo_attempt_counts(
    task: dict[str, Any], role: str, start: int, default_role: str
) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for attempt in (task.get("attempts") or [])[start:]:
        if str(attempt.get("role") or default_role) != role:
            continue
        provider = str(attempt.get("provider") or "")
        if not provider:
            continue
        key = (provider, str(attempt.get("route_model") or "auto"))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _entry_retries(entry: dict[str, Any]) -> int:
    return _clean_route_int(entry.get("retries"), DEFAULT_ROUTE_RETRIES, 0, 10)


def _combo_exhausted(entry: dict[str, Any], counts: dict[tuple[str, str], int]) -> bool:
    """A combo is exhausted once its initial attempt plus all retries are used."""
    return counts.get(route_combo_key(entry), 0) > _entry_retries(entry)


def route_entry_for_role(task: dict[str, Any], role: str, cycle_key: str) -> dict[str, Any] | None:
    """First non-exhausted routing entry (provider + model + settings) for a role."""
    configured = configured_providers()
    attempts = task.get("attempts") or []
    start = max(0, int(task.get(cycle_key, len(attempts)) or 0))
    counts = _combo_attempt_counts(task, role, start, str(task.get("assigned_role") or ""))
    for entry in route_entries_for_role(role):
        if entry["provider"] in configured and not _combo_exhausted(entry, counts):
            return entry
    return None


def cleanup_provider_references(name: str) -> None:
    target = str(name or "").strip()
    if not target:
        return
    routing_path = DATA / "settings" / "routing.json"
    if routing_path.is_file():
        try:
            saved = json.loads(routing_path.read_text(encoding="utf-8"))
        except Exception:
            saved = {}
        if isinstance(saved, dict):
            changed = False
            for role in WORK_ROLES:
                raw = saved.get(role)
                if isinstance(raw, list):
                    def _entry_provider(item: Any) -> str:
                        if isinstance(item, dict):
                            return str(item.get("provider", "")).strip()
                        return str(item).strip()

                    if target in [_entry_provider(p) for p in raw]:
                        saved[role] = [p for p in raw if _entry_provider(p) != target]
                        changed = True
            if changed:
                atomic_json(routing_path, saved)
    if str(read_app_config().get("chat_provider") or "").strip() == target:
        save_app_config({"chat_provider": "Auto"})
    with STATE_LOCK:
        for summary in read_tasks():
            if str(summary.get("provider") or "") != target:
                continue
            if str(summary.get("status") or "") not in ("Ready", "Draft", "Waiting for capacity", "Rework needed"):
                continue
            task = read_task(str(summary.get("id", "")))
            if task:
                task["provider"] = "Automatic"
                write_task(task)


# --- Task route resolution ---
def task_route_entry(task: dict[str, Any]) -> dict[str, Any] | None:
    """Routing entry (provider + model + settings) selected for a task, or None."""
    if task.get("workflow_phase") == "qa":
        return route_entry_for_role(task, "QA", "qa_routing_cycle_start")
    val = str(task.get("provider", ""))
    if val == "Antigravity":
        val = "Automatic"
    configured = configured_providers()
    if is_active_provider(val):
        return {"provider": val, "model": "auto", **default_route_settings()} if val in configured else None
    legacy = {"Gemini": "Gemini", "OpenRouter free agent": "OpenRouter"}
    role = str(task.get("assigned_role", ""))
    if role in legacy:
        p = legacy[role]
        return {"provider": p, "model": "auto", **default_route_settings()} if p in configured else None
    if val not in ("Automatic", "Auto"):
        return None
    attempts = task.get("attempts") or []
    start = max(0, int(task.get("routing_cycle_start") or 0))
    counts = _combo_attempt_counts(task, role, start, role)
    if val == "Auto":
        for name in auto_provider_order():
            if name in configured:
                entry = {"provider": name, "model": "auto", **default_route_settings()}
                if not _combo_exhausted(entry, counts):
                    return entry
        return None
    return route_entry_for_role(task, role or "PM/PO", "routing_cycle_start")


def task_provider(task: dict[str, Any]) -> str:
    entry = task_route_entry(task)
    return entry["provider"] if entry else ""


def provider_for_role(task: dict[str, Any], role: str, cycle_key: str) -> str:
    entry = route_entry_for_role(task, role, cycle_key)
    return entry["provider"] if entry else ""


def has_untried_alternate(task: dict[str, Any], failed_provider: str) -> bool:
    """True when any configured route combo still has attempts remaining."""
    configured = configured_providers()
    attempts = task.get("attempts") or []
    role = str(task.get("assigned_role") or "PM/PO")
    cycle_key = "qa_routing_cycle_start" if role == "QA" else "routing_cycle_start"
    start = max(0, int(task.get(cycle_key, 0) or 0))
    counts = _combo_attempt_counts(task, role, start, role)
    return any(
        entry["provider"] in configured and not _combo_exhausted(entry, counts)
        for entry in route_entries_for_role(role)
    )

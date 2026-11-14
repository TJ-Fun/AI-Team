"""Dynamic AI provider registry: built-in presets, custom providers, and ordering."""

from __future__ import annotations

from ait.common import *


PROVIDER_REGISTRY_FILE = DATA / "settings" / "providers.json"

PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "gemini": {
        "name": "Gemini",
        "kind": "gemini",
        "model": GEMINI_MODEL,
        "base_url": "",
        "env_var": "GEMINI_API_KEY",
    },
    "groq": {
        "name": "GroqCloud",
        "kind": "openai_compat",
        "model": GROQ_MODEL,
        "base_url": "https://api.groq.com/openai/v1",
        "env_var": "GROQ_API_KEY",
        "max_tokens_key": "max_completion_tokens",
        "fixed_max_tokens": 8192,
    },
    "openrouter": {
        "name": "OpenRouter",
        "kind": "openai_compat",
        "model": OPENROUTER_MODEL,
        "base_url": "https://openrouter.ai/api/v1",
        "env_var": "OPENROUTER_API_KEY",
        "openrouter": True,
    },
    "omniroute": {
        "name": "OmniRoute",
        "kind": "openai_compat",
        "model": OMNIROUTE_MODEL,
        "base_url": OMNIROUTE_BASE_URL,
        "env_var": "OMNIROUTE_API_KEY",
    },
    "ninerouter": {
        "name": "9Router",
        "kind": "openai_compat",
        "model": NINEROUTER_MODEL,
        "base_url": NINEROUTER_BASE_URL,
        "env_var": "NINEROUTER_API_KEY",
    },
    "nvidia": {
        "name": "Nvidia",
        "kind": "openai_compat",
        "model": NVIDIA_MODEL,
        "base_url": NVIDIA_BASE_URL,
        "env_var": "NVIDIA_API_KEY",
    },
}
PRESET_ORDER = ["gemini", "groq", "openrouter", "omniroute", "ninerouter", "nvidia"]

_REGISTRY_CACHE: dict[str, Any] = {"at": 0.0, "providers": []}
_REGISTRY_TTL_SECONDS = 5.0


def _normalize_record(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    pid = str(item.get("id", "")).strip()
    name = str(item.get("name", "")).strip()
    kind = str(item.get("kind", "")).strip()
    if not pid or not name or kind not in ("gemini", "openai_compat"):
        return None
    try:
        order = int(item.get("order", 0))
    except (TypeError, ValueError):
        order = 0
    return {
        "id": pid,
        "name": name,
        "kind": kind,
        "model": str(item.get("model", "")).strip(),
        "base_url": str(item.get("base_url", "")).strip().rstrip("/"),
        "order": order,
    }


def _seed_providers() -> list[dict[str, Any]]:
    providers: list[dict[str, Any]] = []
    for idx, pid in enumerate(PRESET_ORDER):
        preset = PROVIDER_PRESETS[pid]
        providers.append(
            {
                "id": pid,
                "name": preset["name"],
                "kind": preset["kind"],
                "model": preset["model"],
                "base_url": preset["base_url"],
                "order": idx,
            }
        )
    return providers


def load_provider_registry() -> list[dict[str, Any]]:
    now = time.time()
    cached = _REGISTRY_CACHE.get("providers") or []
    if cached and now - float(_REGISTRY_CACHE.get("at") or 0) < _REGISTRY_TTL_SECONDS:
        return cached
    providers: list[dict[str, Any]] = []
    if PROVIDER_REGISTRY_FILE.is_file():
        try:
            data = json.loads(PROVIDER_REGISTRY_FILE.read_text(encoding="utf-8"))
            raw = data.get("providers") if isinstance(data, dict) else None
            if isinstance(raw, list):
                for item in raw:
                    record = _normalize_record(item)
                    if record:
                        providers.append(record)
        except Exception as exc:
            logger.warning("Failed to read provider registry, reseeding: %s", str(exc)[:200])
            providers = []
    if not providers:
        providers = _seed_providers()
    providers.sort(key=lambda p: int(p.get("order", 0)))
    _REGISTRY_CACHE["at"] = now
    _REGISTRY_CACHE["providers"] = providers
    return providers


def save_provider_registry(providers: list[dict[str, Any]]) -> None:
    ensure_data()
    atomic_json(PROVIDER_REGISTRY_FILE, {"providers": providers})
    _REGISTRY_CACHE["at"] = 0.0
    _REGISTRY_CACHE["providers"] = []
    logger.info("Provider registry saved (%s providers)", len(providers))


def active_providers() -> list[dict[str, Any]]:
    return load_provider_registry()


def provider_names() -> list[str]:
    return [str(p.get("name", "")) for p in active_providers() if p.get("name")]


def provider_record(name: str) -> dict[str, Any] | None:
    target = str(name or "").strip()
    for record in active_providers():
        if str(record.get("name", "")) == target:
            return record
    return None


def provider_record_by_id(pid: str) -> dict[str, Any] | None:
    target = str(pid or "").strip()
    for record in active_providers():
        if str(record.get("id", "")) == target:
            return record
    return None


def is_active_provider(name: str) -> bool:
    return provider_record(name) is not None


def provider_model(name: str) -> str:
    record = provider_record(name)
    if record and str(record.get("model", "")).strip():
        return str(record["model"]).strip()
    return GEMINI_MODEL


def provider_kind(name: str) -> str:
    record = provider_record(name)
    return str(record.get("kind", "")) if record else ""


def openai_compat_config(name: str) -> dict[str, Any] | None:
    record = provider_record(name)
    if not record or record.get("kind") != "openai_compat":
        return None
    base_url = str(record.get("base_url", "")).strip().rstrip("/")
    if not base_url:
        return None
    preset = PROVIDER_PRESETS.get(str(record.get("id", "")), {})
    return {
        "conn_id": str(record.get("id", "")),
        "model": str(record.get("model", "")).strip(),
        "endpoint": f"{base_url}/chat/completions",
        "max_tokens_key": str(preset.get("max_tokens_key", "max_tokens")),
        "fixed_max_tokens": preset.get("fixed_max_tokens"),
        "openrouter": bool(preset.get("openrouter")),
    }


def connection_specs() -> dict[str, tuple[str, str]]:
    specs: dict[str, tuple[str, str]] = {}
    for record in active_providers():
        preset = PROVIDER_PRESETS.get(str(record.get("id", "")), {})
        specs[str(record["id"])] = (str(record["name"]), str(preset.get("env_var", "")))
    return specs


def task_provider_options() -> list[str]:
    return ["Automatic", "Auto", *provider_names()]


def chat_provider_options() -> list[str]:
    return ["Auto", *provider_names()]


def available_presets() -> list[dict[str, Any]]:
    active_ids = {str(p.get("id", "")) for p in active_providers()}
    result: list[dict[str, Any]] = []
    for pid in PRESET_ORDER:
        if pid in active_ids:
            continue
        preset = PROVIDER_PRESETS[pid]
        result.append(
            {
                "id": pid,
                "name": preset["name"],
                "kind": preset["kind"],
                "model": preset["model"],
                "base_url": preset["base_url"],
            }
        )
    return result


def _unique_custom_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "provider"
    base = f"custom-{slug}"
    existing = {str(p.get("id", "")) for p in active_providers()}
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def add_provider(preset_id: str = "", name: str = "", base_url: str = "", model: str = "") -> dict[str, Any]:
    providers = [dict(p) for p in active_providers()]
    names = {str(p.get("name", "")).strip().lower() for p in providers}
    if preset_id:
        preset = PROVIDER_PRESETS.get(str(preset_id).strip())
        if not preset:
            raise ValueError("Unknown provider preset.")
        if str(preset_id) in {str(p.get("id", "")) for p in providers}:
            raise ValueError(f"{preset['name']} is already in the provider list.")
        if preset["name"].lower() in names:
            raise ValueError(f"A provider named {preset['name']} already exists.")
        record = {
            "id": str(preset_id),
            "name": preset["name"],
            "kind": preset["kind"],
            "model": preset["model"],
            "base_url": preset["base_url"],
            "order": len(providers),
        }
    else:
        clean_name = str(name or "").strip()
        clean_base = str(base_url or "").strip().rstrip("/")
        clean_model = str(model or "").strip() or "auto"
        if not clean_name or len(clean_name) > 60 or "/" in clean_name or "\\" in clean_name:
            raise ValueError("Provider name must be 1-60 characters without slashes.")
        if clean_name.lower() in names:
            raise ValueError(f"A provider named {clean_name} already exists.")
        if not re.match(r"^https?://", clean_base, re.IGNORECASE) or len(clean_base) > 300:
            raise ValueError("Base URL must be a valid http(s) URL.")
        if len(clean_model) > 160:
            raise ValueError("Model name must be 160 characters or fewer.")
        record = {
            "id": _unique_custom_id(clean_name),
            "name": clean_name,
            "kind": "openai_compat",
            "model": clean_model,
            "base_url": clean_base,
            "order": len(providers),
        }
    providers.append(record)
    save_provider_registry(providers)
    logger.info("Provider added: %s (%s)", record["name"], record["id"])
    return record


def update_provider_model(pid: str, model: str) -> dict[str, Any]:
    target = str(pid or "").strip()
    clean_model = str(model or "").strip()
    if not clean_model or len(clean_model) > 160:
        raise ValueError("Model name must be 1-160 characters.")
    providers = [dict(p) for p in active_providers()]
    record = next((p for p in providers if str(p.get("id", "")) == target), None)
    if not record:
        raise ValueError("Unknown provider.")
    record["model"] = clean_model
    save_provider_registry(providers)
    logger.info("Provider model updated: %s -> %s", record.get("name"), clean_model)
    return record


def remove_provider(pid: str) -> dict[str, Any]:
    target = str(pid or "").strip()
    providers = [dict(p) for p in active_providers()]
    record = next((p for p in providers if str(p.get("id", "")) == target), None)
    if not record:
        raise ValueError("Unknown provider.")
    providers = [p for p in providers if str(p.get("id", "")) != target]
    for idx, p in enumerate(providers):
        p["order"] = idx
    save_provider_registry(providers)
    logger.info("Provider removed: %s (%s)", record.get("name"), target)
    return record


def reorder_providers(order_ids: Any) -> list[dict[str, Any]]:
    if not isinstance(order_ids, list) or not all(isinstance(x, str) for x in order_ids):
        raise ValueError("Provider order must be a list of provider ids.")
    providers = [dict(p) for p in active_providers()]
    by_id = {str(p.get("id", "")): p for p in providers}
    if set(order_ids) != set(by_id) or len(order_ids) != len(by_id):
        raise ValueError("Provider order must include every provider exactly once.")
    reordered = [by_id[pid] for pid in order_ids]
    for idx, p in enumerate(reordered):
        p["order"] = idx
    save_provider_registry(reordered)
    logger.info("Provider order updated")
    return reordered

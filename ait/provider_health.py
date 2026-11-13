"""Cached AI provider health checks for status display."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from ait.common import *

HEALTH_TTL_SECONDS = 60
_HEALTH_LOCK = threading.Lock()
_HEALTH: dict[str, Any] = {"checked_at": "", "providers": {}}


def _configured_provider_records() -> list[dict[str, Any]]:
    records = []
    for record in active_providers():
        name = str(record.get("name") or "").strip()
        conn_id = str(record.get("id") or "").strip()
        if name and conn_id and get_provider_key(conn_id):
            records.append(record)
    return records


def _check_provider(record: dict[str, Any]) -> dict[str, Any]:
    name = str(record.get("name") or "")
    conn_id = str(record.get("id") or "")
    checked_at = now_iso()
    try:
        result = test_provider_connection(conn_id)
        return {
            "name": name,
            "id": conn_id,
            "up": bool(result.get("connected")),
            "checked_at": checked_at,
            "error": "",
            "message": str(result.get("message") or "Connected."),
        }
    except Exception as exc:
        return {
            "name": name,
            "id": conn_id,
            "up": False,
            "checked_at": checked_at,
            "error": str(exc)[:300],
            "message": "",
        }


def refresh_provider_health() -> dict[str, Any]:
    records = _configured_provider_records()
    checked_at = now_iso()
    providers: dict[str, Any] = {}
    if records:
        workers = min(8, len(records))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for item in pool.map(_check_provider, records):
                providers[str(item["name"])] = {
                    "up": bool(item.get("up")),
                    "checked_at": str(item.get("checked_at") or checked_at),
                    "error": str(item.get("error") or ""),
                    "message": str(item.get("message") or ""),
                }
    with _HEALTH_LOCK:
        _HEALTH["checked_at"] = checked_at
        _HEALTH["providers"] = providers
    return provider_health_status()


def provider_health_status(refresh: bool = False) -> dict[str, Any]:
    if refresh:
        return refresh_provider_health()
    with _HEALTH_LOCK:
        providers = {name: dict(info) for name, info in (_HEALTH.get("providers") or {}).items()}
        checked_at = str(_HEALTH.get("checked_at") or "")
    up = sum(1 for info in providers.values() if info.get("up"))
    return {
        "up": up,
        "total": len(providers),
        "checked_at": checked_at,
        "providers": providers,
    }

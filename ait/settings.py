"""Application configuration, autonomy settings, and startup logging."""

from __future__ import annotations

from ait.common import *


# --- App Configuration ---
APP_CONFIG_FILE = DATA / "app_config.json"

def read_app_config() -> dict[str, Any]:
    """Read app configuration for repository paths and log level."""
    try:
        if APP_CONFIG_FILE.exists():
            config = json.loads(APP_CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(config, dict):
                return config
    except Exception as exc:
        logger.error(f"Failed to read app config: {exc}")
    return {
        "app_repo": "",
        "app_clone_dir": "",
        "app_working_dir": str(ROOT),
        "log_level": "INFO",
    }


def normalize_log_level(level: Any) -> str | None:
    normalized = str(level or "").strip().upper()
    return normalized if normalized in LOG_LEVELS else None


def configure_file_log_level(level: Any) -> str:
    configured = normalize_log_level(level) or "INFO"
    file_handler.setLevel(LOG_LEVELS[configured])
    return configured

APP_UPDATE_CHECK_INTERVAL_SECONDS = 30 * 60
APP_UPDATE_CACHE: dict[str, Any] = {"checked_at": 0.0, "status": {}}


def current_app_version() -> str:
    try:
        text = (ROOT / "documentation" / "ABOUT.md").read_text(encoding="utf-8")
        m = re.search(r"Version:\s*\*\*([0-9]+(?:\.[0-9]+)*)\*\*", text)
        return m.group(1) if m else ""
    except Exception:
        return ""


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = tuple(int(p) for p in re.findall(r"\d+", version)[:4])
    return parts or (0,)


def remote_app_version(owner: str, repo: str) -> str:
    raw = github_api(f"/repos/{owner}/{repo}/contents/documentation/ABOUT.md")
    if not isinstance(raw, dict) or raw.get("encoding") != "base64":
        return ""
    text = base64.b64decode(re.sub(r"\s+", "", str(raw.get("content", "")))).decode("utf-8", errors="replace")
    m = re.search(r"Version:\s*\*\*([0-9]+(?:\.[0-9]+)*)\*\*", text)
    return m.group(1) if m else ""


def app_update_status(force: bool = False) -> dict[str, Any]:
    now = time.time()
    cached = APP_UPDATE_CACHE.get("status") or {}
    if (
        not force
        and cached
        and now - float(APP_UPDATE_CACHE.get("checked_at") or 0) < APP_UPDATE_CHECK_INTERVAL_SECONDS
    ):
        return cached
    config = read_app_config()
    repo_url = str(config.get("app_repo") or "").strip()
    status: dict[str, Any] = {
        "configured": False,
        "current": current_app_version(),
        "latest": "",
        "update_available": False,
        "checked_at": now_iso(),
        "error": "",
    }
    if repo_url:
        status["configured"] = True
        try:
            owner, repo = repository_parts(normalize_github_repo_url(repo_url))
            latest = remote_app_version(owner, repo)
            status["latest"] = latest
            if latest and _version_tuple(latest) > _version_tuple(str(status["current"])):
                status["update_available"] = True
        except Exception as exc:
            status["error"] = str(exc)[:300]
    APP_UPDATE_CACHE["checked_at"] = now
    APP_UPDATE_CACHE["status"] = status
    return status


def save_app_config(config: dict[str, Any]) -> None:
    """Save app configuration."""
    try:
        merged = read_app_config()
        merged.update(config)
        merged["log_level"] = normalize_log_level(merged.get("log_level")) or "INFO"
        atomic_json(APP_CONFIG_FILE, merged)
        logger.info("App config saved")
    except Exception as exc:
        logger.error(f"Failed to save app config: {exc}")
        raise ValueError(f"Failed to save app config: {exc}")


AUTONOMY_DEFAULTS: dict[str, bool] = {
    "plan_requires_approval": True,
    "escalation_runs_provisional": True,
    "merge_requires_approval": False,
}


def get_autonomy() -> dict[str, bool]:
    saved = read_app_config().get("autonomy")
    result = dict(AUTONOMY_DEFAULTS)
    if isinstance(saved, dict):
        for key in result:
            if isinstance(saved.get(key), bool):
                result[key] = saved[key]
    return result


def save_autonomy(value: Any) -> dict[str, bool]:
    if not isinstance(value, dict):
        raise ValueError("Autonomy settings must be an object.")
    cleaned: dict[str, bool] = {}
    for key, default in AUTONOMY_DEFAULTS.items():
        raw = value.get(key, default)
        if not isinstance(raw, bool):
            raise ValueError(f"Autonomy setting {key} must be true or false.")
        cleaned[key] = raw
    save_app_config({"autonomy": cleaned})
    return cleaned


configure_file_log_level(read_app_config().get("log_level", "INFO"))

# Log startup after applying the persisted file log level.
logger.info("=" * 60)
logger.info("Personal AI Team server starting")
logger.info(f"ROOT: {ROOT}")
logger.info(f"DATA: {DATA}")
logger.info(f"LOG_DIR: {LOG_DIR}")
logger.info(f"LOG_FILE: {LOG_FILE}")
logger.info("=" * 60)

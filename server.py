#!/usr/bin/env python3
"""Personal AI Team local workspace server."""

from __future__ import annotations

import base64
import errno
import json
import logging
import logging.handlers
import os
import re
import signal
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover
    Fernet = None  # type: ignore[assignment]
    InvalidToken = Exception  # type: ignore[assignment,misc]

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA = Path(os.environ.get("TEAM_DATA_DIR", str(ROOT / "data"))).expanduser().resolve()
DOCUMENTATION_DIR = ROOT / "documentation"

HOST = os.environ.get("TEAM_HOST", "127.0.0.1")
PORT = int(os.environ.get("TEAM_PORT", "8765"))

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "orchestrator").strip() or "orchestrator"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openrouter/free").strip() or "openrouter/free"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b").strip() or "openai/gpt-oss-20b"
_RAW_ANTIGRAVITY_AGENT = os.environ.get("ANTIGRAVITY_AGENT", "").strip()
ANTIGRAVITY_AGENT = (
    "antigravity-preview-09-2026"
    if not _RAW_ANTIGRAVITY_AGENT or _RAW_ANTIGRAVITY_AGENT == "antigravity"
    else _RAW_ANTIGRAVITY_AGENT
)
HTTP_USER_AGENT = os.environ.get("TEAM_USER_AGENT", "Personal-AI-Team/1.0").strip() or "Personal-AI-Team/1.0"
LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

WATCHDOG_INTERVAL_SECONDS = 2 * 60 * 60
DEFAULT_ATTEMPT_TIMEOUT_SECONDS = 2 * 60 * 60
MAX_AUTOMATIC_RETRIES = 2

WORK_ROLES = [
    "PM/PO",
    "Frontend developer",
    "Backend developer",
    "QA",
    "UI designer",
    "Documentation",
]

PROVIDERS = ["Gemini", "Antigravity", "GroqCloud", "OpenRouter"]
TASK_PROVIDERS = ["Automatic", *PROVIDERS]

ROUTING_DEFAULTS: dict[str, list[str]] = {
    "PM/PO": ["Gemini", "GroqCloud", "Antigravity", "OpenRouter"],
    "Frontend developer": ["Antigravity", "Gemini", "GroqCloud", "OpenRouter"],
    "Backend developer": ["Antigravity", "Gemini", "GroqCloud", "OpenRouter"],
    "QA": ["GroqCloud", "Gemini", "Antigravity", "OpenRouter"],
    "UI designer": ["Gemini", "Antigravity", "GroqCloud", "OpenRouter"],
    "Documentation": ["Gemini", "Antigravity", "GroqCloud", "OpenRouter"],
}

PROMPT_FILES: dict[str, str] = {
    "shared_rules": "Shared rules",
    "orchestrator": "Orchestrator",
    "pm_po": "PM/PO",
    "frontend_developer": "Frontend developer",
    "backend_developer": "Backend developer",
    "qa": "QA",
    "ui_designer": "UI designer",
    "documentation": "Documentation",
}

PROJECT_CONTEXT_FILES: dict[str, str] = {
    "project_brief": "Project brief",
    "technical_overview": "Technical overview",
    "decisions": "Decision log",
    "outcomes": "Confirmed outcomes",
}

CONNECTION_SPECS: dict[str, tuple[str, str]] = {
    "gemini": ("Gemini / Antigravity", "GEMINI_API_KEY"),
    "groq": ("GroqCloud", "GROQ_API_KEY"),
    "openrouter": ("OpenRouter", "OPENROUTER_API_KEY"),
    "github": ("GitHub repository connector", "GITHUB_TOKEN"),
}

# --- Logging Setup ---
LOG_DIR = DATA / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "app.log"

# Configure logging with 1-week retention using TimedRotatingFileHandler
logger = logging.getLogger("personal_ai_team")
logger.setLevel(logging.DEBUG)

# File handler with daily rotation, 7 days retention
file_handler = logging.handlers.TimedRotatingFileHandler(
    LOG_FILE,
    when="midnight",
    interval=1,
    backupCount=7,
    encoding="utf-8",
)
file_handler.setLevel(logging.INFO)
file_handler.suffix = "%Y-%m-%d"
file_formatter = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

# Console handler for critical errors
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.ERROR)
console_formatter = logging.Formatter("%(levelname)s: %(message)s")
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

CLONE_JOBS: dict[str, dict[str, Any]] = {}
CLONE_JOBS_LOCK = threading.Lock()
CLONE_TARGETS: set[str] = set()

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


configure_file_log_level(read_app_config().get("log_level", "INFO"))

# Log startup after applying the persisted file log level.
logger.info("=" * 60)
logger.info("Personal AI Team server starting")
logger.info(f"ROOT: {ROOT}")
logger.info(f"DATA: {DATA}")
logger.info(f"LOG_DIR: {LOG_DIR}")
logger.info(f"LOG_FILE: {LOG_FILE}")
logger.info("=" * 60)

TRANSITIONS: dict[str, set[str]] = {
    "Draft": {"Ready", "Blocked", "Canceled"},
    "Ready": {"In progress", "Waiting for capacity", "Blocked", "Canceled"},
    "In progress": {"Needs validation", "Waiting for capacity", "Blocked", "Canceled", "Done"},
    "Waiting for capacity": {"Ready", "Blocked", "Canceled"},
    "Needs validation": {"Done", "Rework needed", "Blocked"},
    "Rework needed": {"Ready", "Blocked", "Canceled"},
    "Blocked": {"Draft", "Ready", "Canceled"},
    "Done": set(),
    "Canceled": set(),
}

USER_TRANSITIONS: dict[str, set[str]] = {
    "Draft": {"Ready", "Blocked", "Canceled"},
    "Ready": {"Blocked", "Canceled"},
    "Waiting for capacity": {"Ready", "Blocked", "Canceled"},
    "Rework needed": {"Ready", "Blocked", "Canceled"},
    "Blocked": {"Draft", "Ready", "Canceled"},
}

PM_PLANNING_CONTRACT = (
    "For a PM/PO planning request, return ONLY one valid JSON object, with no markdown. "
    "Use exactly one of these forms. If clarification is needed: "
    '{"type":"questions","summary":"brief summary","questions":[{"title":"short card title",'
    '"question":"one clear question","why":"why it matters","options":[{"label":"choice",'
    '"description":"short effect"},{"label":"another choice","description":"short effect"}],'
    '"allow_free_text":true}]}. Provide 2 to 8 useful options per question and allow free text. '
    "If no more clarification is needed: "
    '{"type":"plan","summary":"brief plan summary","project_overview":{"project_brief":"complete Markdown brief",'
    '"technical_overview":"complete Markdown technical overview","decisions":"complete Markdown decision log"},'
    '"tasks":[{"title":"task title","request":"bounded implementation request",'
    '"role":"Frontend developer","module_id":"optional sub-project id","acceptance_checks":["observable check"],"produces_deliverable":true}]}. '
    "Use only these work roles: PM/PO, Frontend developer, Backend developer, QA, UI designer, Documentation. "
    "Propose 1 to 12 small tasks. When writing the overview, preserve confirmed existing context, separate facts from assumptions, "
    "and provide concise complete content for all three documents. Do not claim the tasks have already been created."
)

STATE_LOCK = threading.RLock()
DISPATCH_EVENT = threading.Event()

ACTIVE_CHAT_LOCK = threading.Lock()
ACTIVE_CHAT_COUNT = 0
ACTIVE_GEMINI_COUNT = 0
ACTIVE_OPENROUTER_COUNT = 0

WATCHDOG_STATUS: dict[str, Any] = {
    "last_run": None,
    "changed": 0,
    "error": None,
}

CPU_SAMPLE_LOCK = threading.Lock()
PREV_CPU_SAMPLE: tuple[int, int] | None = None


class ProviderFailure(Exception):
    def __init__(self, provider: str, status: int, message: str, retry_after: str | None = None):
        super().__init__(message)
        self.provider = provider
        self.status = status
        self.retry_after = retry_after


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_version_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def parse_version_timestamp(version_id: str) -> str:
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(\d{6})Z", version_id)
    if not m:
        return version_id
    return f"{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}:{m[6]}.{m[7][:3]}Z"


def parse_iso_epoch(value: str | None) -> float | None:
    if not value:
        return None
    try:
        cleaned = value.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned).timestamp()
    except Exception:
        return None


def resolve_web_asset(filename: str) -> Path:
    """Resolve a known web asset from the static directory."""
    if filename not in {"index.html", "app.js", "style.css"}:
        raise ValueError("Unknown web asset.")
    return STATIC / filename


def atomic_json(file_path: Path, value: Any) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if file_path.exists():
        shutil.copy2(file_path, file_path.with_suffix(file_path.suffix + ".bak"))
    tmp_path.replace(file_path)


def atomic_text(file_path: Path, text: str) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(file_path)


def ensure_data() -> None:
    for folder in ("tasks", "replies", "logs", "prompts", "project", "settings", "chat"):
        (DATA / folder).mkdir(parents=True, exist_ok=True)
    for slug in PROMPT_FILES:
        src = ROOT / "prompts" / f"{slug}.md"
        dst = DATA / "prompts" / f"{slug}.md"
        if src.is_file() and not dst.exists():
            shutil.copy2(src, dst)
    for slug in PROJECT_CONTEXT_FILES:
        src = ROOT / "project_templates" / f"{slug}.md"
        dst = DATA / "project" / f"{slug}.md"
        if src.is_file() and not dst.exists():
            shutil.copy2(src, dst)
        hist_dir = DATA / "project" / "history" / slug
        if dst.is_file():
            hist_dir.mkdir(parents=True, exist_ok=True)
            if not any(hist_dir.glob("*.md")):
                shutil.copy2(dst, hist_dir / f"{make_version_id()}.md")


def append_event(event: dict[str, Any]) -> None:
    ensure_data()
    with STATE_LOCK:
        log_path = DATA / "logs" / "activity.jsonl"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


# --- Encrypted Credentials & Connections ---
def secret_cipher() -> Any | None:
    if Fernet is None:
        return None
    env_key = os.environ.get("TEAM_SECRET_KEY", "").strip()
    if env_key:
        return Fernet(env_key.encode("utf-8"))
    ensure_data()
    key_path = DATA / "settings" / "secret.key"
    if not key_path.is_file():
        generated = Fernet.generate_key()
        fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(generated)
    return Fernet(key_path.read_bytes().strip())


def read_saved_secrets() -> dict[str, str]:
    cipher = secret_cipher()
    enc_path = DATA / "settings" / "secrets.enc"
    if cipher is None or not enc_path.is_file():
        return {}
    try:
        raw = cipher.decrypt(enc_path.read_bytes())
        parsed = json.loads(raw.decode("utf-8"))
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items() if isinstance(v, str) and v.strip()}
    except (InvalidToken, ValueError, OSError):
        return {}
    return {}


def write_saved_secrets(secrets: dict[str, str]) -> None:
    cipher = secret_cipher()
    if cipher is None:
        raise ValueError("Python 'cryptography' package is required to store encrypted credentials.")
    ensure_data()
    enc_path = DATA / "settings" / "secrets.enc"
    token = cipher.encrypt(json.dumps(secrets).encode("utf-8"))
    tmp_path = enc_path.with_suffix(".enc.tmp")
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(token)
    tmp_path.replace(enc_path)


def get_provider_key_and_source(conn_id: str) -> tuple[str, str]:
    spec = CONNECTION_SPECS.get(conn_id)
    if not spec:
        return "", ""
    saved = read_saved_secrets().get(conn_id, "").strip()
    if saved:
        return saved, "Encrypted on VM"
    env_val = os.environ.get(spec[1], "").strip()
    if env_val:
        return env_val, f"Environment ({spec[1]})"
    return "", ""


def get_provider_key(conn_id: str) -> str:
    return get_provider_key_and_source(conn_id)[0]


def configured_providers() -> list[str]:
    result: list[str] = []
    if get_provider_key("gemini"):
        result.extend(["Gemini", "Antigravity"])
    if get_provider_key("groq"):
        result.append("GroqCloud")
    if get_provider_key("openrouter"):
        result.append("OpenRouter")
    return result


# --- Prompts (Default + Custom Persistence + Reset) ---
def load_default_prompt(slug: str) -> str:
    if slug not in PROMPT_FILES:
        raise ValueError("Unknown prompt role.")
    default_path = ROOT / "prompts" / f"{slug}.md"
    if default_path.is_file():
        return default_path.read_text(encoding="utf-8").strip()
    return ""


def load_prompt(slug: str) -> str:
    if slug not in PROMPT_FILES:
        raise ValueError("Unknown prompt role.")
    ensure_data()
    custom_path = DATA / "prompts" / f"{slug}.md"
    if custom_path.is_file():
        return custom_path.read_text(encoding="utf-8").strip()
    return load_default_prompt(slug)


def save_prompt_file(slug: str, content: str) -> str:
    if slug not in PROMPT_FILES:
        raise ValueError("Unknown prompt role.")
    ensure_data()
    trimmed = content.strip()
    atomic_text(DATA / "prompts" / f"{slug}.md", trimmed + "\n")
    return trimmed


def reset_prompt_file(slug: str) -> str:
    if slug not in PROMPT_FILES:
        raise ValueError("Unknown prompt role.")
    default_content = load_default_prompt(slug)
    if not default_content:
        raise ValueError("Built-in default prompt template was not found in prompts/.")
    save_prompt_file(slug, default_content)
    return default_content


def combined_agent_prompt(role: str) -> str:
    slug_map = {
        "PM/PO": "pm_po",
        "Frontend developer": "frontend_developer",
        "Backend developer": "backend_developer",
        "QA": "qa",
        "UI designer": "ui_designer",
        "Documentation": "documentation",
    }
    slug = slug_map.get(role, "pm_po")
    parts = [load_prompt("shared_rules"), load_prompt(slug)]
    return "\n\n".join(p for p in parts if p)


# --- Routing, Projects, Tasks, Context ---
def get_routing() -> dict[str, list[str]]:
    ensure_data()
    routing_path = DATA / "settings" / "routing.json"
    saved: dict[str, Any] = {}
    if routing_path.is_file():
        try:
            saved = json.loads(routing_path.read_text(encoding="utf-8"))
        except Exception:
            saved = {}
    result: dict[str, list[str]] = {}
    for role in WORK_ROLES:
        defaults = ROUTING_DEFAULTS[role]
        raw = saved.get(role) if isinstance(saved, dict) else None
        order = [p for p in raw if p in PROVIDERS] if isinstance(raw, list) else list(defaults)
        deduped: list[str] = []
        for p in [*order, *defaults]:
            if p not in deduped:
                deduped.append(p)
        result[role] = deduped
    return result


def save_routing(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        raise ValueError("Routing settings must be an object.")
    cleaned: dict[str, list[str]] = {}
    for role in WORK_ROLES:
        order = value.get(role)
        if (
            not isinstance(order, list)
            or len(order) != len(PROVIDERS)
            or len(set(order)) != len(PROVIDERS)
            or not all(p in PROVIDERS for p in order)
        ):
            raise ValueError(f"Set each provider exactly once for {role}.")
        cleaned[role] = list(order)
    atomic_json(DATA / "settings" / "routing.json", cleaned)
    return cleaned


def read_projects() -> list[dict[str, Any]]:
    ensure_data()
    projects_path = DATA / "projects.json"
    if projects_path.is_file():
        try:
            items = json.loads(projects_path.read_text(encoding="utf-8"))
            if isinstance(items, list) and items:
                return items
        except Exception:
            pass
    default_project = [
        {"id": "default", "name": "Main project", "repository": "", "context": "", "modules": []}
    ]
    atomic_json(projects_path, default_project)
    return default_project


def read_tasks() -> list[dict[str, Any]]:
    ensure_data()
    tasks: list[dict[str, Any]] = []
    for p in (DATA / "tasks").glob("*.json"):
        try:
            tasks.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    tasks.sort(key=lambda t: str(t.get("created_at", "")), reverse=True)
    return tasks


def read_task(task_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"T-\d{4,}", task_id):
        return None
    p = DATA / "tasks" / f"{task_id}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_task(task: dict[str, Any]) -> None:
    ensure_data()
    with STATE_LOCK:
        atomic_json(DATA / "tasks" / f"{task['id']}.json", task)


def next_task_id() -> str:
    with STATE_LOCK:
        nums = []
        for t in read_tasks():
            m = re.fullmatch(r"T-(\d{4,})", str(t.get("id", "")))
            if m:
                nums.append(int(m.group(1)))
        nxt = (max(nums) if nums else 0) + 1
        return f"T-{nxt:04d}"


def save_projects(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list) or not items:
        raise ValueError("Keep at least one project.")
    existing_map = {p["id"]: p for p in read_projects() if isinstance(p, dict) and "id" in p}
    seen: set[str] = set()
    cleaned: list[dict[str, Any]] = []

    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("Each project must be an object.")
        pid = re.sub(r"[^a-z0-9-]+", "-", str(raw.get("id", "")).lower()).strip("-")[:40]
        name = str(raw.get("name", "")).strip()[:100]
        repository = str(raw.get("repository", "")).strip()
        local_path = str(raw.get("local_path", "")).strip()
        modules = raw.get("modules", [])
        if not pid or pid in seen or not name or not isinstance(modules, list):
            raise ValueError("Project names and unique IDs are required; modules must be a list.")
        if repository and not re.fullmatch(
            r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", repository
        ):
            raise ValueError(f"Repository for {name} must be a GitHub https URL.")
        if local_path:
            candidate = Path(local_path).expanduser()
            if not candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError(f"Local path for {name} must be an absolute path without '..'.")
        clean_modules: list[dict[str, str]] = []
        mod_ids: set[str] = set()
        for mod in modules:
            if not isinstance(mod, dict):
                raise ValueError("Each sub-project must be an object.")
            mid = re.sub(r"[^a-z0-9-]+", "-", str(mod.get("id", "")).lower()).strip("-")[:40]
            mname = str(mod.get("name", "")).strip()[:100]
            mpath = str(mod.get("path", "")).strip().replace("\\", "/").strip("/")
            if not mid or mid in mod_ids or not mname or ".." in mpath.split("/"):
                raise ValueError("Each sub-project needs a unique ID, name, and safe repository folder path.")
            mod_ids.add(mid)
            clean_modules.append(
                {
                    "id": mid,
                    "name": mname,
                    "path": mpath,
                    "context": str(mod.get("context", ""))[:10000],
                }
            )
        seen.add(pid)
        context = str(raw.get("context", ""))[:20000]
        old_ctx = str(existing_map.get(pid, {}).get("context", ""))
        if old_ctx and old_ctx != context:
            snap = DATA / "projects" / pid / "history" / f"{make_version_id()}.md"
            snap.parent.mkdir(parents=True, exist_ok=True)
            snap.write_text(old_ctx, encoding="utf-8")
        cleaned.append(
            {
                "id": pid,
                "name": name,
                "repository": repository,
                "local_path": local_path,
                "context": context,
                "modules": clean_modules,
            }
        )

    task_pids = {str(t.get("project_id") or "default") for t in read_tasks()}
    for pid in task_pids:
        if pid not in seen:
            raise ValueError(
                "Projects with existing tasks cannot be removed. Keep them and edit their details instead."
            )
    atomic_json(DATA / "projects.json", cleaned)
    return cleaned


def project_background_history(project_id: str) -> list[dict[str, str]]:
    if not any(p.get("id") == project_id for p in read_projects()):
        return []
    folder = DATA / "projects" / project_id / "history"
    if not folder.is_dir():
        return []
    rows: list[dict[str, str]] = []
    for f in sorted(folder.glob("*.md"), reverse=True):
        try:
            txt = f.read_text(encoding="utf-8")
            rows.append(
                {
                    "id": f.stem,
                    "created_at": parse_version_timestamp(f.stem),
                    "preview": re.sub(r"\s+", " ", txt).strip()[:140],
                }
            )
        except Exception:
            continue
    return rows


def load_project_context(slug: str) -> str:
    if slug not in PROJECT_CONTEXT_FILES:
        return ""
    ensure_data()
    p = DATA / "project" / f"{slug}.md"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def save_project_context_document(slug: str, content: str) -> str | None:
    if slug not in PROJECT_CONTEXT_FILES:
        raise ValueError("Unknown project context document.")
    ensure_data()
    p = DATA / "project" / f"{slug}.md"
    prev = p.read_text(encoding="utf-8") if p.is_file() else ""
    normalized = content.strip() + "\n"
    version_id: str | None = None
    if prev and prev != normalized:
        version_id = make_version_id()
        snap = DATA / "project" / "history" / slug / f"{version_id}.md"
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_text(prev, encoding="utf-8")
    atomic_text(p, normalized)
    return version_id


def get_project_context_history(slug: str) -> list[dict[str, str]]:
    if slug not in PROJECT_CONTEXT_FILES:
        return []
    folder = DATA / "project" / "history" / slug
    if not folder.is_dir():
        return []
    rows: list[dict[str, str]] = []
    for f in sorted(folder.glob("*.md"), reverse=True):
        preview = "Saved version could not be read."
        try:
            preview = re.sub(r"\s+", " ", f.read_text(encoding="utf-8")).strip()[:140]
        except Exception:
            pass
        rows.append(
            {
                "id": f.stem,
                "created_at": parse_version_timestamp(f.stem),
                "preview": preview,
            }
        )
    return rows


def project_context_for_role(role: str, task: dict[str, Any] | None = None) -> str:
    sections: list[tuple[str, str]] = [
        ("Project brief", load_project_context("project_brief")),
        ("Technical overview", load_project_context("technical_overview")),
        ("Approved decisions", load_project_context("decisions")),
        ("Confirmed outcomes", load_project_context("outcomes")),
    ]
    if role == "UI designer":
        sections = [sections[0], sections[2], sections[3]]
    elif role == "PM/PO":
        pid = str((task or {}).get("project_id") or "default")
        current = [
            t
            for t in read_tasks()
            if t.get("status") != "Canceled" and str(t.get("project_id") or "default") == pid
        ]
        board_lines = (
            [f"- {t['id']} | {t.get('status')} | {t.get('assigned_role')} | {t.get('title')}" for t in current[:40]]
            if current
            else ["- No tasks recorded."]
        )
        completed: list[str] = []
        for t in current:
            rel = t.get("latest_reply_file")
            if t.get("status") != "Done" or not rel:
                continue
            rp = (DATA / str(rel)).resolve()
            if not str(rp).startswith(str(DATA)):
                continue
            try:
                rep = json.loads(rp.read_text(encoding="utf-8"))
                excerpt = re.sub(r"\s+", " ", str(rep.get("text", ""))).strip()[:500]
            except Exception:
                excerpt = "Saved reply is unavailable."
            completed.append(f"- {t['id']} {t.get('title')}: {excerpt or 'No text reply.'}")
            if len(completed) == 8:
                break
        sections.append(("Current board (live)", "\n".join(board_lines)))
        sections.append(
            (
                "Recent completed agent responses (not proof of integration)",
                "\n".join(completed) if completed else "No completed agent responses yet.",
            )
        )

    projects = read_projects()
    pid = str((task or {}).get("project_id") or "default")
    project = next((p for p in projects if p.get("id") == pid), projects[0] if projects else None)
    if project:
        sections.append(
            (
                "Project",
                f"Name: {project.get('name', '')}\nRepository: {project.get('repository', '')}\n\n{project.get('context', '')}",
            )
        )
        mid = str((task or {}).get("module_id") or "")
        mod = next((m for m in project.get("modules", []) if m.get("id") == mid), None)
        if mod:
            sections.append(
                (
                    f"Sub-project: {mod.get('name')}",
                    f"Repository folder: {mod.get('path', '')}\n{mod.get('context', '')}",
                )
            )
        elif role == "PM/PO" and project.get("modules"):
            cat = "\n".join(
                f"- {m['id']}: {m['name']} ({m.get('path') or 'repository root'}) — {str(m.get('context', ''))[:500]}"
                for m in project["modules"]
            )
            sections.append(("Available sub-projects", cat))

    return "\n\n".join(f"## {title}\n{body[:6000]}" for title, body in sections if body and body.strip())


def read_chat_history() -> list[dict[str, Any]]:
    ensure_data()
    p = DATA / "chat" / "orchestrator.json"
    if not p.is_file():
        return []
    try:
        val = json.loads(p.read_text(encoding="utf-8"))
        return val if isinstance(val, list) else []
    except Exception:
        return []


def write_chat_history(messages: list[dict[str, Any]]) -> None:
    ensure_data()
    with STATE_LOCK:
        atomic_json(DATA / "chat" / "orchestrator.json", messages)


def append_chat_notice(content: str, timestamp: str, task_id: str) -> None:
    with STATE_LOCK:
        hist = read_chat_history()
        hist.append({"role": "assistant", "content": content, "at": timestamp, "task_id": task_id})
        write_chat_history(hist)


def read_task_replies(task_id: str) -> list[dict[str, Any]]:
    task = read_task(task_id)
    if not task:
        return []
    folder_map = {
        "Gemini": "gemini",
        "Antigravity": "antigravity",
        "GroqCloud": "groqcloud",
        "OpenRouter": "openrouter",
    }
    replies: list[dict[str, Any]] = []
    for att in task.get("attempts", []) or []:
        folder = folder_map.get(str(att.get("provider", "")))
        num = att.get("number")
        if not folder or not isinstance(num, int) or num < 1:
            continue
        rp = DATA / "replies" / task_id / folder / f"attempt-{num:03d}.json"
        if rp.is_file():
            try:
                replies.append(json.loads(rp.read_text(encoding="utf-8")))
            except Exception:
                continue
    return replies


def task_provider(task: dict[str, Any]) -> str:
    val = str(task.get("provider", ""))
    configured = configured_providers()
    if val in PROVIDERS:
        return val if val in configured else ""
    legacy = {"Gemini": "Gemini", "OpenRouter free agent": "OpenRouter"}
    role = str(task.get("assigned_role", ""))
    if role in legacy:
        p = legacy[role]
        return p if p in configured else ""
    if val != "Automatic":
        return ""
    attempts = task.get("attempts") or []
    start = max(0, int(task.get("routing_cycle_start") or 0))
    tried = {str(a.get("provider", "")) for a in attempts[start:]}
    order = get_routing().get(role or "PM/PO", PROVIDERS)
    return next((p for p in order if p in configured and p not in tried), "")


def has_untried_alternate(task: dict[str, Any], failed_provider: str) -> bool:
    configured = configured_providers()
    attempts = task.get("attempts") or []
    start = max(0, int(task.get("routing_cycle_start") or 0))
    tried = {str(a.get("provider", "")) for a in attempts[start:]}
    order = get_routing().get(str(task.get("assigned_role") or "PM/PO"), [])
    return any(p != failed_provider and p not in tried and p in configured for p in order)


# --- GitHub Helpers ---
def repository_parts(repository: str) -> tuple[str, str]:
    m = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?", repository)
    if not m:
        raise ValueError("The project's GitHub repository URL is invalid.")
    return m.group(1), m.group(2)


def safe_repo_path(value: str) -> str:
    cleaned = value.strip().replace("\\", "/").strip("/")
    if cleaned and (
        cleaned.startswith(".")
        or any(part in (".", "..") or part.startswith(".") for part in cleaned.split("/"))
    ):
        raise ValueError("Hidden and relative repository paths are not available in this viewer.")
    if ".." in cleaned.split("/") or len(cleaned) > 500:
        raise ValueError("Invalid repository path.")
    return cleaned


def github_api(api_path: str, method: str = "GET", payload: Any = None, require_auth: bool = True) -> Any:
    logger.debug(f"GitHub API request: {method} {api_path}")
    request_started = time.monotonic()
    token = get_provider_key("github")
    if require_auth and not token:
        logger.error("GitHub token is not configured")
        raise ValueError("GitHub token is not configured.")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": HTTP_USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
        logger.debug("Using GitHub token for authentication")
    data_bytes = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"https://api.github.com{api_path}", data=data_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            logger.debug(
                "GitHub API response: method=%s path=%s status=%s bytes=%s elapsed_ms=%s",
                method, api_path, resp.status, len(raw), round((time.monotonic() - request_started) * 1000),
            )
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        logger.error(f"GitHub API HTTP error {exc.code}: {body}")
        raise ValueError(f"GitHub returned HTTP {exc.code}: {body}") from exc
    except Exception as exc:
        logger.error(f"GitHub API request failed: {exc}")
        raise ValueError(f"GitHub request failed: {exc}") from exc


def github_project(project_id: str) -> dict[str, Any]:
    project = next((p for p in read_projects() if p.get("id") == project_id), None)
    if not project or not project.get("repository"):
        raise ValueError("This project does not have a GitHub repository configured.")
    return project


# --- Git Operations ---
def git_project_path(project_id: str) -> Path:
    project = next((p for p in read_projects() if p.get("id") == project_id), None)
    if not project:
        raise ValueError("Project not found.")
    local_path = str(project.get("local_path", "")).strip()
    if not local_path:
        raise ValueError("Local path is not configured for this project.")
    try:
        path = Path(local_path).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"Local path does not exist or is not a directory: {local_path}")
    except PermissionError as exc:
        raise ValueError(
            f"Permission denied accessing project path '{local_path}'. Check filesystem permissions "
            "for the service user and its systemd ReadWritePaths allowlist."
        ) from exc
    return path


def git_auth_environment(token: str = "") -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if token:
        auth = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
        try:
            config_count = max(0, int(env.get("GIT_CONFIG_COUNT", "0")))
        except ValueError:
            config_count = 0
        env[f"GIT_CONFIG_KEY_{config_count}"] = "http.https://github.com/.extraheader"
        env[f"GIT_CONFIG_VALUE_{config_count}"] = f"AUTHORIZATION: Basic {auth}"
        env["GIT_CONFIG_COUNT"] = str(config_count + 1)
    return env


def redact_git_output(output: str, token: str) -> str:
    return output.replace(token, "[redacted]") if token else output


def canonical_github_repository(repository: str) -> tuple[str, str]:
    remote = repository.strip()
    ssh_match = re.fullmatch(r"(?:git@github\.com:|ssh://git@github\.com/)([^/]+)/([^/]+?)(?:\.git)?", remote)
    if ssh_match:
        return ssh_match.group(1).casefold(), ssh_match.group(2).casefold()
    owner, name = repository_parts(normalize_github_repo_url(remote))
    return owner.casefold(), name.casefold()


def update_app_checkout(repository_url: str, checkout_path: Path, token: str) -> str:
    """Clone the configured app repo or fast-forward its existing checkout."""
    checkout_path = checkout_path.resolve()
    git_env = git_auth_environment(token)
    staging_path: Path | None = None
    if checkout_path.exists():
        if not checkout_path.is_dir():
            raise ValueError(f"Repository checkout path is not a directory: {checkout_path}")
        probe = subprocess.run(
            ["git", "-C", str(checkout_path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=15,
        )
        if probe.returncode != 0:
            raise ValueError(
                f"Repository checkout path exists but is not a Git repository: {checkout_path}. "
                "Choose an empty directory or an existing checkout."
            )
        remote = subprocess.run(
            ["git", "-C", str(checkout_path), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=15,
        )
        if remote.returncode != 0:
            raise ValueError(f"Could not read the origin remote for checkout: {remote.stderr.strip()}")
        if canonical_github_repository(remote.stdout) != canonical_github_repository(repository_url):
            raise ValueError(
                f"The existing checkout at '{checkout_path}' belongs to a different repository. "
                "Choose its matching repository URL or another checkout directory."
            )
        operation = "pull"
        command = ["git", "-C", str(checkout_path), "pull", "--ff-only"]
    else:
        checkout_path.parent.mkdir(parents=True, exist_ok=True)
        operation = "clone"
        staging_path = Path(tempfile.mkdtemp(prefix=f".{checkout_path.name}.clone-", dir=checkout_path.parent))
        command = ["git", "clone", "--progress", repository_url, str(staging_path)]

    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=300, env=git_env,
        )
    except PermissionError as exc:
        if staging_path is not None:
            shutil.rmtree(staging_path, ignore_errors=True)
        raise ValueError(
            f"Permission denied accessing repository checkout '{checkout_path}'. "
            "Ensure the service user can write there and systemd allows the path."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        if staging_path is not None:
            shutil.rmtree(staging_path, ignore_errors=True)
        detail = redact_git_output((exc.stderr or "") if isinstance(exc.stderr, str) else "", token).strip()
        raise ValueError(f"Git {operation} timed out after 300 seconds. {detail}".strip()) from exc
    except FileNotFoundError as exc:
        if staging_path is not None:
            shutil.rmtree(staging_path, ignore_errors=True)
        raise ValueError("Git is not installed or is not available to the application service.") from exc

    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip())
    output = redact_git_output(output, token)
    if result.returncode:
        if staging_path is not None:
            shutil.rmtree(staging_path, ignore_errors=True)
        detail = output or f"git exited with status {result.returncode}"
        logger.error("App repository %s failed (exit=%s): %s", operation, result.returncode, detail)
        raise ValueError(f"Git {operation} failed: {detail}")
    if staging_path is not None:
        try:
            os.replace(staging_path, checkout_path)
        except Exception:
            shutil.rmtree(staging_path, ignore_errors=True)
            raise
    logger.info("App repository %s completed (checkout=%s): %s", operation, checkout_path, output or "no changes")
    return operation


def copy_newer_app_files(source: Path, destination: Path) -> tuple[int, int]:
    """Copy newer repository files without deleting files unique to the deployment."""
    source = source.resolve(strict=True)
    destination = destination.resolve(strict=True)
    ignored_directories = {".git", ".venv", "venv", "__pycache__", "node_modules"}
    copied = skipped = 0

    def raise_walk_error(error: OSError) -> None:
        raise error

    for current, dirs, filenames in os.walk(source, followlinks=False, onerror=raise_walk_error):
        current_path = Path(current)
        dirs[:] = [
            name for name in dirs
            if name not in ignored_directories and not (current_path / name).is_symlink()
        ]
        relative_dir = current_path.relative_to(source)
        target_dir = destination / relative_dir
        if target_dir.is_symlink():
            raise ValueError(f"Refusing to write through a symbolic link in the app directory: {target_dir}")
        target_dir.mkdir(parents=True, exist_ok=True)
        if not target_dir.resolve().is_relative_to(destination):
            raise ValueError(f"Refusing to write outside the app working directory: {target_dir}")

        for filename in filenames:
            source_file = current_path / filename
            if source_file.is_symlink():
                continue
            relative_file = source_file.relative_to(source)
            target_file = destination / relative_file
            if target_file.is_symlink():
                raise ValueError(f"Refusing to overwrite a symbolic link in the app directory: {target_file}")
            resolved_target = target_file.resolve(strict=False)
            if not resolved_target.is_relative_to(destination):
                raise ValueError(f"Refusing to write outside the app working directory: {target_file}")
            source_stat = source_file.stat()
            try:
                target_stat = target_file.stat()
            except FileNotFoundError:
                target_stat = None
            if target_stat and target_stat.st_mtime_ns >= source_stat.st_mtime_ns:
                skipped += 1
                continue
            if target_stat and not target_file.is_file():
                raise ValueError(f"Cannot replace non-file app path: {target_file}")

            fd, temporary_name = tempfile.mkstemp(prefix=f".{filename}.", dir=target_dir)
            os.close(fd)
            temporary_file = Path(temporary_name)
            try:
                shutil.copy2(source_file, temporary_file)
                os.replace(temporary_file, target_file)
            finally:
                temporary_file.unlink(missing_ok=True)
            copied += 1
    return copied, skipped


def restart_service_process() -> None:
    logger.info("Exiting app process; systemd will restart the service")
    os.kill(os.getpid(), signal.SIGTERM)


def update_clone_job(job_id: str, **updates: Any) -> None:
    with CLONE_JOBS_LOCK:
        job = CLONE_JOBS.get(job_id)
        if job:
            job.update(updates)
            job["updated_at"] = now_iso()


def append_clone_job_output(job_id: str | None, line: str) -> None:
    if not job_id:
        return
    with CLONE_JOBS_LOCK:
        job = CLONE_JOBS.get(job_id)
        if job:
            output = job.setdefault("output", [])
            output.append(line)
            del output[:-100]
            job["progress"] = line
            job["updated_at"] = now_iso()


def git_status(project_id: str) -> dict[str, Any]:
    path = git_project_path(project_id)
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--branch"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
        branch_line = next((l for l in lines if l.startswith("##")), "## unknown")
        branch = branch_line[3:].split("...")[0] if "..." in branch_line else branch_line[3:]
        modified = [l for l in lines if not l.startswith("##")]
        ahead = "ahead" in branch_line
        behind = "behind" in branch_line
        return {
            "branch": branch,
            "modified": len(modified),
            "ahead": ahead,
            "behind": behind,
            "clean": len(modified) == 0 and not ahead and not behind,
        }
    except subprocess.TimeoutExpired:
        raise ValueError("Git status timed out.")
    except FileNotFoundError:
        raise ValueError("Git is not installed on this system.")
    except Exception as exc:
        raise ValueError(f"Git status failed: {exc}")


def git_pull(project_id: str) -> dict[str, Any]:
    path = git_project_path(project_id)
    token = get_provider_key("github")
    try:
        result = subprocess.run(
            ["git", "pull"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=120,
            env=git_auth_environment(token),
        )
        if result.returncode != 0:
            stderr = redact_git_output(result.stderr or "", token)
            stdout = redact_git_output(result.stdout or "", token)
            detail = stderr.strip() or stdout.strip() or f"git exited with status {result.returncode}"
            logger.error("Git pull failed (project=%s, exit=%s): %s", project_id, result.returncode, detail)
            raise ValueError(f"Git pull failed: {detail}")
        status = git_status(project_id)
        output = redact_git_output(result.stdout or "", token)
        logger.info("Git pull succeeded (project=%s, output_bytes=%s)", project_id, len(output.encode("utf-8")))
        return {"success": True, "output": output, "status": status}
    except subprocess.TimeoutExpired:
        raise ValueError("Git pull timed out.")
    except FileNotFoundError:
        raise ValueError("Git is not installed on this system.")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Git pull failed: {exc}")


def git_push(project_id: str) -> dict[str, Any]:
    path = git_project_path(project_id)
    token = get_provider_key("github")
    try:
        result = subprocess.run(
            ["git", "push"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=120,
            env=git_auth_environment(token),
        )
        if result.returncode != 0:
            stderr = redact_git_output(result.stderr or "", token)
            stdout = redact_git_output(result.stdout or "", token)
            detail = stderr.strip() or stdout.strip() or f"git exited with status {result.returncode}"
            logger.error("Git push failed (project=%s, exit=%s): %s", project_id, result.returncode, detail)
            raise ValueError(f"Git push failed: {detail}")
        status = git_status(project_id)
        output = redact_git_output(result.stdout or "", token)
        logger.info("Git push succeeded (project=%s, output_bytes=%s)", project_id, len(output.encode("utf-8")))
        return {"success": True, "output": output, "status": status}
    except subprocess.TimeoutExpired:
        raise ValueError("Git push timed out.")
    except FileNotFoundError:
        raise ValueError("Git is not installed on this system.")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Git push failed: {exc}")


def git_clone(project_id: str, job_id: str | None = None) -> dict[str, Any]:
    logger.info(f"Git clone requested for project: {project_id}")
    project = next((p for p in read_projects() if p.get("id") == project_id), None)
    if not project or not project.get("repository"):
        logger.error(f"Project repository not configured for {project_id}")
        raise ValueError("Project repository is not configured.")
    local_path = str(project.get("local_path", "")).strip()
    if not local_path:
        logger.error(f"Local path not configured for project {project_id}")
        raise ValueError("Local path is not configured for this project.")
    try:
        target_path = Path(local_path).expanduser().resolve()
        if target_path.exists():
            logger.error(f"Local path already exists: {local_path}")
            raise ValueError(f"Local path already exists: {local_path}")
    except PermissionError as exc:
        raise ValueError(
            f"Permission denied accessing clone destination '{local_path}'. Check filesystem permissions "
            "for the service user and its systemd ReadWritePaths allowlist."
        ) from exc
    
    token = get_provider_key("github")
    repo_url = normalize_github_repo_url(str(project["repository"]))
    owner, repo = repository_parts(repo_url)
    repo_url = f"https://github.com/{owner}/{repo}.git"
    logger.info("Cloning GitHub repository (project=%s, repo=%s/%s, destination=%s)",
                project_id, owner, repo, target_path)
    if token:
        logger.debug("Using the configured GitHub connection for clone authentication")
    else:
        logger.warning("GitHub connection is not configured; private repository clone may fail")

    staging_dir: Path | None = None
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        staging_dir = Path(tempfile.mkdtemp(prefix=f".{target_path.name}.clone-", dir=target_path.parent))
        staging_path = staging_dir / target_path.name
        process = subprocess.Popen(
            ["git", "clone", "--progress", repo_url, str(staging_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=git_auth_environment(token),
        )
        logger.info("Git clone process started (project=%s, pid=%s)", project_id, process.pid)
        output_lines: list[str] = []

        def capture_output() -> None:
            if process.stdout is None:
                return
            for raw_line in process.stdout:
                line = redact_git_output(raw_line.rstrip(), token)[-1500:]
                output_lines.append(line)
                del output_lines[:-100]
                logger.info("Git clone output (project=%s): %s", project_id, line[:1500])
                append_clone_job_output(job_id, line)

        output_reader = threading.Thread(target=capture_output, name=f"git-clone-output-{project_id}", daemon=True)
        output_reader.start()
        try:
            return_code = process.wait(timeout=300)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            output_reader.join(timeout=5)
            detail = "\n".join(output_lines[-20:])
            logger.error("Git clone timed out (project=%s, repo=%s/%s, output=%r)",
                         project_id, owner, repo, detail[-4000:])
            raise ValueError(f"Git clone timed out after 300 seconds.{': ' + detail if detail else ''}") from exc
        output_reader.join(timeout=5)
        stdout = "\n".join(output_lines)
        if return_code != 0:
            detail = stdout.strip() or f"git exited with status {return_code}"
            logger.error(
                "Git clone failed (project=%s, repo=%s/%s, exit=%s, output=%r)",
                project_id, owner, repo, return_code, detail[-4000:],
            )
            raise ValueError(f"Git clone failed: {detail}")
        if target_path.exists():
            raise ValueError(f"Local path appeared during clone and will not be overwritten: {target_path}")
        staging_path.replace(target_path)
    except PermissionError as exc:
        logger.error("Permission denied during Git clone (project=%s, destination=%s): %s",
                     project_id, target_path, exc)
        raise ValueError(
            f"Permission denied creating clone at '{target_path}'. Ensure the service user can write to "
            "the parent folder and that the path is allowed by the systemd ReadWritePaths setting."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(str(exc)) from exc
    except FileNotFoundError as exc:
        logger.exception("Git executable is unavailable during clone (project=%s)", project_id)
        raise ValueError("Git is not installed on this system.") from exc
    except ValueError:
        raise
    except Exception as exc:
        logger.exception("Git clone could not be started (project=%s)", project_id)
        raise ValueError(f"Git clone failed: {exc}") from exc
    finally:
        if staging_dir:
            shutil.rmtree(staging_dir, ignore_errors=True)

    logger.info("Git clone succeeded (project=%s, repo=%s/%s, output_bytes=%s)",
                project_id, owner, repo, len(stdout.encode("utf-8")))
    return {"success": True, "output": stdout}


def start_git_clone(project_id: str) -> dict[str, str]:
    project = next((p for p in read_projects() if p.get("id") == project_id), None)
    if not project or not project.get("repository"):
        raise ValueError("Project repository is not configured.")
    local_path = str(project.get("local_path", "")).strip()
    if not local_path:
        raise ValueError("Local path is not configured for this project.")
    try:
        target_path = Path(local_path).expanduser().resolve()
        if target_path.exists():
            raise ValueError(f"Local path already exists: {target_path}")
    except PermissionError as exc:
        logger.error("Permission denied checking Git clone destination (project=%s, path=%s): %s",
                     project_id, local_path, exc)
        raise ValueError(
            f"Permission denied accessing clone destination '{local_path}'. The service is sandboxed; "
            "allow this directory in systemd ReadWritePaths and ensure the service user can access it."
        ) from exc
    target_key = os.path.normcase(str(target_path))
    with CLONE_JOBS_LOCK:
        if target_key in CLONE_TARGETS:
            raise ValueError("A clone to this local path is already running.")
        active_jobs = sum(job.get("status") == "running" for job in CLONE_JOBS.values())
        if active_jobs >= 8:
            raise ValueError("Too many Git clone operations are running; try again when one finishes.")
        completed_jobs = sorted(
            (job for job in CLONE_JOBS.values() if job.get("status") != "running"),
            key=lambda job: job.get("updated_at", ""),
        )
        while len(CLONE_JOBS) >= 100 and completed_jobs:
            oldest = completed_jobs.pop(0)
            CLONE_JOBS.pop(str(oldest.get("id")), None)
        if len(CLONE_JOBS) >= 100:
            raise ValueError("Git clone job history is full; restart the application before starting another clone.")
        CLONE_TARGETS.add(target_key)
        job_id = uuid.uuid4().hex
        CLONE_JOBS[job_id] = {
            "id": job_id,
            "project_id": project_id,
            "status": "running",
            "progress": "Starting Git clone…",
            "output": [],
            "started_at": now_iso(),
            "updated_at": now_iso(),
        }

    def run_clone() -> None:
        try:
            result = git_clone(project_id, job_id)
            update_clone_job(job_id, status="succeeded", result=result, progress="Clone completed.")
        except ValueError as exc:
            logger.error("Git clone job failed (project=%s, job=%s): %s", project_id, job_id, exc)
            update_clone_job(job_id, status="failed", error=str(exc), progress="Clone failed.")
        except Exception as exc:
            logger.exception("Git clone job failed (project=%s, job=%s)", project_id, job_id)
            update_clone_job(job_id, status="failed", error=str(exc), progress="Clone failed.")
        finally:
            with CLONE_JOBS_LOCK:
                CLONE_TARGETS.discard(target_key)

    try:
        threading.Thread(target=run_clone, name=f"git-clone-{project_id}", daemon=True).start()
    except Exception:
        with CLONE_JOBS_LOCK:
            CLONE_TARGETS.discard(target_key)
            CLONE_JOBS.pop(job_id, None)
        raise
    return {"job_id": job_id, "status": "running"}


def get_git_clone_job(project_id: str, job_id: str) -> dict[str, Any]:
    with CLONE_JOBS_LOCK:
        job = CLONE_JOBS.get(job_id)
        if not job or job.get("project_id") != project_id:
            raise ValueError("Git clone job not found.")
        return {**job, "output": list(job.get("output") or [])}


def resolve_local_view_path(root: Path, relative_path: str) -> Path:
    cleaned = str(relative_path or "").strip().replace("\\", "/")
    if (
        len(cleaned) > 500
        or cleaned.startswith("/")
        or (cleaned and (Path(cleaned).is_absolute() or re.match(r"^[A-Za-z]:", cleaned)))
        or any(part in ("", ".", "..") or part.startswith(".") for part in cleaned.split("/") if cleaned)
    ):
        raise ValueError("Invalid or hidden local path.")
    cleaned = cleaned.rstrip("/")
    root_path = root.expanduser().resolve()
    target = (root_path / cleaned).resolve()
    try:
        target.relative_to(root_path)
    except ValueError as exc:
        raise ValueError("The requested path is outside the local project folder.") from exc
    return target


def local_file_listing(root: Path, relative_path: str = "") -> dict[str, Any]:
    root_path = root.expanduser().resolve()
    if not root_path.is_dir():
        raise ValueError(f"Local folder is not available: {root_path}")
    folder = resolve_local_view_path(root_path, relative_path)
    if not folder.is_dir():
        raise ValueError("The requested local path is not a folder.")
    entries = []
    for item in sorted(folder.iterdir(), key=lambda entry: (not entry.is_dir(), entry.name.casefold())):
        if item.name.startswith("."):
            continue
        try:
            resolved = item.resolve()
            resolved.relative_to(root_path)
        except (OSError, ValueError):
            continue
        if not (resolved.is_dir() or resolved.is_file()):
            continue
        rel = resolved.relative_to(root_path).as_posix()
        entries.append(
            {
                "name": item.name,
                "path": rel,
                "type": "dir" if resolved.is_dir() else "file",
                "size": resolved.stat().st_size if resolved.is_file() else 0,
            }
        )
    return {
        "path": folder.relative_to(root_path).as_posix() if folder != root_path else "",
        "entries": entries[:1000],
        "truncated": len(entries) > 1000,
    }


def read_local_text_file(root: Path, relative_path: str) -> dict[str, str]:
    root_path = root.expanduser().resolve()
    file_path = resolve_local_view_path(root_path, relative_path)
    if not file_path.is_file():
        raise ValueError("The requested local path is not a file.")
    size = file_path.stat().st_size
    if size > 300000:
        raise ValueError("This file is larger than the 300 KB viewer limit.")
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"Could not read the local file: {exc}") from exc
    if b"\x00" in raw:
        raise ValueError("Binary files are not shown in the viewer.")
    return {
        "path": file_path.relative_to(root_path).as_posix(),
        "content": raw.decode("utf-8", errors="replace"),
    }


def local_project_root(project_id: str) -> Path:
    project = next((p for p in read_projects() if p.get("id") == project_id), None)
    if not project:
        raise ValueError("Project not found.")
    local_path = str(project.get("local_path", "")).strip()
    if not local_path:
        raise ValueError("Local path is not configured for this project.")
    return Path(local_path).expanduser().resolve()


def github_file_listing(project_id: str, folder_path: str = "") -> dict[str, Any]:
    project = github_project(project_id)
    owner, repo = repository_parts(project["repository"])
    has_token = bool(get_provider_key("github"))
    meta = github_api(f"/repos/{owner}/{repo}", require_auth=has_token)
    branch = str(meta.get("default_branch") or "main")
    clean_path = safe_repo_path(folder_path)
    suffix = "/" + "/".join(urllib.parse.quote(seg, safe="") for seg in clean_path.split("/")) if clean_path else ""
    res = github_api(
        f"/repos/{owner}/{repo}/contents{suffix}?ref={urllib.parse.quote(branch, safe='')}",
        require_auth=has_token,
    )
    if not isinstance(res, list):
        res = [res]
    entries = [
        {
            "name": str(item.get("name", "")),
            "path": str(item.get("path", "")),
            "type": "dir" if item.get("type") == "dir" else "file",
            "size": int(item.get("size") or 0),
        }
        for item in res
        if isinstance(item, dict)
    ]
    return {"branch": branch, "path": clean_path, "entries": entries}


def github_read_text_file(project_id: str, file_path: str) -> dict[str, Any]:
    project = github_project(project_id)
    owner, repo = repository_parts(project["repository"])
    has_token = bool(get_provider_key("github"))
    meta = github_api(f"/repos/{owner}/{repo}", require_auth=has_token)
    branch = str(meta.get("default_branch") or "main")
    clean_path = safe_repo_path(file_path)
    if not clean_path or clean_path.split("/")[-1].startswith("."):
        raise ValueError("Choose a visible file path.")
    enc_path = "/".join(urllib.parse.quote(seg, safe="") for seg in clean_path.split("/"))
    item = github_api(
        f"/repos/{owner}/{repo}/contents/{enc_path}?ref={urllib.parse.quote(branch, safe='')}",
        require_auth=has_token,
    )
    if not isinstance(item, dict) or item.get("type") != "file":
        raise ValueError("That path is not a file.")
    if int(item.get("size") or 0) > 300000:
        raise ValueError("This file is larger than the 300 KB viewer limit.")
    if item.get("encoding") != "base64":
        raise ValueError("GitHub did not return text file content.")
    decoded_bytes = base64.b64decode(re.sub(r"\s+", "", str(item.get("content", ""))))
    if b"\x00" in decoded_bytes:
        raise ValueError("Binary files are not shown in the viewer.")
    return {"branch": branch, "path": clean_path, "content": decoded_bytes.decode("utf-8", errors="replace")}


def github_task_context(task: dict[str, Any]) -> str:
    project = github_project(str(task.get("project_id") or "default"))
    owner, repo = repository_parts(project["repository"])
    has_token = bool(get_provider_key("github"))
    meta = github_api(f"/repos/{owner}/{repo}", require_auth=has_token)
    branch = str(meta.get("default_branch") or "main")
    mod = next((m for m in project.get("modules", []) if m.get("id") == task.get("module_id")), None)
    scope = safe_repo_path(str((mod or {}).get("path", "")))
    tree_resp = github_api(
        f"/repos/{owner}/{repo}/git/trees/{urllib.parse.quote(branch, safe='')}?recursive=1",
        require_auth=has_token,
    )
    entries = tree_resp.get("tree", []) if isinstance(tree_resp, dict) else []
    allowed_exts = (
        ".md", ".txt", ".html", ".css", ".js", ".ts", ".tsx", ".jsx",
        ".py", ".json", ".toml", ".yml", ".yaml", ".xml", ".sh",
    )
    skip_dirs = {"node_modules", "dist", "build", "vendor", ".git", ".venv", "venv", "coverage"}
    files = []
    for item in entries:
        if not isinstance(item, dict) or item.get("type") != "blob":
            continue
        p = str(item.get("path", ""))
        if not p.lower().endswith(allowed_exts):
            continue
        parts = p.split("/")
        if any(seg in skip_dirs or seg.startswith(".") for seg in parts):
            continue
        if scope and not p.startswith(scope + "/"):
            continue
        files.append(item)

    prio = {"README.md", "readme.md", "package.json", "pyproject.toml"}
    files.sort(key=lambda x: (0 if str(x.get("path", "")).split("/")[-1] in prio else 1, str(x.get("path", ""))))
    chunks: list[str] = []
    total = 0
    for item in files[:16]:
        sz = int(item.get("size") or 0)
        if sz > 12000 or total + sz > 24000:
            continue
        ipath = str(item.get("path", ""))
        enc_path = "/".join(urllib.parse.quote(seg, safe="") for seg in ipath.split("/"))
        try:
            raw = github_api(
                f"/repos/{owner}/{repo}/contents/{enc_path}?ref={urllib.parse.quote(branch, safe='')}",
                require_auth=has_token,
            )
            if raw.get("encoding") != "base64":
                continue
            b = base64.b64decode(re.sub(r"\s+", "", str(raw.get("content", ""))))
            if b"\x00" in b:
                continue
            txt = b.decode("utf-8", errors="replace")
            chunks.append(f"--- {ipath} ---\n{txt}")
            total += len(b)
        except Exception:
            continue
    return (
        f"Repository: {owner}/{repo}, default branch: {branch}, editable scope: {scope or '/'}\n"
        + ("\n\n".join(chunks) if chunks else "No readable source files were found in the selected scope.")
    )


def apply_repository_manifest(task: dict[str, Any], reply: str, provider: str, attempt_number: int) -> None:
    project = github_project(str(task.get("project_id") or "default"))
    owner, repo = repository_parts(project["repository"])
    clean = reply.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean, flags=re.IGNORECASE)
    try:
        manifest = json.loads(clean)
    except Exception as exc:
        raise ValueError("Agent must return a JSON file-change manifest for a GitHub deliverable.") from exc
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list) or not (1 <= len(files) <= 20):
        raise ValueError("File-change manifest must contain 1 to 20 files.")

    mod = next((m for m in project.get("modules", []) if m.get("id") == task.get("module_id")), None)
    scope = safe_repo_path(str((mod or {}).get("path", "")))
    allowed_exts = (
        ".md", ".txt", ".html", ".css", ".js", ".ts", ".tsx", ".jsx",
        ".py", ".json", ".toml", ".yml", ".yaml", ".xml", ".sh",
    )
    protected = {
        "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
        "poetry.lock", "uv.lock", "cargo.lock", "go.sum",
    }
    clean_files: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("content"), str):
            raise ValueError("Each manifest file needs a path and text content.")
        fpath = safe_repo_path(str(item.get("path", "")))
        parts = fpath.split("/")
        fname = parts[-1].lower() if parts else ""
        if (
            not fpath
            or fname in protected
            or fpath in seen
            or (scope and not fpath.startswith(scope + "/"))
            or any(seg.startswith(".") or seg in ("node_modules", "dist", "build", "vendor") for seg in parts)
            or not fpath.lower().endswith(allowed_exts)
        ):
            raise ValueError(f"Refusing unsafe or out-of-scope repository path: {fpath[:120]}")
        content = item["content"]
        if len(content.encode("utf-8")) > 50000 or "\x00" in content:
            raise ValueError(f"File content is too large or binary: {fpath}")
        clean_files.append((fpath, content))
        seen.add(fpath)

    meta = github_api(f"/repos/{owner}/{repo}")
    base = str(meta.get("default_branch") or "main")
    base_ref = github_api(f"/repos/{owner}/{repo}/git/ref/heads/{urllib.parse.quote(base, safe='')}")
    base_commit = base_ref["object"]["sha"]
    commit_obj = github_api(f"/repos/{owner}/{repo}/git/commits/{base_commit}")
    base_tree = commit_obj["tree"]["sha"]
    branch = f"ai-team/{task['id'].lower()}-a{attempt_number:02d}"

    github_api(f"/repos/{owner}/{repo}/git/refs", "POST", {"ref": f"refs/heads/{branch}", "sha": base_commit})
    tree_items = []
    for fpath, fcontent in clean_files:
        blob = github_api(f"/repos/{owner}/{repo}/git/blobs", "POST", {"content": fcontent, "encoding": "utf-8"})
        tree_items.append({"path": fpath, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = github_api(f"/repos/{owner}/{repo}/git/trees", "POST", {"base_tree": base_tree, "tree": tree_items})
    commit = github_api(
        f"/repos/{owner}/{repo}/git/commits",
        "POST",
        {"message": f"{task['id']}: {task.get('title', '')}"[:200], "tree": tree["sha"], "parents": [base_commit]},
    )
    enc_branch = "/".join(urllib.parse.quote(seg, safe="") for seg in branch.split("/"))
    github_api(f"/repos/{owner}/{repo}/git/refs/heads/{enc_branch}", "PATCH", {"sha": commit["sha"], "force": False})
    pull = github_api(
        f"/repos/{owner}/{repo}/pulls",
        "POST",
        {
            "title": f"{task['id']}: {task.get('title', '')}"[:240],
            "head": branch,
            "base": base,
            "body": (
                f"Automated {provider} changes for {task['id']}.\n\n"
                f"{str(task.get('request', ''))[:4000]}\n\n"
                "Agent report and validation are recorded on the local task board."
            ),
        },
    )
    task["pull_request"] = {
        "number": int(pull["number"]),
        "url": str(pull.get("html_url", "")),
        "branch": branch,
        "state": str(pull.get("state", "open")),
        "created_at": now_iso(),
    }
    task["repository_change_summary"] = str(manifest.get("summary", ""))[:2000]
    write_task(task)


def github_postprocess_task(task_id: str, create_pr: bool = False) -> None:
    task = read_task(task_id)
    if not task:
        return
    if (
        create_pr
        and task.get("task_type") != "pm_planning"
        and task.get("assigned_role") in ("Frontend developer", "Backend developer", "Documentation")
    ):
        try:
            project = next((p for p in read_projects() if p.get("id") == (task.get("project_id") or "default")), None)
            if project and project.get("repository"):
                owner, repo = repository_parts(project["repository"])
                branch = f"ai-team/{task_id.lower()}"
                existing_pulls = github_api(
                    f"/repos/{owner}/{repo}/pulls?head={urllib.parse.quote(f'{owner}:{branch}', safe='')}&state=open"
                )
                if isinstance(existing_pulls, list) and existing_pulls:
                    created = existing_pulls[0]
                else:
                    meta = github_api(f"/repos/{owner}/{repo}")
                    created = github_api(
                        f"/repos/{owner}/{repo}/pulls",
                        "POST",
                        {
                            "title": f"{task_id}: {task.get('title', '')}"[:240],
                            "head": branch,
                            "base": meta.get("default_branch") or "main",
                            "body": f"Automated change for {task_id}.\n\n{str(task.get('request', ''))[:4000]}",
                            "draft": False,
                        },
                    )
                latest = read_task(task_id)
                if latest:
                    latest["pull_request"] = {
                        "number": int(created["number"]),
                        "url": str(created.get("html_url", "")),
                        "branch": branch,
                        "state": str(created.get("state", "open")),
                        "created_at": now_iso(),
                    }
                    latest.setdefault("activity", []).append(
                        {"at": now_iso(), "type": "pull_request_created", "message": f"GitHub pull request #{created['number']} created."}
                    )
                    write_task(latest)
                    append_event({"at": now_iso(), "task_id": task_id, "type": "pull_request_created", "pull_request": created["number"]})
        except Exception as exc:
            latest = read_task(task_id)
            if latest:
                msg = str(exc)[:500]
                latest["github_error"] = msg
                latest.setdefault("activity", []).append(
                    {"at": now_iso(), "type": "github_error", "message": f"Could not create the pull request: {msg[:350]}"}
                )
                write_task(latest)

    refreshed = read_task(task_id)
    if not refreshed:
        return
    pull = refreshed.get("pull_request")
    if (
        not isinstance(pull, dict)
        or not pull.get("number")
        or refreshed.get("status") != "Done"
        or (refreshed.get("validation") or {}).get("status") != "passed"
    ):
        return
    project = next((p for p in read_projects() if p.get("id") == (refreshed.get("project_id") or "default")), None)
    if not project or not project.get("repository"):
        return
    try:
        owner, repo = repository_parts(project["repository"])
        merged = github_api(
            f"/repos/{owner}/{repo}/pulls/{pull['number']}/merge",
            "PUT",
            {"merge_method": "squash"},
        )
        if not merged.get("merged"):
            raise ValueError("GitHub did not confirm the merge.")
        pull["state"] = "merged"
        pull["merged_at"] = now_iso()
        refreshed["pull_request"] = pull
        refreshed.setdefault("activity", []).append(
            {
                "at": now_iso(),
                "type": "pull_request_merged",
                "message": f"GitHub pull request #{pull['number']} was merged after validation passed.",
            }
        )
        write_task(refreshed)
        append_event({"at": now_iso(), "task_id": task_id, "type": "pull_request_merged", "pull_request": pull["number"]})
    except Exception as exc:
        msg = str(exc)[:500]
        refreshed["github_error"] = msg
        refreshed.setdefault("activity", []).append(
            {"at": now_iso(), "type": "github_error", "message": f"Automatic merge failed: {msg[:350]}"}
        )
        write_task(refreshed)


# --- Notifications, Quality, Usage, CPU ---
def get_notifications() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    all_tasks = sorted(
        read_tasks(),
        key=lambda t: str(t.get("updated_at") or t.get("created_at") or ""),
        reverse=True,
    )
    for t in all_tasks:
        tid = t.get("id", "")
        upd = t.get("updated_at", "")
        if t.get("status") == "Blocked":
            items.append(
                {
                    "id": f"task:{tid}:blocked:{upd}",
                    "kind": "blocked",
                    "severity": "high",
                    "title": f"{tid} is blocked",
                    "message": t.get("block_reason") or "Task needs attention.",
                    "task_id": tid,
                }
            )
        if t.get("status") == "Waiting for capacity":
            atts = t.get("attempts") or []
            prov = atts[-1].get("provider", "Provider") if atts else "Provider"
            items.append(
                {
                    "id": f"task:{tid}:capacity:{upd}",
                    "kind": "capacity",
                    "severity": "medium",
                    "title": f"{prov} is unavailable",
                    "message": f"{tid} is waiting for capacity.",
                    "task_id": tid,
                }
            )
        if t.get("github_error"):
            items.append(
                {
                    "id": f"task:{tid}:github:{upd}",
                    "kind": "github",
                    "severity": "high",
                    "title": f"GitHub needs attention for {tid}",
                    "message": str(t["github_error"]),
                    "task_id": tid,
                }
            )

    avail = set(configured_providers())
    if not avail and any(t.get("status") == "Ready" for t in all_tasks):
        items.append(
            {
                "id": "providers:none:configured",
                "kind": "provider",
                "severity": "high",
                "title": "No AI provider is connected",
                "message": "Ready tasks are queued. Add a provider key in Connections & routing.",
            }
        )
    for t in all_tasks:
        chosen = str(t.get("provider", ""))
        if t.get("status") == "Ready" and chosen in PROVIDERS and chosen not in avail:
            items.append(
                {
                    "id": f"task:{t['id']}:provider-missing:{chosen}",
                    "kind": "provider",
                    "severity": "medium",
                    "title": f"{t['id']} is waiting for {chosen}",
                    "message": f"This task explicitly selected {chosen}. Connect it or edit the task's provider.",
                    "task_id": t["id"],
                }
            )
    if not get_provider_key("github"):
        items.append(
            {
                "id": "connection:github:missing",
                "kind": "connection",
                "severity": "low",
                "title": "GitHub is not connected",
                "message": "Add a GitHub token in Connections & routing to enable repository workflows.",
            }
        )

    dismissed_path = DATA / "settings" / "notifications_dismissed.json"
    dismissed: set[str] = set()
    if dismissed_path.is_file():
        try:
            arr = json.loads(dismissed_path.read_text(encoding="utf-8"))
            if isinstance(arr, list):
                dismissed = {str(x) for x in arr}
        except Exception:
            pass
    return [i for i in items if i["id"] not in dismissed]


def dismiss_notification(nid: str) -> None:
    ensure_data()
    p = DATA / "settings" / "notifications_dismissed.json"
    items: list[str] = []
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                items = [str(x) for x in raw]
        except Exception:
            items = []
    if nid not in items:
        items.append(nid)
    atomic_json(p, items)


def get_provider_quality() -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for t in read_tasks():
        role = str(t.get("assigned_role") or "Unknown")
        for att in t.get("attempts") or []:
            prov = str(att.get("provider", ""))
            if prov not in PROVIDERS:
                continue
            key = (role, prov)
            row = grouped.setdefault(
                key,
                {
                    "role": role,
                    "provider": prov,
                    "requests": 0,
                    "replies": 0,
                    "quota_or_service_errors": 0,
                    "measured": 0,
                    "passed": 0,
                    "failed": 0,
                    "total_seconds": 0.0,
                    "timed": 0,
                },
            )
            row["requests"] += 1
            if att.get("state") == "succeeded":
                row["replies"] += 1
            if att.get("state") in ("provider_unavailable", "waiting_for_capacity", "timed_out", "failed"):
                row["quota_or_service_errors"] += 1
            qo = att.get("quality_outcome")
            if qo in ("passed", "failed"):
                row["measured"] += 1
                row[qo] += 1
            s_ts = parse_iso_epoch(att.get("started_at"))
            f_ts = parse_iso_epoch(att.get("finished_at"))
            if s_ts is not None and f_ts is not None and f_ts >= s_ts:
                row["total_seconds"] += f_ts - s_ts
                row["timed"] += 1

    out: list[dict[str, Any]] = []
    for r in grouped.values():
        out.append(
            {
                "role": r["role"],
                "provider": r["provider"],
                "requests": r["requests"],
                "replies": r["replies"],
                "quota_or_service_errors": r["quota_or_service_errors"],
                "measured": r["measured"],
                "passed": r["passed"],
                "failed": r["failed"],
                "qa_pass_rate": round(100 * r["passed"] / r["measured"]) if r["measured"] else None,
                "average_seconds": round(r["total_seconds"] / r["timed"], 1) if r["timed"] else None,
            }
        )
    out.sort(key=lambda x: (x["role"], x["provider"]))
    return out


def get_local_llm_usage() -> list[dict[str, Any]]:
    log_path = DATA / "logs" / "activity.jsonl"
    if not log_path.is_file():
        return []
    calls: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        etype = ev.get("type")
        if etype == "local_llm_request_started":
            if pending:
                calls.append(pending)
            pending = {
                "provider": ev.get("provider") or "Ollama",
                "model": ev.get("model") or OLLAMA_MODEL,
                "started_at": ev.get("at"),
                "state": "in_progress",
            }
        elif etype in ("local_llm_reply", "local_llm_error") and pending:
            pending["finished_at"] = ev.get("at")
            pending["state"] = "succeeded" if etype == "local_llm_reply" else "failed"
            pending["duration_seconds"] = ev.get("duration_seconds")
            calls.append(pending)
            pending = None
    if pending:
        calls.append(pending)
    return calls


def read_ai_stat_resets() -> list[dict[str, Any]]:
    p = DATA / "logs" / "ai_stats_resets.json"
    if not p.is_file():
        return []
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
        return v if isinstance(v, list) else []
    except Exception:
        return []


def append_ai_stat_reset(reset: dict[str, Any]) -> None:
    resets = read_ai_stat_resets()
    resets.append(reset)
    atomic_json(DATA / "logs" / "ai_stats_resets.json", resets)


def get_cpu_percent() -> int | None:
    global PREV_CPU_SAMPLE
    stat_file = Path("/proc/stat")
    if stat_file.is_file():
        try:
            first = stat_file.read_text(encoding="utf-8").splitlines()[0]
            nums = [int(x) for x in first.split()[1:]]
            idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
            total = sum(nums)
            with CPU_SAMPLE_LOCK:
                if PREV_CPU_SAMPLE is None:
                    PREV_CPU_SAMPLE = (total, idle)
                    return 0
                prev_total, prev_idle = PREV_CPU_SAMPLE
                PREV_CPU_SAMPLE = (total, idle)
            dt = total - prev_total
            di = idle - prev_idle
            if dt <= 0:
                return 0
            return max(0, min(100, round(100 * (1.0 - di / dt))))
        except Exception:
            return None
    return None


# --- PM Planning, Deterministic Validation & Provider Calls ---
def parse_pm_plan_response(reply: str, project_id: str = "default") -> dict[str, Any]:
    text = reply.strip()
    if text.startswith("```"):
        nl = text.find("\n")
        text = (text[nl + 1 :] if nl >= 0 else text).rstrip("`").strip()
    s = text.find("{")
    e = text.rfind("}")
    if s < 0 or e <= s:
        raise ValueError("PM/PO response did not contain a JSON object.")
    data = json.loads(text[s : e + 1])
    if not isinstance(data, dict):
        raise ValueError("PM/PO response must be a JSON object.")
    kind = data.get("type")
    summary = str(data.get("summary", "")).strip()[:2000]

    if kind == "questions":
        questions = data.get("questions")
        if not isinstance(questions, list) or not (1 <= len(questions) <= 8):
            raise ValueError("A clarification response must contain between 1 and 8 questions.")
        norm_q = []
        for q in questions:
            if not isinstance(q, dict):
                raise ValueError("Each clarification question must be an object.")
            title = str(q.get("title", "")).strip()[:120]
            question = str(q.get("question", "")).strip()[:2000]
            opts = q.get("options")
            if not title or not question or not isinstance(opts, list) or not (2 <= len(opts) <= 8):
                raise ValueError("Each question needs a title, question, and 2 to 8 answer options.")
            clean_opts = []
            for o in opts:
                if isinstance(o, str) and o.strip():
                    clean_opts.append({"label": o.strip()[:160], "description": ""})
                elif isinstance(o, dict) and str(o.get("label", "")).strip():
                    clean_opts.append(
                        {
                            "label": str(o["label"]).strip()[:160],
                            "description": str(o.get("description", "")).strip()[:500],
                        }
                    )
            if len(clean_opts) < 2:
                raise ValueError("Each question must have at least two usable options.")
            norm_q.append(
                {
                    "title": title,
                    "question": question,
                    "why": str(q.get("why", "")).strip()[:1000],
                    "options": clean_opts,
                    "allow_free_text": q.get("allow_free_text") is not False,
                }
            )
        return {"type": "questions", "summary": summary, "questions": norm_q}

    if kind == "plan":
        tasks = data.get("tasks")
        if not isinstance(tasks, list) or not (1 <= len(tasks) <= 12):
            raise ValueError("A plan response must contain between 1 and 12 tasks.")
        proj = next((p for p in read_projects() if p.get("id") == project_id), None)
        valid_mods = {m["id"] for m in (proj or {}).get("modules", [])}
        norm_t = []
        for t in tasks:
            if not isinstance(t, dict):
                raise ValueError("Each proposed task must be an object.")
            title = str(t.get("title", "")).strip()[:160]
            req = str(t.get("request", "")).strip()[:12000]
            role = str(t.get("role", "")).strip()
            mid = str(t.get("module_id", "")).strip()
            checks = t.get("acceptance_checks", [])
            if not title or not req or role not in WORK_ROLES or not isinstance(checks, list) or (mid and mid not in valid_mods):
                raise ValueError("Each proposed task needs a title, request, valid work role, and acceptance-check list.")
            clean_checks = [str(c).strip()[:500] for c in checks if str(c).strip()][:20]
            produces = t["produces_deliverable"] if isinstance(t.get("produces_deliverable"), bool) else (role != "PM/PO")
            if produces and not clean_checks:
                clean_checks.append("Manually review the resulting deliverable against the task request.")
            norm_t.append(
                {
                    "title": title,
                    "request": req,
                    "role": role,
                    "module_id": mid,
                    "acceptance_checks": clean_checks,
                    "produces_deliverable": produces,
                }
            )
        ov = data.get("project_overview")
        if not isinstance(ov, dict):
            raise ValueError("A final plan must include a project_overview for user approval.")
        ov_docs: dict[str, str] = {}
        for slug in ("project_brief", "technical_overview", "decisions"):
            val = ov.get(slug)
            if not isinstance(val, str) or not val.strip() or len(val) > 20000:
                raise ValueError(f"The project overview needs non-empty {slug} text under 20,000 characters.")
            ov_docs[slug] = val.strip()
        return {"type": "plan", "summary": summary, "tasks": norm_t, "project_overview": ov_docs}

    raise ValueError("PM/PO response type must be 'questions' or 'plan'.")


def finish_pm_planning_task(task: dict[str, Any], reply: str, provider: str) -> tuple[str, str]:
    try:
        res = parse_pm_plan_response(reply, str(task.get("project_id") or "default"))
        ts = now_iso()
        ids: list[str] = []
        if res["type"] == "questions":
            for item in res["questions"]:
                cid = next_task_id()
                ids.append(cid)
                child = {
                    "id": cid,
                    "task_type": "clarification_question",
                    "title": item["title"],
                    "request": item["question"],
                    "question_data": {
                        "why": item["why"],
                        "options": item["options"],
                        "allow_free_text": item["allow_free_text"],
                    },
                    "assigned_role": "PM/PO",
                    "provider": "Automatic",
                    "status": "Needs answer",
                    "planning_root_id": task.get("planning_root_id"),
                    "planning_task_id": task["id"],
                    "planning_round": task.get("planning_round") or 1,
                    "project_id": task.get("project_id") or "default",
                    "module_id": "",
                    "created_at": ts,
                    "updated_at": ts,
                    "attempts": [],
                    "validation": None,
                    "activity": [{"at": ts, "type": "created", "message": "PM/PO clarification question; awaiting your answer."}],
                }
                write_task(child)
                append_event({"at": ts, "task_id": cid, "type": "created", "status": "Needs answer", "parent_task_id": task["id"]})
            task["child_task_ids"] = ids
            task["planning_result_type"] = "questions"
            task["planning_summary"] = res["summary"]
            task["validation"] = {
                "status": "passed",
                "checks": [{"check": "PM/PO response structure", "kind": "schema", "passed": True, "message": "Response parsed and saved."}],
                "validated_at": ts,
            }
            return "Done", f"PM/PO clarification questions logged as {', '.join(ids)}. Waiting for your answers."

        ov_id = next_task_id()
        ids.append(ov_id)
        write_task(
            {
                "id": ov_id,
                "task_type": "project_overview",
                "title": "Project overview — review and approve",
                "request": "Review the PM/PO's proposed project brief, technical overview, and decisions. Edit any text before approval.",
                "overview_documents": res["project_overview"],
                "assigned_role": "PM/PO",
                "provider": "Automatic",
                "status": "Needs approval",
                "planning_root_id": task.get("planning_root_id"),
                "source_plan_task_id": task["id"],
                "project_id": task.get("project_id") or "default",
                "module_id": "",
                "created_at": ts,
                "updated_at": ts,
                "attempts": [],
                "validation": None,
                "activity": [{"at": ts, "type": "created", "message": "PM/PO prepared a project overview; waiting for your approval."}],
            }
        )
        work_ids: list[str] = []
        for item in res["tasks"]:
            wid = next_task_id()
            ids.append(wid)
            work_ids.append(wid)
            write_task(
                {
                    "id": wid,
                    "task_type": "work",
                    "title": item["title"],
                    "request": item["request"],
                    "assigned_role": item["role"],
                    "provider": "Automatic",
                    "acceptance_checks": item["acceptance_checks"],
                    "produces_deliverable": item["produces_deliverable"],
                    "status": "Draft",
                    "planning_root_id": task.get("planning_root_id"),
                    "source_plan_task_id": task["id"],
                    "project_id": task.get("project_id") or "default",
                    "module_id": item["module_id"],
                    "created_at": ts,
                    "updated_at": ts,
                    "attempts": [],
                    "validation": None,
                    "activity": [{"at": ts, "type": "created", "message": f"Proposed by PM/PO in {task['id']}; review before marking Ready."}],
                }
            )
        task["project_overview_task_id"] = ov_id
        task["work_task_ids"] = work_ids
        task["child_task_ids"] = ids
        task["planning_result_type"] = "plan"
        task["planning_summary"] = res["summary"]
        task["validation"] = {
            "status": "passed",
            "checks": [{"check": "PM/PO response structure", "kind": "schema", "passed": True, "message": "Response parsed and saved."}],
            "validated_at": ts,
        }
        return "Done", f"PM/PO created project overview {ov_id} and {len(work_ids)} draft work task(s): {', '.join(work_ids)}."
    except Exception as exc:
        msg = str(exc)
        task["validation"] = {
            "status": "failed",
            "checks": [{"check": "PM/PO response structure", "kind": "schema", "passed": False, "message": msg}],
            "validated_at": now_iso(),
        }
        task["planning_error"] = msg
        return "Rework needed", f"{provider} replied, but the PM/PO result could not be logged: {msg}"


def deterministic_validation(checks: list[str], reply: str) -> dict[str, Any]:
    results = []
    for raw_check in checks:
        raw = str(raw_check).strip()
        cmd, sep, expected = raw.partition(":")
        cmd_lower = cmd.strip().lower()
        exp = expected.strip()
        if not sep or cmd_lower not in ("exact", "contains", "not_contains"):
            results.append({"check": raw, "kind": "manual", "passed": None, "message": "Requires manual validation."})
            continue
        if cmd_lower == "exact":
            passed = reply.strip() == exp
            desc = "Reply exactly matches expected text." if passed else "Reply did not exactly match expected text."
        elif cmd_lower == "contains":
            passed = exp in reply
            desc = "Required text is present." if passed else "Required text is missing."
        else:
            passed = exp not in reply
            desc = "Forbidden text is absent." if passed else "Forbidden text was found."
        results.append({"check": raw, "kind": cmd_lower, "passed": passed, "message": desc})

    failed = any(r["passed"] is False for r in results)
    all_auto = bool(results) and all(r["passed"] is not None for r in results)
    status = "failed" if failed else ("passed" if all_auto else "pending_manual")
    return {"status": status, "checks": results, "validated_at": now_iso()}


def build_repo_agent_instructions(task: dict[str, Any], provider: str) -> str:
    if (
        provider == "Antigravity"
        or task.get("task_type") == "pm_planning"
        or task.get("assigned_role") not in ("Frontend developer", "Backend developer", "Documentation")
    ):
        return ""
    proj = next((p for p in read_projects() if p.get("id") == (task.get("project_id") or "default")), None)
    if not proj or not proj.get("repository"):
        return ""
    snapshot = github_task_context(task)
    return (
        "\n\nRepository snapshot (read-only context):\n"
        + snapshot
        + '\n\nReturn only valid JSON, no markdown, with this shape: '
        + '{"summary":"brief change summary","files":[{"path":"relative/path","content":"complete replacement file text"}],"checks":["checks performed"]}. '
        + "Include complete contents for each changed or new text file. Do not return diffs."
    )


def call_gemini_task(task: dict[str, Any]) -> tuple[str, dict[str, Any]]:
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
    if task.get("task_type") == "pm_planning":
        prompt += "\n\n" + PM_PLANNING_CONTRACT
    if (task.get("validation") or {}).get("status") == "failed":
        prompt += "\n\nPrevious attempt failed validation checks:\n" + json.dumps(task["validation"].get("checks", []), indent=2)

    sys_inst = combined_agent_prompt(role) + ("\n\n" + PM_PLANNING_CONTRACT if task.get("task_type") == "pm_planning" else "")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(GEMINI_MODEL, safe='')}:generateContent?key={urllib.parse.quote(api_key, safe='')}"
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


def upsert_antigravity_credential(
    api_key: str,
    cred_id: str,
    token: str,
    header_name: str | None = None,
    prefix: str | None = None,
) -> bool:
    if not api_key or not token:
        return False
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": HTTP_USER_AGENT,
        "x-goog-api-key": api_key,
    }
    payload: dict[str, Any] = {"type": "bearer_token", "token": token}
    if header_name:
        payload["header_name"] = header_name
    if prefix is not None:
        payload["prefix"] = prefix

    patch_req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/credentials/{urllib.parse.quote(cred_id, safe='')}",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(patch_req, timeout=15) as resp:
            resp.read()
        return True
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            return False
    except Exception:
        return False

    post_req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/credentials",
        data=json.dumps({"id": cred_id, **payload}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(post_req, timeout=15) as resp:
            resp.read()
        return True
    except Exception:
        return False


def sync_antigravity_github_credentials(api_key: str, github_token: str) -> tuple[bool, bool]:
    """Create or update Git Smart-HTTP Basic and GitHub REST API Bearer credentials for Antigravity."""
    if not api_key or not github_token:
        return False, False
    basic_token = base64.b64encode(f"x-access-token:{github_token}".encode("utf-8")).decode("ascii")
    git_basic_ok = upsert_antigravity_credential(
        api_key,
        "github-git-basic",
        basic_token,
        header_name="Authorization",
        prefix="Basic",
    )
    api_bearer_ok = upsert_antigravity_credential(api_key, "github-api-bearer", github_token)
    return git_basic_ok, api_bearer_ok


def format_antigravity_error(status: int, raw_detail: str) -> str:
    cleaned = str(raw_detail or "").strip()[:1300]
    if status >= 500 and "internal error" in cleaned.lower():
        return (
            f"{cleaned} (Note: Antigravity managed agent requires a paid-tier Gemini API key with Cloud Billing enabled, "
            "or the remote preview sandbox service encountered a transient internal error.)"
        )
    return cleaned


def call_antigravity_task(task: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    api_key = get_provider_key("gemini")
    if not api_key:
        raise ProviderFailure("Antigravity", 401, "Gemini / Antigravity API key is not configured.")
    role = str(task.get("assigned_role") or "PM/PO")
    proj = next((p for p in read_projects() if p.get("id") == (task.get("project_id") or "default")), None)
    repository = normalize_github_repo_url(str((proj or {}).get("repository", "")))
    is_repo_write_task = (
        bool(repository)
        and task.get("task_type") != "pm_planning"
        and role in ("Frontend developer", "Backend developer", "Documentation")
    )

    sys_inst = combined_agent_prompt(role) + ("\n\n" + PM_PLANNING_CONTRACT if task.get("task_type") == "pm_planning" else "")
    checks = task.get("acceptance_checks") or []

    def build_input(with_mounted_repo: bool, fallback_snapshot: str = "") -> str:
        text = (
            f"Role & operating rules:\n{sys_inst}\n\n---\n\n"
            + f"Task ID: {task['id']}\nRole: {role}\nTask request:\n{task.get('request', '')}\n\n"
            + "Acceptance checks:\n"
            + ("\n".join(f"- {c}" for c in checks) if checks else "- None specified")
            + f"\n\nProject context:\n{project_context_for_role(role, task)}"
        )
        if task.get("task_type") == "pm_planning":
            text += "\n\n" + PM_PLANNING_CONTRACT
        if repository and with_mounted_repo:
            if is_repo_write_task:
                mod = next((m for m in (proj or {}).get("modules", []) if m.get("id") == task.get("module_id")), None)
                branch = f"ai-team/{task['id'].lower()}"
                folder = str((mod or {}).get("path", ""))
                text += (
                    f"\n\nRepository task instructions: Your repository is mounted at /workspace/project. "
                    f"Work only in {folder or 'the repository root'} as relevant. Configure local git user.name and user.email if needed, "
                    f"create and switch to branch {branch} (git checkout -B {branch}; never modify the default branch), "
                    f"implement the requested changes, run available checks, commit finished changes, "
                    f"and push {branch} to origin (git push -u origin {branch} --force-with-lease). Do not merge. "
                    f"In your final response, state the branch name ({branch}), files changed, checks run and results, and any blockers."
                )
            else:
                text += (
                    f"\n\nRepository context note: The project repository is mounted read-only for reference at /workspace/project. "
                    f"Inspect files if helpful, but do not create branches, commit, or push changes for this {role} task."
                )
        elif fallback_snapshot:
            text += f"\n\nRepository snapshot (read-only context):\n{fallback_snapshot}"
        return text

    repo_env_spec: str | dict[str, Any] = "remote"
    if repository:
        env_obj: dict[str, Any] = {
            "type": "remote",
            "sources": [
                {"type": "inline", "target": ".agents/AGENTS.md", "content": sys_inst},
                {"type": "repository", "source": repository, "target": "/workspace/project"},
            ],
        }
        github_token = get_provider_key("github")
        if github_token:
            git_basic_ok, api_bearer_ok = sync_antigravity_github_credentials(api_key, github_token)
            basic_auth = base64.b64encode(f"x-access-token:{github_token}".encode("utf-8")).decode("ascii")
            env_obj["network"] = {
                "allowlist": [
                    (
                        {"domain": "github.com", "credential": "github-git-basic"}
                        if git_basic_ok
                        else {"domain": "github.com", "transform": {"Authorization": f"Basic {basic_auth}"}}
                    ),
                    (
                        {"domain": "api.github.com", "credential": "github-api-bearer"}
                        if api_bearer_ok
                        else {"domain": "api.github.com", "transform": {"Authorization": f"Bearer {github_token}"}}
                    ),
                    {"domain": "*"},
                ]
            }
        repo_env_spec = env_obj

    create_url = "https://generativelanguage.googleapis.com/v1beta/interactions"
    req_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": HTTP_USER_AGENT,
        "x-goog-api-key": api_key,
    }

    interaction: dict[str, Any] | None = None
    last_failure: ProviderFailure | None = None
    for attempt in range(1, 4):
        use_mounted_repo = bool(repository)
        env_to_send: str | dict[str, Any] = repo_env_spec
        fallback_snap = ""
        if attempt == 3 and repository and not is_repo_write_task:
            use_mounted_repo = False
            env_to_send = "remote"
            try:
                fallback_snap = github_task_context(task)
            except Exception:
                fallback_snap = ""
        elif attempt == 3 and repository and is_repo_write_task and isinstance(repo_env_spec, dict):
            env_to_send = {
                **repo_env_spec,
                "sources": [{"type": "repository", "source": repository, "target": "/workspace/project"}],
            }

        body: dict[str, Any] = {
            "agent": ANTIGRAVITY_AGENT,
            "input": build_input(use_mounted_repo, fallback_snap),
            "environment": env_to_send,
            "background": True,
        }
        req = urllib.request.Request(
            create_url,
            data=json.dumps(body).encode("utf-8"),
            headers=req_headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                interaction = json.loads(resp.read().decode("utf-8", errors="replace"))
            last_failure = None
            break
        except urllib.error.HTTPError as exc:
            raw_err = exc.read().decode("utf-8", errors="replace")
            try:
                err_detail = json.loads(raw_err).get("error", {}).get("message") or raw_err
            except Exception:
                err_detail = raw_err
            last_failure = ProviderFailure(
                "Antigravity",
                exc.code,
                format_antigravity_error(exc.code, str(err_detail)),
                exc.headers.get("Retry-After"),
            )
            if exc.code >= 500 and attempt < 3:
                time.sleep(attempt * 2.5)
                continue
            raise last_failure from exc
        except Exception as exc:
            last_failure = ProviderFailure("Antigravity", 503, str(exc)[:1000])
            if attempt < 3:
                time.sleep(attempt * 2.5)
                continue
            raise last_failure from exc

    if interaction is None:
        if last_failure is not None:
            raise last_failure
        raise ProviderFailure("Antigravity", 502, "Antigravity interaction could not be started.")

    iid = interaction.get("id")
    deadline = time.time() + 1800
    while iid and interaction.get("status") in ("in_progress", "queued") and time.time() < deadline:
        time.sleep(5)
        poll_url = f"https://generativelanguage.googleapis.com/v1beta/interactions/{urllib.parse.quote(str(iid), safe='')}?stream=false"
        poll_req = urllib.request.Request(
            poll_url,
            headers={
                "Accept": "application/json",
                "User-Agent": HTTP_USER_AGENT,
                "x-goog-api-key": api_key,
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(poll_req, timeout=30) as resp:
                interaction = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 404):
                raw_err = exc.read().decode("utf-8", errors="replace")[:1000]
                raise ProviderFailure("Antigravity", exc.code, raw_err) from exc
            continue
        except Exception:
            continue

    status_val = str(interaction.get("status") or "")
    if status_val in ("in_progress", "queued"):
        raise ProviderFailure("Antigravity", 408, "Antigravity interaction timed out while still in progress.")
    if status_val in ("failed", "cancelled", "budget_exceeded"):
        errs = interaction.get("errors") or []
        diag = "; ".join(
            f"{e.get('code', '')}: {e.get('message', '')}".strip(": ")
            for e in errs
            if isinstance(e, dict) and (e.get("code") or e.get("message"))
        )
        raise ProviderFailure(
            "Antigravity",
            502,
            f"Antigravity interaction {status_val}" + (f": {diag}" if diag else "."),
        )

    output = str(interaction.get("output_text") or "")
    if not output.strip():
        steps = interaction.get("steps") or []
        trailing_parts: list[str] = []
        collecting = False
        for step in reversed(steps):
            if not isinstance(step, dict):
                continue
            stype = step.get("type")
            if stype == "user_input":
                break
            if stype == "thought":
                continue
            if stype != "model_output":
                if collecting:
                    break
                continue
            stop_outer = False
            for c in reversed(step.get("content") or []):
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text":
                    collecting = True
                    trailing_parts.append(str(c.get("text") or ""))
                elif collecting:
                    stop_outer = True
                    break
            if stop_outer:
                break
        output = "".join(reversed(trailing_parts))
        if not output.strip():
            for step in steps:
                if isinstance(step, dict) and step.get("type") == "model_output":
                    for c in step.get("content") or []:
                        if isinstance(c, dict) and c.get("type") == "text":
                            output += str(c.get("text") or "")
    if not output.strip():
        raise ProviderFailure("Antigravity", 400, "Antigravity returned no text output.")
    return output.strip(), {"usage": interaction.get("usage"), "environment_id": interaction.get("environment_id")}


def call_openai_compatible_task(task: dict[str, Any], provider: str) -> tuple[str, str, dict[str, Any]]:
    conn_id = "groq" if provider == "GroqCloud" else "openrouter"
    api_key = get_provider_key(conn_id)
    if not api_key:
        raise ProviderFailure(provider, 401, f"{provider} API key is not configured.")
    model = GROQ_MODEL if provider == "GroqCloud" else OPENROUTER_MODEL
    endpoint = (
        "https://api.groq.com/openai/v1/chat/completions"
        if provider == "GroqCloud"
        else "https://openrouter.ai/api/v1/chat/completions"
    )
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
    if (task.get("validation") or {}).get("status") == "failed":
        prompt += "\n\nPrevious attempt failed validation checks:\n" + json.dumps(task["validation"].get("checks", []), indent=2)

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": combined_agent_prompt(role)
                + ("\n\n" + PM_PLANNING_CONTRACT if task.get("task_type") == "pm_planning" else ""),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    if provider == "GroqCloud":
        payload["max_completion_tokens"] = 8192
    else:
        payload["max_tokens"] = 4096 if task.get("task_type") == "pm_planning" else 2048

    req_headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": HTTP_USER_AGENT,
    }
    if provider == "OpenRouter":
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

    if conn_id == "antigravity":
        api_key = get_provider_key("gemini")
        if not api_key:
            raise ValueError("Gemini / Antigravity API key is not configured.")
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            data=json.dumps(
                {
                    "agent": ANTIGRAVITY_AGENT,
                    "input": "Reply with OK.",
                    "environment": "remote",
                    "background": True,
                }
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": HTTP_USER_AGENT,
                "x-goog-api-key": api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                created = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw).get("error", {}).get("message") or raw
            except Exception:
                detail = raw
            raise ValueError(f"Antigravity HTTP {exc.code}: {format_antigravity_error(exc.code, str(detail))}") from exc
        except Exception as exc:
            raise ValueError(f"Antigravity request failed: {exc}") from exc
        sid = str(created.get("id") or "")[:18]
        return {
            "connected": True,
            "provider": "Antigravity",
            "model": ANTIGRAVITY_AGENT,
            "message": f"Antigravity connected (agent: {ANTIGRAVITY_AGENT}" + (f", session {sid}… started)." if sid else ")."),
        }

    if conn_id not in CONNECTION_SPECS:
        raise ValueError("Unknown connection.")
    api_key = get_provider_key(conn_id)
    if not api_key:
        raise ValueError(f"{CONNECTION_SPECS[conn_id][0]} key is not configured.")

    if conn_id == "groq":
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/models",
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
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw).get("error", {}).get("message") or raw
            except Exception:
                detail = raw
            raise ValueError(f"GroqCloud HTTP {exc.code}: {str(detail)[:400]}") from exc
        except Exception as exc:
            raise ValueError(f"GroqCloud request failed: {exc}") from exc

        models = [str(m.get("id", "")) for m in (parsed.get("data") or []) if isinstance(m, dict)]
        found = GROQ_MODEL in models
        return {
            "connected": True,
            "provider": "GroqCloud",
            "model": GROQ_MODEL,
            "model_available": found,
            "message": (
                f"GroqCloud connected and model {GROQ_MODEL} is available."
                if found
                else f"GroqCloud connected ({len(models)} models listed; configured GROQ_MODEL '{GROQ_MODEL}' was not found)."
            ),
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
            raise ValueError(f"OpenRouter HTTP {exc.code}: {raw}") from exc
        except Exception as exc:
            raise ValueError(f"OpenRouter request failed: {exc}") from exc
        return {
            "connected": True,
            "provider": "OpenRouter",
            "model": OPENROUTER_MODEL,
            "message": f"OpenRouter connected (default model: {OPENROUTER_MODEL}).",
        }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(GEMINI_MODEL, safe='')}?key={urllib.parse.quote(api_key, safe='')}"
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
        raise ValueError(f"Gemini HTTP {exc.code}: {raw}") from exc
    except Exception as exc:
        raise ValueError(f"Gemini request failed: {exc}") from exc
    return {
        "connected": True,
        "provider": "Gemini",
        "model": GEMINI_MODEL,
        "message": f"Gemini connected (model: {GEMINI_MODEL}).",
    }


def retry_after_time(header: str | None, fallback_hours: int = 2) -> str:
    if header:
        try:
            sec = min(max(0, int(float(header))), 48 * 3600)
            return (datetime.now(timezone.utc) + timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    return (datetime.now(timezone.utc) + timedelta(hours=fallback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def claim_task_for_provider(provider: str) -> dict[str, Any] | None:
    if provider not in configured_providers():
        return None
    model_map = {
        "Gemini": GEMINI_MODEL,
        "Antigravity": ANTIGRAVITY_AGENT,
        "GroqCloud": GROQ_MODEL,
        "OpenRouter": OPENROUTER_MODEL,
    }
    model = model_map[provider]
    with STATE_LOCK:
        for summary in reversed(read_tasks()):
            if summary.get("status") != "Ready" or task_provider(summary) != provider:
                continue
            task = read_task(summary["id"])
            if not task or task.get("status") != "Ready" or task_provider(task) != provider:
                continue
            attempts = task.setdefault("attempts", [])
            attempt_num = max((int(a.get("number", 0)) for a in attempts), default=0) + 1
            ts = now_iso()
            attempts.append(
                {
                    "number": attempt_num,
                    "provider": provider,
                    "model": model,
                    "state": "in_progress",
                    "started_at": ts,
                }
            )
            task["status"] = "In progress"
            task["updated_at"] = ts
            task["attempt_timeout_seconds"] = 3600 if provider == "Antigravity" else 900
            task["dispatch_note"] = f"{provider} is working on this task."
            task.setdefault("activity", []).append(
                {"at": ts, "type": "dispatched", "message": f"Task sent to {provider} ({model}), attempt {attempt_num}."}
            )
            write_task(task)
            append_event({"at": ts, "task_id": task["id"], "type": "dispatched", "provider": provider, "attempt": attempt_num})
            return task
    return None


def dispatch_one_for_provider(provider: str) -> bool:
    global ACTIVE_GEMINI_COUNT, ACTIVE_OPENROUTER_COUNT
    task = claim_task_for_provider(provider)
    if not task:
        return False
    tid = task["id"]
    attempt_num = int(task["attempts"][-1]["number"])
    if provider == "Gemini":
        ACTIVE_GEMINI_COUNT += 1
    else:
        ACTIVE_OPENROUTER_COUNT += 1

    try:
        if provider == "Gemini":
            reply, meta = call_gemini_task(task)
            actual_model = GEMINI_MODEL
        elif provider == "Antigravity":
            reply, meta = call_antigravity_task(task)
            actual_model = ANTIGRAVITY_AGENT
        else:
            reply, actual_model, meta = call_openai_compatible_task(task, provider)

        proj = next((p for p in read_projects() if p.get("id") == (task.get("project_id") or "default")), None)
        if (
            provider != "Antigravity"
            and proj
            and proj.get("repository")
            and task.get("assigned_role") in ("Frontend developer", "Backend developer", "Documentation")
        ):
            apply_repository_manifest(task, reply, provider, attempt_num)

        with STATE_LOCK:
            fresh = read_task(tid)
            if fresh:
                ts = now_iso()
                folder = provider.lower().replace(" ", "")
                rel = f"replies/{tid}/{folder}/attempt-{attempt_num:03d}.json"
                atomic_json(
                    DATA / rel,
                    {
                        "task_id": tid,
                        "provider": provider,
                        "model": actual_model,
                        "attempt": attempt_num,
                        "at": ts,
                        "state": "success",
                        "text": reply,
                        **meta,
                    },
                )
                for att in fresh.get("attempts") or []:
                    if att.get("number") == attempt_num:
                        att["state"] = "succeeded"
                        att["model"] = actual_model
                        att["finished_at"] = ts
                        att["reply_file"] = rel
                fresh["latest_reply_file"] = rel
                fresh["updated_at"] = ts

                if fresh.get("task_type") == "pm_planning":
                    next_status, msg = finish_pm_planning_task(fresh, reply, provider)
                elif fresh.get("produces_deliverable"):
                    val = deterministic_validation(fresh.get("acceptance_checks") or [], reply)
                    fresh["validation"] = val
                    if val["status"] == "passed":
                        next_status, msg = "Done", f"{provider} replied and all deterministic acceptance checks passed."
                    elif val["status"] == "failed":
                        next_status, msg = "Rework needed", f"{provider} replied, but deterministic acceptance checks failed."
                    else:
                        next_status, msg = "Needs validation", f"{provider} replied; this deliverable needs manual validation."
                else:
                    fresh["validation"] = {"status": "not_required", "checks": [], "validated_at": ts}
                    next_status, msg = "Done", f"{provider} replied; no deliverable validation was required."

                for att in fresh.get("attempts") or []:
                    if att.get("number") == attempt_num:
                        att["quality_outcome"] = (
                            "passed"
                            if next_status == "Done" and fresh.get("produces_deliverable")
                            else ("failed" if next_status in ("Rework needed", "Blocked") else "unknown")
                        )
                fresh["status"] = next_status
                fresh.pop("dispatch_note", None)
                fresh.setdefault("activity", []).append(
                    {"at": ts, "type": "agent_replied", "from": "In progress", "to": next_status, "message": msg}
                )
                write_task(fresh)
                append_event({"at": ts, "task_id": tid, "type": "agent_replied", "provider": provider, "to": next_status})

        fresh_after = read_task(tid)
        if fresh_after and (
            (provider == "Antigravity" and proj and proj.get("repository")) or fresh_after.get("pull_request")
        ):
            github_postprocess_task(tid, create_pr=(provider == "Antigravity"))

    except Exception as exc:
        failure = exc if isinstance(exc, ProviderFailure) else ProviderFailure(provider, 503, str(exc)[:1000])
        with STATE_LOCK:
            fresh = read_task(tid)
            if fresh:
                ts = now_iso()
                retryable = failure.status in (408, 429) or failure.status >= 500
                fallbackable = retryable or failure.status in (400, 401, 403)
                fallback = fallbackable and fresh.get("provider") == "Automatic" and has_untried_alternate(fresh, provider)
                next_status = "Ready" if fallback else ("Waiting for capacity" if retryable else "Blocked")
                folder = provider.lower().replace(" ", "")
                rel = f"replies/{tid}/{folder}/attempt-{attempt_num:03d}.json"
                atomic_json(
                    DATA / rel,
                    {
                        "task_id": tid,
                        "provider": provider,
                        "attempt": attempt_num,
                        "at": ts,
                        "state": "error",
                        "error_status": failure.status,
                        "error": str(failure),
                    },
                )
                for att in fresh.get("attempts") or []:
                    if att.get("number") == attempt_num:
                        att["state"] = "provider_unavailable" if fallback else ("waiting_for_capacity" if retryable else "failed")
                        att["finished_at"] = ts
                        att["reply_file"] = rel
                msg = f"{provider} API error {failure.status}: {failure}."
                if fallback:
                    nxt = task_provider(fresh)
                    fresh["dispatch_note"] = f"{provider} unavailable; switching to {nxt}."
                    msg = f"{msg} Trying {nxt} automatically."
                elif retryable:
                    fresh["retry_after"] = retry_after_time(failure.retry_after, 24 if failure.status == 429 else 2)
                    fresh["waiting_reason"] = msg
                else:
                    fresh["block_reason"] = msg
                fresh["status"] = next_status
                fresh["updated_at"] = ts
                fresh["latest_reply_file"] = rel
                fresh.setdefault("activity", []).append(
                    {"at": ts, "type": "agent_error", "from": "In progress", "to": next_status, "message": msg}
                )
                write_task(fresh)
                append_event({"at": ts, "task_id": tid, "type": "agent_error", "provider": provider, "to": next_status})
        if fallback:
            DISPATCH_EVENT.set()
    finally:
        if provider == "Gemini":
            ACTIVE_GEMINI_COUNT = max(0, ACTIVE_GEMINI_COUNT - 1)
        else:
            ACTIVE_OPENROUTER_COUNT = max(0, ACTIVE_OPENROUTER_COUNT - 1)
    return True


def provider_worker_loop(provider: str) -> None:
    while True:
        try:
            worked = dispatch_one_for_provider(provider)
            if not worked:
                DISPATCH_EVENT.wait(timeout=3.0)
                DISPATCH_EVENT.clear()
        except Exception:
            time.sleep(3.0)


def run_watchdog_pass() -> int:
    changed = 0
    now_epoch = time.time()
    with STATE_LOCK:
        for task in read_tasks():
            status = task.get("status")
            if status == "In progress":
                attempts = task.setdefault("attempts", [])
                active = next((a for a in reversed(attempts) if a.get("state") == "in_progress"), None)
                started_ts = parse_iso_epoch((active or {}).get("started_at") or task.get("updated_at"))
                if started_ts is None:
                    continue
                timeout_sec = int(task.get("attempt_timeout_seconds") or DEFAULT_ATTEMPT_TIMEOUT_SECONDS)
                if now_epoch - started_ts < timeout_sec:
                    continue
                if active:
                    active["state"] = "timed_out"
                    active["finished_at"] = now_iso()
                    active["error"] = "No response before the attempt timeout."
                retries = int(task.get("automatic_retries") or 0)
                if retries >= MAX_AUTOMATIC_RETRIES:
                    task["status"] = "Blocked"
                    reason = "Automatic retry limit reached after an agent timeout."
                    task["block_reason"] = reason
                else:
                    wait_h = min(2 * (retries + 1), 6)
                    task["status"] = "Waiting for capacity"
                    task["retry_after"] = (datetime.now(timezone.utc) + timedelta(hours=wait_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    task["waiting_reason"] = "Agent attempt timed out."
                    reason = f"Agent attempt timed out; retry scheduled after {wait_h} hours."
                ts = now_iso()
                task["updated_at"] = ts
                task.setdefault("activity", []).append(
                    {"at": ts, "type": "watchdog_recovery", "from": "In progress", "to": task["status"], "message": reason}
                )
                write_task(task)
                changed += 1
            elif status == "Waiting for capacity" and task.get("retry_after"):
                retry_ts = parse_iso_epoch(task.get("retry_after"))
                if retry_ts is None or now_epoch < retry_ts:
                    continue
                ts = now_iso()
                task["automatic_retries"] = int(task.get("automatic_retries") or 0) + 1
                if task.get("provider") == "Automatic":
                    task["routing_cycle_start"] = len(task.get("attempts") or [])
                task["status"] = "Ready"
                task.pop("retry_after", None)
                task.pop("waiting_reason", None)
                task["dispatch_note"] = "Retry window reached; queued for dispatch."
                task["updated_at"] = ts
                task.setdefault("activity", []).append(
                    {
                        "at": ts,
                        "type": "watchdog_recovery",
                        "from": "Waiting for capacity",
                        "to": "Ready",
                        "message": "Retry time reached; returned to Ready queue.",
                    }
                )
                write_task(task)
                changed += 1
    if changed:
        DISPATCH_EVENT.set()
    return changed


def watchdog_worker_loop() -> None:
    while True:
        try:
            changed = run_watchdog_pass()
            WATCHDOG_STATUS["last_run"] = now_iso()
            WATCHDOG_STATUS["changed"] = changed
            WATCHDOG_STATUS["error"] = None
        except Exception as exc:
            WATCHDOG_STATUS["last_run"] = now_iso()
            WATCHDOG_STATUS["error"] = str(exc)[:300]
        time.sleep(WATCHDOG_INTERVAL_SECONDS)


# --- Local Ollama Chat ---
def check_ollama_config() -> dict[str, Any]:
    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/tags",
            headers={"Accept": "application/json", "User-Agent": HTTP_USER_AGENT},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        names = [str(m.get("name", "")) for m in (data.get("models") or []) if isinstance(m, dict)]
        found = any(n == OLLAMA_MODEL or n.startswith(f"{OLLAMA_MODEL}:") for n in names)
        return {"available": True, "model_found": found, "model": OLLAMA_MODEL}
    except Exception:
        return {"available": False, "model_found": False, "model": OLLAMA_MODEL}


def call_ollama_chat(context_messages: list[dict[str, Any]]) -> str:
    board = read_tasks()
    board_text = (
        "\n".join(f"- {t['id']} | {t.get('status')} | {t.get('assigned_role')} | {t.get('title')}" for t in board[:50])
        or "(No tasks have been created.)"
    )
    sys_prompt = "\n\n".join(
        p
        for p in [
            load_prompt("shared_rules"),
            load_prompt("orchestrator"),
            project_context_for_role("Orchestrator"),
            f"Current task board (read-only context):\n{board_text}",
        ]
        if p
    )
    payload = {
        "model": OLLAMA_MODEL,
        "think": False,
        "stream": False,
        "messages": [
            {"role": "system", "content": sys_prompt},
            *[{"role": m["role"], "content": m["content"]} for m in context_messages],
        ],
        "options": {"temperature": 0.2, "num_predict": 512},
    }
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": HTTP_USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    reply = str((data.get("message") or {}).get("content", "")).strip()
    if not reply:
        raise ValueError("Ollama returned an empty response.")
    return reply


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
        if path == "/static/app.js":
            return self.serve_file(resolve_web_asset("app.js"), "text/javascript; charset=utf-8")
        if path == "/static/style.css":
            return self.serve_file(resolve_web_asset("style.css"), "text/css; charset=utf-8")

        if path == "/api/config":
            cfg = check_ollama_config()
            return self.send_json(
                {
                    **cfg,
                    "gemini_model": GEMINI_MODEL,
                    "gemini_configured": bool(get_provider_key("gemini")),
                    "openrouter_model": OPENROUTER_MODEL,
                    "openrouter_configured": bool(get_provider_key("openrouter")),
                    "groq_model": GROQ_MODEL,
                }
            )

        if path == "/api/status":
            return self.send_json(
                {
                    "cpu_percent": get_cpu_percent(),
                    "llm_working": ACTIVE_CHAT_COUNT > 0,
                    "gemini_working": ACTIVE_GEMINI_COUNT > 0,
                    "gemini_configured": bool(get_provider_key("gemini")),
                    "gemini_model": GEMINI_MODEL,
                    "openrouter_working": ACTIVE_OPENROUTER_COUNT > 0,
                    "openrouter_configured": bool(get_provider_key("openrouter")),
                    "openrouter_model": OPENROUTER_MODEL,
                    "groq_configured": bool(get_provider_key("groq")),
                    "antigravity_configured": bool(get_provider_key("gemini")),
                    "github_configured": bool(get_provider_key("github")),
                    "watchdog": WATCHDOG_STATUS,
                }
            )

        if path == "/api/connections":
            conns = []
            for cid, (label, _) in CONNECTION_SPECS.items():
                val, src = get_provider_key_and_source(cid)
                conns.append({"id": cid, "name": label, "configured": bool(val), "source": src or "Not connected"})
            return self.send_json({"connections": conns, "encryption_available": secret_cipher() is not None})

        if path == "/api/routing":
            return self.send_json(
                {
                    "roles": WORK_ROLES,
                    "providers": PROVIDERS,
                    "configured": configured_providers(),
                    "routing": get_routing(),
                }
            )

        if path == "/api/projects":
            return self.send_json({"projects": read_projects()})

        m_clone_job = re.fullmatch(r"/api/git/([a-z0-9-]+)/clone/([a-f0-9]{32})", path)
        if m_clone_job:
            try:
                return self.send_json(get_git_clone_job(m_clone_job.group(1), m_clone_job.group(2)))
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)

        m_git_status = re.fullmatch(r"/api/git/([a-z0-9-]+)/status", path)
        if m_git_status:
            project = next((p for p in read_projects() if p.get("id") == m_git_status.group(1)), None)
            if not project:
                return self.send_json({"error": "Project not found."}, HTTPStatus.NOT_FOUND)
            local_path = str(project.get("local_path", "")).strip()
            if not local_path or not Path(local_path).expanduser().is_dir():
                return self.send_json({"available": False, "message": "Not cloned yet."})
            try:
                return self.send_json({"available": True, "status": git_status(m_git_status.group(1))})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_gh_files = re.fullmatch(r"/api/projects/([a-z0-9-]+)/github-files", path)
        if m_gh_files:
            pid = m_gh_files.group(1)
            try:
                f_q = query.get("file", [""])[0]
                p_q = query.get("path", [""])[0]
                if f_q:
                    return self.send_json(github_read_text_file(pid, f_q))
                return self.send_json(github_file_listing(pid, p_q))
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if path == "/api/documentation-files":
            try:
                file_path = query.get("file", [""])[0]
                folder_path = query.get("path", [""])[0]
                if file_path:
                    return self.send_json(read_local_text_file(DOCUMENTATION_DIR, file_path))
                return self.send_json(local_file_listing(DOCUMENTATION_DIR, folder_path))
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_local_files = re.fullmatch(r"/api/projects/([a-z0-9-]+)/local-files", path)
        if m_local_files:
            try:
                project_root = local_project_root(m_local_files.group(1))
                file_path = query.get("file", [""])[0]
                folder_path = query.get("path", [""])[0]
                if file_path:
                    return self.send_json(read_local_text_file(project_root, file_path))
                return self.send_json(local_file_listing(project_root, folder_path))
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_proj_hist = re.fullmatch(r"/api/projects/([a-z0-9-]+)/context-history", path)
        if m_proj_hist:
            return self.send_json({"history": project_background_history(m_proj_hist.group(1))})

        if path == "/api/tasks":
            return self.send_json({"tasks": read_tasks()})

        m_replies = re.fullmatch(r"/api/tasks/(T-\d{4,})/replies", path)
        if m_replies:
            tid = m_replies.group(1)
            if not read_task(tid):
                return self.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
            return self.send_json({"replies": read_task_replies(tid)})

        if path == "/api/prompts":
            prompts = []
            for slug, title in PROMPT_FILES.items():
                current = load_prompt(slug)
                default_txt = load_default_prompt(slug)
                prompts.append(
                    {
                        "id": slug,
                        "title": title,
                        "content": current,
                        "default_content": default_txt,
                        "is_custom": current != default_txt,
                    }
                )
            return self.send_json({"prompts": prompts})

        if path == "/api/project-context":
            docs = [
                {"id": slug, "title": title, "content": load_project_context(slug)}
                for slug, title in PROJECT_CONTEXT_FILES.items()
            ]
            return self.send_json({"documents": docs})

        m_ctx_hist = re.fullmatch(r"/api/project-context/([a-z_]+)/history", path)
        if m_ctx_hist:
            slug = m_ctx_hist.group(1)
            if slug not in PROJECT_CONTEXT_FILES:
                return self.send_json({"error": "Project document not found."}, HTTPStatus.NOT_FOUND)
            return self.send_json({"history": get_project_context_history(slug)})

        if path == "/api/notifications":
            return self.send_json({"notifications": get_notifications()})

        if path == "/api/provider-quality":
            return self.send_json({"quality": get_provider_quality()})

        if path == "/api/llm-usage":
            return self.send_json(
                {
                    "local_model": OLLAMA_MODEL,
                    "local_calls": get_local_llm_usage(),
                    "resets": read_ai_stat_resets(),
                }
            )

        if path == "/api/settings/logs":
            return self.send_json({"logs": [], "info": "Use POST to load logs with filters"})

        if path == "/api/settings/config":
            config = read_app_config()
            return self.send_json({"config": config})

        if path == "/api/chat":
            return self.send_json({"messages": read_chat_history()})

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

        if path == "/api/tasks":
            title = str(body.get("title", "")).strip()
            req_text = str(body.get("request", "")).strip()
            role = str(body.get("role", "PM/PO")).strip()
            provider = str(body.get("provider", "Automatic")).strip()
            raw_checks = body.get("acceptance_checks", [])
            produces = bool(body.get("produces_deliverable"))
            logger.info(f"Creating task: title='{title}', role={role}, provider={provider}")
            if not title or not req_text or len(title) > 160 or len(req_text) > 12000:
                return self.send_json({"error": "Valid title and request are required."}, HTTPStatus.BAD_REQUEST)
            if role not in WORK_ROLES or provider not in TASK_PROVIDERS:
                return self.send_json({"error": "Choose a valid work role and AI provider."}, HTTPStatus.BAD_REQUEST)
            pid = str(body.get("project_id") or "default")
            mid = str(body.get("module_id") or "")
            proj = next((p for p in read_projects() if p.get("id") == pid), None)
            if not proj or (mid and not any(m.get("id") == mid for m in proj.get("modules", []))):
                return self.send_json({"error": "Choose a valid project and sub-project."}, HTTPStatus.BAD_REQUEST)
            if not isinstance(raw_checks, list):
                return self.send_json({"error": "Acceptance checks must be a list."}, HTTPStatus.BAD_REQUEST)
            checks = [str(c).strip()[:500] for c in raw_checks if str(c).strip()][:20]
            if (
                proj.get("repository")
                and role in ("Frontend developer", "Backend developer", "Documentation")
                and (not produces or not checks)
            ):
                return self.send_json(
                    {"error": "Repository work must be marked as a deliverable and include at least one acceptance check."},
                    HTTPStatus.BAD_REQUEST,
                )
            ts = now_iso()
            tid = next_task_id()
            task = {
                "id": tid,
                "task_type": "work",
                "title": title,
                "request": req_text,
                "assigned_role": role,
                "provider": provider,
                "project_id": pid,
                "module_id": mid,
                "acceptance_checks": checks,
                "produces_deliverable": produces,
                "status": "Draft",
                "created_at": ts,
                "updated_at": ts,
                "attempts": [],
                "validation": None,
                "activity": [{"at": ts, "type": "created", "message": "Task created as Draft."}],
            }
            write_task(task)
            append_event({"at": ts, "task_id": tid, "type": "created", "status": "Draft"})
            return self.send_json({"task": task}, HTTPStatus.CREATED)

        if path == "/api/pm-requests":
            req_text = str(body.get("request", "")).strip()
            if not req_text or len(req_text) > 12000:
                return self.send_json({"error": "Describe a project request under 12,000 characters."}, HTTPStatus.BAD_REQUEST)
            pid = str(body.get("project_id") or "default")
            if not any(p.get("id") == pid for p in read_projects()):
                return self.send_json({"error": "Choose a valid project."}, HTTPStatus.BAD_REQUEST)
            tid = next_task_id()
            ts = now_iso()
            task = {
                "id": tid,
                "task_type": "pm_planning",
                "title": "PM/PO planning: " + re.sub(r"\s+", " ", req_text)[:130],
                "request": req_text,
                "original_request": req_text,
                "project_id": pid,
                "module_id": "",
                "planning_root_id": tid,
                "planning_round": 1,
                "assigned_role": "PM/PO",
                "provider": "Automatic",
                "status": "Ready",
                "produces_deliverable": False,
                "acceptance_checks": [],
                "attempts": [],
                "validation": None,
                "created_at": ts,
                "updated_at": ts,
                "dispatch_note": "Planning request queued for the PM/PO agent.",
                "activity": [{"at": ts, "type": "created", "message": "Project request sent to PM/PO from Orchestrator chat."}],
            }
            write_task(task)
            append_event({"at": ts, "task_id": tid, "type": "pm_request_created", "status": "Ready"})
            hist = read_chat_history()
            hist.extend(
                [
                    {"role": "user", "content": req_text, "at": ts},
                    {
                        "role": "assistant",
                        "content": f"I sent this request to PM/PO as {tid}. It will return clarification questions or a proposed backlog on the task board.",
                        "at": ts,
                        "task_id": tid,
                    },
                ]
            )
            write_chat_history(hist)
            DISPATCH_EVENT.set()
            return self.send_json({"task": task}, HTTPStatus.CREATED)

        m_prompt_reset = re.fullmatch(r"/api/prompts/([a-z_]+)/reset", path)
        if m_prompt_reset:
            slug = m_prompt_reset.group(1)
            if slug not in PROMPT_FILES:
                return self.send_json({"error": "Prompt not found."}, HTTPStatus.NOT_FOUND)
            try:
                default_txt = reset_prompt_file(slug)
                append_event({"at": now_iso(), "type": "prompt_reset", "prompt": slug})
                return self.send_json(
                    {
                        "saved": True,
                        "id": slug,
                        "title": PROMPT_FILES[slug],
                        "content": default_txt,
                        "default_content": default_txt,
                        "is_custom": False,
                    }
                )
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if path == "/api/routing":
            try:
                routing = save_routing(body.get("routing"))
                append_event({"at": now_iso(), "type": "routing_updated"})
                DISPATCH_EVENT.set()
                return self.send_json({"routing": routing})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_conn_test = re.fullmatch(r"/api/connections/([a-z_]+)/test", path)
        if m_conn_test:
            cid = m_conn_test.group(1)
            if cid != "antigravity" and cid not in CONNECTION_SPECS:
                return self.send_json({"connected": False, "error": "Unknown connection."}, HTTPStatus.NOT_FOUND)
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
                return self.send_json(result)
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
                return self.send_json({"connected": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

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
                return self.send_json({"repositories": repos})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        if path == "/api/llm-usage/reset":
            provider = str(body.get("provider", "")).strip()
            model = str(body.get("model", "")).strip()
            period = str(body.get("period", "")).strip()
            bucket = str(body.get("bucket", "")).strip()
            patterns = {"today": r"^\d{4}-\d{2}-\d{2}$", "month": r"^\d{4}-\d{2}$", "year": r"^\d{4}$", "all": r"^all$"}
            if provider not in ["Ollama", *PROVIDERS] or not model or len(model) > 160:
                return self.send_json({"error": "Choose a valid provider and model."}, HTTPStatus.BAD_REQUEST)
            if period not in patterns or not re.fullmatch(patterns[period], bucket):
                return self.send_json({"error": "Choose a valid statistic period."}, HTTPStatus.BAD_REQUEST)
            reset = {"provider": provider, "model": model, "period": period, "bucket": bucket, "reset_at": now_iso()}
            append_ai_stat_reset(reset)
            append_event({"at": reset["reset_at"], "type": "ai_statistic_reset", **reset})
            return self.send_json({"reset": reset})

        # Git operation endpoints
        m_git_status = re.fullmatch(r"/api/git/([a-z0-9-]+)/status", path)
        if m_git_status:
            pid = m_git_status.group(1)
            try:
                status = git_status(pid)
                return self.send_json({"status": status})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_git_pull = re.fullmatch(r"/api/git/([a-z0-9-]+)/pull", path)
        if m_git_pull:
            pid = m_git_pull.group(1)
            try:
                result = git_pull(pid)
                append_event({"at": now_iso(), "type": "git_pull", "project_id": pid, "success": True})
                return self.send_json(result)
            except ValueError as exc:
                logger.exception("Git pull request failed (project=%s)", pid)
                append_event({"at": now_iso(), "type": "git_pull", "project_id": pid, "success": False, "error": str(exc)[:300]})
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_git_push = re.fullmatch(r"/api/git/([a-z0-9-]+)/push", path)
        if m_git_push:
            pid = m_git_push.group(1)
            try:
                result = git_push(pid)
                append_event({"at": now_iso(), "type": "git_push", "project_id": pid, "success": True})
                return self.send_json(result)
            except ValueError as exc:
                logger.exception("Git push request failed (project=%s)", pid)
                append_event({"at": now_iso(), "type": "git_push", "project_id": pid, "success": False, "error": str(exc)[:300]})
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_git_clone = re.fullmatch(r"/api/git/([a-z0-9-]+)/clone", path)
        if m_git_clone:
            pid = m_git_clone.group(1)
            try:
                job = start_git_clone(pid)
                logger.info("Git clone job accepted (project=%s, job=%s)", pid, job["job_id"])
                append_event({"at": now_iso(), "type": "git_clone", "project_id": pid, "job_id": job["job_id"]})
                return self.send_json(job, HTTPStatus.ACCEPTED)
            except ValueError as exc:
                logger.error("Git clone request rejected (project=%s): %s", pid, exc)
                append_event({"at": now_iso(), "type": "git_clone", "project_id": pid, "success": False, "error": str(exc)[:300]})
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                logger.exception("Could not start Git clone job (project=%s)", pid)
                return self.send_json({"error": f"Could not start Git clone: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        # Settings API endpoints
        if path == "/api/settings/logs":
            level = str(body.get("level", "all")).strip().upper()
            level_number = 0 if level == "ALL" else LOG_LEVELS.get(level)
            if level_number is None:
                return self.send_json({"error": "Choose a valid log display level."}, HTTPStatus.BAD_REQUEST)
            try:
                lines = int(body.get("lines", 1000))
            except (TypeError, ValueError):
                return self.send_json({"error": "Log line count must be an integer."}, HTTPStatus.BAD_REQUEST)
            if not 1 <= lines <= 5000:
                return self.send_json({"error": "Log line count must be between 1 and 5000."}, HTTPStatus.BAD_REQUEST)
            try:
                newest_lines = []
                log_files = sorted(
                    (path for path in LOG_DIR.glob("app.log*") if path.is_file()),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
                for log_file in log_files:
                    content = log_file.read_text(encoding="utf-8", errors="replace")
                    for line in reversed(content.splitlines()):
                        line_level = re.search(r"\|\s*(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*\|", line)
                        if level_number and (
                            not line_level or LOG_LEVELS[line_level.group(1)] < level_number
                        ):
                            continue
                        newest_lines.append(line)
                        if len(newest_lines) >= lines:
                            break
                    if len(newest_lines) >= lines:
                        break
                return self.send_json({"logs": list(reversed(newest_lines))})
            except Exception as exc:
                logger.exception("Failed to load application logs")
                return self.send_json({"error": f"Failed to read logs: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        if path == "/api/settings/log-level":
            level = normalize_log_level(body.get("level"))
            if not level:
                return self.send_json({"error": "Choose a valid log capture level."}, HTTPStatus.BAD_REQUEST)
            config = read_app_config()
            config["log_level"] = level
            try:
                save_app_config(config)
                configure_file_log_level(level)
                return self.send_json({"log_level": level})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

        if path == "/api/settings/logs/clear":
            try:
                file_handler.acquire()
                try:
                    file_handler.flush()
                    if file_handler.stream:
                        file_handler.stream.close()
                        file_handler.stream = None
                    for log_file in LOG_DIR.glob("app.log*"):
                        log_file.unlink(missing_ok=True)
                finally:
                    try:
                        file_handler.stream = file_handler._open()
                    finally:
                        file_handler.release()
                logger.info("Logs cleared by user")
                return self.send_json({"cleared": True})
            except Exception as exc:
                logger.exception("Failed to clear application logs")
                return self.send_json({"error": f"Failed to clear logs: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        if path == "/api/settings/restart":
            logger.info("App restart requested by user")
            self.send_json({"success": True, "message": "Service restart scheduled."})
            threading.Timer(2.0, restart_service_process).start()
            return None

        if path == "/api/settings/update":
            app_repo = str(body.get("app_repo", "")).strip()
            app_clone_dir = str(body.get("app_clone_dir", "")).strip()
            app_working_dir = str(body.get("app_working_dir", "")).strip()
            if not app_repo or not app_clone_dir or not app_working_dir:
                return self.send_json(
                    {"error": "App repository, local checkout directory, and working directory are required."},
                    HTTPStatus.BAD_REQUEST,
                )
            if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", app_repo):
                return self.send_json({"error": "Invalid GitHub repository URL."}, HTTPStatus.BAD_REQUEST)
            
            try:
                logger.info(
                    "App update requested: repo=%s checkout=%s working_dir=%s",
                    app_repo, app_clone_dir, app_working_dir,
                )
                checkout_path = Path(app_clone_dir).expanduser().resolve()
                working_path = Path(app_working_dir).expanduser().resolve()
                if not checkout_path.is_absolute() or not working_path.is_absolute():
                    return self.send_json(
                        {"error": "The local checkout and app working directory must be absolute paths."},
                        HTTPStatus.BAD_REQUEST,
                    )
                if checkout_path == Path(checkout_path.anchor) or working_path == Path(working_path.anchor):
                    return self.send_json(
                        {"error": "A filesystem root cannot be used as an app directory."},
                        HTTPStatus.BAD_REQUEST,
                    )
                if not working_path.is_dir():
                    return self.send_json({"error": f"Working directory does not exist: {app_working_dir}"}, HTTPStatus.BAD_REQUEST)
                if (
                    checkout_path == working_path
                    or checkout_path.is_relative_to(working_path)
                    or working_path.is_relative_to(checkout_path)
                ):
                    return self.send_json(
                        {"error": "The local checkout directory and app working directory must not overlap."},
                        HTTPStatus.BAD_REQUEST,
                    )
                
                token = get_provider_key("github")
                if not token:
                    return self.send_json({"error": "GitHub token is not configured."}, HTTPStatus.BAD_REQUEST)
                
                app_repo_url = normalize_github_repo_url(app_repo)
                owner, repo = repository_parts(app_repo_url)
                app_repo_url = f"https://github.com/{owner}/{repo}.git"
                operation = update_app_checkout(app_repo_url, checkout_path, token)
                copied, skipped = copy_newer_app_files(checkout_path, working_path)
                logger.info(
                    "App files copied from checkout=%s to working_dir=%s (copied=%s, kept_newer_or_equal=%s)",
                    checkout_path, working_path, copied, skipped,
                )
                response = {
                    "success": True,
                    "operation": operation,
                    "copied_files": copied,
                    "kept_files": skipped,
                    "message": f"Repository {operation} completed; copied {copied} newer file(s), kept {skipped} newer or unchanged file(s).",
                }
                self.send_json(response)
                threading.Timer(2.0, restart_service_process).start()
                return None
            except PermissionError as exc:
                logger.exception("App update permission failure")
                return self.send_json(
                    {
                        "error": (
                            f"Permission denied at '{exc.filename or app_clone_dir or app_working_dir}'. "
                            "Grant the ubuntu service user filesystem access and add the exact path "
                            "to systemd ReadWritePaths."
                        )
                    },
                    HTTPStatus.FORBIDDEN,
                )
            except OSError as exc:
                logger.exception("App update filesystem failure")
                if exc.errno == errno.EROFS:
                    detail = (
                        f"'{exc.filename or app_working_dir}' is read-only in the service's filesystem namespace. "
                        "Install the updated personal-ai-team.service with this directory in ReadWritePaths, "
                        "then run systemctl daemon-reload and restart the service before retrying."
                    )
                else:
                    detail = f"Filesystem operation failed at '{exc.filename or app_working_dir}': {exc}"
                return self.send_json({"error": detail}, HTTPStatus.INTERNAL_SERVER_ERROR)
            except subprocess.TimeoutExpired:
                logger.exception("App update command timed out")
                return self.send_json({"error": "Update command timed out."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            except ValueError as exc:
                logger.exception("App update failed")
                return self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            except Exception as exc:
                logger.exception("App update failed unexpectedly")
                return self.send_json({"error": f"Update failed: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        if path == "/api/settings/config":
            try:
                config = body
                save_app_config(config)
                return self.send_json({"success": True, "config": config})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_status = re.fullmatch(r"/api/tasks/(T-\d{4,})/status", path)
        if m_status:
            tid = m_status.group(1)
            target = str(body.get("status", ""))
            note = str(body.get("note", "")).strip()[:500]
            if target not in ("Ready", "Blocked", "Canceled", "Draft"):
                return self.send_json({"error": "Invalid manual status transition."}, HTTPStatus.BAD_REQUEST)
            with STATE_LOCK:
                task = read_task(tid)
                if not task:
                    return self.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                cur = str(task.get("status", ""))
                if target not in TRANSITIONS.get(cur, set()) or target not in USER_TRANSITIONS.get(cur, set()):
                    return self.send_json({"error": f"Cannot move a {cur} task to {target}."}, HTTPStatus.CONFLICT)
                if target == "Ready" and task.get("produces_deliverable") and not (task.get("acceptance_checks") or []):
                    return self.send_json(
                        {"error": "Add at least one acceptance check before marking a deliverable task Ready."},
                        HTTPStatus.BAD_REQUEST,
                    )
                ts = now_iso()
                if target == "Ready":
                    task.pop("retry_after", None)
                    task.pop("waiting_reason", None)
                    if cur == "Waiting for capacity" and (not task.get("provider") or task.get("provider") == "Automatic"):
                        task["routing_cycle_start"] = len(task.get("attempts") or [])
                    sel = task_provider(task)
                    if cur == "Rework needed":
                        msg = "Task returned to the agent queue with its failed validation results included."
                    elif cur == "Waiting for capacity":
                        msg = "Manual retry requested; task queued for immediate dispatch."
                    else:
                        msg = "Task is clear and queued for automatic dispatch."
                    task["dispatch_note"] = (
                        f"Waiting for {sel} to respond."
                        if sel
                        else "No provider in this task's route is connected. Add a provider key in Connections & routing."
                    )
                elif target == "Blocked":
                    task.pop("retry_after", None)
                    task.pop("waiting_reason", None)
                    msg = note or "Task blocked by the user."
                    task["block_reason"] = msg
                elif target == "Canceled":
                    task.pop("retry_after", None)
                    task.pop("waiting_reason", None)
                    msg = note or "Task canceled by the user."
                else:
                    msg = note or "Task returned to Draft."
                    task.pop("block_reason", None)
                task["status"] = target
                task["updated_at"] = ts
                task.setdefault("activity", []).append(
                    {"at": ts, "type": "status_changed", "from": cur, "to": target, "message": msg}
                )
                write_task(task)
                append_event({"at": ts, "task_id": tid, "type": "status_changed", "from": cur, "to": target})
            if target == "Ready":
                DISPATCH_EVENT.set()
            return self.send_json({"task": task})

        m_prov = re.fullmatch(r"/api/tasks/(T-\d{4,})/provider", path)
        if m_prov:
            tid = m_prov.group(1)
            provider = str(body.get("provider", "")).strip()
            if provider not in TASK_PROVIDERS:
                return self.send_json({"error": "Choose Automatic or a valid AI provider."}, HTTPStatus.BAD_REQUEST)
            with STATE_LOCK:
                task = read_task(tid)
                if not task:
                    return self.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                if task.get("status") in ("In progress", "Done", "Canceled"):
                    return self.send_json(
                        {"error": f"Cannot change AI provider while a task is {task.get('status')}."},
                        HTTPStatus.CONFLICT,
                    )
                prev_prov = str(task.get("provider") or "Automatic")
                ts = now_iso()
                task["provider"] = provider
                task["routing_cycle_start"] = len(task.get("attempts") or [])
                if task.get("status") == "Ready":
                    sel = task_provider(task)
                    task["dispatch_note"] = (
                        f"Waiting for {sel} to respond."
                        if sel
                        else "No provider in this task's route is connected. Add a provider key in Connections & routing."
                    )
                task["updated_at"] = ts
                if prev_prov != provider:
                    task.setdefault("activity", []).append(
                        {
                            "at": ts,
                            "type": "provider_changed",
                            "message": f"AI provider changed from {prev_prov} to {provider}.",
                        }
                    )
                write_task(task)
                append_event({"at": ts, "task_id": tid, "type": "provider_changed", "from": prev_prov, "to": provider})
                is_ready = task.get("status") == "Ready"
            if is_ready:
                DISPATCH_EVENT.set()
            return self.send_json({"task": task})

        m_val = re.fullmatch(r"/api/tasks/(T-\d{4,})/validation", path)
        if m_val:
            tid = m_val.group(1)
            decision = str(body.get("decision", ""))
            notes = str(body.get("notes", "")).strip()[:2000]
            if decision not in ("pass", "rework"):
                return self.send_json({"error": "Choose pass or rework."}, HTTPStatus.BAD_REQUEST)
            if decision == "rework" and not notes:
                return self.send_json({"error": "Add a short note describing what needs to change."}, HTTPStatus.BAD_REQUEST)
            with STATE_LOCK:
                task = read_task(tid)
                if not task:
                    return self.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                if task.get("status") != "Needs validation" or not task.get("produces_deliverable"):
                    return self.send_json({"error": "Only deliverables in Needs validation can be reviewed here."}, HTTPStatus.CONFLICT)
                ts = now_iso()
                passed = decision == "pass"
                task["validation"] = {
                    "status": "passed" if passed else "failed",
                    "method": "manual",
                    "reviewed_by": "User",
                    "notes": notes or "Approved after manual review.",
                    "checks": [
                        {
                            "check": "Manual review",
                            "kind": "manual",
                            "passed": passed,
                            "message": notes or "Approved after manual review.",
                        }
                    ],
                    "validated_at": ts,
                }
                task["status"] = "Done" if passed else "Rework needed"
                for att in reversed(task.get("attempts") or []):
                    if att.get("state") == "succeeded":
                        att["quality_outcome"] = "passed" if passed else "failed"
                        break
                task["updated_at"] = ts
                msg = "User approved the deliverable after manual review." if passed else f"User requested rework: {notes}"
                task.setdefault("activity", []).append(
                    {"at": ts, "type": "manual_validation", "from": "Needs validation", "to": task["status"], "message": msg}
                )
                write_task(task)
            if task.get("pull_request"):
                threading.Thread(target=github_postprocess_task, args=(tid, False), daemon=True).start()
            return self.send_json({"task": task})

        m_ans = re.fullmatch(r"/api/tasks/(T-\d{4,})/answer", path)
        if m_ans:
            tid = m_ans.group(1)
            selected = str(body.get("selected_option", "")).strip()
            free_text = str(body.get("free_text", "")).strip()[:5000]
            with STATE_LOCK:
                question = read_task(tid)
                if not question:
                    return self.send_json({"error": "Question not found."}, HTTPStatus.NOT_FOUND)
                if question.get("task_type") != "clarification_question" or question.get("status") != "Needs answer":
                    return self.send_json({"error": "This question is not waiting for an answer."}, HTTPStatus.CONFLICT)
                q_data = question.get("question_data") or {}
                opts = [o.get("label") for o in (q_data.get("options") or []) if isinstance(o, dict)]
                if selected and selected not in opts:
                    return self.send_json({"error": "Choose one of the listed options."}, HTTPStatus.BAD_REQUEST)
                if not selected and not free_text:
                    return self.send_json({"error": "Choose an option or enter a response."}, HTTPStatus.BAD_REQUEST)
                if q_data.get("allow_free_text") is False and not selected:
                    return self.send_json({"error": "Choose one of the listed options."}, HTTPStatus.BAD_REQUEST)

                ans = (f"Selected option: {selected}\n" if selected else "") + (
                    f"Additional response: {free_text}" if free_text else ""
                )
                ts = now_iso()
                question["status"] = "Done"
                question["selected_option"] = selected or None
                question["free_text"] = free_text
                question["answer"] = ans
                question["updated_at"] = ts
                question.setdefault("activity", []).append(
                    {"at": ts, "type": "question_answered", "message": "Clarification answered by the user."}
                )
                write_task(question)

                all_t = read_tasks()
                siblings = [
                    t
                    for t in all_t
                    if t.get("task_type") == "clarification_question"
                    and t.get("planning_task_id") == question.get("planning_task_id")
                ]
                followup = None
                if siblings and all(s.get("status") == "Done" for s in siblings):
                    parent = read_task(str(question.get("planning_task_id") or ""))
                    if parent and not parent.get("followup_created"):
                        parent["followup_created"] = True
                        parent.setdefault("activity", []).append(
                            {
                                "at": ts,
                                "type": "clarifications_complete",
                                "message": "All questions answered; sending answers back to PM/PO.",
                            }
                        )
                        write_task(parent)
                        root_id = question.get("planning_root_id")
                        answered_q = sorted(
                            [
                                t
                                for t in read_tasks()
                                if t.get("task_type") == "clarification_question"
                                and t.get("planning_root_id") == root_id
                                and t.get("status") == "Done"
                            ],
                            key=lambda x: (int(x.get("planning_round") or 1), str(x.get("created_at", ""))),
                        )
                        qa_txt = "\n".join(f"Q: {q.get('request')}\nA: {q.get('answer', '')}" for q in answered_q)
                        fid = next_task_id()
                        next_round = int(parent.get("planning_round") or 1) + 1
                        followup = {
                            "id": fid,
                            "task_type": "pm_planning",
                            "title": f"PM/PO planning follow-up, round {next_round}",
                            "request": (
                                f"Original project request:\n{parent.get('original_request', '')}\n\n"
                                f"Clarifications answered so far:\n{qa_txt}\n\n"
                                "Continue planning. Ask focused questions only if critical details are still missing; otherwise return the proposed task backlog."
                            ),
                            "original_request": parent.get("original_request", ""),
                            "planning_root_id": root_id,
                            "planning_round": next_round,
                            "assigned_role": "PM/PO",
                            "provider": "Automatic",
                            "status": "Ready",
                            "project_id": parent.get("project_id") or "default",
                            "module_id": "",
                            "produces_deliverable": False,
                            "acceptance_checks": [],
                            "attempts": [],
                            "validation": None,
                            "created_at": ts,
                            "updated_at": ts,
                            "dispatch_note": "Answered clarifications queued for PM/PO.",
                            "activity": [{"at": ts, "type": "created", "message": "Follow-up planning request queued."}],
                        }
                        write_task(followup)
                        append_chat_notice(
                            f"All clarification answers are in. I sent them back to PM/PO as {fid}.", ts, fid
                        )
                        DISPATCH_EVENT.set()
            return self.send_json({"task": question, "followup_task": followup})

        m_app_ov = re.fullmatch(r"/api/tasks/(T-\d{4,})/approve-overview", path)
        if m_app_ov:
            tid = m_app_ov.group(1)
            try:
                documents = clean_overview_documents(body)
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            with STATE_LOCK:
                task = read_task(tid)
                if not task:
                    return self.send_json({"error": "Project overview task not found."}, HTTPStatus.NOT_FOUND)
                if task.get("task_type") != "project_overview" or task.get("status") != "Needs approval":
                    return self.send_json({"error": "This project overview is no longer awaiting approval."}, HTTPStatus.CONFLICT)
                ts = now_iso()
                snapshots: dict[str, str | None] = {}
                pid = str(task.get("project_id") or "default")
                if pid == "default":
                    for slug, content in documents.items():
                        snapshots[slug] = save_project_context_document(slug, content)
                else:
                    projs = read_projects()
                    proj = next((p for p in projs if p.get("id") == pid), None)
                    if not proj:
                        return self.send_json({"error": "Attached project no longer exists."}, HTTPStatus.CONFLICT)
                    proj["context"] = "\n\n".join(
                        f"# {PROJECT_CONTEXT_FILES[slug]}\n\n{content}" for slug, content in documents.items()
                    )
                    save_projects(projs)
                    hist = project_background_history(pid)
                    snapshots["project_background"] = hist[0]["id"] if hist else None
                task["overview_documents"] = documents
                task["status"] = "Done"
                task["updated_at"] = ts
                task["approved_at"] = ts
                task["validation"] = {
                    "status": "passed",
                    "method": "user_approval",
                    "reviewed_by": "User",
                    "notes": "Approved and applied to Project context.",
                    "snapshots": snapshots,
                    "validated_at": ts,
                }
                task.setdefault("activity", []).append(
                    {"at": ts, "type": "overview_approved", "from": "Needs approval", "to": "Done", "message": "User approved project overview."}
                )
                write_task(task)
            return self.send_json({"task": task, "snapshots": snapshots})

        m_ctx_res = re.fullmatch(r"/api/project-context/([a-z_]+)/restore", path)
        if m_ctx_res:
            slug = m_ctx_res.group(1)
            vid = str(body.get("snapshot_id", ""))
            if slug not in PROJECT_CONTEXT_FILES:
                return self.send_json({"error": "Project document not found."}, HTTPStatus.NOT_FOUND)
            if not re.fullmatch(r"\d{8}T\d{12}Z", vid):
                return self.send_json({"error": "Choose a valid saved version."}, HTTPStatus.BAD_REQUEST)
            snap = DATA / "project" / "history" / slug / f"{vid}.md"
            if not snap.is_file():
                return self.send_json({"error": "Saved version not found."}, HTTPStatus.NOT_FOUND)
            txt = snap.read_text(encoding="utf-8")
            new_snap = save_project_context_document(slug, txt)
            append_event({"at": now_iso(), "type": "project_context_restored", "document": slug, "restored_snapshot": vid, "snapshot": new_snap})
            return self.send_json({"saved": True, "id": slug, "title": PROJECT_CONTEXT_FILES[slug], "content": txt.strip()})

        m_proj_res = re.fullmatch(r"/api/projects/([a-z0-9-]+)/restore-context", path)
        if m_proj_res:
            pid = m_proj_res.group(1)
            vid = str(body.get("version_id", ""))
            if not re.fullmatch(r"\d{8}T\d{12}Z", vid):
                return self.send_json({"error": "Choose a valid saved project version."}, HTTPStatus.BAD_REQUEST)
            snap = DATA / "projects" / pid / "history" / f"{vid}.md"
            if not snap.is_file():
                return self.send_json({"error": "Saved project version not found."}, HTTPStatus.NOT_FOUND)
            restored = snap.read_text(encoding="utf-8")
            projs = read_projects()
            proj = next((p for p in projs if p.get("id") == pid), None)
            if not proj:
                return self.send_json({"error": "Project not found."}, HTTPStatus.NOT_FOUND)
            proj["context"] = restored
            save_projects(projs)
            return self.send_json({"restored": True, "context": restored})

        if path == "/api/chat":
            text = str(body.get("message", "")).strip()
            if not text or len(text) > 12000:
                return self.send_json({"error": "Write a message up to 12,000 characters."}, HTTPStatus.BAD_REQUEST)
            with ACTIVE_CHAT_LOCK:
                if ACTIVE_CHAT_COUNT > 0:
                    return self.send_json({"error": "Another Orchestrator reply is still in progress."}, HTTPStatus.CONFLICT)
                ACTIVE_CHAT_COUNT += 1
            t0 = time.time()
            try:
                hist = read_chat_history()
                hist.append({"role": "user", "content": text, "at": now_iso()})
                write_chat_history(hist)
                append_event({"at": now_iso(), "type": "local_llm_request_started", "provider": "Ollama", "model": OLLAMA_MODEL})
                try:
                    reply = call_ollama_chat(hist[-16:])
                    dur = round(time.time() - t0, 2)
                    msg = {"role": "assistant", "content": reply, "at": now_iso(), "provider": "Ollama", "duration_seconds": dur}
                    hist.append(msg)
                    write_chat_history(hist)
                    append_event({"at": now_iso(), "type": "local_llm_reply", "provider": "Ollama", "model": OLLAMA_MODEL, "duration_seconds": dur})
                    return self.send_json({"reply": msg, "messages": hist})
                except Exception as exc:
                    dur = round(time.time() - t0, 2)
                    append_event({"at": now_iso(), "type": "local_llm_error", "provider": "Ollama", "model": OLLAMA_MODEL, "duration_seconds": dur})
                    err_msg = f"I couldn’t reach Ollama ({OLLAMA_MODEL}): {exc}"
                    hist.append({"role": "assistant", "content": err_msg, "at": now_iso(), "error": True})
                    write_chat_history(hist)
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

        m_prompt = re.fullmatch(r"/api/prompts/([a-z_]+)", path)
        if m_prompt:
            slug = m_prompt.group(1)
            if slug not in PROMPT_FILES:
                return self.send_json({"error": "Prompt not found."}, HTTPStatus.NOT_FOUND)
            content = body.get("content")
            if not isinstance(content, str) or not content.strip():
                return self.send_json({"error": "Prompt text cannot be empty."}, HTTPStatus.BAD_REQUEST)
            if len(content) > 20000:
                return self.send_json({"error": "Prompt text must be 20,000 characters or fewer."}, HTTPStatus.BAD_REQUEST)
            saved_txt = save_prompt_file(slug, content)
            default_txt = load_default_prompt(slug)
            append_event({"at": now_iso(), "type": "prompt_saved", "prompt": slug})
            return self.send_json(
                {
                    "saved": True,
                    "id": slug,
                    "title": PROMPT_FILES[slug],
                    "content": saved_txt,
                    "default_content": default_txt,
                    "is_custom": saved_txt != default_txt,
                }
            )

        m_conn = re.fullmatch(r"/api/connections/([a-z_]+)", path)
        if m_conn:
            cid = m_conn.group(1)
            if cid not in CONNECTION_SPECS:
                return self.send_json({"error": "Unknown connection."}, HTTPStatus.NOT_FOUND)
            secret = str(body.get("secret", "")).strip()
            if not secret or len(secret) > 4000:
                return self.send_json({"error": "Provide a valid key or token."}, HTTPStatus.BAD_REQUEST)
            try:
                secrets = read_saved_secrets()
                secrets[cid] = secret
                write_saved_secrets(secrets)
                append_event({"at": now_iso(), "type": "connection_saved", "connection": cid})
                DISPATCH_EVENT.set()
                return self.send_json({"saved": True, "id": cid})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_ctx = re.fullmatch(r"/api/project-context/([a-z_]+)", path)
        if m_ctx:
            slug = m_ctx.group(1)
            if slug not in PROJECT_CONTEXT_FILES:
                return self.send_json({"error": "Project document not found."}, HTTPStatus.NOT_FOUND)
            content = body.get("content")
            if not isinstance(content, str) or not content.strip() or len(content) > 20000:
                return self.send_json({"error": "Project document must be non-empty and under 20,000 characters."}, HTTPStatus.BAD_REQUEST)
            snap = save_project_context_document(slug, content)
            append_event({"at": now_iso(), "type": "project_context_saved", "document": slug, "snapshot": snap})
            return self.send_json({"saved": True, "id": slug, "title": PROJECT_CONTEXT_FILES[slug], "content": content.strip(), "snapshot": snap})

        m_ov = re.fullmatch(r"/api/tasks/(T-\d{4,})/overview", path)
        if m_ov:
            tid = m_ov.group(1)
            try:
                documents = clean_overview_documents(body)
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            with STATE_LOCK:
                task = read_task(tid)
                if not task:
                    return self.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                if task.get("task_type") != "project_overview" or task.get("status") != "Needs approval":
                    return self.send_json({"error": "Overview is no longer awaiting approval."}, HTTPStatus.CONFLICT)
                ts = now_iso()
                task["overview_documents"] = documents
                task["updated_at"] = ts
                task.setdefault("activity", []).append(
                    {"at": ts, "type": "overview_edited", "message": "User edited proposed project overview."}
                )
                write_task(task)
            return self.send_json({"task": task})

        m_task = re.fullmatch(r"/api/tasks/(T-\d{4,})", path)
        if m_task:
            tid = m_task.group(1)
            title = str(body.get("title", "")).strip()
            req_text = str(body.get("request", "")).strip()
            role = str(body.get("role", "")).strip()
            provider = str(body.get("provider", "Automatic")).strip()
            raw_checks = body.get("acceptance_checks", [])
            produces = bool(body.get("produces_deliverable"))
            if not title or not req_text or len(title) > 160 or len(req_text) > 12000:
                return self.send_json({"error": "Enter a valid title and request."}, HTTPStatus.BAD_REQUEST)
            if role not in WORK_ROLES or provider not in TASK_PROVIDERS or not isinstance(raw_checks, list):
                return self.send_json({"error": "Invalid role, provider, or acceptance checks."}, HTTPStatus.BAD_REQUEST)
            pid = str(body.get("project_id") or "default")
            mid = str(body.get("module_id") or "")
            proj = next((p for p in read_projects() if p.get("id") == pid), None)
            if not proj or (mid and not any(m.get("id") == mid for m in proj.get("modules", []))):
                return self.send_json({"error": "Choose a valid project and sub-project."}, HTTPStatus.BAD_REQUEST)
            checks = [str(c).strip()[:500] for c in raw_checks if str(c).strip()][:20]
            with STATE_LOCK:
                task = read_task(tid)
                if not task:
                    return self.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                if task.get("status") not in ("Draft", "Blocked", "Rework needed", "Waiting for capacity"):
                    return self.send_json(
                        {"error": "Only Draft, Blocked, Rework needed, or Waiting for capacity tasks can be edited."},
                        HTTPStatus.CONFLICT,
                    )
                ts = now_iso()
                task.update(
                    {
                        "title": title,
                        "request": req_text,
                        "assigned_role": role,
                        "provider": provider,
                        "project_id": pid,
                        "module_id": mid,
                        "acceptance_checks": checks,
                        "produces_deliverable": produces,
                        "updated_at": ts,
                    }
                )
                task.setdefault("activity", []).append({"at": ts, "type": "edited", "message": "Task details updated."})
                write_task(task)
            return self.send_json({"task": task})

        if path == "/api/projects":
            try:
                projects = save_projects(body.get("projects"))
                append_event({"at": now_iso(), "type": "projects_updated", "count": len(projects)})
                return self.send_json({"projects": projects})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        self.send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        path = urllib.parse.urlparse(self.path).path

        m_task = re.fullmatch(r"/api/tasks/(T-\d{4,})", path)
        if m_task:
            tid = m_task.group(1)
            with STATE_LOCK:
                tpath = DATA / "tasks" / f"{tid}.json"
                rdir = DATA / "replies" / tid
                lpath = DATA / "logs" / "activity.jsonl"
                artifacts = [tpath, tpath.with_suffix(".json.bak"), tpath.with_suffix(".json.tmp")]
                existed = any(p.exists() for p in artifacts) or rdir.exists()
                if not existed:
                    return self.send_json({"error": "Task not found."}, HTTPStatus.NOT_FOUND)
                if lpath.is_file():
                    kept = []
                    for line in lpath.read_text(encoding="utf-8", errors="replace").splitlines():
                        if not line.strip():
                            continue
                        try:
                            if json.loads(line).get("task_id") == tid:
                                continue
                        except Exception:
                            pass
                        kept.append(line + "\n")
                    atomic_text(lpath, "".join(kept))
                for p in artifacts:
                    p.unlink(missing_ok=True)
                if rdir.exists():
                    shutil.rmtree(rdir, ignore_errors=True)
            return self.send_json({"deleted": True, "task_id": tid})

        m_conn = re.fullmatch(r"/api/connections/([a-z_]+)", path)
        if m_conn:
            cid = m_conn.group(1)
            if cid not in CONNECTION_SPECS:
                return self.send_json({"error": "Unknown connection."}, HTTPStatus.NOT_FOUND)
            try:
                secrets = read_saved_secrets()
                secrets.pop(cid, None)
                write_saved_secrets(secrets)
                append_event({"at": now_iso(), "type": "connection_cleared", "connection": cid})
                return self.send_json({"deleted": True, "id": cid})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        m_ctx_del = re.fullmatch(r"/api/project-context/([a-z_]+)/history/(\d{8}T\d{12}Z)", path)
        if m_ctx_del:
            slug, vid = m_ctx_del.group(1), m_ctx_del.group(2)
            snap = DATA / "project" / "history" / slug / f"{vid}.md"
            if slug not in PROJECT_CONTEXT_FILES or not snap.is_file():
                return self.send_json({"error": "Saved version not found."}, HTTPStatus.NOT_FOUND)
            snap.unlink()
            return self.send_json({"deleted": True, "id": slug, "deleted_version": vid})

        m_proj_del = re.fullmatch(r"/api/projects/([a-z0-9-]+)/context-history/(\d{8}T\d{12}Z)", path)
        if m_proj_del:
            pid, vid = m_proj_del.group(1), m_proj_del.group(2)
            snap = DATA / "projects" / pid / "history" / f"{vid}.md"
            if not snap.is_file():
                return self.send_json({"error": "Saved project version not found."}, HTTPStatus.NOT_FOUND)
            snap.unlink()
            return self.send_json({"deleted": True, "project_id": pid, "deleted_version": vid})

        if path.startswith("/api/notifications/"):
            nid = urllib.parse.unquote(path[len("/api/notifications/") :])
            if not nid:
                return self.send_json({"error": "Missing notification ID."}, HTTPStatus.BAD_REQUEST)
            dismiss_notification(nid)
            return self.send_json({"dismissed": True, "id": nid})

        if path == "/api/chat":
            write_chat_history([])
            append_event({"at": now_iso(), "type": "chat_cleared"})
            return self.send_json({"cleared": True, "messages": []})

        self.send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)


def start_background_workers() -> None:
    for provider in PROVIDERS:
        threading.Thread(target=provider_worker_loop, args=(provider,), daemon=True).start()
    threading.Thread(target=watchdog_worker_loop, daemon=True).start()


def main() -> None:
    ensure_data()
    get_cpu_percent()
    start_background_workers()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Personal AI Team listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

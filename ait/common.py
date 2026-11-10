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

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
DATA = Path(os.environ.get("TEAM_DATA_DIR", str(ROOT / "data"))).expanduser().resolve()
DOCUMENTATION_DIR = ROOT / "documentation"

HOST = os.environ.get("TEAM_HOST", "127.0.0.1")
PORT = int(os.environ.get("TEAM_PORT", "8765"))

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openrouter/free").strip() or "openrouter/free"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b").strip() or "openai/gpt-oss-20b"
OMNIROUTE_MODEL = os.environ.get("OMNIROUTE_MODEL", "auto").strip() or "auto"
NINEROUTER_MODEL = os.environ.get("NINEROUTER_MODEL", "auto").strip() or "auto"
NVIDIA_MODEL = os.environ.get("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct").strip() or "meta/llama-3.3-70b-instruct"
OMNIROUTE_BASE_URL = os.environ.get("OMNIROUTE_BASE_URL", "http://localhost:20128/v1").strip().rstrip("/") or "http://localhost:20128/v1"
NINEROUTER_BASE_URL = os.environ.get("NINEROUTER_BASE_URL", "http://localhost:20129/v1").strip().rstrip("/") or "http://localhost:20129/v1"
NVIDIA_BASE_URL = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").strip().rstrip("/") or "https://integrate.api.nvidia.com/v1"
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

PROVIDERS = ["Gemini", "GroqCloud", "OpenRouter", "OmniRoute", "9Router", "Nvidia"]
TASK_PROVIDERS = ["Automatic", *PROVIDERS]

ROUTING_DEFAULTS: dict[str, list[str]] = {
    "PM/PO": ["Gemini", "GroqCloud", "OpenRouter", "OmniRoute", "9Router", "Nvidia"],
    "Frontend developer": ["Gemini", "GroqCloud", "OpenRouter", "OmniRoute", "9Router", "Nvidia"],
    "Backend developer": ["Gemini", "GroqCloud", "OpenRouter", "OmniRoute", "9Router", "Nvidia"],
    "QA": ["GroqCloud", "Gemini", "OpenRouter", "OmniRoute", "9Router", "Nvidia"],
    "UI designer": ["Gemini", "GroqCloud", "OpenRouter", "OmniRoute", "9Router", "Nvidia"],
    "Documentation": ["Gemini", "GroqCloud", "OpenRouter", "OmniRoute", "9Router", "Nvidia"],
}

PROMPT_FILES: dict[str, str] = {
    "shared_rules": "Shared rules",
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
    "gemini": ("Gemini", "GEMINI_API_KEY"),
    "groq": ("GroqCloud", "GROQ_API_KEY"),
    "openrouter": ("OpenRouter", "OPENROUTER_API_KEY"),
    "omniroute": ("OmniRoute", "OMNIROUTE_API_KEY"),
    "ninerouter": ("9Router", "NINEROUTER_API_KEY"),
    "nvidia": ("Nvidia", "NVIDIA_API_KEY"),
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
    '"role":"Frontend developer","module_id":"valid project sub-project ID or empty string","acceptance_checks":["observable check"],"produces_deliverable":true,"phase":1}]}. '
    "Use only these work roles: PM/PO, Frontend developer, Backend developer, QA, UI designer, Documentation. "
    "Use only sub-project IDs listed in the project context; use an empty module_id for the whole project. "
    "Propose 1 to 12 small tasks. Assign each task a phase number (1 = first milestone, 2 = next, and so on); "
    "later phases only start after earlier phases complete. Every plan that produces deliverables must include "
    "at least one Documentation task per phase covering user-facing or technical documentation. "
    "When writing the overview, preserve confirmed existing context, separate facts from assumptions, "
    "and provide concise complete content for all three documents. Do not claim the tasks have already been created."
)

ESCALATION_CONTRACT = (
    "For a PM/PO escalation request, return ONLY one valid JSON object, with no markdown: "
    '{"type":"escalation","summary":"brief summary","design_change":{"description":"what changes and why","reason":"root cause"},'
    '"team_tasks":[{"title":"task title","request":"bounded implementation request",'
    '"role":"Frontend developer","module_id":"valid project sub-project ID or empty string","acceptance_checks":["observable check"],"produces_deliverable":true,"phase":1}],'
    '"alternatives":[{"title":"option name","description":"trade-offs and impact"}],'
    '"recommendation":"which option you recommend and why"}. '
    "team_tasks are executed immediately while the user reviews the design change; keep them small and reversible. "
    "Set phase when the work belongs to a planned phase; if omitted, the blocked task's phase is inherited. "
    "alternatives must list 1 to 4 other viable solutions with honest trade-offs. "
    "If the user approves the design change, the blocked source task is canceled as superseded; include any still-required work in team_tasks. "
    "You may not edit existing confirmed tasks; express every requirement change through this response. "
    "If the failure needs no design change, return design_change as null and team_tasks as an empty list."
)

WORK_REPLY_CONTRACT = (
    "Return ONLY one valid JSON object, no markdown, with this shape: "
    '{"summary":"one-line outcome","details":"complete answer or report text"}. '
    "Keep details complete but concise. Do not include file contents or diffs."
)

STATE_LOCK = threading.RLock()
DISPATCH_EVENT = threading.Event()

ACTIVE_CHAT_LOCK = threading.Lock()
ACTIVE_CHAT_COUNT = 0
ACTIVE_GEMINI_COUNT = 0
ACTIVE_GROQ_COUNT = 0
ACTIVE_OPENROUTER_COUNT = 0
ACTIVE_OMNIROUTE_COUNT = 0
ACTIVE_NINEROUTER_COUNT = 0
ACTIVE_NVIDIA_COUNT = 0
ACTIVE_REPOSITORY_TASKS: set[str] = set()
MAX_QA_FAILURES = 3

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


def resolve_static_asset(relative_path: str) -> Path:
    """Resolve a static asset path safely below the static directory."""
    candidate = (STATIC / relative_path).resolve()
    try:
        candidate.relative_to(STATIC.resolve())
    except ValueError as exc:
        raise ValueError("Invalid static asset path.") from exc
    if candidate.suffix.lower() not in {".html", ".css", ".js", ".mjs"}:
        raise ValueError("Unsupported static asset type.")
    return candidate


def static_content_type(file_path: Path) -> str:
    """Return the content type for a supported static asset."""
    return {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8",
    }.get(file_path.suffix.lower(), "application/octet-stream")


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


def append_event(event: dict[str, Any]) -> None:
    ensure_data()
    with STATE_LOCK:
        log_path = DATA / "logs" / "activity.jsonl"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

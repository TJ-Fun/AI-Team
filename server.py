#!/usr/bin/env python3
"""Small, local-only task board and Ollama chat for the personal AI team."""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import stat
import threading
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse, parse_qs

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # credentials UI remains disabled until cryptography is installed
    Fernet = None
    InvalidToken = Exception


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA = Path(os.environ.get("TEAM_DATA_DIR", str(ROOT / "data"))).expanduser()
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "orchestrator")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()
GEMINI_MAX_OUTPUT_TOKENS = int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", "1024"))
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = "openrouter/free"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b").strip()
ANTIGRAVITY_AGENT = os.environ.get("ANTIGRAVITY_AGENT", "antigravity-preview-09-2026").strip()
SECRET_KEY_FILE = Path(os.environ.get("TEAM_SECRET_KEY_FILE", str(DATA / ".credential-key"))).expanduser()
PORT = int(os.environ.get("TEAM_PORT", "8765"))
WATCHDOG_INTERVAL_SECONDS = 2 * 60 * 60
DEFAULT_ATTEMPT_TIMEOUT_SECONDS = 2 * 60 * 60
MAX_AUTOMATIC_RETRIES = 2
WORK_ROLES = ("PM/PO", "Frontend developer", "Backend developer", "QA", "UI designer", "Documentation")
PROVIDERS = ("Gemini", "Antigravity", "GroqCloud", "OpenRouter")
TASK_PROVIDERS = ("Automatic", *PROVIDERS)
ROUTING_DEFAULTS = {
    "PM/PO": ["Gemini", "GroqCloud", "Antigravity", "OpenRouter"],
    "Frontend developer": ["Antigravity", "Gemini", "GroqCloud", "OpenRouter"],
    "Backend developer": ["Antigravity", "Gemini", "GroqCloud", "OpenRouter"],
    "QA": ["GroqCloud", "Gemini", "Antigravity", "OpenRouter"],
    "UI designer": ["Gemini", "Antigravity", "GroqCloud", "OpenRouter"],
    "Documentation": ["Gemini", "Antigravity", "GroqCloud", "OpenRouter"],
}
LOCK = threading.RLock()
ACTIVE_CHAT = 0
ACTIVE_GEMINI = 0
ACTIVE_OPENROUTER = 0
CPU_LOCK = threading.Lock()
PREVIOUS_CPU = None
WATCHDOG_LAST_RUN = None
WATCHDOG_LAST_CHANGED = 0
WATCHDOG_ERROR = None
ANTIGRAVITY_GITHUB_SYNCED_HASH = None
PROMPT_FILES = {
    "shared_rules": "Shared rules",
    "orchestrator": "Orchestrator",
    "pm_po": "PM/PO",
    "frontend_developer": "Frontend developer",
    "backend_developer": "Backend developer",
    "qa": "QA",
    "ui_designer": "UI designer",
    "documentation": "Documentation",
}
PROJECT_CONTEXT_FILES = {
    "project_brief": "Project brief",
    "technical_overview": "Technical overview",
    "decisions": "Decision log",
    "outcomes": "Confirmed outcomes",
}

TRANSITIONS = {
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
USER_TRANSITIONS = {
    "Draft": {"Ready", "Blocked", "Canceled"},
    "Ready": {"Blocked", "Canceled"},
    "Rework needed": {"Ready", "Blocked", "Canceled"},
    "Blocked": {"Draft", "Ready", "Canceled"},
}

SYSTEM_PROMPT = (
    "You are the local Orchestrator for one personal software project. "
    "Help the user clarify work, plan tasks, and understand task progress. "
    "Be concise. The task board is the source of truth. This chat cannot itself "
    "change task states or validate code. The Send to PM/PO action creates a planning task; "
    "valid PM/PO questions and planned work are logged by the app, while work tasks stay Draft "
    "until the user marks them Ready. Ready tasks are dispatched automatically using their "
    "role's configured provider order. Never claim that you personally dispatched an agent "
    "or performed validation."
)


class GeminiFailure(Exception):
    def __init__(self, status: int, message: str, retry_after: str | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class OpenRouterFailure(Exception):
    def __init__(self, status: int, message: str, retry_after: str | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class GroqFailure(Exception):
    def __init__(self, status: int, message: str, retry_after: str | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class AntigravityFailure(Exception):
    def __init__(self, status: int, message: str, retry_after: str | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def ensure_data() -> None:
    for name in ("tasks", "replies", "logs", "prompts", "project"):
        (DATA / name).mkdir(parents=True, exist_ok=True)
    for slug in PROMPT_FILES:
        default = ROOT / "prompts" / f"{slug}.md"
        custom = DATA / "prompts" / f"{slug}.md"
        if default.exists() and not custom.exists():
            shutil.copyfile(default, custom)
    for slug in PROJECT_CONTEXT_FILES:
        default = ROOT / "project_templates" / f"{slug}.md"
        saved = DATA / "project" / f"{slug}.md"
        if default.exists() and not saved.exists():
            shutil.copyfile(default, saved)
        history = DATA / "project" / "history" / slug
        if saved.exists() and not any(history.glob("*.md")):
            history.mkdir(parents=True, exist_ok=True)
            version_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            shutil.copyfile(saved, history / f"{version_id}.md")


def prompt_path(slug: str) -> Path:
    if slug not in PROMPT_FILES:
        raise ValueError("Unknown prompt role.")
    ensure_data()
    return DATA / "prompts" / f"{slug}.md"


def load_prompt(slug: str) -> str:
    path = prompt_path(slug)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def combined_agent_prompt(role: str) -> str:
    slug = {
        "PM/PO": "pm_po",
        "Frontend developer": "frontend_developer",
        "Backend developer": "backend_developer",
        "QA": "qa",
        "UI designer": "ui_designer",
        "Documentation": "documentation",
    }.get(role, "pm_po")
    return "\n\n".join(item for item in (load_prompt("shared_rules"), load_prompt(slug)) if item)


def load_project_context(slug: str) -> str:
    if slug not in PROJECT_CONTEXT_FILES:
        return ""
    ensure_data()
    try:
        return (DATA / "project" / f"{slug}.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def project_context_history_path(slug: str, version_id: str | None = None) -> Path:
    base = DATA / "project" / "history" / slug
    return base if version_id is None else base / f"{version_id}.md"


def save_project_context_document(slug: str, content: str) -> str | None:
    """Save one context file, snapshotting the previous text when it changes."""
    if slug not in PROJECT_CONTEXT_FILES:
        raise ValueError("Unknown project context document.")
    ensure_data()
    path = DATA / "project" / f"{slug}.md"
    try:
        previous = path.read_text(encoding="utf-8")
    except OSError:
        previous = ""
    normalized = content.strip() + "\n"
    version_id = None
    if previous and previous != normalized:
        version_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        snapshot = project_context_history_path(slug, version_id)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(previous, encoding="utf-8")
    temp = path.with_suffix(".md.tmp")
    temp.write_text(normalized, encoding="utf-8")
    os.replace(temp, path)
    return version_id


def project_context_history(slug: str) -> list[dict]:
    if slug not in PROJECT_CONTEXT_FILES:
        return []
    folder = project_context_history_path(slug)
    entries = []
    paths = sorted(folder.glob("*.md"), reverse=True) if folder.exists() else []
    for path in paths:
        version_id = path.stem
        try:
            timestamp = datetime.strptime(version_id, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc).isoformat(timespec="microseconds")
        except ValueError:
            timestamp = version_id
        try:
            preview = " ".join(path.read_text(encoding="utf-8").split())[:140]
        except OSError:
            preview = "Saved version could not be read."
        entries.append({"id": version_id, "created_at": timestamp, "preview": preview})
    return entries


def project_context_for_role(role: str, task: dict | None = None) -> str:
    sections = [
        ("Project brief", load_project_context("project_brief")),
        ("Technical overview", load_project_context("technical_overview")),
        ("Approved decisions", load_project_context("decisions")),
        ("Confirmed outcomes", load_project_context("outcomes")),
    ]
    if role == "UI designer":
        sections = [sections[0], sections[2], sections[3]]
    elif role == "PM/PO":
        tasks = read_tasks()
        project_id = (task or {}).get("project_id", "default")
        current = [item for item in tasks if item.get("status") not in ("Canceled",) and item.get("project_id", "default") == project_id]
        board_lines = [
            f"- {task.get('id')} | {task.get('status')} | {task.get('assigned_role')} | {task.get('title')}"
            for task in current[:40]
        ] or ["- No tasks recorded."]
        completed = []
        for task in current:
            if task.get("status") != "Done" or not task.get("latest_reply_file"):
                continue
            reply_path = (DATA / str(task["latest_reply_file"])).resolve()
            if DATA.resolve() not in reply_path.parents:
                continue
            try:
                reply = json.loads(reply_path.read_text(encoding="utf-8"))
                excerpt = " ".join(str(reply.get("text", "")).split())[:500]
            except (OSError, json.JSONDecodeError):
                excerpt = "Saved reply is unavailable."
            completed.append(f"- {task.get('id')} {task.get('title')}: {excerpt or 'No text reply.'}")
            if len(completed) == 8:
                break
        sections.append(("Current board (live)", "\n".join(board_lines)))
        sections.append((
            "Recent completed agent responses (not proof of integration)",
            "\n".join(completed) or "No completed agent responses yet.",
        ))
    projects = read_projects()
    project_id = (task or {}).get("project_id", "default")
    project = next((item for item in projects if item.get("id") == project_id), projects[0] if projects else {})
    if project:
        sections.append(("Project", f"Name: {project.get('name', '')}\nRepository: {project.get('repository', '')}\n\n{project.get('context', '')}"))
        module_id = (task or {}).get("module_id", "")
        module = next((item for item in project.get("modules", []) if item.get("id") == module_id), None)
        if module:
            sections.append((f"Sub-project: {module.get('name', '')}", f"Repository folder: {module.get('path', '')}\n{module.get('context', '')}"))
        elif role == "PM/PO" and project.get("modules"):
            catalog = "\n".join(f"- {item.get('id')}: {item.get('name')} ({item.get('path') or 'repository root'}) — {item.get('context', '')[:500]}" for item in project["modules"])
            sections.append(("Available sub-projects", catalog))
    return "\n\n".join(f"## {title}\n{content[:6000]}" for title, content in sections if content)


def next_task_id() -> str:
    current = [
        int(match.group(1)) for task in read_tasks()
        if (match := re.fullmatch(r"T-(\d{4,})", task.get("id", "")))
    ]
    return f"T-{max(current, default=0) + 1:04d}"


def parse_pm_plan_response(reply: str, project_id: str = "default") -> dict:
    text = reply.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("PM/PO response did not contain a JSON object.")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("PM/PO response must be a JSON object.")
    kind = data.get("type")
    summary = str(data.get("summary", "")).strip()[:2000]
    if kind == "questions":
        questions = data.get("questions")
        if not isinstance(questions, list) or not 1 <= len(questions) <= 8:
            raise ValueError("A clarification response must contain between 1 and 8 questions.")
        normalized = []
        for item in questions:
            if not isinstance(item, dict):
                raise ValueError("Each clarification question must be an object.")
            title = str(item.get("title", "")).strip()[:120]
            question = str(item.get("question", "")).strip()[:2000]
            options = item.get("options")
            if not title or not question or not isinstance(options, list) or not 2 <= len(options) <= 8:
                raise ValueError("Each question needs a title, question, and 2 to 8 answer options.")
            clean_options = []
            for option in options:
                if isinstance(option, str):
                    label, description = option.strip(), ""
                elif isinstance(option, dict):
                    label = str(option.get("label", "")).strip()
                    description = str(option.get("description", "")).strip()[:500]
                else:
                    continue
                if label:
                    clean_options.append({"label": label[:160], "description": description})
            if len(clean_options) < 2:
                raise ValueError("Each question must have at least two usable options.")
            normalized.append({
                "title": title, "question": question,
                "why": str(item.get("why", "")).strip()[:1000],
                "options": clean_options,
                "allow_free_text": item.get("allow_free_text", True) is not False,
            })
        return {"type": kind, "summary": summary, "questions": normalized}
    if kind == "plan":
        tasks = data.get("tasks")
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 12:
            raise ValueError("A plan response must contain between 1 and 12 tasks.")
        normalized = []
        for item in tasks:
            if not isinstance(item, dict):
                raise ValueError("Each proposed task must be an object.")
            title = str(item.get("title", "")).strip()[:160]
            request = str(item.get("request", "")).strip()[:12000]
            role = str(item.get("role", "")).strip()
            module_id = str(item.get("module_id", "")).strip()
            checks = item.get("acceptance_checks", [])
            project = next((candidate for candidate in read_projects() if candidate.get("id") == project_id), {})
            valid_modules = {module.get("id") for module in project.get("modules", [])}
            if not title or not request or role not in WORK_ROLES or not isinstance(checks, list) or (module_id and module_id not in valid_modules):
                raise ValueError("Each proposed task needs a title, request, valid work role, and acceptance-check list.")
            clean_checks = [str(check).strip()[:500] for check in checks if str(check).strip()][:20]
            produces_value = item.get("produces_deliverable", role != "PM/PO")
            produces = produces_value if isinstance(produces_value, bool) else role != "PM/PO"
            if produces and not clean_checks:
                clean_checks.append("Manually review the resulting deliverable against the task request.")
            normalized.append({
                "title": title, "request": request, "role": role,
                "module_id": module_id,
                "acceptance_checks": clean_checks,
                "produces_deliverable": produces,
            })
        overview = data.get("project_overview")
        if not isinstance(overview, dict):
            raise ValueError("A final plan must include a project_overview for user approval.")
        overview_docs = {}
        for slug in ("project_brief", "technical_overview", "decisions"):
            content = overview.get(slug)
            if not isinstance(content, str) or not content.strip() or len(content) > 20_000:
                raise ValueError(f"The project overview needs non-empty {slug} text under 20,000 characters.")
            overview_docs[slug] = content.strip()
        return {"type": kind, "summary": summary, "tasks": normalized, "project_overview": overview_docs}
    raise ValueError("PM/PO response type must be 'questions' or 'plan'.")


def create_planning_children(parent: dict, result: dict) -> list[str]:
    timestamp = now()
    ids = []
    if result["type"] == "questions":
        children = [("clarification_question", item) for item in result["questions"]]
    else:
        overview_id = next_task_id()
        ids.append(overview_id)
        overview_task = {
            "id": overview_id, "task_type": "project_overview",
            "title": "Project overview — review and approve",
            "request": "Review the PM/PO's proposed project brief, technical overview, and decisions. Edit any text before approval.",
            "overview_documents": result["project_overview"],
            "assigned_role": "PM/PO", "provider": "Automatic", "status": "Needs approval",
            "planning_root_id": parent["planning_root_id"], "source_plan_task_id": parent["id"],
            "project_id": parent.get("project_id", "default"), "module_id": "",
            "created_at": timestamp, "updated_at": timestamp,
            "attempts": [], "validation": None,
            "activity": [{"at": timestamp, "type": "created", "message": "PM/PO prepared a project overview; waiting for your approval."}],
        }
        atomic_json(DATA / "tasks" / f"{overview_id}.json", overview_task)
        append_event({"at": timestamp, "task_id": overview_id, "type": "project_overview_proposed", "status": "Needs approval", "parent_task_id": parent["id"]})
        parent["project_overview_task_id"] = overview_id
        parent["work_task_ids"] = []
        children = [("work", item) for item in result["tasks"]]
    for child_type, item in children:
        task_id = next_task_id()
        ids.append(task_id)
        if child_type == "clarification_question":
            task = {
                "id": task_id, "task_type": child_type,
                "title": item["title"], "request": item["question"],
                "question_data": {"why": item["why"], "options": item["options"], "allow_free_text": item["allow_free_text"]},
                "assigned_role": "PM/PO", "provider": "Automatic", "status": "Needs answer",
                "planning_root_id": parent["planning_root_id"], "planning_task_id": parent["id"],
                "planning_round": parent.get("planning_round", 1),
                "project_id": parent.get("project_id", "default"), "module_id": "",
                "created_at": timestamp, "updated_at": timestamp,
                "attempts": [], "validation": None,
                "activity": [{"at": timestamp, "type": "created", "message": "PM/PO clarification question; awaiting your answer."}],
            }
        else:
            parent.setdefault("work_task_ids", []).append(task_id)
            task = {
                "id": task_id, "task_type": "work",
                "title": item["title"], "request": item["request"],
                "assigned_role": item["role"], "provider": "Automatic",
                "acceptance_checks": item["acceptance_checks"],
                "produces_deliverable": item["produces_deliverable"], "status": "Draft",
                "planning_root_id": parent["planning_root_id"], "source_plan_task_id": parent["id"],
                "project_id": parent.get("project_id", "default"), "module_id": item.get("module_id", ""),
                "created_at": timestamp, "updated_at": timestamp,
                "attempts": [], "validation": None,
                "activity": [{"at": timestamp, "type": "created", "message": f"Proposed by PM/PO in {parent['id']}; review before marking Ready."}],
            }
        atomic_json(DATA / "tasks" / f"{task_id}.json", task)
        append_event({"at": timestamp, "task_id": task_id, "type": "created", "status": task["status"], "source": "pm_po", "parent_task_id": parent["id"]})
    parent["child_task_ids"] = ids
    parent["planning_result_type"] = result["type"]
    parent["planning_summary"] = result.get("summary", "")
    return ids


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


def finish_pm_planning_task(task: dict, reply: str, provider: str) -> tuple[str, str]:
    try:
        result = parse_pm_plan_response(reply, task.get("project_id", "default"))
        children = create_planning_children(task, result)
    except (ValueError, json.JSONDecodeError) as exc:
        task["validation"] = {
            "status": "failed",
            "checks": [{"check": "PM/PO response structure", "kind": "schema", "passed": False, "message": str(exc)}],
            "validated_at": now(),
        }
        task["planning_error"] = str(exc)
        return "Rework needed", f"{provider} replied, but the PM/PO result could not be logged: {exc}"
    task["validation"] = {"status": "passed", "checks": [{"check": "PM/PO response structure", "kind": "schema", "passed": True, "message": "Response parsed and saved to the board."}], "validated_at": now()}
    task["planning_summary"] = result.get("summary", "")
    if result["type"] == "questions":
        return "Done", f"PM/PO clarification questions logged as {', '.join(children)}. Waiting for your answers."
    overview_id = task.get("project_overview_task_id", "")
    work_ids = task.get("work_task_ids", [])
    return "Done", f"PM/PO created project overview {overview_id} for your approval and {len(work_ids)} draft work task(s): {', '.join(work_ids)}. Review them on the board."


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_bytes(path.read_bytes())
    os.replace(temp, path)


def append_event(event: dict) -> None:
    with (DATA / "logs" / "activity.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


SECRET_NAMES = {
    "gemini": "Gemini API key",
    "openrouter": "OpenRouter API key",
    "groq": "GroqCloud API key",
    "github": "GitHub fine-grained token",
}


def secret_cipher(create: bool = False):
    if Fernet is None:
        raise RuntimeError("Secure credential storage is unavailable. Install python3-cryptography first.")
    if not SECRET_KEY_FILE.exists():
        if not create:
            return None
        SECRET_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        descriptor = os.open(SECRET_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(key)
        try:
            os.chmod(SECRET_KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
    try:
        key = SECRET_KEY_FILE.read_bytes().strip()
        return Fernet(key)
    except (OSError, ValueError) as exc:
        raise RuntimeError("The credential encryption key could not be read.") from exc


def read_secrets() -> dict:
    path = DATA / "secrets.enc.json"
    if not path.exists():
        return {}
    cipher = secret_cipher()
    if cipher is None:
        raise RuntimeError("Credentials exist but the encryption key file is missing.")
    try:
        encrypted = json.loads(path.read_text(encoding="utf-8"))
        return json.loads(cipher.decrypt(encrypted["payload"].encode("ascii")).decode("utf-8"))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, InvalidToken) as exc:
        raise RuntimeError("The encrypted credential store could not be opened; no credentials were changed.") from exc


def save_secret(name: str, value: str | None) -> None:
    if name not in SECRET_NAMES:
        raise ValueError("Unknown credential.")
    path = DATA / "secrets.enc.json"
    secrets = read_secrets()
    if value:
        secrets[name] = value.strip()
    else:
        secrets.pop(name, None)
    cipher = secret_cipher(create=bool(secrets))
    if secrets:
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps({"payload": cipher.encrypt(json.dumps(secrets).encode("utf-8")).decode("ascii")}, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(temp, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        os.replace(temp, path)
        path.with_suffix(".json.bak").unlink(missing_ok=True)
    else:
        path.unlink(missing_ok=True)
        path.with_suffix(".json.bak").unlink(missing_ok=True)


def provider_key(name: str) -> str:
    saved = read_secrets().get(name)
    if saved:
        return str(saved)
    return {"gemini": GEMINI_API_KEY, "openrouter": OPENROUTER_API_KEY,
            "groq": os.environ.get("GROQ_API_KEY", "").strip(),
            "github": os.environ.get("GITHUB_TOKEN", "").strip()}.get(name, "")


def configured_providers() -> list[str]:
    available = {"Gemini": bool(provider_key("gemini")), "Antigravity": bool(provider_key("gemini")),
                 "GroqCloud": bool(provider_key("groq")), "OpenRouter": bool(provider_key("openrouter"))}
    return [name for name in PROVIDERS if available.get(name)]


def get_routing() -> dict:
    path = DATA / "settings" / "routing.json"
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        saved = {}
    result = {}
    configured = configured_providers()
    for role, defaults in ROUTING_DEFAULTS.items():
        order = saved.get(role, defaults) if isinstance(saved, dict) else defaults
        if not isinstance(order, list):
            order = defaults
        unique = [provider for provider in order if provider in PROVIDERS]
        result[role] = unique + [provider for provider in defaults if provider not in unique]
    return result


def save_routing(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Routing settings must be an object.")
    cleaned = {}
    for role in WORK_ROLES:
        order = value.get(role)
        if not isinstance(order, list) or len(order) != len(PROVIDERS) or set(order) != set(PROVIDERS):
            raise ValueError(f"Set each provider exactly once for {role}.")
        cleaned[role] = order
    atomic_json(DATA / "settings" / "routing.json", cleaned)
    return cleaned


def read_projects() -> list[dict]:
    path = DATA / "projects.json"
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(items, list) and items:
            return items
    except (OSError, json.JSONDecodeError):
        pass
    default = {"id": "default", "name": "Main project", "repository": "", "context": "", "modules": []}
    atomic_json(path, [default])
    return [default]


def save_projects(items: object) -> list[dict]:
    if not isinstance(items, list) or not items:
        raise ValueError("Keep at least one project.")
    cleaned = []
    existing = {item.get("id"): item for item in read_projects()}
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each project must be an object.")
        project_id = re.sub(r"[^a-z0-9-]+", "-", str(item.get("id", "")).lower()).strip("-")[:40]
        name = str(item.get("name", "")).strip()[:100]
        repository = str(item.get("repository", "")).strip()
        modules = item.get("modules", [])
        if not project_id or project_id in seen or not name or not isinstance(modules, list):
            raise ValueError("Project names and unique IDs are required; modules must be a list.")
        if repository and not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", repository):
            raise ValueError(f"Repository for {name} must be a GitHub https URL.")
        clean_modules = []
        module_ids = set()
        for module in modules:
            if not isinstance(module, dict):
                raise ValueError("Each sub-project must be an object.")
            module_id = re.sub(r"[^a-z0-9-]+", "-", str(module.get("id", "")).lower()).strip("-")[:40]
            module_name = str(module.get("name", "")).strip()[:100]
            module_path = str(module.get("path", "")).strip().replace("\\", "/").strip("/")
            if not module_id or module_id in module_ids or not module_name or ".." in module_path.split("/"):
                raise ValueError("Each sub-project needs a unique ID, name, and safe repository folder path.")
            module_ids.add(module_id)
            clean_modules.append({"id": module_id, "name": module_name, "path": module_path,
                                  "context": str(module.get("context", ""))[:10000]})
        seen.add(project_id)
        context = str(item.get("context", ""))[:20000]
        old = existing.get(project_id, {})
        if old.get("context", "") and old.get("context", "") != context:
            version_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            snapshot = DATA / "projects" / project_id / "history" / f"{version_id}.md"
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_text(str(old["context"]), encoding="utf-8")
        cleaned.append({"id": project_id, "name": name, "repository": repository, "context": context, "modules": clean_modules})
    current_ids = {item.get("id", "default") for item in read_tasks()}
    missing = current_ids - seen
    if missing:
        raise ValueError("Projects with existing tasks cannot be removed. Keep them and edit their details instead.")
    atomic_json(DATA / "projects.json", cleaned)
    return cleaned


def project_background_history(project_id: str) -> list[dict]:
    if not any(project.get("id") == project_id for project in read_projects()):
        return []
    folder = DATA / "projects" / project_id / "history"
    rows = []
    for path in sorted(folder.glob("*.md"), reverse=True) if folder.exists() else []:
        try:
            stamp = datetime.strptime(path.stem, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc).isoformat(timespec="microseconds")
            content = path.read_text(encoding="utf-8")
        except (ValueError, OSError):
            continue
        rows.append({"id": path.stem, "created_at": stamp, "preview": " ".join(content.split())[:140]})
    return rows


def notifications() -> list[dict]:
    items = []
    for task in read_tasks():
        if task.get("status") == "Blocked":
            items.append({"id": f"task:{task.get('id')}:blocked:{task.get('updated_at', '')}", "kind": "blocked", "severity": "high",
                          "title": f"{task.get('id')} is blocked", "message": task.get("block_reason", "Task needs attention."), "task_id": task.get("id")})
        if task.get("status") == "Waiting for capacity":
            provider = (task.get("attempts") or [{}])[-1].get("provider", "Provider")
            items.append({"id": f"task:{task.get('id')}:capacity:{task.get('updated_at', '')}", "kind": "capacity", "severity": "medium",
                          "title": f"{provider} is unavailable", "message": f"{task.get('id')} is waiting for capacity.", "task_id": task.get("id")})
        if task.get("github_error"):
            items.append({"id": f"task:{task.get('id')}:github:{task.get('updated_at', '')}", "kind": "github", "severity": "high",
                          "title": f"GitHub needs attention for {task.get('id')}", "message": task.get("github_error"), "task_id": task.get("id")})
    available = set(configured_providers())
    all_tasks = read_tasks()
    if not available and any(task.get("status") == "Ready" for task in all_tasks):
        items.append({"id": "providers:none:configured", "kind": "provider", "severity": "high", "title": "No AI provider is connected", "message": "Ready tasks are queued. Add at least one provider credential in Connections & routing."})
    for task in all_tasks:
        chosen = task.get("provider")
        if task.get("status") == "Ready" and chosen in PROVIDERS and chosen not in available:
            items.append({"id": f"task:{task.get('id')}:provider-missing:{chosen}", "kind": "provider", "severity": "medium",
                          "title": f"{task.get('id')} is waiting for {chosen}", "message": f"This task explicitly selected {chosen}. Connect it or edit the task's provider selection.", "task_id": task.get("id")})
    attempts_by_role: dict[str, dict[str, list[dict]]] = {}
    for task in read_tasks():
        role = task.get("assigned_role", "")
        for attempt in task.get("attempts", []):
            provider = attempt.get("provider")
            if provider not in PROVIDERS or attempt.get("state") not in ("succeeded", "failed", "timed_out", "provider_unavailable", "waiting_for_capacity"):
                continue
            quality = attempt.get("quality_outcome")
            if quality is None and attempt.get("state") == "succeeded":
                quality = "unknown"
            attempts_by_role.setdefault(role, {}).setdefault(provider, []).append({**attempt, "quality_outcome": quality})
    for role, providers in attempts_by_role.items():
        ranked = []
        for provider, records in providers.items():
            labeled = [r for r in records if r.get("quality_outcome") in ("passed", "failed")]
            if len(labeled) >= 10:
                rate = sum(r["quality_outcome"] == "passed" for r in labeled) / len(labeled)
                ranked.append((rate, len(labeled), provider))
        ranked.sort(reverse=True)
        if len(ranked) > 1 and ranked[0][1] >= ranked[1][1] and ranked[0][0] - ranked[1][0] >= 0.15:
            best = ranked[0]
            items.append({"id": f"provider-better:{role}:{best[2]}", "kind": "provider_suggestion", "severity": "low",
                          "title": f"Consider {best[2]} as {role} default", "message": f"It passed QA on {round(best[0]*100)}% of {best[1]} measured attempts in this role. Routing order has not been changed."})
    if not provider_key("github"):
        items.append({"id": "connection:github:missing", "kind": "connection", "severity": "low", "title": "GitHub is not connected", "message": "Add a fine-grained token in Connections to enable repository workflows."})
    try:
        events = []
        with (DATA / "logs" / "activity.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                try: events.append(json.loads(line))
                except json.JSONDecodeError: continue
        last_github_test = next((event for event in reversed(events) if event.get("type") == "github_connection_test"), None)
        if last_github_test and not last_github_test.get("success"):
            items.append({"id": f"connection:github:test:{last_github_test.get('at', '')}", "kind": "connection", "severity": "high",
                          "title": "GitHub connection failed", "message": str(last_github_test.get("error", "Check the token and its repository permissions."))[:350]})
    except OSError:
        pass
    return items


def provider_quality() -> list[dict]:
    grouped: dict[tuple[str, str], dict] = {}
    for task in read_tasks():
        role = task.get("assigned_role", "Unknown")
        for attempt in task.get("attempts", []):
            provider = attempt.get("provider")
            if provider not in PROVIDERS:
                continue
            row = grouped.setdefault((role, provider), {"role": role, "provider": provider, "requests": 0, "replies": 0,
                "quota_or_service_errors": 0, "measured": 0, "passed": 0, "failed": 0, "total_seconds": 0, "timed": 0})
            row["requests"] += 1
            if attempt.get("state") == "succeeded": row["replies"] += 1
            if attempt.get("state") in ("provider_unavailable", "waiting_for_capacity", "timed_out", "failed"): row["quota_or_service_errors"] += 1
            outcome = attempt.get("quality_outcome")
            if outcome in ("passed", "failed"):
                row["measured"] += 1
                row[outcome] += 1
            if attempt.get("started_at") and attempt.get("finished_at"):
                try:
                    seconds = (parse_time(attempt["finished_at"]) - parse_time(attempt["started_at"])).total_seconds()
                    if seconds >= 0: row["total_seconds"] += seconds; row["timed"] += 1
                except (TypeError, ValueError):
                    pass
    rows = list(grouped.values())
    for row in rows:
        row["qa_pass_rate"] = round(100 * row["passed"] / row["measured"]) if row["measured"] else None
        row["average_seconds"] = round(row["total_seconds"] / row["timed"], 1) if row["timed"] else None
        row.pop("total_seconds"); row.pop("timed")
    return sorted(rows, key=lambda row: (row["role"], row["provider"]))


def github_api(path: str, method: str = "GET", payload: dict | None = None) -> dict | list:
    token = provider_key("github")
    if not token:
        raise ValueError("GitHub token is not configured.")
    request = urllib.request.Request("https://api.github.com" + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Personal-AI-Team"}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise ValueError(f"GitHub returned HTTP {exc.code}: {detail}") from exc


def github_project(project_id: str) -> dict:
    project = next((item for item in read_projects() if item.get("id") == project_id), None)
    if not project or not project.get("repository"):
        raise ValueError("This project does not have a GitHub repository configured.")
    return project


def safe_repo_path(value: str) -> str:
    path = value.strip().replace("\\", "/").strip("/")
    if path and (path.startswith(".") or any(part in (".", "..") or part.startswith(".") for part in path.split("/"))):
        raise ValueError("Hidden and relative repository paths are not available in this viewer.")
    if ".." in path.split("/") or len(path) > 500:
        raise ValueError("Invalid repository path.")
    return path


def github_file_listing(project_id: str, path: str = "") -> dict:
    project = github_project(project_id)
    owner, repo = repository_parts(project["repository"])
    metadata = github_api(f"/repos/{owner}/{repo}")
    branch = str(metadata.get("default_branch", "main"))
    path = safe_repo_path(path)
    suffix = "/" + quote(path, safe="/") if path else ""
    result = github_api(f"/repos/{owner}/{repo}/contents{suffix}?ref={quote(branch, safe='')}")
    if not isinstance(result, list):
        result = [result]
    return {"branch": branch, "path": path, "entries": [{"name": item.get("name", ""), "path": item.get("path", ""), "type": item.get("type"), "size": item.get("size", 0)} for item in result if isinstance(item, dict)]}


def github_read_text_file(project_id: str, path: str) -> dict:
    project = github_project(project_id)
    owner, repo = repository_parts(project["repository"])
    branch = str(github_api(f"/repos/{owner}/{repo}").get("default_branch", "main"))
    path = safe_repo_path(path)
    if not path or path.split("/")[-1].startswith("."):
        raise ValueError("Choose a visible file path.")
    item = github_api(f"/repos/{owner}/{repo}/contents/{quote(path, safe='/')}?ref={quote(branch, safe='')}")
    if not isinstance(item, dict) or item.get("type") != "file":
        raise ValueError("That path is not a file.")
    if int(item.get("size", 0)) > 300_000:
        raise ValueError("This file is larger than the 300 KB viewer limit.")
    if item.get("encoding") != "base64":
        raise ValueError("GitHub did not return text file content.")
    import base64
    content = base64.b64decode(item.get("content", "")).decode("utf-8", errors="replace")
    if "\x00" in content:
        raise ValueError("Binary files are not shown in the viewer.")
    return {"branch": branch, "path": path, "content": content}


def github_task_context(task: dict) -> str:
    """Give text-only providers a small, scoped view of the current repository."""
    project = github_project(str(task.get("project_id", "default")))
    owner, repo = repository_parts(project["repository"])
    metadata = github_api(f"/repos/{owner}/{repo}")
    branch = str(metadata.get("default_branch", "main"))
    module = next((item for item in project.get("modules", []) if item.get("id") == task.get("module_id")), None)
    scope = safe_repo_path(str((module or {}).get("path", "")))
    tree = github_api(f"/repos/{owner}/{repo}/git/trees/{quote(branch, safe='')}?recursive=1")
    entries = tree.get("tree", []) if isinstance(tree, dict) else []
    allowed = (".md", ".txt", ".html", ".css", ".js", ".ts", ".tsx", ".jsx", ".py", ".json", ".toml", ".yml", ".yaml", ".xml", ".sh")
    skip = {"node_modules", "dist", "build", "vendor", ".git", ".venv", "venv", "coverage"}
    files = [item for item in entries if item.get("type") == "blob" and item.get("path", "").lower().endswith(allowed)
             and not any(part in skip or part.startswith(".") for part in item.get("path", "").split("/"))
             and (not scope or item.get("path", "").startswith(scope + "/"))]
    files.sort(key=lambda item: (item["path"].split("/")[-1] not in ("README.md", "readme.md", "package.json", "pyproject.toml"), item["path"]))
    chunks, total = [], 0
    for item in files[:16]:
        if int(item.get("size", 0)) > 12_000 or total + int(item.get("size", 0)) > 24_000:
            continue
        raw = github_api(f"/repos/{owner}/{repo}/contents/{quote(item['path'], safe='/')}?ref={quote(branch, safe='')}")
        if not isinstance(raw, dict) or raw.get("encoding") != "base64":
            continue
        import base64
        text = base64.b64decode(raw.get("content", "")).decode("utf-8", errors="replace")
        if "\x00" in text:
            continue
        chunks.append(f"--- {item['path']} ---\n{text}")
        total += len(text.encode("utf-8"))
    return f"Repository: {owner}/{repo}, default branch: {branch}, editable scope: {scope or '/'}\n" + ("\n\n".join(chunks) if chunks else "No readable source files were found in the selected scope.")


def apply_repository_manifest(task: dict, reply: str, provider: str, attempt_number: int) -> None:
    """Commit a text agent's JSON file replacements to a task branch and open a PR."""
    project = github_project(str(task.get("project_id", "default")))
    owner, repo = repository_parts(project["repository"])
    clean = reply.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean, flags=re.I)
    try:
        manifest = json.loads(clean)
    except json.JSONDecodeError as exc:
        raise ValueError("Agent must return a JSON file-change manifest for a GitHub deliverable.") from exc
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list) or not files or len(files) > 20:
        raise ValueError("File-change manifest must contain 1 to 20 files.")
    module = next((item for item in project.get("modules", []) if item.get("id") == task.get("module_id")), None)
    scope = safe_repo_path(str((module or {}).get("path", "")))
    clean_files, seen = [], set()
    allowed = (".md", ".txt", ".html", ".css", ".js", ".ts", ".tsx", ".jsx", ".py", ".json", ".toml", ".yml", ".yaml", ".xml", ".sh")
    protected_names = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "uv.lock", "cargo.lock", "go.sum"}
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("content"), str):
            raise ValueError("Each manifest file needs a path and text content.")
        path = safe_repo_path(str(item.get("path", "")))
        parts = path.split("/")
        if not path or parts[-1].lower() in protected_names or path in seen or (scope and not path.startswith(scope + "/")) or any(part.startswith(".") or part in {"node_modules", "dist", "build", "vendor"} for part in parts) or not path.lower().endswith(allowed):
            raise ValueError(f"Refusing unsafe or out-of-scope repository path: {path[:120]}")
        content = item["content"]
        if len(content.encode("utf-8")) > 50_000 or "\x00" in content:
            raise ValueError(f"File content is too large or binary: {path}")
        clean_files.append((path, content)); seen.add(path)
    metadata = github_api(f"/repos/{owner}/{repo}")
    base = str(metadata.get("default_branch", "main"))
    base_ref = github_api(f"/repos/{owner}/{repo}/git/ref/heads/{quote(base, safe='')}")
    base_commit = base_ref["object"]["sha"]
    base_tree = github_api(f"/repos/{owner}/{repo}/git/commits/{base_commit}")["tree"]["sha"]
    branch = f"ai-team/{task['id'].lower()}-a{attempt_number:02d}"
    github_api(f"/repos/{owner}/{repo}/git/refs", "POST", {"ref": f"refs/heads/{branch}", "sha": base_commit})
    tree_items = []
    for path, content in clean_files:
        blob = github_api(f"/repos/{owner}/{repo}/git/blobs", "POST", {"content": content, "encoding": "utf-8"})
        tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = github_api(f"/repos/{owner}/{repo}/git/trees", "POST", {"base_tree": base_tree, "tree": tree_items})
    commit = github_api(f"/repos/{owner}/{repo}/git/commits", "POST", {"message": f"{task['id']}: {task.get('title', '')}"[:200], "tree": tree["sha"], "parents": [base_commit]})
    github_api(f"/repos/{owner}/{repo}/git/refs/heads/{quote(branch, safe='/')}", "PATCH", {"sha": commit["sha"], "force": False})
    pull = github_api(f"/repos/{owner}/{repo}/pulls", "POST", {"title": f"{task['id']}: {task.get('title', '')}"[:240], "head": branch, "base": base, "body": f"Automated {provider} changes for {task['id']}.\n\n{str(task.get('request', ''))[:4000]}\n\nAgent report and validation are recorded on the local task board."})
    task["pull_request"] = {"number": pull.get("number"), "url": pull.get("html_url"), "branch": branch, "state": pull.get("state", "open"), "created_at": now()}
    task["repository_change_summary"] = str(manifest.get("summary", ""))[:2000]
    atomic_json(DATA / "tasks" / f"{task['id']}.json", task)


def repository_agent_instructions(task: dict, provider: str) -> str:
    if provider == "Antigravity" or task.get("task_type") == "pm_planning" or task.get("assigned_role") not in ("Frontend developer", "Backend developer", "Documentation"):
        return ""
    project = next((item for item in read_projects() if item.get("id") == task.get("project_id", "default")), {})
    if not project.get("repository"):
        return ""
    return (
        "\n\nRepository snapshot (read-only context):\n" + github_task_context(task) +
        "\n\nReturn only valid JSON, no markdown, with this shape: "
        '{"summary":"brief change summary","files":[{"path":"relative/path","content":"complete replacement file text"}],"checks":["checks performed"]}. '
        "Include complete contents for each changed or new text file. Do not return diffs. Do not delete files. "
        "Only edit files under the supplied editable scope and use the existing project structure and conventions. "
        "Keep related code grouped in cohesive files. Treat around 500 lines in a handwritten source file as a review trigger: split only when there is a clear logical boundary; do not create one-file-per-function. "
        "Do not modify credentials, hidden files, workflows, generated output, dependencies, or lockfiles. If the requested work cannot be done safely from this snapshot, return an empty files array and explain the blocker in summary."
    )


def repository_parts(repository: str) -> tuple[str, str]:
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?", repository)
    if not match:
        raise ValueError("The project's GitHub repository URL is invalid.")
    return match.group(1), match.group(2)


def open_task_pull_request(task_id: str) -> None:
    path = DATA / "tasks" / f"{task_id}.json"
    try:
        task = json.loads(path.read_text(encoding="utf-8"))
        project = next(item for item in read_projects() if item.get("id") == task.get("project_id", "default"))
        owner, repo = repository_parts(project.get("repository", ""))
        branch = f"ai-team/{task_id.lower()}"
        metadata = github_api(f"/repos/{owner}/{repo}")
        created = github_api(f"/repos/{owner}/{repo}/pulls", "POST", {
            "title": f"{task_id}: {task.get('title', '')}"[:240], "head": branch,
            "base": metadata.get("default_branch", "main"),
            "body": f"Automated change for {task_id}.\n\n{str(task.get('request', ''))[:4000]}\n\nAgent report and check results are recorded in the local task board.",
            "draft": False,
        })
        if not isinstance(created, dict):
            raise ValueError("GitHub did not return a pull request record.")
        task["pull_request"] = {"number": created.get("number"), "url": created.get("html_url"), "branch": branch,
                                 "state": created.get("state", "open"), "created_at": now()}
        task.setdefault("activity", []).append({"at": now(), "type": "pull_request_created", "message": f"GitHub pull request #{created.get('number')} created."})
        atomic_json(path, task)
        append_event({"at": now(), "task_id": task_id, "type": "pull_request_created", "provider": "GitHub", "pull_request": created.get("number")})
    except Exception as exc:
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        task["github_error"] = str(exc)[:500]
        task.setdefault("activity", []).append({"at": now(), "type": "github_error", "message": f"Could not create the pull request: {str(exc)[:350]}"})
        atomic_json(path, task)
        append_event({"at": now(), "task_id": task_id, "type": "github_error", "error": str(exc)[:300]})


def merge_task_pull_request(task: dict) -> None:
    pull = task.get("pull_request") or {}
    if not pull.get("number") or task.get("status") != "Done" or (task.get("validation") or {}).get("status") != "passed":
        return
    project = next((item for item in read_projects() if item.get("id") == task.get("project_id", "default")), {})
    try:
        owner, repo = repository_parts(project.get("repository", ""))
        merged = github_api(f"/repos/{owner}/{repo}/pulls/{pull['number']}/merge", "PUT", {"merge_method": "squash"})
        if not isinstance(merged, dict) or not merged.get("merged"):
            raise ValueError("GitHub did not confirm the merge.")
        pull["state"] = "merged"
        pull["merged_at"] = now()
        task["pull_request"] = pull
        task.setdefault("activity", []).append({"at": now(), "type": "pull_request_merged", "message": f"GitHub pull request #{pull['number']} was merged after validation passed."})
        atomic_json(DATA / "tasks" / f"{task['id']}.json", task)
        append_event({"at": now(), "task_id": task["id"], "type": "pull_request_merged", "pull_request": pull["number"]})
    except Exception as exc:
        task["github_error"] = str(exc)[:500]
        task.setdefault("activity", []).append({"at": now(), "type": "github_error", "message": f"Automatic merge failed: {str(exc)[:350]}"})
        atomic_json(DATA / "tasks" / f"{task['id']}.json", task)
        append_event({"at": now(), "task_id": task["id"], "type": "github_merge_error", "error": str(exc)[:300]})


def github_postprocess_task(task_id: str, create_pull_request: bool = False) -> None:
    path = DATA / "tasks" / f"{task_id}.json"
    try:
        task = json.loads(path.read_text(encoding="utf-8"))
        if create_pull_request and task.get("assigned_role") in ("Frontend developer", "Backend developer", "Documentation"):
            open_task_pull_request(task_id)
            task = json.loads(path.read_text(encoding="utf-8"))
        merge_task_pull_request(task)
    except (OSError, json.JSONDecodeError):
        return


def local_llm_usage() -> list[dict]:
    """Return recorded local Orchestrator calls, independent of chat history."""
    log_path = DATA / "logs" / "activity.jsonl"
    calls: list[dict] = []
    pending: dict | None = None
    try:
        with log_path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_type = event.get("type")
                if event_type == "local_llm_request_started":
                    if pending:
                        calls.append(pending)
                    pending = {"provider": "Ollama", "model": event.get("model", OLLAMA_MODEL),
                               "started_at": event.get("at"), "state": "in_progress"}
                elif event_type in {"local_llm_reply", "local_llm_error"} and pending:
                    pending.update({"finished_at": event.get("at"),
                                    "state": "succeeded" if event_type == "local_llm_reply" else "failed",
                                    "duration_seconds": event.get("duration_seconds")})
                    calls.append(pending)
                    pending = None
    except OSError:
        pass
    if pending:
        calls.append(pending)
    return calls


def read_ai_stat_resets() -> list[dict]:
    path = DATA / "logs" / "ai_stats_resets.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def read_tasks() -> list[dict]:
    ensure_data()
    tasks = []
    for path in (DATA / "tasks").glob("*.json"):
        try:
            tasks.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(tasks, key=lambda task: task.get("created_at", ""), reverse=True)


def task_provider(task: dict) -> str:
    """Resolve a task's provider, including records created by older versions."""
    value = task.get("provider", "")
    configured = configured_providers()
    if value in PROVIDERS:
        return value if value in configured else ""
    legacy = {"Gemini": "Gemini", "OpenRouter free agent": "OpenRouter"}.get(task.get("assigned_role"))
    if legacy:
        return legacy
    if value != "Automatic":
        return ""
    attempts = task.get("attempts", [])
    if not isinstance(attempts, list):
        attempts = []
    try:
        attempts = attempts[max(0, int(task.get("routing_cycle_start", 0))):]
    except (TypeError, ValueError):
        pass
    tried = {attempt.get("provider") for attempt in attempts if isinstance(attempt, dict)}
    order = get_routing().get(task.get("assigned_role", "PM/PO"), list(PROVIDERS))
    return next((provider for provider in order if provider in configured and provider not in tried), "")


def has_untried_alternate(task: dict, failed_provider: str) -> bool:
    configured = configured_providers()
    attempts = task.get("attempts", [])
    if not isinstance(attempts, list):
        attempts = []
    try:
        attempts = attempts[max(0, int(task.get("routing_cycle_start", 0))):]
    except (TypeError, ValueError):
        pass
    tried = {attempt.get("provider") for attempt in attempts if isinstance(attempt, dict)}
    return any(provider != failed_provider and provider not in tried for provider in get_routing().get(task.get("assigned_role", "PM/PO"), [] ) if provider in configured)


def ollama_request(payload: dict, timeout: int = 300) -> dict:
    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def cpu_percent() -> float | None:
    """Return whole-VM CPU usage since the previous status poll (0-100%)."""
    global PREVIOUS_CPU
    try:
        fields = Path("/proc/stat").read_text(encoding="ascii").splitlines()[0].split()
        values = [int(value) for value in fields[1:]]
        total = sum(values)
        idle = values[3] + (values[4] if len(values) > 4 else 0)
    except (OSError, IndexError, ValueError):
        return None
    with CPU_LOCK:
        previous = PREVIOUS_CPU
        PREVIOUS_CPU = (total, idle)
    if previous is None:
        return None
    total_delta = total - previous[0]
    idle_delta = idle - previous[1]
    if total_delta <= 0:
        return None
    return round(max(0.0, min(100.0, 100 * (1 - idle_delta / total_delta))))


def call_gemini(task: dict) -> tuple[str, dict]:
    api_key = provider_key("gemini")
    if not api_key:
        raise GeminiFailure(401, "Gemini API key is not configured on the VM.")
    checks = task.get("acceptance_checks", [])
    project_context = project_context_for_role(task.get("assigned_role", "PM/PO"), task)
    prompt = (
        (f"Project context (confirmed reference information):\n{project_context}\n\n" if project_context else "")
        + f"Task ID: {task['id']}\n"
        f"Assigned role: {task.get('assigned_role', 'PM/PO')}\n"
        "Provider: Gemini\n"
        f"Request:\n{task.get('request', '')}\n\n"
        f"Acceptance checks:\n" + ("\n".join(f"- {item}" for item in checks) or "- None specified")
    )
    prompt += repository_agent_instructions(task, "Gemini")
    if task.get("task_type") == "pm_planning":
        prompt += "\n\n" + PM_PLANNING_CONTRACT
    validation = task.get("validation") or {}
    if task.get("status") == "Ready" and validation.get("status") == "failed":
        prompt += "\n\nPrevious attempt failed these validation checks. Correct the work and return a revised result:\n"
        prompt += json.dumps(validation.get("checks", []), ensure_ascii=False, indent=2)
    payload = {
        "systemInstruction": {"parts": [{"text": combined_agent_prompt(task.get("assigned_role", "PM/PO")) + ("\n\n" + PM_PLANNING_CONTRACT if task.get("task_type") == "pm_planning" else "")}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max(GEMINI_MAX_OUTPUT_TOKENS, 4096) if task.get("task_type") == "pm_planning" else GEMINI_MAX_OUTPUT_TOKENS,
            "thinkingConfig": {"thinkingLevel": "minimal"},
        },
    }
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{quote(GEMINI_MODEL, safe='.-')}:generateContent",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=150) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            details = json.loads(raw).get("error", {}).get("message", raw)
        except json.JSONDecodeError:
            details = raw
        raise GeminiFailure(exc.code, str(details)[:2000], exc.headers.get("Retry-After")) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise GeminiFailure(503, str(getattr(exc, "reason", exc))[:1000]) from exc
    candidates = result.get("candidates", [])
    parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
    text = "".join(part.get("text", "") for part in parts if isinstance(part, dict) and not part.get("thought", False)).strip()
    if not text:
        raise GeminiFailure(400, "Gemini returned no text. The response may have been blocked or stopped before producing a result.")
    return text, result


def call_antigravity(task: dict) -> tuple[str, dict]:
    global ANTIGRAVITY_GITHUB_SYNCED_HASH
    api_key = provider_key("gemini")
    if not api_key:
        raise AntigravityFailure(401, "Gemini API key is not configured for Antigravity.")
    project = next((item for item in read_projects() if item.get("id") == task.get("project_id", "default")), {})
    repository = project.get("repository", "")
    input_text = (
        f"Task ID: {task['id']}\nRole: {task.get('assigned_role', 'PM/PO')}\n"
        f"Task request:\n{task.get('request', '')}\n\nAcceptance checks:\n"
        + ("\n".join(f"- {check}" for check in task.get("acceptance_checks", [])) or "- None specified")
        + "\n\nProject context:\n" + project_context_for_role(task.get("assigned_role", "PM/PO"), task)
    )
    if task.get("task_type") == "pm_planning":
        input_text += "\n\n" + PM_PLANNING_CONTRACT
    if repository:
        module = next((m for m in project.get("modules", []) if m.get("id") == task.get("module_id")), None)
        branch = f"ai-team/{task['id'].lower()}"
        folder = module.get("path", "") if module else ""
        input_text += (
            f"\n\nRepository task instructions: Your repository is mounted at /workspace/project. "
            f"Work only in {folder or 'the repository root'} as relevant. Create and work on branch {branch} "
            "(never modify the default branch), implement the requested changes, run the project's available checks, "
            "commit the finished changes, and push that branch to origin. Do not merge. In your final response, "
            f"state the branch name exactly ({branch}), files changed, checks run and results, and any blockers."
        )
    environment = "remote"
    if repository:
        network = {"allowlist": [{"domain": "github.com"}, {"domain": "api.github.com"}, {"domain": "*"}]}
        token = provider_key("github")
        if token:
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            if token_hash != ANTIGRAVITY_GITHUB_SYNCED_HASH:
                credential_url = "https://generativelanguage.googleapis.com/v1beta/credentials/github-production"
                headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
                get_request = urllib.request.Request(credential_url, headers={"x-goog-api-key": api_key})
                try:
                    with urllib.request.urlopen(get_request, timeout=30):
                        exists = True
                except urllib.error.HTTPError as exc:
                    if exc.code == 404:
                        exists = False
                    else:
                        raise AntigravityFailure(exc.code, "Could not check the managed GitHub credential status.") from exc
                except (urllib.error.URLError, TimeoutError) as exc:
                    raise AntigravityFailure(503, f"Could not reach the managed credential service: {getattr(exc, 'reason', exc)}") from exc
                payload = {"type": "bearer_token", "token": token}
                method = "PATCH" if exists else "POST"
                endpoint = credential_url if exists else "https://generativelanguage.googleapis.com/v1beta/credentials"
                if not exists:
                    payload["id"] = "github-production"
                credential_request = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method=method)
                try:
                    with urllib.request.urlopen(credential_request, timeout=30):
                        pass
                except urllib.error.HTTPError as exc:
                    if not exists and exc.code == 409:
                        update_request = urllib.request.Request(credential_url, data=json.dumps({"type": "bearer_token", "token": token}).encode("utf-8"), headers=headers, method="PATCH")
                        try:
                            with urllib.request.urlopen(update_request, timeout=30):
                                pass
                        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as update_error:
                            raise AntigravityFailure(getattr(update_error, "code", 503), "Could not update the managed GitHub credential.") from update_error
                    else:
                        raise AntigravityFailure(exc.code, "Could not save the managed GitHub credential.") from exc
                except (urllib.error.URLError, TimeoutError) as exc:
                    raise AntigravityFailure(503, f"Could not reach the managed credential service: {getattr(exc, 'reason', exc)}") from exc
                ANTIGRAVITY_GITHUB_SYNCED_HASH = token_hash
            network["allowlist"][0]["credential"] = "github-production"
            network["allowlist"][1]["credential"] = "github-production"
        environment = {"type": "remote", "sources": [{"type": "repository", "source": repository, "target": "/workspace/project"}], "network": network}
    payload = {"agent": ANTIGRAVITY_AGENT, "input": input_text,
               "system_instruction": combined_agent_prompt(task.get("assigned_role", "PM/PO")) + ("\n\n" + PM_PLANNING_CONTRACT if task.get("task_type") == "pm_planning" else ""),
               "environment": environment, "background": True, "store": True,
               "agent_config": {"type": "antigravity", "max_total_tokens": 40000}}
    request = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/interactions",
        data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "x-goog-api-key": api_key}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.loads(response.read().decode("utf-8"))
        interaction_id = result.get("id")
        deadline = time.monotonic() + 60 * 60
        while result.get("status") in ("in_progress", "queued") and interaction_id and time.monotonic() < deadline:
            time.sleep(5)
            poll = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/interactions/{quote(str(interaction_id), safe='')}", headers={"x-goog-api-key": api_key})
            with urllib.request.urlopen(poll, timeout=45) as response:
                result = json.loads(response.read().decode("utf-8"))
        if result.get("status") not in (None, "completed"):
            raise AntigravityFailure(429 if result.get("status") == "incomplete" else 503, f"Antigravity interaction ended with status {result.get('status', 'unknown')}.")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1500]
        raise AntigravityFailure(exc.code, detail, exc.headers.get("Retry-After")) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise AntigravityFailure(503, str(getattr(exc, "reason", exc))[:1000]) from exc
    output = result.get("output_text", "")
    if not output:
        chunks = result.get("output", [])
        output = "\n".join(str(item.get("text", "")) for item in chunks if isinstance(item, dict) and item.get("text"))
    if not output.strip():
        raise AntigravityFailure(400, "Antigravity returned no text output.")
    return output.strip(), result


def deterministic_validation(checks: list[str], reply: str) -> dict:
    results = []
    for check in checks:
        raw = str(check).strip()
        command, separator, expected = raw.partition(":")
        command = command.strip().lower()
        expected = expected.strip()
        if not separator or command not in {"exact", "contains", "not_contains"}:
            results.append({"check": raw, "kind": "manual", "passed": None, "message": "Requires manual validation."})
            continue
        if command == "exact":
            passed = reply.strip() == expected
            description = "Reply exactly matches expected text." if passed else "Reply did not exactly match expected text."
        elif command == "contains":
            passed = expected in reply
            description = "Required text is present." if passed else "Required text is missing."
        else:
            passed = expected not in reply
            description = "Forbidden text is absent." if passed else "Forbidden text was found."
        results.append({"check": raw, "kind": command, "passed": passed, "message": description})
    failed = any(item["passed"] is False for item in results)
    all_machine_checked = bool(results) and all(item["passed"] is not None for item in results)
    result_status = "failed" if failed else "passed" if all_machine_checked else "pending_manual"
    return {"status": result_status, "checks": results, "validated_at": now()}


def retry_after_time(header: str | None, fallback_hours: int = 2) -> datetime:
    if header:
        try:
            seconds = max(0.0, min(float(header), 48 * 60 * 60))
            return datetime.now(timezone.utc) + timedelta(seconds=seconds)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(header)
                return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
            except (TypeError, ValueError, OverflowError):
                pass
    return datetime.now(timezone.utc) + timedelta(hours=fallback_hours)


def clean_overview_documents(body: dict) -> dict:
    documents = body.get("documents")
    if not isinstance(documents, dict):
        raise ValueError("Project brief, technical overview, and decisions are required.")
    cleaned = {}
    for slug in ("project_brief", "technical_overview", "decisions"):
        content = documents.get(slug)
        if not isinstance(content, str) or not content.strip() or len(content) > 20_000:
            raise ValueError(f"{PROJECT_CONTEXT_FILES[slug]} must contain text under 20,000 characters.")
        cleaned[slug] = content.strip()
    return cleaned


class Handler(BaseHTTPRequestHandler):
    server_version = "PersonalTeam/0.2"

    def log_message(self, fmt: str, *args: object) -> None:
        # Keep ordinary requests out of the console; errors are returned to the UI.
        return

    def send_json(self, status: int, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        if size > 200_000:
            raise ValueError("Request is too large")
        value = json.loads(self.rfile.read(size) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return value

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/connections":
            try:
                secrets = read_secrets()
                connections = []
                for key, name in SECRET_NAMES.items():
                    configured = bool(provider_key(key))
                    connections.append({"id": key, "name": name, "configured": configured,
                                        "source": "Saved on VM" if secrets.get(key) else "Environment" if configured else "Not connected"})
                return self.send_json(200, {"connections": connections, "encryption_available": Fernet is not None})
            except RuntimeError as exc:
                return self.send_json(503, {"error": str(exc)})
        if path == "/api/projects":
            return self.send_json(200, {"projects": read_projects()})
        github_view_match = re.fullmatch(r"/api/projects/([a-z0-9-]+)/github-files", path)
        if github_view_match:
            query = parse_qs(urlparse(self.path).query)
            try:
                if query.get("file"):
                    return self.send_json(200, github_read_text_file(github_view_match.group(1), query["file"][0]))
                return self.send_json(200, github_file_listing(github_view_match.group(1), query.get("path", [""])[0]))
            except ValueError as exc:
                return self.send_json(400, {"error": str(exc)})
        project_history_match = re.fullmatch(r"/api/projects/([a-z0-9-]+)/context-history", path)
        if project_history_match:
            return self.send_json(200, {"history": project_background_history(project_history_match.group(1))})
        if path == "/api/routing":
            return self.send_json(200, {"roles": list(WORK_ROLES), "providers": list(PROVIDERS), "configured": configured_providers(), "routing": get_routing()})
        if path == "/api/provider-quality":
            return self.send_json(200, {"quality": provider_quality()})
        if path == "/api/notifications":
            dismissed_path = DATA / "settings" / "notifications_dismissed.json"
            try:
                dismissed = set(json.loads(dismissed_path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, TypeError):
                dismissed = set()
            return self.send_json(200, {"notifications": [item for item in notifications() if item["id"] not in dismissed]})
        if path == "/api/tasks":
            with LOCK:
                return self.send_json(200, {"tasks": read_tasks()})
        if path == "/api/llm-usage":
            return self.send_json(200, {"local_model": OLLAMA_MODEL, "local_calls": local_llm_usage(), "resets": read_ai_stat_resets()})
        if path == "/api/prompts":
            with LOCK:
                return self.send_json(200, {"prompts": [
                    {"id": slug, "title": title, "content": load_prompt(slug)}
                    for slug, title in PROMPT_FILES.items()
                ]})
        if path == "/api/project-context":
            with LOCK:
                return self.send_json(200, {"documents": [
                    {"id": slug, "title": title, "content": load_project_context(slug)}
                    for slug, title in PROJECT_CONTEXT_FILES.items()
                ]})
        context_history_match = re.fullmatch(r"/api/project-context/([a-z_]+)/history", path)
        if context_history_match:
            slug = context_history_match.group(1)
            if slug not in PROJECT_CONTEXT_FILES:
                return self.send_json(404, {"error": "Project document not found."})
            return self.send_json(200, {"history": project_context_history(slug)})
        reply_match = re.fullmatch(r"/api/tasks/(T-\d{4,})/replies", path)
        if reply_match:
            return self.task_replies(reply_match.group(1))
        if path == "/api/status":
            with LOCK:
                active = ACTIVE_CHAT > 0
                gemini_active = ACTIVE_GEMINI > 0
                openrouter_active = ACTIVE_OPENROUTER > 0
                watchdog_status = {"last_run": WATCHDOG_LAST_RUN, "changed": WATCHDOG_LAST_CHANGED, "error": WATCHDOG_ERROR}
            return self.send_json(200, {"cpu_percent": cpu_percent(), "llm_working": active, "gemini_working": gemini_active, "gemini_configured": bool(provider_key("gemini")), "gemini_model": GEMINI_MODEL, "openrouter_working": openrouter_active, "openrouter_configured": bool(provider_key("openrouter")), "openrouter_model": OPENROUTER_MODEL, "groq_configured": bool(provider_key("groq")), "antigravity_configured": bool(provider_key("gemini")), "github_configured": bool(provider_key("github")), "watchdog": watchdog_status})
        if path == "/api/chat":
            ensure_data()
            chat_path = DATA / "chat" / "orchestrator.json"
            with LOCK:
                try:
                    messages = json.loads(chat_path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    messages = []
            return self.send_json(200, {"messages": messages})
        if path == "/api/config":
            try:
                request = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
                with urllib.request.urlopen(request, timeout=3) as response:
                    available_models = json.loads(response.read().decode("utf-8")).get("models", [])
                names = [item.get("name", "") for item in available_models]
                found = any(name == OLLAMA_MODEL or name.startswith(OLLAMA_MODEL + ":") for name in names)
                return self.send_json(200, {"model": OLLAMA_MODEL, "available": True, "model_found": found, "gemini_model": GEMINI_MODEL, "gemini_configured": bool(provider_key("gemini")), "openrouter_model": OPENROUTER_MODEL, "openrouter_configured": bool(provider_key("openrouter")), "groq_model": GROQ_MODEL})
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
                return self.send_json(200, {"model": OLLAMA_MODEL, "available": False, "model_found": False, "gemini_model": GEMINI_MODEL, "gemini_configured": bool(provider_key("gemini")), "openrouter_model": OPENROUTER_MODEL, "openrouter_configured": bool(provider_key("openrouter")), "groq_model": GROQ_MODEL})
        if path == "/" or path == "/index.html":
            return self.serve_file(STATIC / "index.html", "text/html; charset=utf-8")
        if path in ("/app.js", "/style.css"):
            mime = "text/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8"
            return self.serve_file(STATIC / path.lstrip("/"), mime)
        self.send_json(404, {"error": "Not found"})

    def task_replies(self, task_id: str) -> None:
        with LOCK:
            try:
                task = json.loads((DATA / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))
            except FileNotFoundError:
                return self.send_json(404, {"error": "Task not found."})
            replies = []
            for attempt in task.get("attempts", []):
                provider = attempt.get("provider")
                folder = {"Gemini": "gemini", "Antigravity": "antigravity", "GroqCloud": "groqcloud", "OpenRouter": "openrouter"}.get(provider)
                if folder is None:
                    continue
                number = attempt.get("number")
                if not isinstance(number, int) or number < 1:
                    continue
                reply_path = DATA / "replies" / task_id / folder / f"attempt-{number:03d}.json"
                try:
                    replies.append(json.loads(reply_path.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
        self.send_json(200, {"replies": replies})

    def serve_file(self, path: Path, content_type: str) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            return self.send_json(404, {"error": "Page file not found"})
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self.read_json()
            if path == "/api/tasks":
                return self.create_task(body)
            if path == "/api/pm-requests":
                return self.create_pm_request(body)
            if path == "/api/llm-usage/reset":
                return self.reset_ai_statistic(body)
            if path == "/api/connections/github/test":
                return self.test_github_connection()
            if path == "/api/connections/github/repositories":
                return self.github_repositories()
            if path == "/api/routing":
                with LOCK:
                    routing = save_routing(body.get("routing"))
                    append_event({"at": now(), "type": "routing_updated"})
                return self.send_json(200, {"routing": routing})
            project_restore_match = re.fullmatch(r"/api/projects/([a-z0-9-]+)/restore-context", path)
            if project_restore_match:
                project_id = project_restore_match.group(1)
                version_id = str(body.get("version_id", ""))
                if not re.fullmatch(r"\d{8}T\d{12}Z", version_id):
                    return self.send_json(400, {"error": "Choose a valid saved project version."})
                snapshot = DATA / "projects" / project_id / "history" / f"{version_id}.md"
                try:
                    restored_content = snapshot.read_text(encoding="utf-8")
                except OSError:
                    return self.send_json(404, {"error": "That project version was not found."})
                projects = read_projects()
                project = next((item for item in projects if item.get("id") == project_id), None)
                if not project:
                    return self.send_json(404, {"error": "Project not found."})
                project["context"] = restored_content
                with LOCK:
                    save_projects(projects)
                    append_event({"at": now(), "type": "project_background_restored", "project_id": project_id, "version_id": version_id})
                return self.send_json(200, {"restored": True, "context": restored_content})
            if path.startswith("/api/tasks/") and path.endswith("/status"):
                task_id = path[len("/api/tasks/"):-len("/status")].strip("/")
                return self.change_task_status(task_id, body)
            if path.startswith("/api/tasks/") and path.endswith("/validation"):
                task_id = path[len("/api/tasks/"):-len("/validation")].strip("/")
                return self.record_manual_validation(task_id, body)
            if path.startswith("/api/tasks/") and path.endswith("/answer"):
                task_id = path[len("/api/tasks/"):-len("/answer")].strip("/")
                return self.answer_clarification(task_id, body)
            overview_match = re.fullmatch(r"/api/tasks/(T-\d{4,})/approve-overview", path)
            if overview_match:
                return self.approve_project_overview(overview_match.group(1), body)
            context_restore_match = re.fullmatch(r"/api/project-context/([a-z_]+)/restore", path)
            if context_restore_match:
                return self.restore_project_context(context_restore_match.group(1), body)
            if path == "/api/chat":
                return self.chat(body)
            self.send_json(404, {"error": "Not found"})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})

    def do_DELETE(self) -> None:  # noqa: N802
        global ACTIVE_CHAT
        path = urlparse(self.path).path
        task_match = re.fullmatch(r"/api/tasks/(T-\d{4,})", path)
        if task_match:
            return self.delete_task(task_match.group(1))
        context_version_match = re.fullmatch(r"/api/project-context/([a-z_]+)/history/(\d{8}T\d{12}Z)", path)
        if context_version_match:
            return self.delete_project_context_version(context_version_match.group(1), context_version_match.group(2))
        project_version_match = re.fullmatch(r"/api/projects/([a-z0-9-]+)/context-history/(\d{8}T\d{12}Z)", path)
        if project_version_match:
            project_id, version_id = project_version_match.groups()
            snapshot = DATA / "projects" / project_id / "history" / f"{version_id}.md"
            if not snapshot.is_file():
                return self.send_json(404, {"error": "That project version was not found."})
            snapshot.unlink()
            append_event({"at": now(), "type": "project_background_version_deleted", "project_id": project_id, "version_id": version_id})
            return self.send_json(200, {"deleted": True})
        notification_match = re.fullmatch(r"/api/notifications/(.+)", path)
        if notification_match:
            notification_id = notification_match.group(1)
            path = DATA / "settings" / "notifications_dismissed.json"
            try:
                dismissed = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                dismissed = []
            if notification_id not in dismissed:
                dismissed.append(notification_id)
            atomic_json(path, dismissed)
            return self.send_json(200, {"dismissed": True})
        credential_match = re.fullmatch(r"/api/connections/(gemini|openrouter|groq|github)", path)
        if credential_match:
            with LOCK:
                save_secret(credential_match.group(1), None)
                append_event({"at": now(), "type": "credential_removed", "connection": credential_match.group(1)})
            return self.send_json(200, {"deleted": True})
        if path != "/api/chat":
            return self.send_json(404, {"error": "Not found"})
        with LOCK:
            if ACTIVE_CHAT:
                return self.send_json(409, {"error": "Wait for the current Orchestrator reply to finish, then clear the conversation."})
            chat_path = DATA / "chat" / "orchestrator.json"
            for path in (chat_path, chat_path.with_suffix(".json.bak"), chat_path.with_suffix(".json.tmp")):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            append_event({"at": now(), "type": "chat_cleared"})
        self.send_json(200, {"cleared": True})

    def delete_task(self, task_id: str) -> None:
        with LOCK:
            task_path = DATA / "tasks" / f"{task_id}.json"
            reply_dir = DATA / "replies" / task_id
            log_path = DATA / "logs" / "activity.jsonl"
            task_artifacts = (task_path, task_path.with_suffix(".json.bak"), task_path.with_suffix(".json.tmp"))
            existed = any(path.exists() for path in task_artifacts) or reply_dir.exists()
            if not existed and log_path.exists():
                for line in log_path.read_text(encoding="utf-8").splitlines():
                    try:
                        if json.loads(line).get("task_id") == task_id:
                            existed = True
                            break
                    except (json.JSONDecodeError, AttributeError):
                        continue
            if not existed:
                return self.send_json(404, {"error": "Task not found."})
            try:
                if log_path.exists():
                    kept_lines = []
                    for line in log_path.read_text(encoding="utf-8").splitlines():
                        try:
                            event = json.loads(line)
                        except (json.JSONDecodeError, AttributeError):
                            kept_lines.append(line)
                            continue
                        if event.get("task_id") != task_id:
                            kept_lines.append(line)
                    temp_log = log_path.with_suffix(".jsonl.tmp")
                    temp_log.write_text("".join(line + "\n" for line in kept_lines), encoding="utf-8")
                else:
                    temp_log = None
                for path in task_artifacts:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                if reply_dir.exists():
                    shutil.rmtree(reply_dir)
                if temp_log is not None:
                    os.replace(temp_log, log_path)
            except OSError as exc:
                return self.send_json(500, {"error": f"Task cleanup was incomplete: {exc}"})
        self.send_json(200, {"deleted": True, "task_id": task_id})

    def test_github_connection(self) -> None:
        try:
            user = github_api("/user")
            if not isinstance(user, dict):
                raise ValueError("GitHub returned an unexpected account response.")
            with LOCK:
                append_event({"at": now(), "type": "github_connection_test", "success": True, "login": user.get("login", "")})
            self.send_json(200, {"connected": True, "login": user.get("login", "")})
        except (ValueError, urllib.error.URLError, TimeoutError) as exc:
            with LOCK:
                append_event({"at": now(), "type": "github_connection_test", "success": False, "error": str(exc)[:300]})
            self.send_json(400, {"connected": False, "error": str(exc)})

    def github_repositories(self) -> None:
        try:
            result = github_api("/user/repos?per_page=100&sort=updated&affiliation=owner,collaborator")
            repos = [{"full_name": item.get("full_name", ""), "clone_url": item.get("clone_url", ""),
                      "private": bool(item.get("private")), "default_branch": item.get("default_branch", "main")}
                     for item in result if isinstance(item, dict)] if isinstance(result, list) else []
            self.send_json(200, {"repositories": repos})
        except (ValueError, urllib.error.URLError, TimeoutError) as exc:
            self.send_json(400, {"error": str(exc)})

    def create_pm_request(self, body: dict) -> None:
        request_text = str(body.get("request", "")).strip()
        if not request_text:
            return self.send_json(400, {"error": "Describe the project request first."})
        if len(request_text) > 12_000:
            return self.send_json(400, {"error": "Project request is too long."})
        project_id = str(body.get("project_id", "default"))
        if not any(item.get("id") == project_id for item in read_projects()):
            return self.send_json(400, {"error": "Choose a valid project."})
        with LOCK:
            task_id = next_task_id()
            timestamp = now()
            title = "PM/PO planning: " + " ".join(request_text.split())[:130]
            task = {
                "id": task_id, "task_type": "pm_planning", "title": title,
                "request": request_text, "original_request": request_text,
                "project_id": project_id, "module_id": "",
                "planning_root_id": task_id, "planning_round": 1,
                "assigned_role": "PM/PO", "provider": "Automatic", "status": "Ready",
                "produces_deliverable": False, "acceptance_checks": [],
                "attempts": [], "validation": None,
                "created_at": timestamp, "updated_at": timestamp,
                "dispatch_note": "Planning request queued for the PM/PO agent.",
                "activity": [{"at": timestamp, "type": "created", "message": "Project request sent to PM/PO from Orchestrator chat."}],
            }
            atomic_json(DATA / "tasks" / f"{task_id}.json", task)
            append_event({"at": timestamp, "task_id": task_id, "type": "pm_request_created", "status": "Ready"})
            chat_path = DATA / "chat" / "orchestrator.json"
            try:
                history = json.loads(chat_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                history = []
            history.extend([
                {"role": "user", "content": request_text, "at": timestamp},
                {"role": "assistant", "content": f"I sent this request to PM/PO as {task_id}. It will return clarification questions or a proposed backlog on the task board.", "at": timestamp, "task_id": task_id},
            ])
            atomic_json(chat_path, history)
        self.send_json(201, {"task": task})

    def answer_clarification(self, task_id: str, body: dict) -> None:
        if not re.fullmatch(r"T-\d{4,}", task_id):
            return self.send_json(400, {"error": "Invalid task ID."})
        selected = str(body.get("selected_option", "")).strip()
        free_text = str(body.get("free_text", "")).strip()[:5000]
        with LOCK:
            path = DATA / "tasks" / f"{task_id}.json"
            try:
                question = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return self.send_json(404, {"error": "Question not found."})
            if question.get("task_type") != "clarification_question" or question.get("status") != "Needs answer":
                return self.send_json(409, {"error": "This question is not waiting for an answer."})
            question_data = question.get("question_data", {})
            options = [item.get("label", "") for item in question_data.get("options", []) if isinstance(item, dict)]
            if selected and selected not in options:
                return self.send_json(400, {"error": "Choose one of the listed options."})
            if not selected and not free_text:
                return self.send_json(400, {"error": "Choose an option or enter a response."})
            if not question_data.get("allow_free_text", True) and not selected:
                return self.send_json(400, {"error": "Choose one of the listed options."})
            answer = (f"Selected option: {selected}\n" if selected else "") + (f"Additional response: {free_text}" if free_text else "")
            timestamp = now()
            question.update({"status": "Done", "selected_option": selected or None, "free_text": free_text, "answer": answer, "updated_at": timestamp})
            question.setdefault("activity", []).append({"at": timestamp, "type": "question_answered", "message": "Clarification answered by the user."})
            atomic_json(path, question)
            append_event({"at": timestamp, "task_id": task_id, "type": "question_answered", "status": "Done"})

            siblings = [task for task in read_tasks() if task.get("task_type") == "clarification_question" and task.get("planning_task_id") == question.get("planning_task_id")]
            followup = None
            if siblings and all(item.get("status") == "Done" for item in siblings):
                parent_path = DATA / "tasks" / f"{question['planning_task_id']}.json"
                try:
                    parent = json.loads(parent_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    parent = None
                if parent and not parent.get("followup_created"):
                    parent["followup_created"] = True
                    parent.setdefault("activity", []).append({"at": timestamp, "type": "clarifications_complete", "message": "All questions answered; sending the answers back to PM/PO."})
                    atomic_json(parent_path, parent)
                    root_id = question.get("planning_root_id")
                    all_questions = sorted(
                        [item for item in read_tasks() if item.get("task_type") == "clarification_question" and item.get("planning_root_id") == root_id and item.get("status") == "Done"],
                        key=lambda item: (item.get("planning_round", 1), item.get("created_at", "")),
                    )
                    qa_text = "\n".join(f"Q: {item.get('request')}\nA: {item.get('answer')}" for item in all_questions)
                    followup_id = next_task_id()
                    followup_request = (
                        f"Original project request:\n{parent.get('original_request', '')}\n\n"
                        f"Clarifications answered so far:\n{qa_text}\n\n"
                        "Continue planning. Ask another round of focused questions only if important information is still missing. "
                        "Otherwise return the proposed task backlog. Do not repeat questions already answered."
                    )
                    followup = {
                        "id": followup_id, "task_type": "pm_planning",
                        "title": f"PM/PO planning follow-up, round {int(parent.get('planning_round', 1)) + 1}",
                        "request": followup_request, "original_request": parent.get("original_request", ""),
                        "planning_root_id": root_id, "planning_round": int(parent.get("planning_round", 1)) + 1,
                        "assigned_role": "PM/PO", "provider": "Automatic", "status": "Ready",
                "project_id": parent.get("project_id", "default"), "module_id": item.get("module_id", ""),
                        "produces_deliverable": False, "acceptance_checks": [],
                        "attempts": [], "validation": None, "created_at": timestamp, "updated_at": timestamp,
                        "dispatch_note": "Answered clarifications queued for PM/PO.",
                        "activity": [{"at": timestamp, "type": "created", "message": f"Follow-up planning request based on answers to {len(all_questions)} clarification question(s)."}],
                    }
                    atomic_json(DATA / "tasks" / f"{followup_id}.json", followup)
                    append_event({"at": timestamp, "task_id": followup_id, "type": "pm_followup_created", "status": "Ready", "parent_task_id": parent["id"]})
                    self.append_chat_notice(f"All clarification answers are in. I sent them back to PM/PO as {followup_id}.", timestamp, followup_id)
        self.send_json(200, {"task": question, "followup_task": followup})

    def append_chat_notice(self, content: str, timestamp: str, task_id: str) -> None:
        chat_path = DATA / "chat" / "orchestrator.json"
        try:
            history = json.loads(chat_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            history = []
        history.append({"role": "assistant", "content": content, "at": timestamp, "task_id": task_id})
        atomic_json(chat_path, history)

    def create_task(self, body: dict) -> None:
        title = str(body.get("title", "")).strip()
        request_text = str(body.get("request", "")).strip()
        role = str(body.get("role", "PM/PO")).strip()
        provider = str(body.get("provider", "Automatic")).strip()
        checks = body.get("acceptance_checks", [])
        produces_deliverable = bool(body.get("produces_deliverable", False))
        if not title or not request_text:
            return self.send_json(400, {"error": "Title and request are required."})
        if len(title) > 160 or len(request_text) > 12_000:
            return self.send_json(400, {"error": "Title or request is too long."})
        if role not in WORK_ROLES:
            return self.send_json(400, {"error": "Choose a role from the list."})
        if provider not in TASK_PROVIDERS:
            return self.send_json(400, {"error": "Choose Automatic or a configured provider."})
        project_id = str(body.get("project_id", "default"))
        module_id = str(body.get("module_id", ""))
        project = next((item for item in read_projects() if item.get("id") == project_id), None)
        if not project or (module_id and not any(item.get("id") == module_id for item in project.get("modules", []))):
            return self.send_json(400, {"error": "Choose a valid project and sub-project."})
        if not isinstance(checks, list):
            return self.send_json(400, {"error": "Acceptance checks must be a list."})
        checks = [str(item).strip()[:500] for item in checks if str(item).strip()][:20]
        if project.get("repository") and role in ("Frontend developer", "Backend developer", "Documentation") and (not produces_deliverable or not checks):
            return self.send_json(400, {"error": "Repository work must be marked as a deliverable and include at least one acceptance check."})
        timestamp = now()
        with LOCK:
            existing_ids = [int(match.group(1)) for task in read_tasks()
                            if (match := re.fullmatch(r"T-(\d{4,})", task.get("id", "")))]
            task_id = f"T-{max(existing_ids, default=0) + 1:04d}"
            task = {
                "id": task_id,
                "title": title,
                "request": request_text,
                "assigned_role": role,
                "provider": provider,
                "project_id": project_id,
                "module_id": module_id,
                "acceptance_checks": checks,
                "produces_deliverable": produces_deliverable,
                "status": "Draft",
                "created_at": timestamp,
                "updated_at": timestamp,
                "attempts": [],
                "validation": None,
                "activity": [{"at": timestamp, "type": "created", "message": "Task created as Draft."}],
            }
            atomic_json(DATA / "tasks" / f"{task_id}.json", task)
            append_event({"at": timestamp, "task_id": task_id, "type": "created", "status": "Draft"})
        self.send_json(201, {"task": task})

    def do_PUT(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self.read_json()
            prompt_match = re.fullmatch(r"/api/prompts/([a-z_]+)", path)
            if prompt_match:
                return self.save_prompt(prompt_match.group(1), body)
            context_match = re.fullmatch(r"/api/project-context/([a-z_]+)", path)
            if context_match:
                return self.save_project_context(context_match.group(1), body)
            overview_match = re.fullmatch(r"/api/tasks/(T-\d{4,})/overview", path)
            if overview_match:
                return self.save_overview_edits(overview_match.group(1), body)
            match = re.fullmatch(r"/api/tasks/(T-\d{4,})", path)
            if match:
                return self.edit_task(match.group(1), body)
            connection_match = re.fullmatch(r"/api/connections/(gemini|openrouter|groq|github)", path)
            if connection_match:
                secret = str(body.get("secret", "")).strip()
                if not secret or len(secret) > 4096:
                    return self.send_json(400, {"error": "Paste a credential (up to 4,096 characters)."})
                with LOCK:
                    save_secret(connection_match.group(1), secret)
                    append_event({"at": now(), "type": "credential_saved", "connection": connection_match.group(1)})
                return self.send_json(200, {"saved": True})
            if path == "/api/projects":
                with LOCK:
                    projects = save_projects(body.get("projects"))
                    append_event({"at": now(), "type": "projects_updated", "count": len(projects)})
                return self.send_json(200, {"projects": projects})
            self.send_json(404, {"error": "Not found"})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})

    def save_prompt(self, slug: str, body: dict) -> None:
        if slug not in PROMPT_FILES:
            return self.send_json(404, {"error": "Prompt not found."})
        content = body.get("content")
        if not isinstance(content, str) or not content.strip():
            return self.send_json(400, {"error": "Prompt text cannot be empty."})
        if len(content) > 20_000:
            return self.send_json(400, {"error": "Prompt text must be 20,000 characters or fewer."})
        with LOCK:
            path = prompt_path(slug)
            temp = path.with_suffix(".md.tmp")
            temp.write_text(content.strip() + "\n", encoding="utf-8")
            os.replace(temp, path)
            append_event({"at": now(), "type": "prompt_saved", "prompt": slug})
        self.send_json(200, {"saved": True, "id": slug, "title": PROMPT_FILES[slug], "content": content.strip()})

    def save_project_context(self, slug: str, body: dict) -> None:
        if slug not in PROJECT_CONTEXT_FILES:
            return self.send_json(404, {"error": "Project document not found."})
        content = body.get("content")
        if not isinstance(content, str) or not content.strip():
            return self.send_json(400, {"error": "Project document cannot be empty."})
        if len(content) > 20_000:
            return self.send_json(400, {"error": "Project document must be 20,000 characters or fewer."})
        with LOCK:
            version_id = save_project_context_document(slug, content)
            append_event({"at": now(), "type": "project_context_saved", "document": slug, "snapshot": version_id})
        self.send_json(200, {"saved": True, "id": slug, "title": PROJECT_CONTEXT_FILES[slug], "content": content.strip(), "snapshot": version_id})

    def restore_project_context(self, slug: str, body: dict) -> None:
        if slug not in PROJECT_CONTEXT_FILES:
            return self.send_json(404, {"error": "Project document not found."})
        version_id = str(body.get("snapshot_id", ""))
        if not re.fullmatch(r"\d{8}T\d{12}Z", version_id):
            return self.send_json(400, {"error": "Choose a valid saved version."})
        snapshot = project_context_history_path(slug, version_id)
        with LOCK:
            try:
                content = snapshot.read_text(encoding="utf-8")
            except OSError:
                return self.send_json(404, {"error": "That saved version was not found."})
            new_snapshot = save_project_context_document(slug, content)
            append_event({"at": now(), "type": "project_context_restored", "document": slug, "restored_snapshot": version_id, "snapshot": new_snapshot})
        self.send_json(200, {"saved": True, "id": slug, "title": PROJECT_CONTEXT_FILES[slug], "content": content.strip(), "restored_snapshot": version_id})

    def reset_ai_statistic(self, body: dict) -> None:
        provider = str(body.get("provider", "")).strip()
        model = str(body.get("model", "")).strip()
        period = str(body.get("period", "")).strip()
        bucket = str(body.get("bucket", "")).strip()
        if provider not in {"Ollama", *PROVIDERS} or not model or len(model) > 160:
            return self.send_json(400, {"error": "Choose a valid provider and model."})
        bucket_patterns = {"today": r"\d{4}-\d{2}-\d{2}", "month": r"\d{4}-\d{2}", "year": r"\d{4}", "all": r"all"}
        if period not in bucket_patterns or not re.fullmatch(bucket_patterns[period], bucket):
            return self.send_json(400, {"error": "Choose a valid statistic period."})
        reset = {"provider": provider, "model": model, "period": period, "bucket": bucket, "reset_at": now()}
        with LOCK:
            resets = read_ai_stat_resets()
            resets.append(reset)
            atomic_json(DATA / "logs" / "ai_stats_resets.json", resets)
            append_event({"at": reset["reset_at"], "type": "ai_statistic_reset", **reset})
        self.send_json(200, {"reset": reset})

    def delete_project_context_version(self, slug: str, version_id: str) -> None:
        if slug not in PROJECT_CONTEXT_FILES:
            return self.send_json(404, {"error": "Project document not found."})
        snapshot = project_context_history_path(slug, version_id)
        with LOCK:
            if not snapshot.is_file():
                return self.send_json(404, {"error": "That saved version was not found."})
            snapshot.unlink()
            append_event({"at": now(), "type": "project_context_version_deleted", "document": slug, "deleted_version": version_id})
        self.send_json(200, {"deleted": True, "id": slug, "deleted_version": version_id})

    def save_overview_edits(self, task_id: str, body: dict) -> None:
        try:
            documents = clean_overview_documents(body)
        except ValueError as exc:
            return self.send_json(400, {"error": str(exc)})
        with LOCK:
            path = DATA / "tasks" / f"{task_id}.json"
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return self.send_json(404, {"error": "Project overview task not found."})
            if task.get("task_type") != "project_overview" or task.get("status") != "Needs approval":
                return self.send_json(409, {"error": "This project overview is no longer awaiting approval."})
            timestamp = now()
            task["overview_documents"] = documents
            task["updated_at"] = timestamp
            task.setdefault("activity", []).append({"at": timestamp, "type": "overview_edited", "message": "User edited the proposed project overview; it has not been applied yet."})
            atomic_json(path, task)
            append_event({"at": timestamp, "task_id": task_id, "type": "overview_edited"})
        self.send_json(200, {"task": task})

    def approve_project_overview(self, task_id: str, body: dict) -> None:
        try:
            documents = clean_overview_documents(body)
        except ValueError as exc:
            return self.send_json(400, {"error": str(exc)})
        with LOCK:
            path = DATA / "tasks" / f"{task_id}.json"
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return self.send_json(404, {"error": "Project overview task not found."})
            if task.get("task_type") != "project_overview" or task.get("status") != "Needs approval":
                return self.send_json(409, {"error": "This project overview is no longer awaiting approval."})
            timestamp = now()
            snapshots = {}
            project_id = task.get("project_id", "default")
            if project_id == "default":
                for slug, content in documents.items():
                    snapshots[slug] = save_project_context_document(slug, content)
                    append_event({"at": timestamp, "type": "project_context_replaced_by_approval", "document": slug, "task_id": task_id, "snapshot": snapshots[slug]})
            else:
                projects = read_projects()
                project = next((item for item in projects if item.get("id") == project_id), None)
                if not project:
                    return self.send_json(409, {"error": "The project attached to this overview no longer exists."})
                project["context"] = "\n\n".join(f"# {PROJECT_CONTEXT_FILES[slug]}\n\n{content}" for slug, content in documents.items())
                save_projects(projects)
                history = project_background_history(project_id)
                snapshots["project_background"] = history[0]["id"] if history else None
                append_event({"at": timestamp, "type": "project_background_replaced_by_approval", "project_id": project_id, "task_id": task_id, "snapshot": snapshots["project_background"]})
            task["overview_documents"] = documents
            task["status"] = "Done"
            task["updated_at"] = timestamp
            task["approved_at"] = timestamp
            task["validation"] = {"status": "passed", "method": "user_approval", "reviewed_by": "User", "notes": "Approved and applied to Project context.", "snapshots": snapshots, "validated_at": timestamp}
            task.setdefault("activity", []).append({"at": timestamp, "type": "overview_approved", "from": "Needs approval", "to": "Done", "message": f"User approved the project overview; {('the project-specific background notes' if project_id != 'default' else 'Project brief, Technical overview, and Decision log')} were updated. Previous versions are available in history."})
            atomic_json(path, task)
            append_event({"at": timestamp, "task_id": task_id, "type": "overview_approved", "status": "Done", "snapshots": snapshots})
        self.send_json(200, {"task": task, "snapshots": snapshots})

    def edit_task(self, task_id: str, body: dict) -> None:
        title = str(body.get("title", "")).strip()
        request_text = str(body.get("request", "")).strip()
        role = str(body.get("role", "")).strip()
        provider = str(body.get("provider", "Automatic")).strip()
        checks = body.get("acceptance_checks", [])
        if not title or not request_text or len(title) > 160 or len(request_text) > 12_000:
            return self.send_json(400, {"error": "Enter a title (up to 160 characters) and request (up to 12,000 characters)."})
        if role not in WORK_ROLES:
            return self.send_json(400, {"error": "Choose a role from the list."})
        if provider not in TASK_PROVIDERS:
            return self.send_json(400, {"error": "Choose Automatic or a configured provider."})
        project_id = str(body.get("project_id", "default"))
        module_id = str(body.get("module_id", ""))
        project = next((item for item in read_projects() if item.get("id") == project_id), None)
        if not project or (module_id and not any(item.get("id") == module_id for item in project.get("modules", []))):
            return self.send_json(400, {"error": "Choose a valid project and sub-project."})
        if not isinstance(checks, list):
            return self.send_json(400, {"error": "Acceptance checks must be a list."})
        checks = [str(item).strip()[:500] for item in checks if str(item).strip()][:20]
        if project.get("repository") and role in ("Frontend developer", "Backend developer") and (not bool(body.get("produces_deliverable", False)) or not checks):
            return self.send_json(400, {"error": "Code tasks linked to GitHub must be marked as deliverables and include at least one acceptance check."})
        with LOCK:
            path = DATA / "tasks" / f"{task_id}.json"
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return self.send_json(404, {"error": "Task not found."})
            if task.get("status") not in ("Draft", "Blocked", "Rework needed"):
                return self.send_json(409, {"error": "Only Draft, Blocked, or Rework needed tasks can be edited."})
            timestamp = now()
            task.update({
                "title": title, "request": request_text, "assigned_role": role, "provider": provider,
                "project_id": project_id, "module_id": module_id,
                "acceptance_checks": checks,
                "produces_deliverable": bool(body.get("produces_deliverable", False)),
                "updated_at": timestamp,
            })
            task.setdefault("activity", []).append({"at": timestamp, "type": "edited", "message": "Task details updated."})
            atomic_json(path, task)
            append_event({"at": timestamp, "task_id": task_id, "type": "edited"})
        self.send_json(200, {"task": task})

    def change_task_status(self, task_id: str, body: dict) -> None:
        if not re.fullmatch(r"T-\d{4,}", task_id):
            return self.send_json(400, {"error": "Invalid task ID."})
        target = str(body.get("status", ""))
        note = str(body.get("note", "")).strip()[:500]
        if target not in {"Ready", "Blocked", "Canceled", "Draft"}:
            return self.send_json(400, {"error": "That status is set by the agent or validation workflow, not manually."})
        with LOCK:
            task_path = DATA / "tasks" / f"{task_id}.json"
            try:
                task = json.loads(task_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return self.send_json(404, {"error": "Task not found."})
            current = task.get("status")
            if target not in TRANSITIONS.get(current, set()):
                return self.send_json(409, {"error": f"Cannot move a {current} task to {target}."})
            if target not in USER_TRANSITIONS.get(current, set()):
                return self.send_json(409, {"error": f"Cannot move a {current} task to {target}."})
            if target == "Ready" and task.get("produces_deliverable") and not task.get("acceptance_checks"):
                return self.send_json(400, {"error": "Add at least one acceptance check before marking a deliverable task Ready."})
            timestamp = now()
            if target == "Ready":
                if current == "Rework needed":
                    message = "Task returned to the agent queue with its failed validation results included."
                    selected = task_provider(task)
                    configured = selected in configured_providers()
                    agent = selected or None
                    task["dispatch_note"] = f"Queued for {agent} rework." if configured and agent else f"{agent} API key is not configured." if agent else "Waiting for an agent connector."
                else:
                    message = "Task is clear and queued for automatic dispatch."
                    selected = task_provider(task)
                    task["dispatch_note"] = f"Waiting for {selected} to respond." if selected else "No provider in this task's route is connected. Add a credential or select a configured provider in Connections & routing."
            elif target == "Blocked":
                message = note or "Task blocked by the user."
                task["block_reason"] = message
            elif target == "Canceled":
                message = note or "Task canceled by the user."
            else:
                message = note or "Task returned to Draft for clarification."
                task.pop("block_reason", None)
            task["status"] = target
            task["updated_at"] = timestamp
            task.setdefault("activity", []).append({
                "at": timestamp, "type": "status_changed", "from": current,
                "to": target, "message": message,
            })
            atomic_json(task_path, task)
            append_event({"at": timestamp, "task_id": task_id, "type": "status_changed", "from": current, "to": target, "message": message})
        self.send_json(200, {"task": task})

    def record_manual_validation(self, task_id: str, body: dict) -> None:
        if not re.fullmatch(r"T-\d{4,}", task_id):
            return self.send_json(400, {"error": "Invalid task ID."})
        decision = str(body.get("decision", ""))
        notes = str(body.get("notes", "")).strip()[:2000]
        if decision not in ("pass", "rework"):
            return self.send_json(400, {"error": "Choose pass or rework."})
        if decision == "rework" and not notes:
            return self.send_json(400, {"error": "Add a short note describing what needs to change."})
        with LOCK:
            task_path = DATA / "tasks" / f"{task_id}.json"
            try:
                task = json.loads(task_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return self.send_json(404, {"error": "Task not found."})
            if task.get("status") != "Needs validation" or not task.get("produces_deliverable"):
                return self.send_json(409, {"error": "Only deliverables in Needs validation can be reviewed here."})
            timestamp = now()
            passed = decision == "pass"
            task["validation"] = {
                "status": "passed" if passed else "failed",
                "method": "manual",
                "reviewed_by": "User",
                "notes": notes or "Approved after manual review.",
                "checks": [{
                    "check": "Manual review",
                    "kind": "manual",
                    "passed": passed,
                    "message": notes or "Approved after manual review.",
                }],
                "validated_at": timestamp,
            }
            old_status = task["status"]
            task["status"] = "Done" if passed else "Rework needed"
            for attempt in reversed(task.get("attempts", [])):
                if isinstance(attempt, dict) and attempt.get("state") == "succeeded":
                    attempt["quality_outcome"] = "passed" if passed else "failed"
                    break
            task["updated_at"] = timestamp
            if not passed:
                task["manual_rework_notes"] = notes
            message = "User approved the deliverable after manual review." if passed else f"User requested rework: {notes}"
            task.setdefault("activity", []).append({"at": timestamp, "type": "manual_validation", "from": old_status, "to": task["status"], "message": message})
            atomic_json(task_path, task)
            if task.get("pull_request"):
                threading.Thread(target=github_postprocess_task, args=(task_id,), daemon=True).start()
            append_event({"at": timestamp, "task_id": task_id, "type": "manual_validation", "decision": decision, "to": task["status"]})
        self.send_json(200, {"task": task})

    def chat(self, body: dict) -> None:
        global ACTIVE_CHAT
        text = str(body.get("message", "")).strip()
        if not text:
            return self.send_json(400, {"error": "Write a message first."})
        if len(text) > 12_000:
            return self.send_json(400, {"error": "Message is too long."})
        with LOCK:
            if ACTIVE_CHAT:
                return self.send_json(409, {"error": "Another Orchestrator reply is still in progress."})
            ACTIVE_CHAT += 1
        try:
            ensure_data()
            chat_path = DATA / "chat" / "orchestrator.json"
            with LOCK:
                try:
                    history = json.loads(chat_path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    history = []
                history.append({"role": "user", "content": text, "at": now()})
                atomic_json(chat_path, history)
            # Keep the full local history; bound context sent to Ollama.
            context = history[-16:]
            board = read_tasks()
            board_text = "\n".join(
                f"- {task['id']} | {task['status']} | {task['assigned_role']} | {task['title']}"
                for task in board[:50]
            ) or "(No tasks have been created.)"
            payload = {
                "model": OLLAMA_MODEL,
                "think": False,
                "stream": False,
                "messages": [{"role": "system", "content": "\n\n".join(item for item in (
                    load_prompt("shared_rules"), load_prompt("orchestrator"),
                    project_context_for_role("Orchestrator"),
                    "Current task board (read-only context):\n" + board_text,
                ) if item)}]
                + [{"role": m["role"], "content": m["content"]} for m in context],
                "options": {"temperature": 0.2, "num_predict": 512},
            }
            llm_started = time.monotonic()
            append_event({"at": now(), "type": "local_llm_request_started", "provider": "Ollama", "model": OLLAMA_MODEL})
            try:
                result = ollama_request(payload)
                reply = result.get("message", {}).get("content", "").strip()
                if not reply:
                    raise RuntimeError("Ollama returned an empty reply.")
            except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, OSError) as exc:
                duration = round(time.monotonic() - llm_started, 3)
                append_event({"at": now(), "type": "local_llm_error", "provider": "Ollama", "model": OLLAMA_MODEL, "duration_seconds": duration})
                detail = getattr(exc, "reason", str(exc))
                error_message = f"I couldn’t reach Ollama: {detail}"
                history.append({"role": "assistant", "content": error_message, "at": now(), "error": True})
                with LOCK:
                    atomic_json(chat_path, history)
                return self.send_json(502, {"error": error_message})
            duration = round(time.monotonic() - llm_started, 3)
            history.append({"role": "assistant", "content": reply, "at": now(), "provider": "Ollama", "duration_seconds": duration})
            with LOCK:
                atomic_json(chat_path, history)
                append_event({"at": now(), "type": "local_llm_reply", "provider": "Ollama", "model": OLLAMA_MODEL, "duration_seconds": duration})
            self.send_json(200, {"reply": history[-1], "messages": history})
        finally:
            with LOCK:
                ACTIVE_CHAT -= 1


def claim_gemini_task() -> dict | None:
    if not provider_key("gemini"):
        return None
    with LOCK:
        # read_tasks sorts newest first; choose the oldest eligible task first.
        for summary in reversed(read_tasks()):
            if summary.get("status") != "Ready" or task_provider(summary) != "Gemini":
                continue
            task_id = summary.get("id", "")
            if not re.fullmatch(r"T-\d{4,}", task_id):
                continue
            task_path = DATA / "tasks" / f"{task_id}.json"
            try:
                task = json.loads(task_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if task.get("status") != "Ready" or task_provider(task) != "Gemini":
                continue
            attempts = task.get("attempts")
            if not isinstance(attempts, list):
                attempts = []
                task["attempts"] = attempts
            numbers = [a.get("number", 0) for a in attempts if isinstance(a, dict) and isinstance(a.get("number"), int)]
            attempt_number = max(numbers, default=0) + 1
            timestamp = now()
            attempt = {
                "number": attempt_number,
                "provider": "Gemini",
                "model": GEMINI_MODEL,
                "state": "in_progress",
                "started_at": timestamp,
            }
            attempts.append(attempt)
            task["status"] = "In progress"
            task["updated_at"] = timestamp
            task["attempt_timeout_seconds"] = 15 * 60
            task["dispatch_note"] = "Gemini is working on this task."
            task.setdefault("activity", []).append({"at": timestamp, "type": "dispatched", "message": f"Task sent to Gemini ({GEMINI_MODEL}), attempt {attempt_number}."})
            atomic_json(task_path, task)
            append_event({"at": timestamp, "task_id": task_id, "type": "dispatched", "provider": "Gemini", "attempt": attempt_number})
            return task
    return None


def reply_file_path(task_id: str, attempt_number: int) -> Path:
    return DATA / "replies" / task_id / "gemini" / f"attempt-{attempt_number:03d}.json"


def record_gemini_failure(task_id: str, attempt_number: int, failure: GeminiFailure) -> None:
    with LOCK:
        task_path = DATA / "tasks" / f"{task_id}.json"
        try:
            task = json.loads(task_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        timestamp = now()
        retryable = failure.status in (408, 429) or failure.status >= 500
        fallbackable = retryable or failure.status in (401, 403)
        fallback = fallbackable and task.get("provider") == "Automatic" and has_untried_alternate(task, "Gemini")
        next_status = "Ready" if fallback else "Waiting for capacity" if retryable else "Blocked"
        message = f"Gemini API error {failure.status}: {failure}."
        reply_path = reply_file_path(task_id, attempt_number)
        atomic_json(reply_path, {
            "task_id": task_id, "provider": "Gemini", "model": GEMINI_MODEL,
            "attempt": attempt_number, "at": timestamp, "state": "error",
            "error_status": failure.status, "error": str(failure),
        })
        for attempt in task.get("attempts", []):
            if isinstance(attempt, dict) and attempt.get("number") == attempt_number:
                attempt.update({"state": "provider_unavailable" if fallback else "waiting_for_capacity" if retryable else "failed", "finished_at": timestamp, "reply_file": str(reply_path.relative_to(DATA))})
        if fallback:
            next_provider = task_provider(task)
            task["dispatch_note"] = f"Gemini is unavailable for this attempt; automatic routing is switching to {next_provider}."
            message = f"{message} Trying {next_provider} automatically."
            task.pop("retry_after", None)
            task.pop("waiting_reason", None)
            task.pop("wait_reason_kind", None)
        elif retryable:
            retry_at = retry_after_time(failure.retry_after, fallback_hours=24 if failure.status == 429 else 2)
            task["retry_after"] = retry_at.isoformat(timespec="seconds")
            task["waiting_reason"] = message
            task["wait_reason_kind"] = "quota" if failure.status == 429 else "service"
        else:
            task["block_reason"] = message
        task["status"] = next_status
        task["updated_at"] = timestamp
        task.setdefault("activity", []).append({"at": timestamp, "type": "agent_error", "from": "In progress", "to": next_status, "message": message})
        task["latest_reply_file"] = str(reply_path.relative_to(DATA))
        atomic_json(task_path, task)
        append_event({"at": timestamp, "task_id": task_id, "type": "agent_error", "provider": "Gemini", "status": failure.status, "to": next_status})


def record_gemini_success(task_id: str, attempt_number: int, reply: str, api_result: dict) -> None:
    with LOCK:
        task_path = DATA / "tasks" / f"{task_id}.json"
        try:
            task = json.loads(task_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        timestamp = now()
        reply_path = reply_file_path(task_id, attempt_number)
        candidate = (api_result.get("candidates") or [{}])[0]
        atomic_json(reply_path, {
            "task_id": task_id, "provider": "Gemini", "model": GEMINI_MODEL,
            "attempt": attempt_number, "at": timestamp, "state": "success",
            "text": reply, "finish_reason": candidate.get("finishReason"),
            "usage_metadata": api_result.get("usageMetadata"),
        })
        for attempt in task.get("attempts", []):
            if isinstance(attempt, dict) and attempt.get("number") == attempt_number:
                attempt.update({"state": "succeeded", "finished_at": timestamp, "reply_file": str(reply_path.relative_to(DATA))})
        task["latest_reply_file"] = str(reply_path.relative_to(DATA))
        task["updated_at"] = timestamp
        if task.get("task_type") == "pm_planning":
            next_status, message = finish_pm_planning_task(task, reply, "Gemini")
        elif task.get("produces_deliverable"):
            validation = deterministic_validation(task.get("acceptance_checks", []), reply)
            task["validation"] = validation
            if validation["status"] == "passed":
                next_status = "Done"
                message = "Gemini replied and all deterministic acceptance checks passed."
            elif validation["status"] == "failed":
                next_status = "Rework needed"
                message = "Gemini replied, but one or more deterministic acceptance checks failed."
            else:
                next_status = "Needs validation"
                message = "Gemini replied; this deliverable needs manual validation."
        else:
            task["validation"] = {"status": "not_required", "checks": [], "validated_at": timestamp}
            next_status = "Done"
            message = "Gemini replied; no deliverable validation was required."
        task["status"] = next_status
        task.pop("dispatch_note", None)
        task.setdefault("activity", []).append({"at": timestamp, "type": "agent_replied", "from": "In progress", "to": next_status, "message": message})
        atomic_json(task_path, task)
        if task.get("pull_request"):
            threading.Thread(target=github_postprocess_task, args=(task_id,), daemon=True).start()
        append_event({"at": timestamp, "task_id": task_id, "type": "agent_replied", "provider": "Gemini", "attempt": attempt_number, "to": next_status})


def dispatch_one_gemini_task() -> bool:
    global ACTIVE_GEMINI
    task = claim_gemini_task()
    if not task:
        return False
    task_id = task["id"]
    attempt_number = task["attempts"][-1]["number"]
    with LOCK:
        ACTIVE_GEMINI += 1
    try:
        reply, api_result = call_gemini(task)
        project = next((item for item in read_projects() if item.get("id") == task.get("project_id", "default")), {})
        if project.get("repository") and task.get("assigned_role") in ("Frontend developer", "Backend developer", "Documentation"):
            apply_repository_manifest(task, reply, "Gemini", attempt_number)
        record_gemini_success(task_id, attempt_number, reply, api_result)
    except GeminiFailure as failure:
        record_gemini_failure(task_id, attempt_number, failure)
    except Exception as exc:
        record_gemini_failure(task_id, attempt_number, GeminiFailure(503, f"Unexpected adapter error: {exc}"))
    finally:
        with LOCK:
            ACTIVE_GEMINI -= 1
    return True


def gemini_worker_loop() -> None:
    while True:
        try:
            dispatched = dispatch_one_gemini_task()
            if not dispatched:
                threading.Event().wait(10)
        except Exception as exc:
            print(f"Gemini worker error: {exc}")
            threading.Event().wait(10)


def call_openrouter(task: dict, provider: str = "OpenRouter") -> tuple[str, dict]:
    key_name = "groq" if provider == "GroqCloud" else "openrouter"
    api_key = provider_key(key_name)
    if not api_key:
        failure = GroqFailure if provider == "GroqCloud" else OpenRouterFailure
        raise failure(401, f"{provider} API key is not configured on the VM.")
    model = GROQ_MODEL if provider == "GroqCloud" else ANTIGRAVITY_AGENT if provider == "Antigravity" else OPENROUTER_MODEL
    endpoint = "https://api.groq.com/openai/v1/chat/completions" if provider == "GroqCloud" else "https://openrouter.ai/api/v1/chat/completions"
    checks = task.get("acceptance_checks", [])
    project_context = project_context_for_role(task.get("assigned_role", "PM/PO"), task)
    prompt = (
        (f"Project context (confirmed reference information):\n{project_context}\n\n" if project_context else "")
        + f"Task ID: {task['id']}\nAssigned role: {task.get('assigned_role', 'PM/PO')}\nProvider: {provider}\n"
        f"Request:\n{task.get('request', '')}\n\nAcceptance checks:\n"
        + ("\n".join(f"- {item}" for item in checks) or "- None specified")
    )
    prompt += repository_agent_instructions(task, provider)
    validation = task.get("validation") or {}
    if task.get("status") == "Ready" and validation.get("status") == "failed":
        prompt += "\n\nPrevious attempt failed these validation checks. Correct the work and return a revised result:\n"
        prompt += json.dumps(validation.get("checks", []), ensure_ascii=False, indent=2)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": combined_agent_prompt(task.get("assigned_role", "PM/PO")) + ("\n\n" + PM_PLANNING_CONTRACT if task.get("task_type") == "pm_planning" else "")},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 4096 if task.get("task_type") == "pm_planning" else 2048,
        "temperature": 0.2,
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "X-Title": "Personal AI Team",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=150) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            details = json.loads(raw).get("error", {}).get("message", raw)
        except json.JSONDecodeError:
            details = raw
        failure = GroqFailure if provider == "GroqCloud" else OpenRouterFailure
        raise failure(exc.code, str(details)[:2000], exc.headers.get("Retry-After")) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        failure = GroqFailure if provider == "GroqCloud" else OpenRouterFailure
        raise failure(503, str(getattr(exc, "reason", exc))[:1000]) from exc
    choices = result.get("choices", [])
    content = choices[0].get("message", {}).get("content", "") if choices else ""
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    text = str(content or "").strip()
    if not text:
        failure = GroqFailure if provider == "GroqCloud" else OpenRouterFailure
        raise failure(400, f"{provider} returned no text.")
    return text, result


def claim_openrouter_task(provider: str = "OpenRouter") -> dict | None:
    if provider not in configured_providers():
        return None
    model = GROQ_MODEL if provider == "GroqCloud" else ANTIGRAVITY_AGENT if provider == "Antigravity" else OPENROUTER_MODEL
    with LOCK:
        for summary in reversed(read_tasks()):
            if summary.get("status") != "Ready" or task_provider(summary) != provider:
                continue
            task_id = summary.get("id", "")
            if not re.fullmatch(r"T-\d{4,}", task_id):
                continue
            task_path = DATA / "tasks" / f"{task_id}.json"
            try:
                task = json.loads(task_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if task.get("status") != "Ready" or task_provider(task) != provider:
                continue
            attempts = task.get("attempts")
            if not isinstance(attempts, list):
                attempts = []
                task["attempts"] = attempts
            numbers = [a.get("number", 0) for a in attempts if isinstance(a, dict) and isinstance(a.get("number"), int)]
            attempt_number = max(numbers, default=0) + 1
            timestamp = now()
            attempts.append({"number": attempt_number, "provider": provider, "model": model, "state": "in_progress", "started_at": timestamp})
            task["status"] = "In progress"
            task["updated_at"] = timestamp
            task["attempt_timeout_seconds"] = 60 * 60 if provider == "Antigravity" else 15 * 60
            task["dispatch_note"] = f"{provider} is working on this task."
            task.setdefault("activity", []).append({"at": timestamp, "type": "dispatched", "message": f"Task sent to {provider} ({model}), attempt {attempt_number}."})
            atomic_json(task_path, task)
            append_event({"at": timestamp, "task_id": task_id, "type": "dispatched", "provider": provider, "attempt": attempt_number})
            return task
    return None


def record_openrouter_failure(task_id: str, attempt_number: int, failure: OpenRouterFailure | GroqFailure | AntigravityFailure, provider: str = "OpenRouter") -> None:
    with LOCK:
        task_path = DATA / "tasks" / f"{task_id}.json"
        try:
            task = json.loads(task_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        timestamp = now()
        retryable = failure.status in (408, 429) or failure.status >= 500
        fallbackable = retryable or failure.status in (401, 403)
        fallback = fallbackable and task.get("provider") == "Automatic" and has_untried_alternate(task, provider)
        next_status = "Ready" if fallback else "Waiting for capacity" if retryable else "Blocked"
        model = GROQ_MODEL if provider == "GroqCloud" else ANTIGRAVITY_AGENT if provider == "Antigravity" else OPENROUTER_MODEL
        folder = provider.lower().replace(" ", "")
        message = f"{provider} API error {failure.status}: {failure}."
        reply_path = DATA / "replies" / task_id / folder / f"attempt-{attempt_number:03d}.json"
        atomic_json(reply_path, {"task_id": task_id, "provider": provider, "model": model, "attempt": attempt_number, "at": timestamp, "state": "error", "error_status": failure.status, "error": str(failure)})
        for attempt in task.get("attempts", []):
            if isinstance(attempt, dict) and attempt.get("number") == attempt_number:
                attempt.update({"state": "provider_unavailable" if fallback else "waiting_for_capacity" if retryable else "failed", "finished_at": timestamp, "reply_file": str(reply_path.relative_to(DATA))})
        if fallback:
            next_provider = task_provider(task)
            task["dispatch_note"] = f"{provider} is unavailable; automatic routing is switching to {next_provider}."
            message = f"{message} Trying {next_provider} automatically."
            task.pop("retry_after", None)
            task.pop("waiting_reason", None)
            task.pop("wait_reason_kind", None)
        elif retryable:
            fallback_hours = 24 if failure.status == 429 else 2
            task["retry_after"] = retry_after_time(failure.retry_after, fallback_hours=fallback_hours).isoformat(timespec="seconds")
            task["waiting_reason"] = message
            task["wait_reason_kind"] = "quota" if failure.status == 429 else "service"
        else:
            task["block_reason"] = message
        task["status"] = next_status
        task["updated_at"] = timestamp
        task["latest_reply_file"] = str(reply_path.relative_to(DATA))
        task.setdefault("activity", []).append({"at": timestamp, "type": "agent_error", "from": "In progress", "to": next_status, "message": message})
        atomic_json(task_path, task)
        append_event({"at": timestamp, "task_id": task_id, "type": "agent_error", "provider": provider, "status": failure.status, "to": next_status})


def record_openrouter_success(task_id: str, attempt_number: int, reply: str, api_result: dict, provider: str = "OpenRouter") -> None:
    with LOCK:
        task_path = DATA / "tasks" / f"{task_id}.json"
        try:
            task = json.loads(task_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        timestamp = now()
        actual_model = api_result.get("model", GROQ_MODEL if provider == "GroqCloud" else ANTIGRAVITY_AGENT if provider == "Antigravity" else OPENROUTER_MODEL)
        folder = provider.lower().replace(" ", "")
        reply_path = DATA / "replies" / task_id / folder / f"attempt-{attempt_number:03d}.json"
        choice = (api_result.get("choices") or [{}])[0]
        atomic_json(reply_path, {"task_id": task_id, "provider": provider, "model": actual_model, "attempt": attempt_number, "at": timestamp, "state": "success", "text": reply, "finish_reason": choice.get("finish_reason"), "usage": api_result.get("usage")})
        for attempt in task.get("attempts", []):
            if isinstance(attempt, dict) and attempt.get("number") == attempt_number:
                attempt.update({"state": "succeeded", "model": actual_model, "finished_at": timestamp, "reply_file": str(reply_path.relative_to(DATA))})
        task["latest_reply_file"] = str(reply_path.relative_to(DATA))
        task["updated_at"] = timestamp
        if task.get("task_type") == "pm_planning":
            next_status, message = finish_pm_planning_task(task, reply, provider)
        elif task.get("produces_deliverable"):
            validation = deterministic_validation(task.get("acceptance_checks", []), reply)
            task["validation"] = validation
            if validation["status"] == "passed":
                next_status, message = "Done", f"{provider} replied and all deterministic acceptance checks passed."
            elif validation["status"] == "failed":
                next_status, message = "Rework needed", f"{provider} replied, but one or more deterministic acceptance checks failed."
            else:
                next_status, message = "Needs validation", f"{provider} replied; this deliverable needs manual validation."
        else:
            task["validation"] = {"status": "not_required", "checks": [], "validated_at": timestamp}
            next_status, message = "Done", f"{provider} replied; no deliverable validation was required."
        for attempt in task.get("attempts", []):
            if isinstance(attempt, dict) and attempt.get("number") == attempt_number:
                attempt["quality_outcome"] = "passed" if next_status == "Done" and task.get("produces_deliverable") else "failed" if next_status in ("Rework needed", "Blocked") else "unknown"
        task["status"] = next_status
        task.pop("dispatch_note", None)
        task.setdefault("activity", []).append({"at": timestamp, "type": "agent_replied", "from": "In progress", "to": next_status, "message": message})
        atomic_json(task_path, task)
        project = next((item for item in read_projects() if item.get("id") == task.get("project_id", "default")), {})
        has_repository = bool(project.get("repository"))
        if (provider == "Antigravity" and has_repository and task.get("assigned_role") in ("Frontend developer", "Backend developer", "Documentation")) or task.get("pull_request"):
            threading.Thread(target=github_postprocess_task, args=(task_id, provider == "Antigravity"), daemon=True).start()
        append_event({"at": timestamp, "task_id": task_id, "type": "agent_replied", "provider": provider, "attempt": attempt_number, "to": next_status})


def dispatch_one_openrouter_task(provider: str = "OpenRouter") -> bool:
    global ACTIVE_OPENROUTER
    task = claim_openrouter_task(provider)
    if not task:
        return False
    task_id = task["id"]
    attempt_number = task["attempts"][-1]["number"]
    with LOCK:
        ACTIVE_OPENROUTER += 1
    try:
        if provider == "Antigravity":
            reply, antigravity_result = call_antigravity(task)
            api_result = {"model": ANTIGRAVITY_AGENT, "choices": [{"message": {"content": reply}}], "usage": antigravity_result.get("usage")}
        else:
            reply, api_result = call_openrouter(task, provider)
        project = next((item for item in read_projects() if item.get("id") == task.get("project_id", "default")), {})
        if provider != "Antigravity" and project.get("repository") and task.get("assigned_role") in ("Frontend developer", "Backend developer", "Documentation"):
            apply_repository_manifest(task, reply, provider, attempt_number)
        record_openrouter_success(task_id, attempt_number, reply, api_result, provider)
    except (OpenRouterFailure, GroqFailure, AntigravityFailure) as failure:
        record_openrouter_failure(task_id, attempt_number, failure, provider)
    except Exception as exc:
        failure = AntigravityFailure if provider == "Antigravity" else GroqFailure if provider == "GroqCloud" else OpenRouterFailure
        record_openrouter_failure(task_id, attempt_number, failure(503, f"Unexpected adapter error: {exc}"), provider)
    finally:
        with LOCK:
            ACTIVE_OPENROUTER -= 1
    return True


def openrouter_worker_loop() -> None:
    while True:
        try:
            dispatched = dispatch_one_openrouter_task("OpenRouter")
            if not dispatched:
                threading.Event().wait(10)
        except Exception as exc:
            print(f"OpenRouter worker error: {exc}")
            threading.Event().wait(10)


def groq_worker_loop() -> None:
    while True:
        try:
            dispatched = dispatch_one_openrouter_task("GroqCloud")
            if not dispatched:
                threading.Event().wait(10)
        except Exception as exc:
            print(f"GroqCloud worker error: {exc}")
            threading.Event().wait(10)


def antigravity_worker_loop() -> None:
    while True:
        try:
            dispatched = dispatch_one_openrouter_task("Antigravity")
            if not dispatched:
                threading.Event().wait(10)
        except Exception as exc:
            print(f"Antigravity worker error: {exc}")
            threading.Event().wait(10)


def watchdog_pass() -> int:
    """Recover expired agent attempts and due capacity waits using recorded timestamps."""
    changed = 0
    current_time = datetime.now(timezone.utc)
    with LOCK:
        for path in (DATA / "tasks").glob("*.json"):
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(task, dict):
                continue
            status = task.get("status")
            reason = None
            if status == "In progress":
                attempts = task.get("attempts")
                if not isinstance(attempts, list):
                    attempts = []
                    task["attempts"] = attempts
                active_attempt = next((a for a in reversed(attempts) if isinstance(a, dict) and a.get("state") == "in_progress"), None)
                started_text = (active_attempt or {}).get("started_at") or task.get("updated_at")
                try:
                    started_at = parse_time(started_text)
                except (AttributeError, TypeError, ValueError):
                    continue
                try:
                    timeout = int(task.get("attempt_timeout_seconds", DEFAULT_ATTEMPT_TIMEOUT_SECONDS))
                except (TypeError, ValueError):
                    timeout = DEFAULT_ATTEMPT_TIMEOUT_SECONDS
                if current_time - started_at < timedelta(seconds=timeout):
                    continue
                if active_attempt:
                    active_attempt.update({"state": "timed_out", "finished_at": current_time.isoformat(timespec="seconds"), "error": "No response before the attempt timeout."})
                try:
                    retry_count = int(task.get("automatic_retries", 0))
                except (TypeError, ValueError):
                    retry_count = 0
                if retry_count >= MAX_AUTOMATIC_RETRIES:
                    status = "Blocked"
                    reason = "Automatic retry limit reached after an agent timeout."
                    task["block_reason"] = reason
                else:
                    wait_hours = min(2 * (retry_count + 1), 6)
                    status = "Waiting for capacity"
                    retry_at = current_time + timedelta(hours=wait_hours)
                    task["retry_after"] = retry_at.isoformat(timespec="seconds")
                    task["waiting_reason"] = "Agent attempt timed out."
                    task["wait_reason_kind"] = "timeout"
                    reason = f"Agent attempt timed out; retry is scheduled after {wait_hours} hours."
            elif status == "Waiting for capacity" and task.get("retry_after"):
                try:
                    retry_at = parse_time(task["retry_after"])
                except (AttributeError, TypeError, ValueError):
                    continue
                if current_time < retry_at:
                    continue
                if task.get("wait_reason_kind") == "quota":
                    task["routing_cycle_start"] = len(task.get("attempts", []))
                    status = "Ready"
                    task["dispatch_note"] = "Quota retry window reached; the provider order will be tried again."
                    task.pop("retry_after", None)
                    task.pop("waiting_reason", None)
                    task.pop("wait_reason_kind", None)
                    reason = "Quota retry window reached; provider routing cycle restarted."
                    timestamp = current_time.isoformat(timespec="seconds")
                    old_status = task.get("status")
                    task["status"] = status
                    task["updated_at"] = timestamp
                    task.setdefault("activity", []).append({"at": timestamp, "type": "watchdog_recovery", "from": old_status, "to": status, "message": reason})
                    atomic_json(path, task)
                    append_event({"at": timestamp, "task_id": task.get("id"), "type": "watchdog_recovery", "from": old_status, "to": status, "message": reason})
                    changed += 1
                    continue
                try:
                    retry_count = int(task.get("automatic_retries", 0))
                except (TypeError, ValueError):
                    retry_count = 0
                if retry_count >= MAX_AUTOMATIC_RETRIES:
                    status = "Blocked"
                    reason = "Automatic retry limit reached; manual review is needed."
                    task["block_reason"] = reason
                else:
                    task["automatic_retries"] = retry_count + 1
                    task["routing_cycle_start"] = len(task.get("attempts", []))
                    status = "Ready"
                    task["dispatch_note"] = "Retry window reached; queued for automatic dispatch when the agent connector is available."
                    task.pop("retry_after", None)
                    task.pop("waiting_reason", None)
                    task.pop("wait_reason_kind", None)
                    reason = "Retry window reached; task returned to Ready."
            else:
                continue
            timestamp = current_time.isoformat(timespec="seconds")
            old_status = task.get("status")
            task["status"] = status
            task["updated_at"] = timestamp
            task.setdefault("activity", []).append({"at": timestamp, "type": "watchdog_recovery", "from": old_status, "to": status, "message": reason})
            atomic_json(path, task)
            append_event({"at": timestamp, "task_id": task.get("id"), "type": "watchdog_recovery", "from": old_status, "to": status, "message": reason})
            changed += 1
    return changed


def watchdog_loop() -> None:
    global WATCHDOG_LAST_RUN, WATCHDOG_LAST_CHANGED, WATCHDOG_ERROR
    while True:
        try:
            changed = watchdog_pass()
            with LOCK:
                WATCHDOG_LAST_RUN = now()
                WATCHDOG_LAST_CHANGED = changed
                WATCHDOG_ERROR = None
            print(f"Watchdog check complete; recovered {changed} task(s).")
        except Exception as exc:  # keep the page service alive if a recovery record is malformed
            with LOCK:
                WATCHDOG_LAST_RUN = now()
                WATCHDOG_ERROR = str(exc)
            print(f"Watchdog check failed: {exc}")
        threading.Event().wait(WATCHDOG_INTERVAL_SECONDS)


def main() -> None:
    ensure_data()
    threading.Thread(target=watchdog_loop, name="task-watchdog", daemon=True).start()
    threading.Thread(target=gemini_worker_loop, name="gemini-worker", daemon=True).start()
    threading.Thread(target=openrouter_worker_loop, name="openrouter-worker", daemon=True).start()
    threading.Thread(target=groq_worker_loop, name="groq-worker", daemon=True).start()
    threading.Thread(target=antigravity_worker_loop, name="antigravity-worker", daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Personal AI Team listening on http://127.0.0.1:{PORT}")
    print(f"Data directory: {DATA}")
    print(f"Ollama model: {OLLAMA_MODEL} at {OLLAMA_URL}")
    server.serve_forever()


if __name__ == "__main__":
    main()

"""Prompt templates: defaults, custom overrides, and reset."""

from __future__ import annotations

from ait.common import *


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

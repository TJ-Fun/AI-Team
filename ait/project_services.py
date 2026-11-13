"""Project services/deployment manifest schema and summaries."""

from __future__ import annotations

from ait.common import *

SERVICE_ENUMS: dict[str, tuple[str, ...]] = {
    "web_host": ("", "vercel", "netlify", "github-pages", "oci", "other"),
    "api_runtime": ("", "oci", "vercel", "netlify", "other"),
    "database": ("", "supabase", "firebase", "oci-postgres", "other"),
    "auth": ("", "supabase", "firebase", "clerk", "other"),
    "media": ("", "cloudinary", "imagekit", "other"),
    "bot_protection": ("", "cloudflare-turnstile", "other"),
}
SERVICE_TEXT_FIELDS = ("preview_url", "staging_url", "production_url", "health_path")
SERVICE_LABELS = {
    "web_host": "Web host",
    "api_runtime": "API runtime",
    "database": "Database",
    "auth": "Auth",
    "media": "Media",
    "bot_protection": "Bot protection",
    "preview_url": "Preview URL",
    "staging_url": "Staging URL",
    "production_url": "Production URL",
    "health_path": "Health path",
}


def clean_project_services(raw: Any) -> dict[str, str]:
    data = raw if isinstance(raw, dict) else {}
    cleaned: dict[str, str] = {}
    for key, allowed in SERVICE_ENUMS.items():
        val = str(data.get(key, "") or "").strip().lower()
        if val not in allowed:
            raise ValueError(f"Invalid services.{key}: {val or '(empty)'}.")
        cleaned[key] = val
    for key in SERVICE_TEXT_FIELDS:
        cleaned[key] = str(data.get(key, "") or "").strip()[:500]
    return cleaned


def project_services_summary(project: dict[str, Any]) -> str:
    services = project.get("services") if isinstance(project.get("services"), dict) else {}
    lines: list[str] = []
    for key, label in SERVICE_LABELS.items():
        val = str(services.get(key, "") or "").strip()
        if val:
            lines.append(f"- {label}: {val}")
    return "\n".join(lines)

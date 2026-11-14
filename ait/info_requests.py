"""On-demand project document requests (info_requests) for agent calls."""

from __future__ import annotations

from ait.common import *


def extract_info_requests(reply: str) -> list[str]:
    """Return validated project-doc slugs from a reply's optional info_requests field."""
    clean = reply.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean, flags=re.IGNORECASE).strip()
    try:
        obj = json.loads(clean)
    except Exception:
        return []
    if not isinstance(obj, dict):
        return []
    raw = obj.get("info_requests")
    if not isinstance(raw, list):
        return []
    slugs: list[str] = []
    for item in raw:
        slug = str(item).strip()
        if is_project_doc_slug(slug) and slug not in slugs:
            slugs.append(slug)
        if len(slugs) >= INFO_REQUEST_MAX_DOCS:
            break
    return slugs


def requested_documents_text(cache: dict[str, str]) -> str:
    sections = []
    for slug, content in cache.items():
        body = content.strip() or "(This document has not been created yet.)"
        sections.append(f"## {project_doc_title(slug)} ({slug})\n{body[:20000]}")
    return "Requested project documents (provided on demand):\n\n" + "\n\n".join(sections)


def call_task_provider(task: dict[str, Any], provider: str) -> tuple[str, str, dict[str, Any]]:
    """Call the task provider, resolving info_requests with bounded follow-up calls.

    Returns (reply, actual_model, meta). Requested documents are cached per attempt so
    the agent cannot loop on the same document; at most INFO_REQUEST_MAX_ROUNDS
    follow-up calls are made.
    """
    pid = str(task.get("project_id") or "default")
    route_model = str(task.get("_route_model") or "auto")
    idle_timeout = int(task.get("_route_timeout") or STREAM_IDLE_TIMEOUT_SECONDS)
    stream_enabled = bool(task.get("_route_stream", True))
    cache: dict[str, str] = {}
    extra_context = ""
    reply = ""
    actual_model = ""
    meta: dict[str, Any] = {}
    for round_no in range(INFO_REQUEST_MAX_ROUNDS + 1):
        if provider_kind(provider) == "gemini":
            reply, meta = call_gemini_task(task, extra_context, route_model, idle_timeout, stream_enabled)
            actual_model = provider_model(provider, route_model)
        else:
            reply, actual_model, meta = call_openai_compatible_task(task, provider, extra_context, route_model, idle_timeout, stream_enabled)
        if round_no >= INFO_REQUEST_MAX_ROUNDS:
            break
        new_slugs = [s for s in extract_info_requests(reply) if s not in cache]
        if not new_slugs:
            break
        for slug in new_slugs:
            try:
                cache[slug] = load_project_context(pid, slug)
            except Exception:
                cache[slug] = ""
        extra_context = requested_documents_text(cache)
        logger.info(
            "Agent requested project documents task_id=%s provider=%s docs=%s",
            task.get("id"),
            provider,
            new_slugs,
        )
        append_event(
            {
                "at": now_iso(),
                "task_id": task.get("id"),
                "type": "info_documents_requested",
                "provider": provider,
                "documents": new_slugs,
            }
        )
    return reply, actual_model, meta

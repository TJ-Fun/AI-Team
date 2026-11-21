"""On-demand project document requests (info_requests) for agent calls."""

from __future__ import annotations

from ait.common import *


def _requestable_info_slug(slug: str) -> bool:
    """Slugs an agent may pull via info_requests (excludes user-owned and auto-generated docs)."""
    if not is_project_doc_slug(slug):
        return False
    if slug == "project_vision" or slug == ROADMAP_TRACKER_DOC_SLUG:
        return False
    return not bool(PHASE_TRACKER_DOC_RE.fullmatch(slug))


def extract_info_requests_full(reply: str) -> tuple[list[str], list[str]]:
    """Return (accepted, rejected) project-doc slugs from a reply's info_requests field.

    Accepted slugs are requestable and within the per-reply cap; rejected slugs are
    project docs that were filtered out (user-owned, auto-generated) or dropped over
    the cap, so callers can tell the agent why they were not provided.
    """
    clean = reply.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean, flags=re.IGNORECASE).strip()
    try:
        obj = json.loads(clean)
    except Exception:
        return [], []
    if not isinstance(obj, dict):
        return [], []
    raw = obj.get("info_requests")
    if not isinstance(raw, list) and str(obj.get("type") or "").strip() == "info_requests":
        for key in ("slugs", "requests", "documents", "docs"):
            candidate = obj.get(key)
            if isinstance(candidate, list):
                raw = candidate
                break
    if not isinstance(raw, list):
        return [], []
    accepted: list[str] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for item in raw:
        slug = str(item).strip()
        if not is_project_doc_slug(slug) or slug in seen:
            continue
        seen.add(slug)
        if _requestable_info_slug(slug) and len(accepted) < INFO_REQUEST_MAX_DOCS:
            accepted.append(slug)
        else:
            rejected.append(slug)
    if len(accepted) >= INFO_REQUEST_MAX_DOCS and any(_requestable_info_slug(s) for s in rejected):
        logger.warning(
            "info_requests dropped %d slug(s) over the %d-doc cap: %s",
            sum(1 for s in rejected if _requestable_info_slug(s)),
            INFO_REQUEST_MAX_DOCS,
            [s for s in rejected if _requestable_info_slug(s)],
        )
    return accepted, rejected


def extract_info_requests(reply: str) -> list[str]:
    """Return validated project-doc slugs from a reply's optional info_requests field."""
    return extract_info_requests_full(reply)[0]


def extract_doc_edit_request(reply: str) -> dict[str, Any] | None:
    """Return a validated doc_edit request from a chat reply, or None.

    The PM/PO may answer a chat message with only a JSON object of the form
    {"doc_edit_request": {"message": "...", "request": "...", "documents": ["slug"]}}.
    """
    clean = reply.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean, flags=re.IGNORECASE).strip()
    try:
        obj = json.loads(clean)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    raw = obj.get("doc_edit_request")
    if not isinstance(raw, dict):
        return None
    request = str(raw.get("request") or "").strip()
    if not request or len(request) > MAX_DOC_EDIT_REQUEST_CHARS:
        return None
    message = str(raw.get("message") or "").strip()[:2000]
    docs_raw = raw.get("documents")
    documents = (
        [str(s).strip() for s in docs_raw if is_doc_edit_slug(str(s).strip())]
        if isinstance(docs_raw, list)
        else []
    )
    return {"request": request, "message": message, "documents": documents}


def requested_documents_text(cache: dict[str, str]) -> str:
    sections = []
    for slug, content in cache.items():
        body = content.strip() or "(This document has not been created yet.)"
        sections.append(f"## {project_doc_title(slug)} ({slug})\n{body[:MAX_DOC_CHARS]}")
    return (
        "Requested project documents (provided on demand — you already have these; "
        "do not request them again):\n\n" + "\n\n".join(sections)
    )


def info_requests_context(cache: dict[str, str], sent: set[str], rejected: list[str]) -> str:
    """Build extra_context with full text only for docs not yet sent to the agent."""
    lines = [
        "Requested project documents (provided on demand — you already have these; "
        "do not request them again):"
    ]
    if cache:
        lines.append("Already provided: " + ", ".join(cache.keys()) + ".")
    if rejected:
        lines.append(
            "Not provided (user-owned, auto-generated, unknown, or over the per-reply limit): "
            + ", ".join(rejected)
            + " — do not request these again."
        )
    sections = []
    for slug, content in cache.items():
        if slug in sent:
            continue
        body = content.strip() or "(This document has not been created yet.)"
        sections.append(f"## {project_doc_title(slug)} ({slug})\n{body[:MAX_DOC_CHARS]}")
    if sections:
        lines.append("\n\n".join(sections))
    return "\n\n".join(lines)


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
    sent: set[str] = set()
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
        requested, rejected = extract_info_requests_full(reply)
        new_slugs = [s for s in requested if s not in cache]
        if not new_slugs:
            if requested and cache:
                extra_context = (
                    info_requests_context(cache, sent, rejected)
                    + "\n\nNote: you already received these documents. Do not request them again — "
                    "reply now with your final structured answer."
                )
                continue
            break
        for slug in new_slugs:
            try:
                cache[slug] = load_project_context(pid, slug)
            except Exception:
                cache[slug] = ""
        extra_context = info_requests_context(cache, sent, rejected)
        sent.update(new_slugs)
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
    accepted_final, rejected_final = extract_info_requests_full(reply)
    if cache and (accepted_final or rejected_final):
        raise ProviderFailure(
            provider,
            400,
            "Agent kept requesting project documents after they were already provided "
            f"({', '.join(sorted(cache))}); reply with the final structured object instead.",
            error_kind="format",
        )
    return reply, actual_model, meta

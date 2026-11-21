# Plan: Truncation detection + info_requests hardening (B+D)

Date: 2026-11-21 · Version targets: 0.144 / 0.145

Context: T-0019 (Party Tracker doc_update) failed — attempt 4 reply truncated at `max_tokens=8192` (`finish_reason=length` ignored), attempts 1–3 looped on `info_requests` with unhelpful errors. Approach approved: **B (detect truncation) + D (harden info_requests)**. Option A (raise max_tokens) deferred pending provider cap check; C (split doc_update) rejected for now.

Scope: `ait/provider_dispatch.py`, `ait/info_requests.py`, docs/version files. No file-size issues (all well under limits).

## Batch 1 — Truncation detection (v0.144)

`ait/provider_dispatch.py`, in the success path right after `reply_raw = reply` (~line 252), before the reply is stored:

- Read `meta.get("finish_reason")`, normalize `.lower()`; treat `"length"` / `"max_tokens"` as truncation (OpenAI `length`, Gemini `MAX_TOKENS`).
- Only when the task expects a structured reply: `planning_contract_for_task(task)` or `needs_work_reply_envelope(task)` — free-text deliverable replies are unaffected.
- Raise `ProviderFailure(provider, 400, "<provider> reply was cut off at the output token limit (finish_reason=...); the response is incomplete. Shorten the answer and try again.", error_kind="format")`.
- `reply_raw` is already assigned → the truncated reply is preserved in the error reply JSON; `error_kind="format"` routes through `maybe_format_retry`, so `format_retry_feedback` tells the model exactly why it failed.

## Batch 2 — info_requests hardening (v0.145)

`ait/info_requests.py` `call_task_provider`:

- When a reply is `info_requests` but `new_slugs` is empty **and** `cache` is non-empty **and** rounds remain: rebuild `extra_context` via `requested_documents_text(cache)` plus an explicit note ("you already received these documents; do not request them again — reply with your final structured answer") and `continue` the loop instead of breaking. Gives the model an in-attempt corrective nudge.
- When the loop ends and the final reply is still `info_requests` with a non-empty cache: raise `ProviderFailure(provider, 400, "Agent kept requesting project documents after they were already provided (...); reply with the final structured object instead.", error_kind="format")` so format-retry feedback is actionable instead of "must have type 'doc_update'".
- `requested_documents_text`: add "Do not request these documents again." to the header.
- `extract_info_requests`: `logger.warning` when valid slugs exceed `INFO_REQUEST_MAX_DOCS` and get dropped (currently silent).

## Post-step protocol (per batch)

Bump `ABOUT.md` / `README.md` / `DOCUMENTATION.md` header + `VERSIONS.md` entry → commit + push → next batch. Docs: mention truncation-as-format-failure and the info_requests re-request guard in the relevant `DOCUMENTATION.md` sections.

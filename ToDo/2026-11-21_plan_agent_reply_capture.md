# Plan: agent reply capture, error taxonomy, and exchange logging

## Goal
Fix the blind-failure problem seen on chain subtask T-0001/[Review 2]: Nvidia returned a non-envelope reply, the raw text was discarded, the error was mislabeled "API error 400", and nothing reached the logs. Make every agent exchange inspectable and format failures self-correcting.

## Root cause (confirmed)
- `provider_dispatch.py:250-254` parses the work envelope **before** the reply file is written; on `ValueError` it raises `ProviderFailure(provider, 400, ...)` and the raw reply is lost forever.
- The error-path reply file (`:513-524`) stores only `error`/`error_status`, no `text`.
- The `except` block never calls `logger.*`; only task activity + `append_event` (no content).

## Design decisions (proposed — confirm or adjust)
- Reply file is written **once, early**, right after `call_task_provider` returns, with `state: "received"` and full `text`. Later stages update the same file's `state` (`success` / `format_error` / `stale`) via rewrite or a companion field.
- Format/parse failures get `error_kind: "format"` (not HTTP 400). Activity message: "Nvidia reply format invalid: ...". Retry uses the route's normal retry_delay (no special backoff change in v1).
- Format failures on envelope tasks get **one automatic format-retry**: the parse error is injected as `format_retry_feedback` (same mechanism as PM contracts via `maybe_format_retry`) so the next attempt sees "reply with ONLY the JSON envelope". Cap: reuse existing per-task format-retry counter pattern; after cap → Rework needed.
- Outgoing prompt saved per attempt as `replies/{tid}/{provider}/attempt-NNN.prompt.txt` (plain text, truncated at ~200 KB safety).
- New activity events: `agent_request` (prompt saved) is implicit via file; log `agent_reply_raw` event with sizes. Errors get `logger.warning` with task_id/provider/attempt + first 500 chars of reply.
- UI: task-detail "Agent replies" entries become expandable showing the stored JSON (text/meta/error + error_kind). New **"Agent traffic"** section in Settings → Logs listing recent `agent_error`/`agent_replied` events with reply excerpts — OR simpler: a filter chip in the existing log viewer. Decide during implementation; prefer the lighter option.

## Batches

### Batch 1 — Raw reply persistence + error kind (backend core)
- `provider_dispatch.py`: move reply-file write before envelope parse; add `error_kind` to ProviderFailure (default `"api"`); format errors → `error_kind="format"`, status kept for retry logic but message no longer says "API error".
- Error-path reply file gains `text` (truncated 50 KB) + `error_kind` when a reply existed.
- `logger.warning` on every dispatch failure with task/provider/attempt/reply excerpt.
- Activity message distinguishes format vs API errors.
- **Verify**: py_compile + manual trace; deploy and retry the failing task — the reply file should now contain the raw text.

### Batch 2 — Format-retry feedback for work envelope
- Extend `maybe_format_retry` usage (or a small sibling) to envelope parse failures: store `format_retry_feedback` on task, inject into next prompt (prompt builder already appends it? verify — PM path does; ensure work path does too), cap retries, then Rework needed.
- `provider_stream.py`/prompt assembly: confirm `format_retry_feedback` is appended for non-PM tasks.
- **Verify**: unit-ish check that a bad reply → Ready with feedback → good reply → Done.

### Batch 3 — Prompt capture + exchange events
- Save `attempt-NNN.prompt.txt` before each provider call (in `call_task_provider` or dispatch); include model/route info in reply JSON meta.
- `append_event` types: `agent_prompt_saved`, `agent_reply_received` (with char counts, not content).
- **Verify**: files appear under `data/replies/...`; events in activity.jsonl.

### Batch 4 — UI: raw exchange viewer + log filter
- `static/js/task-detail.js` (+actions): reply entries expandable → fetch reply JSON, show `text`/`error`/`error_kind`/meta; link to prompt file if endpoint exists (may need `GET /api/tasks/{id}/replies/{file}` — check existing reply-serving route first; task replies API may already expose files).
- Logs tab: add "Agent" filter category (or new chip) showing agent-related events/replies.
- **Verify**: browser check on the failing task.

### Batch 5 — Prompt hardening + docs
- `WORK_REPLY_CONTRACT`: restate requirement at end of prompt; for report tasks hint "full report inside details".
- Optional tolerant parse: extract first balanced `{...}` before json.loads (only if Batch 1 shows fence/prose wrapping is the common failure).
- Docs: DOCUMENTATION.md §provider dispatch, VERSIONS.md, ABOUT/README version bumps per batch (0.116+).

## Impact analysis
- `ait/provider_dispatch.py` — core restructure (~60 lines moved, +30 new). Watch STATE_LOCK sections.
- `ait/qa_review.py` — `parse_work_reply_envelope` maybe tolerant-parse tweak (Batch 5 only).
- `ait/format_retry.py` — possibly generalize for envelope tasks.
- `ait/provider_stream.py` / prompt assembly — feedback injection check.
- `ait/info_requests.py` — `call_task_provider` returns meta; prompt capture likely here or in dispatch.
- `static/js/task-detail.js`, `task-detail-actions.js`, settings logs JS — viewer + filter.
- Routes: check `routes_tasks_core.py` for existing reply-file serving before adding endpoints.
- No schema changes to task JSON except new optional fields (`error_kind`, `format_retry_feedback` reuse).

## Risks
- Writing reply file early changes `latest_reply_file` semantics — set it only on terminal states as today.
- Prompt files add disk usage (~50 KB × attempts) — acceptable; note in docs.
- Format-retry must not loop: reuse existing cap logic, verify counter resets on success.

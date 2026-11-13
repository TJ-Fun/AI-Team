# Plan: streaming provider calls with 15-min idle timeout

Status: **Approved — executing**

## Goal

Replace blocking non-streaming provider calls with SSE streaming so long agent
replies (5–10+ min) are not killed at 150/180s, while still detecting dead
providers: 15 min of *silence between chunks* = failure → retryable 503 →
existing Waiting-for-capacity / fallback machinery.

## Confirmed decisions

- Idle timeout 900s, reset on every received chunk; no total duration cap.
- Stream stall → `ProviderFailure(provider, 503, ...)` → retryable path.
- All provider kinds covered automatically via `kind`: `gemini` →
  `streamGenerateContent?alt=sse`; `openai_compat` (OpenRouter, Groq,
  OmniRoute, 9Router, Nvidia, custom) → `chat/completions` `stream: true`.
- Non-SSE openai_compat endpoints fall back to the legacy non-streaming call.

## Batches

### Batch 1 — provider_stream.py + wiring → v0.93
- `ait/common.py`: `STREAM_IDLE_TIMEOUT_SECONDS = 900`.
- `ait/provider_stream.py` **new**: SSE line iterator, Gemini + OpenAI-compat
  streamed task calls (moved from `provider_calls.py`), chunk→text assembly,
  usage/finish_reason capture, non-SSE fallback for openai_compat.
- `ait/provider_calls.py`: keep connection/model tests, `normalize_github_repo_url`,
  `retry_after_time`; task calls now live in provider_stream (namespace injection
  keeps `call_task_provider` working unchanged).
- `ait/__init__.py`: register `provider_stream`.
- Verify: `compileall`, import smoke.

### Batch 2 — polish → v0.94
- Record `streamed`/`fallback` markers in reply meta; log fallback usage.
- Verify edge cases (empty stream, `[DONE]`, malformed SSE lines skipped).

### Batch 3 — docs/version final → v0.95
- `DOCUMENTATION.md`, `VERSIONS.md`, `ABOUT.md`.

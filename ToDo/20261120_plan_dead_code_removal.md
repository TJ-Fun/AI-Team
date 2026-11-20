# Plan: Dead code removal (investigate → confirm → remove → document → bump → push)

Date: 2026-11-20 · Pending user "Go"

## Rules for every batch

1. **Separate investigation per item** — re-verify zero references across `ait/`, `static/`, routes, and dynamic dispatch (`getattr`, `import *`) before removing anything.
2. Remove only confirmed-dead code; keep the app fully functional after each batch.
3. Track each removal in `documentation/VERSIONS.md` (one bullet per item) and bump `ABOUT.md` / `README.md` / `DOCUMENTATION.md` header.
4. Verify with `python -m py_compile ait/*.py` after each batch.
5. Commit + push after each batch.

## Batch 1 — v0.107: dead constants in `ait/common.py`

Investigate individually, then remove if confirmed:

- `TASK_PROVIDERS` (common.py:106)
- `ROUTING_DEFAULTS` dict (common.py:108, ~45 lines)
- `CONNECTION_SPECS` dict (common.py:156)
- `ACTIVE_GEMINI_COUNT`, `ACTIVE_GROQ_COUNT`, `ACTIVE_OPENROUTER_COUNT`, `ACTIVE_OMNIROUTE_COUNT`, `ACTIVE_NINEROUTER_COUNT`, `ACTIVE_NVIDIA_COUNT` (common.py:303-308; `ACTIVE_CHAT_COUNT` stays — it is used)

## Batch 2 — v0.108: dead functions (provider/chat)

- `chat_provider_options()` — `ait/provider_registry.py:211`
- `set_chat_model()` — `ait/chat.py:29` (confirm `/api/routing` path in `static/js/providers.js` is the only writer of `chat_models`)

## Batch 3 — v0.109: dead functions/constants (git/release/health)

- `github_checks_for_task()` — `ait/github_checks.py:64`
- `project_open_release_tasks()` — `ait/releases.py:148`
- `OPEN_TASK_STATUSES` — `ait/releases.py:9` (`DONE_TASK_STATUSES` stays — used)
- `HEALTH_TTL_SECONDS` — `ait/provider_health.py:10`

## Out of scope

- `TEAM_SECRET_KEY_FILE` in `personal-ai-team.service` — config, not code; already documented as unread.
- `do_GET`/`do_POST`/`do_PUT`/`do_DELETE`/`log_message` — framework overrides, not dead.

## Impact analysis

- Files touched per batch: the module(s) holding the dead code + `documentation/VERSIONS.md`, `documentation/ABOUT.md`, `documentation/DOCUMENTATION.md` (header), `README.md`.
- No API, data-format, or frontend changes; all removals are unreferenced symbols.

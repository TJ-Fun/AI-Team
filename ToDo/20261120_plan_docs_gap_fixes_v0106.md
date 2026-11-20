# Plan: Close documentation gaps, bump to 0.106

Date: 2026-11-20 · Approved by user request ("fix all documentation gaps. bump the version to 0.106 and push.")

## Scope (documentation-only; no code changes)

Gaps found in scan of README.md, documentation/, ait/, static/js/:

1. **Version drift** — DOCUMENTATION.md header says 0.99.7, README says 0.101, ABOUT says 0.105.
2. **Repo map omissions** — `ait/`: doc_updates, format_retry, github_repo_wipe, model_refresh, pm_planning(+submodules), project_cleanup, qa_review; `static/js/`: chat.js, notifications.js, settings.js, statistics.js.
3. **Undocumented features** — project clear-tasks, GitHub wipe (`wipe-github`), doc_update/roadmap_update approval tasks, info_requests, format-retry, watchdog pass, prompts/templates usage, data dir layout, API route-module map.
4. **Config table gaps** — TEAM_SECRET_KEY, GEMINI_MODEL, OPENROUTER_MODEL, GROQ_MODEL, TEAM_USER_AGENT; note TEAM_SECRET_KEY_FILE in unit is unused by code.

## Batches

- **Batch 1** — `documentation/DOCUMENTATION.md`: header version, repo map, §3 data layout + API map, §4.1/4.2/4.3/4.4 workflow additions, new prompts/templates note, §6 config rows. Commit.
- **Batch 2** — Version bump: ABOUT.md → 0.106 (2026-11-20), README.md → 0.106, DOCUMENTATION.md header → 0.106, VERSIONS.md new entry. Commit + push.

## Impact analysis

- Files touched: `documentation/DOCUMENTATION.md`, `documentation/ABOUT.md`, `documentation/VERSIONS.md`, `README.md`. No code, no service unit, no data files.
- `TEAM_SECRET_KEY_FILE` stays in `personal-ai-team.service` (harmless unused env); documented as currently unread.

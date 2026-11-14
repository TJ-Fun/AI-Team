# Version log

## 0.98 — 2026-11-14

- Settings → Routing role cards are now collapsible accordions (collapsed by default, header shows role + route count).
- Each provider/model route row gained per-combo settings: **Timeout** (per-attempt limit, default 15 min), **Retries** (same-combo retries after any failure, default 2), **Retry delay** (wait between retries, default 5 min), and **Stream** (default on).
- Stream on = SSE streaming where every received chunk resets the idle timer; Stream off = blocking call with a hard deadline (no answer within Timeout = failed attempt).
- Retry model reworked: the global `MAX_AUTOMATIC_RETRIES` cap and multi-hour backoffs were replaced by the per-combo chain — every failure (timeout, provider error, malformed reply) consumes the combo's retries, an exhausted combo moves to the next route immediately, and the task is Blocked + escalated only after the last combo is exhausted. Provider `Retry-After` headers are honored as a minimum on top of Retry delay.
- Route entries in `routing.json` now store `timeout_seconds`, `retries`, `retry_delay_seconds`, and `stream` (legacy entries migrate to defaults on read); attempts snapshot these settings and the watchdog hard cap is `timeout × 8` when streaming (≈ previous 2 h cap at 15 min) or `timeout` when not.
- Watchdog logic moved from `provider_dispatch.py` to `ait/provider_watchdog.py`; added a blocking (non-SSE) Gemini task call for Stream-off routes.
- Updated documentation to 0.98.

## 0.97 — 2026-11-14

- Routing data model reworked: `routing.json` now stores per-role route entries `{"provider", "model"}` (up to `MAX_ROUTE_ENTRIES` = 25) instead of plain provider-name lists; legacy string entries are migrated on read and duplicate providers are allowed.
- Provider selection now resolves full route entries (`route_entries_for_role`, `route_entry_for_role`, `task_route_entry`); dispatch records `route_model` on each attempt and "tried" tracking keys on provider+model so the same provider can appear multiple times with different models.
- `/api/routing` now returns `max_routes` instead of `max_preferred`.
- Per-route model overrides now flow through dispatch into the Gemini and OpenAI-compatible call paths (`provider_model`/`openai_compat_config` accept an override; `call_task_provider` reads the stashed `_route_model`).
- Provider model lists (`GET /api/providers/{id}/models`) now hide non-chat models (embeddings, image/audio generation, TTS, moderation, rerankers, guard models) via `is_non_chat_model`.
- Settings → Routing UI reworked: each role shows configurable provider/model route rows (default 3, add/remove up to 25, min 1) with per-row model dropdowns fed by the provider model cache, Auto-provider rows deactivate everything below them, and duplicate providers are allowed.
- Updated documentation to 0.97.

## 0.96 — 2026-11-13

- Fixed the app-update progress bar not appearing when the update is started from the notification center: the progress element is now created only after the confirmation and in-progress flag, so board polling can no longer wipe it mid-update.
- Updated documentation to 0.96.

## 0.95 — 2026-11-13

- Updated `documentation/DOCUMENTATION.md` for streamed provider task calls, the 15-minute chunk-reset idle timeout, and the non-SSE fallback; refreshed the repository map with `ait/provider_stream.py`.
- Updated documentation to 0.95.

## 0.94 — 2026-11-13

- Verified streamed reply metadata (`streamed`, `usage`, `finish_reason`) is stored with each agent reply and that SSE parsing skips keep-alives, blanks, and `[DONE]` correctly; stream stalls surface as retryable provider errors.
- Updated documentation to 0.94.

## 0.93 — 2026-11-13

- Added `ait/provider_stream.py`: provider task calls now stream SSE responses (Gemini `streamGenerateContent?alt=sse`, OpenAI-compatible `chat/completions` `stream: true`) with a 15-minute idle timeout that resets on every chunk; stalled streams fail as retryable 503 and non-SSE endpoints fall back to blocking calls.
- Moved `call_gemini_task`/`call_openai_compatible_task` out of `ait/provider_calls.py` (now connection/model testing only) and added `STREAM_IDLE_TIMEOUT_SECONDS` to `ait/common.py`.
- Updated documentation to 0.93.

## 0.92 — 2026-11-13

- Updated `documentation/DOCUMENTATION.md` for the header agent-health chip, `GET /api/providers/health`, `/api/status` `app_version`/`agents` fields, and the removed board provider-status line.
- Updated documentation to 0.92.

## 0.91 — 2026-11-13

- Replaced the header chat chip with a clickable agent-health status (`Agents X/Y`) plus the current app version; health refreshes through `GET /api/providers/health` and opens Settings → Connections on click.
- Removed the per-provider connection sentence from the Project board.
- Updated documentation to 0.91.

## 0.90 — 2026-11-13

- Added cached AI-provider health checks (`ait/provider_health.py`) using the existing lightweight connection tests, exposed `GET /api/providers/health`, and added `app_version` plus cached `agents` summary to `/api/status`.
- Updated documentation to 0.90.

## 0.89 — 2026-11-13

- Updated `documentation/DOCUMENTATION.md` with the env_setup workflow, project services manifest, and GitHub Actions QA evidence; refreshed the repository map for the new backend/frontend modules.
- Updated documentation to 0.89.

## 0.88 — 2026-11-13

- Added read-only GitHub Actions check-run evidence: `GET /api/git/{project}/checks?ref=...`, normalized check summaries, and automatic inclusion of CI results in QA review instructions for pull-request tasks.
- Updated documentation to 0.88.

## 0.87 — 2026-11-13

- Added a per-project **Services & hosting** manifest (web host, API runtime, database, auth, media, bot protection, preview/staging/prod URLs, health path) stored on the project and editable in Settings → Projects.
- The manifest is summarized into PM/PO project context so planning accounts for external services.
- Updated documentation to 0.87.

## 0.86 — 2026-11-13

- Added the env_setup task UI: board ENV badge, detail section with ordered steps, expected output, pasted-output textarea, Confirm/Skip/Problem/Change actions, blocked-issue panel, and Ask PM/PO to troubleshoot.
- Updated documentation to 0.86.

## 0.85 — 2026-11-13

- Added manual **env_setup** tasks: PM/PO plans can propose `kind:"env_setup"` tasks with 1–10 detailed steps, and users can create them via `POST /api/env-tasks`.
- Env tasks are never dispatched to providers; they support Start, per-step Confirm/Problem/Change/Skip feedback with pasted output, auto-complete to Done, Block on problems, and escalate to PM/PO via `env-escalate`.
- Updated documentation to 0.85.

## 0.84 — 2026-11-13

- Refactored `static/js/project-settings.js` (398 lines) by moving project editor rendering into `static/js/project-editor.js`; the orchestrator keeps selector state, listeners, load/save, and git/danger actions. No behavior change; prerequisite for the services manifest UI.
- Updated documentation to 0.84.

## 0.83 — 2026-11-13

- Refactored `static/js/task-detail.js` (583 lines) into `task-detail.js` (render/orchestrator) and `task-detail-actions.js` (action buttons + handlers). No behavior change; prerequisite for env-setup task UI.
- Updated documentation to 0.83.

## 0.82 — 2026-11-13

- Refactored `ait/pm_planning.py` (559 lines) into focused modules: `pm_planning_parse.py`, `pm_planning_finish.py`, and `pm_planning_escalation.py`, with `pm_planning.py` kept as a facade. No behavior change; prerequisite for manual env-setup task support.
- Updated documentation to 0.82.

## 0.81 — 2026-11-13

- Added a **Project** dropdown at the top of Settings → Projects so only one project editor is visible at a time; Save still submits all project drafts.
- Selection is persisted by project id in `localStorage`, restored on load, and kept valid across Add/Remove project. Git-status loading now runs only for the visible project.
- Updated documentation to 0.81.

## 0.80 — 2026-11-13

- Exposed the per-project destructive actions in Settings → Projects: **Clear tasks** uses a danger confirmation, and **Wipe GitHub contents** requires typing the exact `owner/repo` name before calling the backend.
- Both buttons call the v0.78/v0.79 endpoints, refresh the board on success, and keep chat history and project context documents intact.
- Added `.project-danger-actions` styling so the two danger buttons align. Updated documentation to 0.80.

## 0.79 — 2026-11-13

- Added the per-project **Wipe GitHub contents** backend: `POST /api/projects/{id}/wipe-github` resets the repository's default branch to a single empty commit, deletes all other branches, tags, and releases, then deletes the configured local clone while keeping the repository object, project `repository`/`local_path`, tasks, chat, and context docs.
- Safety gates: requires typing `owner/repo` (`confirm_text`), refuses shared repositories, `In progress` tasks, running clones, and dirty/ahead local clones; provider dispatch skips projects under maintenance during the wipe.
- Settings → Projects buttons for both destructive actions land in the next batch. Updated documentation to 0.79.

## 0.78 — 2026-11-13

- Added the per-project **Clear tasks** backend: `POST /api/projects/{id}/clear-tasks` deletes only that project's tasks, task replies, and activity-log entries while keeping chat history, project context documents, project config, modules, and the local clone.
- The endpoint blocks with `409 Conflict` while the project has `In progress` tasks or a running Git clone, and requires `{"confirm": true}`.
- Settings → Projects buttons for this action land in the next batch. Updated documentation to 0.78.

## 0.77 — 2026-11-10

- Refactored `ait/routes_tasks.py` (858 lines) into a thin dispatcher plus three focused modules: `routes_tasks_core.py` (task list/replies, create, edit, delete), `routes_tasks_pm.py` (PM planning requests, doc/roadmap updates, clarification answers, overview and design-review approvals), and `routes_tasks_ops.py` (status, provider, validation, local sync, merge approval).
- No API or behavior changes; `handle_tasks_get/post/put/delete` signatures are unchanged and all endpoints verified by smoke test.
- Updated documentation to 0.77.

## 0.76 — 2026-11-10

- Completed the on-demand project-context feature with the frontend: **Update docs** and **Update roadmap** buttons in the board header create the corresponding PM/PO update tasks for the active project.
- Task detail now shows a "Proposed project document update" section for `doc_update`/`roadmap_update` tasks (read-only document previews plus the active phase for roadmap updates) with **Approve and apply** / **Reject** actions wired to `POST /api/tasks/{id}/doc-update-decision`.
- The project context dialog already lists `master_plan` and phase documents since the context-documents API returns them; roadmap docs are editable and versioned like the other documents.
- Updated documentation to 0.76.

## 0.75 — 2026-11-10

- Added two approval-gated PM/PO update task types: **Update project documentation** (`doc_update`) and **Update roadmap** (`roadmap_update`), created via `POST /api/doc-updates` with `kind` = `docs` or `roadmap`.
- New `ait/doc_updates.py`: strict reply parsers (`type: doc_update` returns all four project documents; `type: roadmap_update` returns `master_plan`, optional `phase_1`–`phase_9` plans, and `active_phase`), finish handlers that park the proposal on the task as `Needs approval`, and `apply_doc_update`/`decide_doc_update` which write approved content through the existing versioned context-document storage (and set the project's `active_phase`).
- New endpoint `POST /api/tasks/{id}/doc-update-decision` approves (applies + Done) or rejects (Canceled, documents unchanged) a pending update; every document write keeps its history snapshot.
- `DOC_UPDATE_CONTRACT`/`ROADMAP_UPDATE_CONTRACT` reply contracts added; both task types are exempt from the work-reply envelope and routed through the dispatch finish branches. PM can pull current on-request documents via `info_requests` before proposing updates.
- Updated documentation to 0.75.

## 0.74 — 2026-11-10

- Extended `info_requests` to the PM/PO chat: when the PM replies with only a JSON object like `{"info_requests":["slug"]}`, the app loads the requested project documents, appends them to the conversation, and calls the provider again (same `INFO_REQUEST_MAX_ROUNDS` bound and per-conversation cache as task dispatch). Requests are logged as `chat_info_documents_requested` events.
- Refactored `ait/chat.py`: the provider HTTP call moved into `chat_provider_request`; `call_chat_provider` now loops over it for document requests. Chat history alternation is preserved for both Gemini and OpenAI-compatible providers.
- Updated agent prompts: `prompts/shared_rules.md` gained a "Project documents" section explaining the map, the auto/on-request split, and the strict request-only-what-you-need rule; the chat system prompt now documents the JSON-only request format.
- Updated documentation to 0.74.

## 0.73 — 2026-11-10

- Added the `info_requests` mechanism for task dispatch: any agent reply (work envelope, repo manifest, PM planning, escalation, or QA review JSON) may include `"info_requests": ["slug", ...]` to pull project documents on demand.
- New `ait/info_requests.py`: `extract_info_requests` validates slugs against the project document registry (unknown slugs ignored, deduplicated, capped at `INFO_REQUEST_MAX_DOCS`), `requested_documents_text` formats the provided documents, and `call_task_provider` runs the provider call with bounded follow-ups — the initial call plus up to `INFO_REQUEST_MAX_ROUNDS` (2) continuation calls. Requested documents are cached per attempt so an agent cannot loop on the same document, and each request round is logged (`info_documents_requested` event + app log).
- `call_gemini_task` and `call_openai_compatible_task` accept an optional `extra_context` appended to the prompt; `dispatch_one_for_provider` now routes through `call_task_provider`.
- Updated documentation to 0.73.

## 0.72 — 2026-11-10

- Started the on-demand project-context rework: agents no longer receive every project document on each call. `project_context_for_role` now injects only the role's auto documents plus a compact **Project documents** map (slug, title, size, auto/on-request flag, one-line description, and the `active_phase` pointer when set).
- Added the role → auto/on-request matrix (`ROLE_AUTO_DOCS`): PM/PO gets brief + master plan; developers get the technical overview; QA gets technical overview + outcomes; UI designer keeps brief/decisions/outcomes; Documentation gets brief/technical overview/outcomes. Everything else is on-request.
- Added roadmap document support: `master_plan` and `phase_1`–`phase_9` slugs are valid project documents with the same history/versioning as the existing context docs, and the context-documents API now lists and edits them.
- Added `info_requests` limits (`INFO_REQUEST_MAX_ROUNDS`, `INFO_REQUEST_MAX_DOCS`) and per-doc descriptions for the map; the follow-up mechanism lands in the next batch.
- Updated documentation to 0.72.

## 0.71 — 2026-11-10

- Completed the `ait/git_ops.py` split: moved the remaining task post-processing helpers (`apply_repository_manifest`, `sync_local_checkout_after_merge`, `github_postprocess_task`) into the new `ait/git_task_postprocess.py` module and deleted `git_ops.py`.
- The former monolith is now four focused modules: `git_github_api.py`, `git_local_ops.py`, `git_app_sync.py`, and `git_task_postprocess.py`. Flat-namespace injection keeps all callers unchanged. No behavior change.
- Updated documentation to 0.71.

## 0.70 — 2026-11-10

- Continued splitting `ait/git_ops.py`: moved app checkout update, mirror sync, and service restart helpers (`update_app_checkout`, `mirror_path_protected`, `sync_app_files`, `restart_service_process`, plus `MIRROR_IGNORED_DIRECTORIES`/`MIRROR_PROTECTED_ROOTS` constants) into the new `ait/git_app_sync.py` module.
- Registered in `ait/__init__.py`; flat-namespace injection keeps all callers unchanged. No behavior change.
- Updated documentation to 0.70.

## 0.69 — 2026-11-10

- Continued splitting `ait/git_ops.py`: moved local git operations, clone-job tracking, and local file viewer helpers (`git_project_path`, `git_auth_environment`, `redact_git_output`, `git_status`, `git_pull`, `git_push`, `git_clone`, `start_git_clone`, `get_git_clone_job`, `update_clone_job`, `append_clone_job_output`, `resolve_local_view_path`, `local_file_listing`, `read_local_text_file`, `local_project_root`) into the new `ait/git_local_ops.py` module.
- Registered in `ait/__init__.py`; flat-namespace injection keeps all callers unchanged. No behavior change.
- Updated documentation to 0.69.

## 0.68 — 2026-11-10

- Started splitting `ait/git_ops.py`: moved the GitHub API client, repository file viewers, and task-context helpers (`repository_parts`, `safe_repo_path`, `github_api`, `github_project`, `canonical_github_repository`, `github_file_listing`, `github_read_text_file`, `github_task_context`, `github_check_runs_summary`) into the new `ait/git_github_api.py` module.
- Registered in `ait/__init__.py`; flat-namespace injection keeps all callers unchanged. No behavior change.
- Updated documentation to 0.68.

## 0.67 — 2026-11-10

- Added the **Repair App** button next to **Update App** in Settings → About. Repair always replaces the app with a fresh mirror of the configured repository (clone + sync + restart), regardless of version.
- **Update App** now shows "Up to Date" (with current/latest versions) when the remote version is not newer, instead of reinstalling; the notification-triggered update handles the same response.
- Updated the App Update section description to match the mirror-sync behavior. No backend behavior change.
- Updated documentation to 0.67.

## 0.66 — 2026-11-10

- Added a version gate to `POST /api/settings/update`: `mode=update` (default) now compares the current app version with the remote repository version first. If the remote version is the same or older, the endpoint returns `{"up_to_date": true, "message": "Up to Date"}` without cloning or restarting. A new `mode=repair` skips the check and always performs the fresh clone + mirror sync + restart.
- If the version check fails, the update is blocked with an error, the failure is logged and recorded as an `app_update_check_failed` event, and a notification is raised via `get_notifications()`.
- Updated documentation to 0.66.

## 0.65 — 2026-11-10

- Completed the `ait/http_handler.py` split: consolidated the per-route dispatch calls into one call per route module in each HTTP method, leaving `http_handler.py` as a thin dispatcher plus the chat routes (which share global state).
- Final structure: `routes_tasks.py`, `routes_projects.py`, `routes_git.py`, `routes_settings.py`, `routes_providers.py`, all registered in `ait/__init__.py`. No behavior change.
- Updated documentation to 0.65.

## 0.64 — 2026-11-10

- Continued splitting `ait/http_handler.py`: moved provider, connection, routing, and usage API routes (connections list/test/save/clear, GitHub repositories, providers add/remove/reorder/model update/test, routing get/save, llm-usage get/reset, provider-quality) into the new `ait/routes_providers.py` module.
- `http_handler.py` now dispatches these routes to `handle_providers_get/post/put/delete`. No behavior change.
- Updated documentation to 0.64.

## 0.63 — 2026-11-10

- Continued splitting `ait/http_handler.py`: moved settings, prompt, notification, and app-update API routes (settings config/logs/log-level/clear/restart/update, app-update check, notifications list/dismiss, prompts list/save/reset, documentation files) into the new `ait/routes_settings.py` module.
- `http_handler.py` now dispatches these routes to `handle_settings_get/post/put/delete`. No behavior change.
- Updated documentation to 0.63.

## 0.62 — 2026-11-10

- Continued splitting `ait/http_handler.py`: moved all Git operation API routes (clone job status, repository status, pull, push, clone) into the new `ait/routes_git.py` module.
- `http_handler.py` now dispatches git routes to `handle_git_get/post`. No behavior change.
- Updated documentation to 0.62.

## 0.61 — 2026-11-10

- Continued splitting `ait/http_handler.py`: moved all project and project-context API routes (project list, GitHub/local file browsing, context documents, context history, restores, project save) into the new `ait/routes_projects.py` module.
- `http_handler.py` now dispatches project routes to `handle_projects_get/post/put/delete`. No behavior change.
- Updated documentation to 0.61.

## 0.60 — 2026-11-10

- Started splitting `ait/http_handler.py`: moved all task and PM-request API routes (create, list, replies, status, provider, validation, sync-local, answer, approve-overview, design-review, approve-merge, overview edit, task edit, task delete) into the new `ait/routes_tasks.py` module.
- `http_handler.py` now dispatches task routes to `handle_tasks_get/post/put/delete`; the flat-namespace injection keeps all cross-module calls unchanged. No behavior change.
- Updated documentation to 0.60.

## 0.59 — 2026-11-10

- Completed the `ait/providers.py` split: moved the remaining PM planning and escalation handling into the new `ait/pm_planning.py` module and deleted `ait/providers.py`.
- The former monolith is now four focused modules: `pm_planning.py`, `qa_review.py`, `provider_calls.py`, and `provider_dispatch.py`, all registered in `ait/__init__.py`. The flat-namespace injection keeps all cross-module calls unchanged. No behavior change.
- Updated documentation to 0.59.

## 0.58 — 2026-11-10

- Continued splitting `ait/providers.py`: moved project milestones, phase gating, deterministic validation, QA review instructions/parsing, work-reply envelope handling, and repo-agent instructions into the new `ait/qa_review.py` module.
- Registered the new module in `ait/__init__.py`; the flat-namespace injection keeps all cross-module calls unchanged. No behavior change.
- Updated documentation to 0.58.

## 0.57 — 2026-11-10

- Continued splitting `ait/providers.py`: moved auto provider selection, task claiming/dispatch, provider worker loops, watchdog, CI polling, and activity status into the new `ait/provider_dispatch.py` module.
- Registered the new module in `ait/__init__.py`; the flat-namespace injection keeps all cross-module calls unchanged. No behavior change.
- Updated documentation to 0.57.

## 0.56 — 2026-11-10

- Started splitting `ait/providers.py` into focused modules: moved provider HTTP calls and connection/model testing (`call_gemini_task`, `call_openai_compatible_task`, `test_provider_connection`, `list_provider_models`, `test_provider_model`, `retry_after_time`, `normalize_github_repo_url`) into the new `ait/provider_calls.py` module.
- Registered the new module in `ait/__init__.py`; the flat-namespace injection keeps all cross-module calls unchanged. No behavior change.
- Updated documentation to 0.56.

## 0.55 — 2026-11-10

- Completed the frontend modularization: extracted the settings dialog (tab switching, app config save, logs viewer/clear, restart, and app update) from `static/app.js` into `static/js/settings.js`, and removed the IIFE so `app.js` is now pure module wiring plus boot/polling startup.
- `app.js` initializes all feature modules and passes shared callbacks (`refreshBoard`, `loadChat`, `openTaskDetail`, provider/project-settings helpers, `openDocumentationFile`) through `featureContext` and initializer parameters; behavior, API endpoints, and HTML IDs are unchanged.
- Updated documentation to 0.55.

## 0.54 — 2026-11-10

- Continued the frontend modularization: extracted notifications (including the app-update action), provider statistics/quality rendering and reset, and the PM/PO chat from `static/app.js` into `static/js/notifications.js`, `static/js/statistics.js`, and `static/js/chat.js`.
- `app.js` initializes the modules with `refreshBoard`, `openTaskDetail`, and `loadChat` wired through the shared `featureContext`; behavior, API endpoints, and HTML IDs are unchanged.
- Updated documentation to 0.54.

## 0.53 — 2026-11-10

- Continued the frontend modularization: extracted the task detail dialog (rendering, provider selector, status/delete/sync/merge/overview actions) from `static/app.js` into `static/js/task-detail.js`.
- `app.js` initializes the module with `refreshBoard`, `loadChat`, `populateProviderSelects`, and `openTaskDialog` via the shared `featureContext`; behavior, API endpoints, and HTML IDs are unchanged.
- Updated documentation to 0.53.

## 0.52 — 2026-11-10

- Reworked the app self-update: the configured checkout is now always replaced with a fresh `git clone` of the repository (local modifications and untracked files in the checkout can no longer block the update), and the working directory is synced as an exact mirror — files are overwritten unconditionally and stale files/directories are removed, while `data/`, `.env*`, logs, virtualenvs, caches, and `.git` are protected.
- Updated documentation to 0.52.

## 0.51 — 2026-11-10

- Continued the frontend modularization: extracted project/sub-project selectors, task board rendering/actions/`refreshBoard()`, the create/edit task dialog, and the shared `getAskedProvider` helper from `static/app.js` into `static/js/project-selectors.js`, `static/js/task-board.js`, `static/js/task-dialog.js`, and `static/js/task-utils.js`.
- `app.js` now initializes those modules with a shared `featureContext` for cross-module callbacks and passes the task-board `refreshBoard()` into providers/project-settings/task-dialog; behavior, API endpoints, and HTML IDs are unchanged.
- Updated documentation to 0.51.

## 0.50 — 2026-11-10

- Continued the frontend modularization: extracted header/status rendering, provider connections/routing/model selects, and the projects/modules editor with Git actions from `static/app.js` into `static/js/header.js`, `static/js/providers.js`, and `static/js/project-settings.js`.
- `app.js` now initializes those modules and passes `refreshStatus()`/`refreshBoard()` callbacks plus the providers API where needed; behavior, API endpoints, and HTML IDs are unchanged.
- Updated documentation to 0.50.

## 0.49 — 2026-11-10

- Continued the frontend modularization: extracted the agent prompt editor, project context dialog, and local documentation/project file viewer from `static/app.js` into `static/js/prompts.js`, `static/js/project-context.js`, and `static/js/repository-viewer.js`.
- `app.js` now initializes those modules and uses their exported APIs; behavior, API endpoints, and HTML IDs are unchanged.
- Updated documentation to 0.49.

## 0.48 — 2026-11-10

- Fixed app updates failing when the configured app checkout could not fast-forward after a remote force-update or divergence: the updater now fetches `origin` and hard-resets the dedicated checkout to its tracked remote branch before copying files, while leaving other pull failures unchanged.
- Updated documentation to 0.48.

## 0.47 — 2026-11-10

- Started the frontend modularization: `static/app.js` is now an ES module entry point and shared state, API, dialog, update-progress, and formatting helpers moved to `static/js/core.js`.
- The server now serves static assets generically under `/static/` with path-traversal protection, so future frontend modules under `static/js/` do not require per-file routes.
- Updated documentation to 0.47.

## 0.46 — 2026-11-08

- Fixed clipped text in the provider **Model** dropdown: the Connections form's `.task-form select` styles (padding 10px, font 13px/1.5) override the dropdown's own styles, so its natural height matches the input fields (~41.5px); the fixed 36px height introduced in 0.45 shrank the content box below the line-height and hid the text. The fixed height was removed.
- Enlarged buttons to match the input/dropdown height (~42px) across **Settings → Connections** (Test model, Save, Test, Clear, Remove, Add provider) and the **Logs** tab (Load logs, Clear logs), with inline-flex vertical centering.
- Updated documentation to 0.46.

## 0.45 — 2026-11-08

- Shrunk the provider **Model** dropdown in **Settings → Connections** to a fixed 220px width and moved the **Test model** button directly beside it on the same line.
- Unified control heights in Connections to 36px (model dropdown, Test model, credential inputs/buttons), matching the global button/input standard.
- Fixed **Test model** errors again showing in the inline error line: they now open the popup dialog as intended (the fix had been overwritten by a stale editor buffer).
- Updated documentation to 0.45.

## 0.44 — 2026-11-08

- Fixed two custom-dialog bugs caused by CSS specificity: the **Details** input and the **Cancel** button were always visible because `display:flex`/`display:inline-flex` rules overrode the `hidden` attribute. A global `[hidden]{display:none!important}` rule now enforces correct behavior in every dialog.
- Moved the **Test model** button inline with the provider Model dropdown in **Settings → Connections** instead of below it.
- Connection **Test** and **Test model** failures now show in a popup dialog instead of the inline error line at the bottom of the section; successes remain inline.
- Aligned the **Add provider** button on the same row as the Name/Base URL/Key inputs.
- Updated documentation to 0.44.

## 0.43 — 2026-11-08

- Added a model dropdown to the PM/PO chat header: when a specific chat provider is selected it overrides the model used for chat only (stored per provider in app configuration via `chat_models`); **Auto** keeps using each provider's configured model. The override is exposed through `/api/config` and `/api/routing`.
- Added a **Test model** button under each provider's Model dropdown in **Settings → Connections**: it sends a minimal "Hi" prompt to the selected model (`POST /api/providers/{id}/test-model`) and shows the reply in a dialog.
- Completed the custom popup migration: all remaining native `alert`/`confirm` calls (clear conversation, clear logs, restart app, update app, settings save, statistic reset, and error paths) now use the styled app dialog, which finally received its missing CSS; the connection test result is shown inline instead of a popup.
- Fixed Gemini model selection being ignored: chat and task dispatch now use the configured provider model instead of the `GEMINI_MODEL` environment default.
- Updated documentation to 0.43.

## 0.42 — 2026-11-08

- Added per-provider model selection in **Settings → Connections**: each provider row now shows a Model dropdown populated from the provider's own model list (`GET /api/providers/{id}/models` — Gemini's model API or the OpenAI-compatible `/models` endpoint). Choosing a model saves it via `PUT /api/providers/{id}`; OpenAI-compatible providers also offer an **Auto** model option, and providers whose endpoint cannot list models keep a read-only display of the configured model.
- **Auto** is now accepted as a preferred-provider choice in **Settings → Routing**: it expands to every active provider ordered by audit score (ties broken by Connections order), both within the preferred slots and as the fallback tail.
- Custom providers can be created with `auto` as the model name; empty model input now defaults to `auto` instead of being rejected.
- Updated documentation to 0.42.

## 0.41 — 2026-11-08

- Added a dynamic provider control system: providers can now be added (from built-in presets or as custom OpenAI-compatible endpoints with name/base URL/model), removed (including built-ins), and reordered in **Settings → Connections** via up/down arrows. The Connections list order is the default routing order for all agents.
- Per-role routing in **Settings → Routing** now uses up to four preferred provider dropdowns; when preferred providers are exhausted or a task/chat provider is set to **Auto**, the dispatcher selects the best provider by audit score, breaking ties by the Connections order.
- Provider workers start and stop automatically as providers are added or removed; `/api/status` and the header now report per-provider connection/working state dynamically, and provider selects in task forms are populated from the active provider list.
- Updated documentation to 0.41.

## 0.40 — 2026-11-08

- Added Nvidia (NIM / build.nvidia.com) as a provider: new `nvidia` connection family with `NVIDIA_API_KEY`, OpenAI-compatible dispatch through `NVIDIA_BASE_URL` (default `https://integrate.api.nvidia.com/v1`) and `NVIDIA_MODEL` (default `meta/llama-3.3-70b-instruct`), connection test against `/models`, per-provider activity counter and status reporting, routing defaults, chat provider option, and task provider selectors.
- Updated documentation to 0.40.

## 0.39 — 2026-11-08

- Added an update progress bar with stage labels to the app update flow in both places: **Settings → About → Update App** and the **Update now** button in the notification center. The bar advances through preparing/fetching/copying stages while the update request runs, then shows service-restart polling progress until the page reloads; failures turn the bar red with the error message.
- The notification list is not re-rendered while an update is in progress, so the injected progress bar is not wiped by the 5-second board refresh.
- Fixed remaining dark-theme gaps: chat input frame, chat message bubbles, dialogs, settings sections, form controls, the header notification trigger, danger buttons, and status/feedback boxes now use dark surfaces; the Plan button stays secondary in dark mode; the send button uses an SVG arrow icon; close buttons are rounded; design-review action buttons are laid out.
- Updated documentation to 0.39.

## 0.38 — 2026-11-07

- Switched the browser interface to a dark theme: dark surfaces, borders, form controls, dialogs, chat, statistics, and state badges, with `color-scheme: dark` for native controls and scrollbars.
- Updated documentation to 0.38.

## 0.37 — 2026-11-07

- Added OmniRoute and 9Router as AI providers. Both are self-hosted OpenAI-compatible gateways; the app talks to them through configurable base URLs (`OMNIROUTE_BASE_URL`, `NINEROUTER_BASE_URL`, defaulting to `http://localhost:20128/v1` and `http://localhost:20129/v1`) and model names (`OMNIROUTE_MODEL`, `NINEROUTER_MODEL`, defaulting to `auto`).
- New connection families `omniroute` and `ninerouter` with `OMNIROUTE_API_KEY` / `NINEROUTER_API_KEY` credentials, connection tests against each gateway's `/models` endpoint, and per-provider activity counters exposed in `/api/status`.
- The OpenAI-compatible provider path was generalized into a provider config table shared by task dispatch and PM/PO chat; gateways participate in automatic routing, fallback, statistics, and repository manifest tasks like the existing providers.
- Updated documentation to 0.37.

## 0.36 — 2026-11-07

- Refined all default agent prompts to match the enforced system contracts: PM/PO planning and escalation limits are now stated explicitly, and the JSON-schema requirement is scoped to planning requests so it no longer conflicts with chat mode.
- Frontend, Backend, and Documentation prompts now describe the repository file-change manifest workflow, including the 20-file limit, accepted text extensions, protected lock files, editable scope, and the optional `feedback` field.
- Repository agents can now raise PM/PO feedback through an optional `feedback` field in the file-change JSON manifest; it is stored on the task, logged in activity, and included in the pull request body.
- QA prompt now warns that repository snapshots may be partial and that self-reported checks are claims, not evidence.
- Shared rules now route repository-task feedback through the manifest field instead of a `PM/PO feedback` section.
- UI designer prompt welcomes concrete HTML/CSS snippets for implementation-detail tasks.
- Updated documentation to 0.36.

## 0.35 — 2026-11-07

- Fixed provider activity accounting: GroqCloud dispatches were counted as OpenRouter work; each provider now has its own busy counter and `/api/status` exposes `groq_working`.
- The status bar now shows "GroqCloud working" when a GroqCloud task is in flight.
- Provider worker loops now log dispatch exceptions instead of failing silently.
- `automatic_retries` resets after a successful agent reply, so recovered tasks no longer hit the retry limit prematurely.
- Secret key generation is now guarded by a lock to avoid a first-start race.
- Updated documentation to 0.35.

## 0.34 — 2026-11-07

- Split the monolithic `server.py` (~5,900 lines) into the `ait` package: `common.py` (constants, logging, locks, shared helpers), `settings.py`, `secrets.py`, `prompts.py`, `projects.py`, `git_ops.py`, `notifications.py`, `providers.py`, `chat.py`, `http_handler.py`, and `main.py`.
- `server.py` remains a thin entry point so the systemd unit and existing deployment commands keep working unchanged.
- The package shares one flat namespace across modules, mirroring the original single-file layout; provider busy flags are now read through `provider_activity_status()` so the status API reports live values.
- Updated documentation to 0.34.

## 0.33 — 2026-11-07

- Made plan approval the default gate: PM-planned work tasks are created as drafts and released by project-overview approval unless autonomy disables it.
- Tightened PM escalation design reviews: a design change must include executable provisional team tasks and at least one alternative.
- Approving a design review now cancels the superseded blocked source task automatically; reject/alternative still cancels unfinished provisional tasks and queues PM/PO round 2.
- Updated documentation to 0.33.

## 0.32 — 2026-11-07

- Approving a PM/PO project overview now releases that plan's draft work tasks into the Ready queue, making overview approval the single plan gate when plan approval is required.
- Clarified autonomy copy: plan approval is enforced by the project-overview approval step.
- Updated documentation to 0.32.

## 0.31 — 2026-11-07

- Added a chat **Plan** button that sends the current message to PM/PO planning (`/api/pm-requests`); chat replies remain advisory.
- Added decision notifications for PM/PO clarification questions, project-overview and design-review approvals, and merge approvals; task notifications can open the related task.
- Started the CI/milestone poller so pending-CI merges and milestone notifications run every 90 seconds.
- Closed the escalation loop: escalation team tasks inherit phase/module/planning metadata, design-review approval queues drafted provisional tasks, and the blocked source task records review outcomes.
- Added autonomy controls in Settings → Routing for plan approval, provisional escalation tasks, and merge approval.
- Updated documentation to 0.31.

## 0.30 — 2026-11-07

- Added periodic app self-update checks (every 30 minutes) against the configured repository; a newer version raises a notification with an Update now button that runs the standard update flow.
- Made the notification badge neutral ("All clear" when empty, total count otherwise) and added a per-kind breakdown (issues vs updates) in the notification dialog.
- Added `GET /api/app-update/check` for on-demand update status.

## 0.29 — 2026-11-07

- Added strict reply envelopes for all remaining agent roles; malformed replies are rejected and fall back to another provider.
- PR branches are now based on the exact repository commit the agent's source snapshot was taken from, preventing stale-snapshot overwrites.
- Added GitHub Actions check-run gating: merges wait for pending checks, failed checks block the merge with a notification, and a 90-second poller completes merges automatically once checks pass.
- Removed the unused Orchestrator prompt; the chat is PM/PO-only.
- Documented existing concurrency limits (one task per provider, serialized per repository) and attempt timeouts.

## 0.28 — 2026-11-07

- Turned the chat panel into a direct PM/PO agent conversation with a provider dropdown in the chat header.
- Removed the Send to PM/PO button; planning requests are now handled conversationally and tasks are created from the board.
- Fit all six Settings tabs on one row with smaller tab styling.
- Updated the documentation for the PM/PO chat.

## 0.27 — 2026-11-01

- Removed the local Ollama runtime; the Orchestrator chat now runs on a hosted provider (Gemini, GroqCloud, or OpenRouter) selected in Settings → Routing → Chat window.
- Chat history is stored per project, and every chat request includes the active project's context documents and task board so conversations stay scoped to the selected project.
- Removed the Antigravity provider entirely; Gemini, GroqCloud, and OpenRouter remain for agent tasks, with legacy Antigravity task selections treated as Automatic.
- Added project-scope isolation instructions to the shared agent rules and Orchestrator prompt.
- Updated the documentation for the hosted chat provider and Antigravity removal.

## 0.26 — 2026-11-01

- Automatically queued valid PM/PO work tasks while keeping proposed project overviews behind user approval.
- Added automatic QA review for deliverables in Needs validation, routed through the QA provider settings.
- Returned failed QA reviews to the original work role and blocked tasks after three unsuccessful QA reviews for user intervention.
- Serialized repository-changing tasks and QA per GitHub repository, prioritized QA before new work in that repository, and reviewed the exact PR head before merge.
- Fast-forwarded the configured local checkout after a validated PR merge only when origin, default branch, and clean-worktree checks pass; added actionable local-sync notifications and a guarded task-detail retry action.
- Updated task-detail polling to rerender only after task changes while restoring focus and scroll position.
- Updated QA instructions, workflow documentation, and About version to 0.26.

## 0.25 — 2026-11-01

- Accept valid PM/PO plans with unknown sub-project IDs by assigning those tasks to the whole project, and report precise schema validation errors.
- Clarified PM/PO response schema and sub-project ID instructions; aligned shared agent response guidance with task-provided output formats.
- Added notifications for tasks in Rework needed.
- Logged every task creation and status transition in the activity event log and application log; logged when agent reply files are saved.
- Updated the documentation for task logging, project-specific context, PM/PO plans, and the Agent prompts settings tab.
- Updated the About version to 0.25.

## 0.24 — 2026-11-01

- Removed duplicate Close settings actions from the Projects, Connections, and Routing tabs; the shared Settings Close action remains.
- Wrapped log, version-log, and documentation text to prevent horizontal overflow.
- Removed the unused file-list column from the About document viewer.
- Aligned the Update App and Restart App buttons.
- Updated the About version to 0.24.

## 0.23 — 2026-11-01

- Reorganized Settings into Projects, Connections, Routing, Logs, and About tabs; kept the active-project selector on the main board.
- Added a dedicated Routing tab for per-role provider priority and kept credential management in Connections.
- Added an About page with release metadata and local Version log and Documentation viewers.
- Improved Settings tab visibility, mobile layout, and App Update field styling.
- Kept Update App and Restart App together in About's app update section.
- Expanded Statistics with period-based request, reply-success, and response-time summaries, plus an all-time deliverable QA pass rate.
- Added project documentation covering architecture, workflows, configuration, and Oracle Cloud Ubuntu deployment steps.

Earlier release entries have not been maintained in this file. See the Git commit history for changes before version 0.23.

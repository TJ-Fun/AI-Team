# Frontend `app.js` split — refactoring tracker

Status: **All phases complete** in version **0.55**. The `app.js` split is finished.
Last updated: 2026-11-10

## Goal

Split `static/app.js` into smaller ES modules without changing behavior. Work must stay in small, independently verifiable batches so the refactor can be interrupted and resumed safely.

## Current state

- `static/app.js` is an ES module entry point containing only module imports, feature-module initialization, cross-feature callback wiring, and boot/polling startup (no IIFE, no feature code).
- `static/js/core.js` contains shared frontend primitives: `state`, `$`, `escapeHtml`, `api`, app dialog helpers, update-progress helper, and formatting/date helpers.
- `static/js/prompts.js`, `static/js/project-context.js`, and `static/js/repository-viewer.js` contain the Phase 2 dialog modules and export `initX()` functions used by `app.js`.
- `static/js/header.js`, `static/js/providers.js`, and `static/js/project-settings.js` contain the Phase 3 header/provider/project-settings modules; `app.js` passes `refreshStatus()`/`refreshBoard()` callbacks into the provider/project-settings initializers.
- `static/js/task-utils.js`, `static/js/project-selectors.js`, `static/js/task-board.js`, and `static/js/task-dialog.js` contain the Phase 4 task selector/board/dialog modules; `app.js` shares a `featureContext` for cross-module callbacks and passes `refreshBoard()` into providers/project-settings/task-dialog.
- `static/js/task-detail.js` contains the Phase 5 task detail module; `app.js` initializes it with `refreshBoard`, `loadChat`, `populateProviderSelects`, and `openTaskDialog`, and exposes `openTaskDetail`/`renderTaskDetailContent` through `featureContext`.
- `static/js/notifications.js`, `static/js/statistics.js`, and `static/js/chat.js` contain the Phase 6 modules; `app.js` initializes them with `refreshBoard`/`openTaskDetail`/`loadChat` wired through `featureContext`.
- `static/js/settings.js` contains the Phase 7 settings module; `app.js` is now pure module wiring plus boot/polling startup (no IIFE).
- `static/index.html` loads `app.js` with `type="module"`.
- The Python server serves `/static/*` assets safely, including future files under `static/js/`.

## Phase 1 — completed

What was done:

- Created `static/js/core.js` and moved shared state/API/dialog/update/formatting helpers out of `app.js`.
- Converted `static/app.js` to import those helpers from `./js/core.js`.
- Changed `index.html` to load `app.js` as a module.
- Replaced per-file static routes with safe generic `/static/` serving for `.html`, `.css`, `.js`, and `.mjs`.
- Updated README/project documentation and bumped version to **0.47**.

How it was done:

- Kept the remaining `app.js` feature code inside the existing IIFE for now to keep the batch small and avoid a huge indentation diff.
- Used dependency-free ES modules; no bundler or npm build step was added.
- Added path-traversal protection in `ait/common.py` and generic static handling in `ait/http_handler.py`.

Verification already run:

- `node --check static/app.js`
- `node --check static/js/core.js`
- `python -m compileall -q ait server.py`
- `git diff --check`
- Static resolver smoke test for `js/core.js` and blocked `../secret.js`

## Phase 2 — completed

What was done:

- Extracted `static/js/prompts.js`, `static/js/project-context.js`, and `static/js/repository-viewer.js` from `app.js`.
- Each module imports shared helpers from `./core.js` and exports an `initX()` function; `app.js` initializes them inside the existing IIFE and uses returned APIs (`loadAgentPrompts`, `openRepositoryView`, `openDocumentationFile`).
- Updated README/project documentation and bumped version to **0.49**.

How it was done:

- Moved the Agent Prompts, Project Context Dialog, and Local Documentation/Project Files sections without changing behavior, API endpoints, or HTML IDs.
- Kept shared mutable state in `core.js`; modules mutate the same `state` object.
- Verified extracted bodies match the original `app.js` sections exactly and that `app.js` equals the expected transformation.

Verification already run:

- `node --check static/app.js`
- `node --check static/js/*.js`
- `git diff --check`
- Local HTTP smoke test for `/static/app.js` and all `static/js/*.js` modules

## Phase 3 — completed

What was done:

- Extracted `static/js/header.js`, `static/js/providers.js`, and `static/js/project-settings.js` from `app.js`.
- `header.js` exports `loadConfig(providers)` and `refreshStatus()`; `app.js` passes the providers API into `loadConfig`.
- `providers.js` exports `initProviders({ refreshStatus, refreshBoard })` and returns provider/routing/chat-select helpers used by `app.js` and `header.js`.
- `project-settings.js` exports `initProjectSettings({ refreshBoard })` and returns `loadProjectsIntoSettings`.
- Updated README/project documentation and bumped version to **0.50**.

How it was done:

- Moved the Header & Status, Connections/Routing, and Projects & Sub-projects sections without changing behavior, API endpoints, or HTML IDs.
- Avoided circular imports by injecting `refreshStatus()`/`refreshBoard()` callbacks and passing the providers API into `loadConfig`.
- Kept shared mutable state in `core.js`; modules mutate the same `state` object.

Verification already run:

- `node --check static/app.js`
- `node --check static/js/*.js`
- `git diff --check`
- Local HTTP smoke test for `/static/app.js` and all `static/js/*.js` modules

## Phase 4 — completed

What was done:

- Extracted `static/js/task-utils.js`, `static/js/project-selectors.js`, `static/js/task-board.js`, and `static/js/task-dialog.js` from `app.js`.
- `task-utils.js` exports `getAskedProvider`; `project-selectors.js` exports `initProjectSelectors(context)`; `task-board.js` exports `initTaskBoard(context)` and returns `refreshBoard`/`renderTasks`; `task-dialog.js` exports `initTaskDialog({ refreshBoard })` and returns `openTaskDialog`.
- `app.js` builds a `featureContext` for cross-module callbacks (`loadChat`, notifications/statistics/task-detail/repository view, `renderTasks`, `refreshBoard`) and initializes providers/project-settings with the task-board `refreshBoard`.
- Updated README/project documentation and bumped version to **0.51**.

How it was done:

- Moved the Projects & Active selectors, Task Board, and New/Edit Task Dialog sections without changing behavior, API endpoints, or HTML IDs.
- Kept `refreshBoard()` centralized in `task-board.js`; notifications/statistics/task-detail remain in `app.js` and are called through `featureContext` until later phases.
- Preserved polling, focus, and scroll restoration behavior in `refreshBoard()`.
- Kept shared mutable state in `core.js`; modules mutate the same `state` object.

Verification already run:

- `node --check static/app.js`
- `node --check static/js/*.js`
- `git diff --check`
- Exact-match verification against pre-Phase4 `app.js` for all new modules and the `app.js` transformation
- Local HTTP smoke test for `/static/app.js` and all `static/js/*.js` modules

## Phase 5 — completed

What was done:

- Extracted `static/js/task-detail.js` from `app.js`: task detail rendering, provider selector change handling, and the detail-actions click handler (status transitions, delete, local sync retry, merge approval, project overview save/approve).
- `task-detail.js` exports `initTaskDetail({ refreshBoard, loadChat, populateProviderSelects, openTaskDialog })` and returns `openTaskDetail`/`renderTaskDetailContent`; `app.js` assigns both into `featureContext` for the task board and notifications.
- Updated README/project documentation and bumped version to **0.53**.

How it was done:

- Moved the Task Detail Dialog section without changing behavior, API endpoints, or HTML IDs; the extracted body is byte-identical to the removed code.
- Kept shared mutable state in `core.js`; modules mutate the same `state` object.

Verification already run:

- `node --check static/app.js`
- `node --check static/js/*.js`
- `git diff --check`
- Exact-match verification of the extracted module body against the removed `app.js` section
- Node smoke test evaluating `app.js` with DOM stubs (no ReferenceError)

## Phase 6 — completed

What was done:

- Extracted `static/js/notifications.js` (notification rendering, dismiss/open-task actions, and the app-update flow), `static/js/statistics.js` (provider statistics/quality rendering and per-model reset), and `static/js/chat.js` (PM/PO chat rendering, send/plan/clear actions) from `app.js`.
- `notifications.js` exports `initNotifications({ refreshBoard, openTaskDetail })`; `statistics.js` exports `initStatistics({ refreshBoard })`; `chat.js` exports `initChat({ refreshBoard })` and returns `loadChat`.
- `app.js` initializes chat before task-detail so `loadChat` is available for the detail module, and assigns `renderNotifications`/`renderStatistics`/`loadChat` into `featureContext`.
- Updated README/project documentation and bumped version to **0.54**.

How it was done:

- Moved the Notifications, Statistics Dialog, and PM/PO Chat sections without changing behavior, API endpoints, or HTML IDs; extracted bodies are byte-identical to the removed code.
- Kept shared mutable state in `core.js`; modules mutate the same `state` object.
- Trimmed now-unused imports from `app.js`.

Verification already run:

- `node --check static/app.js`
- `node --check static/js/*.js`
- `git diff --check`
- Line-count and boundary verification of extracted module bodies against the removed `app.js` sections
- Node smoke test evaluating `app.js` with DOM stubs (no ReferenceError)

## Phase 7 — completed

What was done:

- Extracted `static/js/settings.js` (settings dialog tab switching, app config save, logs viewer/clear, restart, and app update) from `app.js`.
- `settings.js` exports `initSettings({ loadProjectsIntoSettings, reloadSettingsData, setConnectionsSuccess, loadAgentPrompts, openDocumentationFile })`.
- Removed the IIFE from `app.js`; it is now pure module wiring (context creation, module initialization, cross-feature callback wiring) plus boot/polling startup.
- Updated README/project documentation and bumped version to **0.55**.

How it was done:

- Moved the Settings Dialog section without changing behavior, API endpoints, or HTML IDs; the extracted body is byte-identical to the removed code.
- `app.js` no longer imports `core.js` helpers directly — all feature code lives in modules.

Verification already run:

- `node --check static/app.js`
- `node --check static/js/*.js`
- `git diff --check`
- Identifier scan confirming `settings.js` uses only its imports and context params
- Node smoke test evaluating `app.js` with DOM stubs (no ReferenceError)

## Remaining phases

None — the split is complete.

## Progress checklist

- [x] Phase 1 — module infrastructure + `core.js`
- [x] Phase 2 — prompts, project context, repository viewer
- [x] Phase 3 — providers, header/status, project settings
- [x] Phase 4 — project selectors, task board, task dialog
- [x] Phase 5 — task detail
- [x] Phase 6 — notifications, statistics, chat
- [x] Phase 7 — settings orchestration + final bootstrap/docs

## Resume after interruption

1. Run `git status --short` and inspect the current diff.
2. Read this file and continue from the first unchecked phase.
3. Keep each phase behavior-preserving and verify before moving on.
4. If a phase is partially done, finish or revert that phase before starting the next one.

## Verification commands

```bash
node --check static/app.js
for f in static/js/*.js; do node --check "$f"; done
python -m compileall -q ait server.py
git diff --check
```

Manual smoke test after each phase should cover the affected UI area plus board polling.

## Risks and notes

- `state` is shared mutable frontend state; keep it centralized in `core.js` for now.
- `refreshBoard()` is the main orchestrator; preserve its polling and detail-refresh behavior.
- Task detail, providers, notifications, and settings are the most coupled areas; extract them later and use context callbacks to avoid circular imports.
- There is no JS test suite; rely on syntax checks, Python compile checks, and manual browser regression.
- Do not change API endpoints, HTML IDs, or user-visible behavior during the split.

# Frontend `app.js` split — refactoring tracker

Status: **Phase 1 complete** in version **0.47**. Next phase: **Phase 2 — low-coupling dialogs**.
Last updated: 2026-11-10

## Goal

Split `static/app.js` into smaller ES modules without changing behavior. Work must stay in small, independently verifiable batches so the refactor can be interrupted and resumed safely.

## Current state after Phase 1

- `static/app.js` is now an ES module entry point. It imports shared helpers from `static/js/core.js` and still contains the remaining feature code inside the existing IIFE.
- `static/js/core.js` contains shared frontend primitives: `state`, `$`, `escapeHtml`, `api`, app dialog helpers, update-progress helper, and formatting/date helpers.
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

## Remaining phases

### Phase 2 — low-coupling dialogs

Extract self-contained feature modules:

- `prompts.js` — agent prompt editor.
- `project-context.js` — project context documents/history.
- `repository-viewer.js` — documentation/local file viewer.

How:

- Move each section from `app.js` into `static/js/*.js`.
- Import shared helpers from `./core.js`.
- Register DOM listeners inside `initX(context)` functions or keep temporary local wiring until the final bootstrap phase.
- Keep behavior unchanged; do not rename APIs or alter HTML IDs.

### Phase 3 — providers, header/status, project settings

Extract:

- `providers.js` — connections, routing, provider models, chat provider/model selects.
- `header.js` — config/status header rendering.
- `project-settings.js` — projects/modules editor and git actions.

How:

- Use injected callbacks for cross-feature calls such as `refreshStatus()` and `refreshBoard()`.
- Avoid direct circular imports between providers/header/task modules.

### Phase 4 — task board, selectors, task dialog

Extract:

- `project-selectors.js` — active project/sub-project selectors.
- `task-utils.js` — small shared task helpers such as `getAskedProvider`.
- `task-board.js` — task rendering, task-list actions, `refreshBoard()`.
- `task-dialog.js` — create/edit task form.

How:

- Keep `refreshBoard()` centralized in `task-board.js`.
- Call notifications/statistics/task-detail through the shared context until those modules are extracted.
- Preserve polling, focus, and scroll restoration behavior.

### Phase 5 — task detail

Extract `task-detail.js` from the largest remaining section.

How:

- Move task detail rendering and actions as one module first.
- Only split it further later if needed; do not combine this with unrelated changes.

### Phase 6 — notifications, statistics, chat

Extract:

- `notifications.js`
- `statistics.js`
- `chat.js`

How:

- Wire `openTaskDetail()`, `refreshBoard()`, and update helpers through the shared context.
- Keep notification update/app-update behavior unchanged.

### Phase 7 — settings orchestration + final bootstrap

Extract `settings.js` and reduce `static/app.js` to:

- context creation,
- module initialization,
- cross-feature callback wiring,
- boot/polling startup.

How:

- Remove the temporary IIFE once all feature modules are extracted.
- Update documentation/version again if this is released as a separate version.

## Progress checklist

- [x] Phase 1 — module infrastructure + `core.js`
- [ ] Phase 2 — prompts, project context, repository viewer
- [ ] Phase 3 — providers, header/status, project settings
- [ ] Phase 4 — project selectors, task board, task dialog
- [ ] Phase 5 — task detail
- [ ] Phase 6 — notifications, statistics, chat
- [ ] Phase 7 — settings orchestration + final bootstrap/docs

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

# Plan: Task card & detail UI polish + Copy-to-clipboard

Version bump: 0.121 → 0.122

## Changes

### 1. Task card (`static/js/task-board.js`, `static/style.css`)
- Delete button already has `danger` class but `.task-card-actions .card-delete` CSS overrides it to dark theme → add a more specific red rule so it renders red.
- Convert `Details` text-button into a `secondary` button.
- Add a `Copy` button (`data-copy-task`) that copies all task data (request, checks, replies, activity, etc.) to clipboard.

### 2. Task detail modal (`static/js/task-detail-actions.js`, `static/style.css`)
- Split `#detail-actions` into left group (`Delete task` + `Copy`) and right group (all other action buttons).
- Add `Copy` button (`data-detail-copy`) copying the same full task dump.

### 3. New module `static/js/task-copy.js`
- `taskCopyText(task, replies)` — builds a readable plain-text dump: header fields, request, PM summary, answer, design review, overview/update payloads, env steps, acceptance checks, validation, PR, agent replies (with raw text), activity log.
- `copyTaskToClipboard(taskId)` — finds task in `state.tasks`, fetches `/api/tasks/{id}/replies`, writes text via `navigator.clipboard`, returns boolean.

### 4. Board header (`static/index.html`)
- Move `Update docs` and `Major replan` buttons to after `toggle-project-active` (Pause/Resume).

### 5. Docs & version
- Bump `documentation/ABOUT.md` to 0.122, add `documentation/VERSIONS.md` entry, update `documentation/DOCUMENTATION.md` if it describes the board/detail UI.
- Commit & push.

## Batches
- Batch 1: `task-copy.js` + card/modal button changes + CSS + index.html reorder.
- Batch 2: docs/version + commit.

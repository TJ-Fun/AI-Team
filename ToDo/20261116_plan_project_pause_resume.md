# Plan: Project pause/resume (activate/deactivate)

Date: 2026-11-16
Status: Awaiting "Go"
Target version: **0.100** (subsequent changes use 0.101, 0.102, …)

## Confirmed decisions
- **Scope (Option A):** pause stops *new* dispatch only. In-flight provider calls finish normally. Release publish worker and auto-advance queue skip paused projects. Chat, board, and manual task creation stay available.
- **UI:** Pause/Resume button on the board-actions line, right after **＋ New task**. Also remove the **Documentation** button (docs remain reachable via **Files**).
- Versioning moves to 0.100+.

## Design
- New per-project boolean `active` (default `true`) persisted in `data/projects.json` via `save_projects` cleaning in `ait/projects.py` (preserve `raw["active"]` falling back to `old_proj`).
- New endpoint `POST /api/projects/{id}/active` with body `{"active": bool}` in `ait/routes_projects.py`; validates project exists, saves, appends activity event, returns updated project.
- Helper `paused_project_ids()` (or `project_is_active`) in `ait/projects.py`.

## Enforcement points (Option A)
- `ait/provider_dispatch.py` `claim_task_for_provider`: exclude tasks whose `project_id` is paused (alongside existing `PROJECT_MAINTENANCE` filter).
- `ait/release_publish.py` `run_release_publish_pass`: skip paused projects entirely (no publish, no `maybe_queue_release_plan`).

## UI changes
- `static/index.html`: delete `#open-documentation` button; add `<button class="secondary" id="toggle-project-active">` after `#new-task`.
- `static/js/repository-viewer.js`: remove `open-documentation` listener (Files button stays).
- `static/js/project-selectors.js` or `task-board.js`: render button label ("Pause project" / "Resume project") and a PAUSED badge from `state.projects` active flag; click handler POSTs to the new endpoint then `refreshBoard()`.
- `static/style.css`: `.state-paused` badge style (light + dark).

## Batches
1. **Backend** — `active` field, endpoint, paused helper, dispatcher + release-publish enforcement. Smoke test toggle + gating. Version 0.100, docs, commit.
2. **UI** — button, badge, Documentation removal, wiring. Commit.
3. **Docs** — DOCUMENTATION.md/README/VERSIONS/ABOUT version bump. Commit.

## Impact analysis
- `ait/projects.py` — `save_projects` cleaned dict gains `"active"`.
- `ait/routes_projects.py` — new POST route.
- `ait/provider_dispatch.py`, `ait/release_publish.py` — skip paused projects.
- `static/index.html`, `static/js/{repository-viewer,project-selectors,task-board}.js`, `static/style.css`.
- `documentation/{VERSIONS,DOCUMENTATION,ABOUT}.md`, `README.md`.
- No task-schema or prompt changes; paused state is project-level only.

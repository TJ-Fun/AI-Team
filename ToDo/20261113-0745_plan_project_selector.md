# Plan: Settings → Projects single-project selector

Status: **Complete** (v0.81, 2026-11-13)

## Goal

Add a **Project** dropdown at the top of Settings → Projects so only one project editor is visible at a time. Save still submits all project drafts.

## Approach

- `static/index.html`: add `<select id="project-selector">` above `#project-editors`.
- `static/js/project-settings.js`:
  - Keep `projectDrafts` as the source of truth; render only the selected editor while preserving original `data-proj-idx` values so existing input/button handlers keep working.
  - Populate selector options from project names with `Project N` fallback.
  - Persist selection by project id in `localStorage` (`ai_team_settings_project_id`); restore on load, fallback to first project.
  - **Add project** auto-selects the new draft; **Remove project** clamps selection; git-status loading runs only for the selected editor.
- `static/style.css`: minor selector spacing only if needed.

## Impact

- `static/index.html` — one select element.
- `static/js/project-settings.js` — currently 379 lines; compact changes should stay under the 400-line hard limit. If it exceeds, extract editor rendering into `static/js/project-editors.js` in the same batch.
- No backend/API changes.

## Batch

### Batch 1 — selector UI → v0.81
- Implement dropdown + selection state + persistence.
- Verify: `node --check static/js/project-settings.js`, manual Settings → Projects smoke (select/add/remove/save).
- Docs → 0.81 → commit + push.

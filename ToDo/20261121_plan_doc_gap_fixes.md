# Plan: Documentation gap fixes (from gap report)

Date: 2026-11-21 · Version targets: 0.140 / 0.141 / 0.142 / 0.143

Scope: `documentation/DOCUMENTATION.md`, `README.md`, `documentation/ABOUT.md`, `documentation/VERSIONS.md` only. No code changes.

**Version protocol (corrected):** every batch bumps ALL of `ABOUT.md`, `README.md`, `DOCUMENTATION.md` header, and adds a `VERSIONS.md` entry — so the self-updater keeps working.

## Batch 1 — Repository map fix (v0.140)

`DOCUMENTATION.md` §2:
- Remove `projects.py` from the `ait/` listing; add `routing.py`, `tasks_store.py`, `projects_store.py`, `project_context.py`, `replan.py`, `replan_stages.py`, `outcomes.py` in sensible positions.
- Add `provider-shared.js`, `provider-chat-select.js`, `provider-routing.js`, `provider-connections.js` to the `static/js/` module list.

## Batch 2 — App update flow rewrite (v0.141)

- `DOCUMENTATION.md` §4.5: rewrite steps 2–3 to match `ait/git_app_sync.py` — every update does a fresh clone into the checkout (staging dir + atomic replace), then `sync_app_files` mirrors repo files into the working dir and deletes stale files; protected paths: `data/`, `.env*`, `*.log`, `.git`, `__pycache__`, `venv`/`venv`, `node_modules`. Keep the systemd/permission notes.
- `DOCUMENTATION.md` §9: remove/replace the stale "does not delete files absent from Git" limitation (mirror sync now deletes stale files; note protected paths survive).
- `README.md` App Update paragraph: same correction (fresh clone + mirror sync, stale files removed except protected runtime data).

## Batch 3 — Replan chain + cosmetics (v0.142)

- `DOCUMENTATION.md` §4.2: rename "**Update roadmap**" → "**Major replan**"; fix `` `master_plan` `` → `` `roadmap` `` (legacy slug note).
- Add a short subsection (or extend §4.2/§4.8) documenting the staged replan chain: `chain_plan` task → user approves stage plan → sequential `chain_review`/`chain_doc`/`roadmap_update`/`board_delta` subtasks (`ait/replan.py`, `ait/replan_stages.py`) → `board_delta` mutations applied only on approval; reject cancels the chain.

## Batch 4 — Outcomes auto-logging + ABOUT (v0.143)

- `DOCUMENTATION.md` §4.1 or §4.8: document `ait/outcomes.py` — deliverable tasks reaching Done auto-append a one-line outcome entry to the `outcomes` project document.
- `ABOUT.md`: expand slightly with a short feature bullet list (board, PM/PO chat + doc edits, provider routing, GitHub PR workflow, releases/trackers) — keep it brief; it is the version source for the updater.

## Post-step protocol (per batch)

Bump all four version files → VERSIONS.md entry → commit + push → next batch.

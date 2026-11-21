# Plan: User-owned Project vision + Roadmap/Phase plans + auto trackers

Date: 2026-11-21 · Version target: 0.123

## Workstream A — Project vision is user-editable only

PM/PO keeps the vision as read-only reference; clarification answers and direct user instructions override it.

### A1. Contracts & parsing
- `ait/common.py` `PM_PLANNING_CONTRACT`: `project_overview` drops `project_vision` → `{project_brief, technical_overview, decisions}`. Add precedence rule: "Project vision is user-owned and read-only for you; user answers to clarification questions and direct user instructions override it."
- `ait/common.py` `DOC_UPDATE_CONTRACT`: drop `project_vision` → 4 docs (brief, technical_overview, decisions, outcomes).
- `ait/doc_updates.py`: `DOC_UPDATE_SLUGS` minus `project_vision`; `apply_doc_update` doc_update branch unchanged (iterates payload ∩ slugs).
- `ait/pm_planning_parse.py`: overview validation requires the 3 remaining docs.
- `ait/chat.py` `clean_overview_documents`: require 3 docs.
- `ait/qa_review.py`: remove `vision_update` contract mapping (legacy tasks still finish via dispatch).

### A2. Approval & apply paths
- `ait/routes_tasks_pm.py` approve-overview: save only the 3 docs; `project_vision` untouched. Update "Update docs" request text (4 docs).
- `ait/replan.py`: remove `"vision"` from `CHAIN_STAGES`, `STAGE_TITLES`, `STAGE_KINDS`, `KIND_TASK_TYPES`, `STAGE_REQUESTS`. Chain becomes review → docs → roadmap → board_delta. Keep `finish_vision_update_task`/`apply_doc_update` vision branch for any pending legacy `vision_update` tasks.
- `ait/common.py`: remove `VISION_UPDATE_CONTRACT` (no new tasks reference it).

### A3. UI
- `static/js/task-detail.js`: overview section — remove editable `project_vision` textarea; show read-only note "User-owned — edit via Project context".
- `static/js/task-detail-actions.js`: approve confirm text updated (3 docs + roadmap/phases per Workstream B).
- `static/index.html`: "Major replan" tooltip — remove "vision" from stage list.

## Workstream B — Roadmap rename, phase plans, auto trackers, PM feasibility

### B0. Bug fix (do first — independent)
- `ait/routes_projects.py` lines ~148, ~195: replace `PROJECT_CONTEXT_FILES[slug]` with `project_doc_title(slug)` — fixes 500 on saving `master_plan`/`phase_N` docs (file was written but response crashed).
- `static/js/project-context.js`: add "New document" flow — allow creating `phase_N`, `phase_N_tracker`, `roadmap_tracker` docs that don't exist yet (PUT already accepts them once slug is valid).

### B1. Roadmap rename `master_plan` → `roadmap`
- `ait/common.py`: `ROADMAP_DOC_SLUG = "roadmap"`; add `LEGACY_ROADMAP_SLUG = "master_plan"`; `PROJECT_DOC_DESCRIPTIONS` entry → "Roadmap: phase overview and release plan."; `PROJECT_DOC_DESCRIPTIONS`/`project_doc_title` → "Roadmap".
- `ait/projects.py`: `is_project_doc_slug` accepts legacy `master_plan` (mapped to `roadmap` in `project_context_file`/`load`/`save`); one-time migration renames `data/projects/*/context/master_plan.md` → `roadmap.md` inside `ensure_project_context`.
- `ait/releases.py`: uses `ROADMAP_DOC_SLUG` constant — auto-follows; verify release-plan JSON block parsing unchanged.
- `ait/common.py` `ROADMAP_UPDATE_CONTRACT`: payload key `master_plan` → `roadmap`.
- `ait/doc_updates.py` `parse_roadmap_update_response`/`apply_doc_update`: `master_plan` → `roadmap`.
- `ait/replan.py` `STAGE_REQUESTS["roadmap"]`: wording update.
- `static/js/task-detail.js` + `task-copy.js`: `roadmap_update` entries key `master_plan` → `roadmap`.
- `static/index.html` + `task-board.js`: button labels/tooltips "Major replan" unchanged; docs wording "Roadmap".
- Docs: DOCUMENTATION.md, VERSIONS.md, ABOUT.md version bump.

### B2. Tracker documents (system-owned, auto-synced)
New doc slugs: `roadmap_tracker`, `phase_N_tracker` (regex `phase_[1-9]_tracker`).

- `ait/common.py`: slug regexes/constants; `PROJECT_DOC_DESCRIPTIONS` entries ("Auto-generated — do not edit; regenerated from the task board").
- `ait/projects.py`: `is_project_doc_slug`, `project_doc_title` ("Roadmap tracker", "Phase N tracker"), `project_doc_slugs` includes `roadmap_tracker` always + `phase_N_tracker` for each existing `phase_N`.
- New `ait/trackers.py` (~150 lines): `sync_project_trackers(project_id)` — regenerates both docs deterministically from the task board:
  - `roadmap_tracker`: per-phase state Pending / In Progress / Done (done = all phase tasks Done/Canceled; in progress = any open task in the earliest open phase or `active_phase`), plus release summary.
  - `phase_N_tracker`: per-task rows (id, title, role, status, release) for that phase.
- Sync triggers: task status POST route, `provider_dispatch` after task finish, overview approval, `apply_board_delta_update`, replan chain advance, and lazily on GET `context-documents` (cheap, guarantees freshness).
- PM never writes trackers; contracts state they are system-generated.

### B3. PM planning rework — Roadmap + phase docs in one approval
- `PM_PLANNING_CONTRACT` plan form: `project_overview` gains `"roadmap":"complete Markdown roadmap (phase overview only)"` and `"phases":{"phase_1":"complete Markdown task-by-task phase plan", ...}` for every phase used by tasks. No `project_vision`.
- `ait/pm_planning_parse.py`: validate `roadmap` + `phases` (keys `phase_1..9`, non-empty, <20k); warn when a task's phase lacks a phase doc.
- `ait/pm_planning_finish.py`: overview task stores `overview_documents` incl. roadmap + phase docs.
- `ait/chat.py` `clean_overview_documents`: accept roadmap + phase_N keys.
- `ait/routes_tasks_pm.py` approve-overview: save roadmap + phase docs via `save_project_context_document`; then call `sync_project_trackers(pid)`.
- `static/js/task-detail.js`: overview section renders roadmap + phase doc textareas (editable until approval).
- `static/js/task-copy.js`: overview copy includes new docs (already generic).

### B4. PM feasibility rule + env_setup enrichment
- `PM_PLANNING_CONTRACT` + PM role prompt text: "Verify every proposed task is executable by the assigned agent role with repo/provider access. Anything needing user/host action (accounts, credentials, consoles, billing, manual installs, third-party dashboards) MUST be `kind: env_setup`, role User, with complete steps; include troubleshooting and fallback options to adjust the plan/phase."
- `ait/env_tasks.py` `clean_env_steps` (or equivalent): steps gain optional `troubleshooting` and `fallback` fields; task-level optional `troubleshooting`/`fallback` markdown.
- `static/js/task-env.js` + `task-detail.js`: render troubleshooting/fallback in env section.
- `static/js/task-copy.js`: include troubleshooting/fallback in env copy block.

## Batches (each leaves app functional)

1. **B0** — phase/roadmap save bug fix + context-dialog new-document flow. *(bug fix, independent)*
2. **A1–A3** — vision immutability (contracts, parse, replan chain, UI). App fully functional; PM can no longer write vision.
3. **B1** — roadmap rename + file migration + all references.
4. **B2** — tracker docs + `ait/trackers.py` + sync triggers.
5. **B3** — PM planning rework (roadmap + phase docs in overview approval).
6. **B4** — feasibility rule + env_setup troubleshooting/fallback.
7. Docs/version bump + commit per batch per protocol.

## Impact analysis

- `ait/common.py` (~432 lines, already over target): contract edits only; if it grows past ~450, move PM planning contract constants to new `ait/plan_contracts.py`.
- `ait/projects.py` (~800 lines): slug/regex/title additions only; tracker logic lives in new `ait/trackers.py`.
- `ait/pm_planning_parse.py` (271): +roadmap/phases validation — watch 400-line limit; split doc validation into `ait/pm_plan_docs.py` if needed.
- `ait/routes_projects.py` (242): 2-line bug fix + no structural change.
- `ait/routes_tasks_pm.py` (484, over limit): approve-overview additions — keep minimal; consider extracting overview save helper if it grows.
- `ait/replan.py` (380): stage removal shrinks it.
- `ait/doc_updates.py` (186), `ait/releases.py` (207), `ait/chat.py` (231), `ait/qa_review.py`: small edits.
- Frontend: `task-detail.js` (424, over limit) — overview section rework may need extraction to `task-overview.js`; `project-context.js` (130) new-doc flow; `task-env.js` (158) troubleshooting render.
- Data migration: `master_plan.md` → `roadmap.md` per project; legacy slug alias keeps old references working.

## Risks
- PM responses that still include `project_vision`/`master_plan` keys → parsers ignore unknown keys; contracts updated so new replies conform. Pending legacy `vision_update`/`roadmap_update` tasks remain appliable.
- Tracker auto-sync overwrites manual tracker edits — documented as system-generated; user edits to other docs unaffected.
- Overview approval payload grows (roadmap + up to 9 phase docs) — bounded by existing 20k/doc limit; UI shows all fields.

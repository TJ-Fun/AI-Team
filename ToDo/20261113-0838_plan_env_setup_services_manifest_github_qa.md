# Plan: manual env_setup tasks + project services manifest + GitHub Actions QA evidence

Status: **Pending approval**

## Goal

Production-only AI-Team update for external-service projects (example: web on Vercel/Netlify/GitHub Pages, API+Postgres on OCI, Supabase/Firebase auth, Cloudflare Turnstile, Cloudinary/ImageKit media):

1. **Manual `env_setup` tasks** — PM/PO can create small, clear, non-provider tasks for the user with extremely detailed step-by-step instructions. Each step supports pasted command output to confirm, or report a problem / request a decision change that blocks the task and can escalate to PM/PO.
2. **Project services/deployment manifest** — structured per-project hosting/runtime targets so PM/PO and QA know web host, API runtime, DB, auth, media, bot protection, and env URLs.
3. **GitHub Actions QA evidence** — read-only check-run/workflow evidence attached to QA tasks so the QA agent reviews real CI results.

## Key constraints

- Agents remain non-executing: PM/PO propose env tasks and code; the user executes host/account steps; GitHub Actions provides repo QA evidence.
- `env_setup` tasks must never be dispatched to providers and must not affect repository serialization.
- Several touched files are already over the 400-line hard limit (`ait/pm_planning.py` ~559, `static/js/task-detail.js` ~583, `ait/projects.py` ~669). New logic goes into new modules; prerequisite refactors keep changed files under the limit where practical.

## Impact analysis

- `ait/common.py` — add `env_setup` contract text / status helpers if needed.
- `ait/pm_planning.py` — split, then extend plan schema for `kind:"env_setup"` tasks with `steps`.
- `ait/projects.py` — minimal calls into new `ait/project_services.py`; add `env_setup` to non-repository task types.
- `ait/provider_dispatch.py` — exclude `env_setup` from provider claim.
- `ait/routes_tasks*.py` — keep thin; add `ait/routes_env_tasks.py` + `ait/env_tasks.py`.
- `static/js/task-detail.js` — split, then render env steps via `static/js/task-env.js`.
- `static/js/project-settings.js` — split, then render services fields via `static/js/project-services.js`.
- `static/index.html` / `static/style.css` — env step UI containers and services fields styling.
- `ait/github_checks.py` + QA instruction builder — fetch/attach check-run summaries.

## Batches

### Batch 1 — Refactor `ait/pm_planning.py` → v0.82
- Split into `pm_planning_parse.py`, `pm_planning_finish.py`, `pm_planning_escalation.py`; preserve behavior.
- Verify: `compileall`, PM planning smoke.

### Batch 2 — Refactor `static/js/task-detail.js` → v0.83
- Split into `task-detail.js` orchestrator + `task-detail-render.js` + `task-detail-actions.js`; preserve behavior.
- Verify: `node --check` all touched JS.

### Batch 3 — Refactor `static/js/project-settings.js` → v0.84
- Split into `project-settings.js` orchestrator + `project-editor.js` + `project-actions.js`; preserve single-project selector behavior.
- Verify: `node --check`.

### Batch 4 — Manual env_setup backend → v0.85
- New `ait/env_tasks.py`: schema for `env_steps` (`id,title,instructions,expected,status,output,note`), create/validate, step feedback, status flow `Draft/Ready → In progress → Done/Blocked/Canceled`.
- New `ait/routes_env_tasks.py`: `POST /api/tasks/{id}/env-start`, `POST /api/tasks/{id}/env-feedback`, `POST /api/tasks/{id}/env-escalate` (creates PM/PO troubleshoot task), optional `POST /api/env-tasks` for manual creation.
- Exclude `env_setup` in `provider_dispatch`, `task_repository_key`, provider selection; register modules in `ait/__init__.py`.
- Extend refactored PM planning parse/finish to accept `kind:"env_setup"` tasks with detailed `steps`; update `PM_PLANNING_CONTRACT`.
- Smoke tests with disposable `TEAM_DATA_DIR`.

### Batch 5 — Env_setup UI → v0.86
- Task board badge/scope for `env_setup`; detail view renders ordered steps with instructions, expected output, output textarea, and **Confirm step / Report problem / Request change** actions.
- Problem/change stores output+note, sets task `Blocked` with `env_issue`, offers **Return to step**, **Edit task**, **Ask PM/PO to troubleshoot**.
- Auto-complete to `Done` when all steps confirmed/skipped.

### Batch 6 — Project services manifest → v0.87
- New `ait/project_services.py` schema/validation/defaults; `projects.py` stores sanitized `services`.
- Settings → Projects services section (web host, API runtime, DB, auth, media, bot protection, preview/staging/prod URLs, health path) using refactored project modules.
- Include manifest summary in PM/PO project context payload.

### Batch 7 — GitHub Actions QA evidence → v0.88
- New `ait/github_checks.py`: fetch check runs / workflow runs for repo ref or task PR head SHA.
- Endpoint `GET /api/projects/{id}/checks?ref=...` and/or task-scoped checks; enrich QA instructions with CI summary + links.
- No workflow execution from the app; read-only evidence.

### Batch 8 — Docs/templates → v0.89
- PM prompt contract examples for env tasks; project services template; env_setup runbook; VERSIONS/ABOUT final update.

## Verification

- `python -m compileall ait`
- `node --check` for every touched JS file.
- Disposable-data smoke tests for env task create/feedback/escalate and services validation.
- Manual Settings → Projects and task-detail smoke after UI batches.

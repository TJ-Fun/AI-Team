# Plan: Release-Driven Project Workflow (v0.N phases / v0.N.M releases)

Date: 2026-11-16
Status: Awaiting "Go"

## Confirmed decisions
- Releases published via `gh release create` (GitHub token already available).
- Release advancement is fully automatic (no user approval gate).
- Env/user tasks live inside a release; release cannot start until its env tasks are Done.
- `master_plan` stored as a project context document, updated by PM.
- One release in progress per project; phases strictly sequential.

## Data model
- Task fields added: `release` (e.g. "v0.1.2"), `release_order` (int), `depends_on` optional.
- `master_plan` project doc contains a fenced ```json block:
  `{phases:[{id:"v0.1", releases:[{id:"v0.1.1", status, task_ids:[]}]}], current_phase, current_release}`
- Release status derived from task statuses + master_plan marker.

## Batches

### Batch 1 — Schema & parsing
- Extend `pm_planning_parse.py`: accept `release`, `release_order` on work/env tasks; validate semver-ish format `v\d+\.\d+(\.\d+)?`.
- Add `parse_master_plan(content)` helper: extract JSON block, validate structure.
- Update PM prompt (`prompts/`) to emit release-tagged tasks + master_plan JSON.
- Files: `ait/pm_planning_parse.py`, `prompts/*`, maybe `ait/common.py` (constants).

### Batch 2 — Release gate in dispatch
- In dispatch/selection logic: only tasks whose `release` == project's `current_release` (from master_plan) and whose release's env tasks are all Done may leave Draft/Ready.
- Block later releases' tasks (keep Draft with `waiting_reason`).
- Files: dispatcher module (locate in `ait/`), `ait/projects.py` helpers.

### Batch 3 — Release completion + GitHub publish
- On last task of a release → Done: call `publish_release(project, version)`:
  `git tag vX.Y.Z && git push origin vX.Y.Z && gh release create vX.Y.Z --generate-notes` (repo from project config).
- Update master_plan JSON: mark release done, set next current_release.
- Files: new `ait/release_publish.py`, hook into task-completion path (`provider_dispatch.py` / status-change in `projects.py`).

### Batch 4 — Watchdog: auto-advance
- Periodic check (reuse dispatcher loop timer): per project, if no open tasks AND current release complete AND master_plan defines next release → create `pm_planning` task "plan next release"; PM response updates master_plan + creates next release's tasks (env first).
- Files: new `ait/release_watchdog.py`, wire into server loop.

### Batch 5 — Env task ordering & UX polish
- Ensure env tasks for a release are surfaced first in UI; verify step feedback/escalation flow (`routes_env_tasks.py`, `env_tasks.py`) covers "different approach" → PM escalation.
- UI: show release badge on tasks (static/).

### Batch 6 — Docs, version bump, README
- Update `documentation/`, README, project version; commit per batch.

## Impact analysis
- `pm_planning_parse.py` — schema extension (backward compatible: missing release → treated as unversioned/legacy).
- Dispatcher — gate check added; legacy tasks without `release` bypass gate.
- `projects.py` — possibly new helpers `get_project_release_state(pid)`.
- `provider_dispatch.py` — completion hook for publish.
- `server.py` — watchdog timer registration.
- `static/` — release badge display (optional, last).
- Backward compat: existing tasks without `release` field keep working (gate skipped).

## Risks
- `gh` CLI auth on host — verify token works before Batch 3; fallback to tag-only if `gh` missing.
- master_plan JSON parse failures → watchdog skips project, logs warning, notifies user.
- PM emitting inconsistent release ids → parser normalizes/validates.

# Report: Large files — split candidates

Date: 2026-11-21 · Scan of `*.py` (ait/) and `*.js` (static/js/), excluding `__pycache__`.
Standard: target 100–300 lines, hard limit 400 (per project rules).

## Summary

| Lines | File | Verdict |
|------:|------|---------|
| 815 | `static/js/providers.js` | **Split — highest priority** |
| 733 | `ait/projects.py` | **Split — high priority** |
| 668 | `ait/provider_dispatch.py` | **Split — high priority** |
| 470 | `ait/routes_tasks_pm.py` | Split — medium |
| 409 | `static/js/task-detail.js` | Split — medium (just over limit) |
| 392 | `ait/git_local_ops.py` | Borderline — optional |
| 385 | `ait/common.py` | Borderline — watch only |
| 370 | `ait/notifications.py` | Borderline — optional |
| 359 | `ait/git_task_postprocess.py` | Borderline — optional |
| 341 | `static/js/task-board.js` | Borderline — optional |
| 340 | `ait/replan.py` | Borderline — optional |
| 331 | `ait/routes_settings.py` | Borderline — optional |

Everything else is ≤ ~300 lines and fine.

## Detailed candidates

### 1. `static/js/providers.js` — 815 lines (worst offender, ~2× hard limit)

Single `initProviders()` closure mixing four responsibilities:

- **Connections UI** (~lines 17–152, 467–646, 775–824): provider list render, add/remove/reorder, model selects.
- **Routing UI** (~lines 154–298, 648–773, 826+): per-role route rows, route settings, expand/collapse.
- **Chat provider/model selectors** (~lines 4–15, 342–433): `chatProviderOptions`, model select render + change handlers.
- **Settings data reload** (`reloadSettingsData`, ~435–465).

Suggested split (all under `static/js/`):
- `providers.js` — keep `initProviders` as orchestrator only.
- `provider-connections.js` — connections list + add/remove/reorder + model refresh.
- `provider-routing.js` — routing roles render + route settings + save.
- `provider-chat-select.js` — chat provider/model dropdowns.

### 2. `ait/projects.py` — 733 lines (~1.8× hard limit)

At least four distinct domains in one module:

- **Routing engine** (lines ~9–218): `default_route_settings`, `get_routing`/`save_routing`, `route_entries_for_role`, `route_entry_for_role`, attempt counting, `cleanup_provider_references`.
- **Project/task storage** (~250–355): `read_projects`, `read_tasks`, `read_task`, `write_task`, `next_task_id`, `save_projects`, legacy migration.
- **Project context documents** (~455–600+): doc slugs, titles, phase/tracker discovery, load/save/history, roadmap migration.
- **Project background/history** (~455+).

Suggested split:
- `ait/routing.py` — all route resolution/selection logic (biggest chunk, ~200 lines).
- `ait/project_docs.py` — context document slugs, load/save/history, phase/tracker helpers.
- `ait/projects.py` — keep project/task CRUD + migration only (~250 lines).

Note: `projects.py` is imported by nearly every module — split must preserve re-exports or update imports carefully (high blast radius, do as its own batch with full regression pass).

### 3. `ait/provider_dispatch.py` — 668 lines (~1.7× hard limit)

Mixed concerns:

- **Auto provider ordering** (~11–51): audit-score ordering cache.
- **Task claiming** (~54–230): `claim_task_for_provider` and helpers — the largest single block.
- **Dispatch execution** (~239–647): `dispatch_one_for_provider` — attempt loop, provider calls, retries, post-processing hooks.
- **Worker lifecycle + CI poll** (~648–713): provider worker threads, `run_ci_poll_pass`, `ci_poll_worker_loop`, activity status.

Suggested split:
- `ait/dispatch_claim.py` — auto ordering + task claiming.
- `ait/dispatch_run.py` — `dispatch_one_for_provider` execution path.
- `ait/provider_workers.py` — worker loops, reconcile, CI poll, activity status.
- `provider_dispatch.py` — thin re-export/orchestrator.

### 4. `ait/routes_tasks_pm.py` — 470 lines

Single `handle_tasks_pm_post` (~460 lines) handling many endpoints: pm-requests, overview save/approve, chain decisions, status changes, sync-local, merge approve. Suggested: split per endpoint group into `routes_tasks_pm_overview.py`, `routes_tasks_pm_chain.py`, etc., with a small dispatcher — or extract handler bodies into `ait/pm_actions/*.py`. Medium priority; only ~70 lines over the hard limit.

### 5. `static/js/task-detail.js` — 409 lines

`initTaskDetail` renders many independent dialog sections (provider row, questions, design review, update payload, PR section, env section, replies, activity). Suggested: extract section renderers into `task-detail-sections.js` (or per-section modules like the existing `task-env.js` pattern). Just over the limit — low urgency.

## Borderline (370–392 lines, under hard limit)

- `ait/git_local_ops.py` (392) — could split clone-job management from local file browser (`local_file_listing`, `read_local_text_file`, `resolve_local_view_path` → `ait/local_files.py`).
- `ait/common.py` (385) — already flagged in earlier plans; if it grows, move PM contracts to `ait/plan_contracts.py`.
- `ait/notifications.py` (370) — could split quality stats (`get_provider_quality`) and CPU sampling from notification aggregation.
- `ait/git_task_postprocess.py` (359), `static/js/task-board.js` (341), `ait/replan.py` (340), `ait/routes_settings.py` (331) — acceptable; revisit only if they grow.

## Recommended order

1. `providers.js` — pure frontend, zero backend risk, biggest win.
2. `provider_dispatch.py` — self-contained backend module.
3. `projects.py` — highest value but highest blast radius; schedule carefully with import audit.
4. `routes_tasks_pm.py` + `task-detail.js` — opportunistic.

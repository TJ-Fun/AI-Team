# `ait/providers.py` split — refactoring tracker

Status: **complete** (2026-11-10). All batches done; `ait/providers.py` deleted and replaced by `pm_planning.py`, `qa_review.py`, `provider_calls.py`, and `provider_dispatch.py`.
Last updated: 2026-11-10

## Goal

Split `ait/providers.py` (1,865 lines) into focused modules without changing behavior. Work must stay in small, independently verifiable batches so the refactor can be interrupted and resumed safely.

## Current state

- `ait/providers.py` mixes four distinct concerns:
  1. **PM planning & escalation** — parse/finish PM plan responses, escalation parsing/finishing, escalation creation.
  2. **Milestones, QA review & agent instructions** — phase gating, milestone checks, deterministic validation, QA review instructions/parsing, work-reply envelope, repo-agent instructions.
  3. **Provider HTTP calls & connection testing** — `call_gemini_task`, `call_openai_compatible_task`, `test_provider_connection`, `list_provider_models`, `test_provider_model`, `retry_after_time`, `normalize_github_repo_url`.
  4. **Auto-selection, dispatch & background workers** — auto provider ordering/selection, task claiming, per-provider dispatch, provider worker loops, watchdog, CI poll, activity status.
- The package uses a **flat namespace**: `ait/__init__.py` imports every domain module and injects all public names into each other's globals. Cross-module calls use bare function names, so moving a function to a new domain module requires **no changes** in callers — only registering the new module in `ait/__init__.py`.
- No test suite exists. Verification is `python -m compileall`, a server smoke test, and manual API checks.
- Module-level constants that must move with their functions: `AUTO_ORDER_TTL_SECONDS`, `PROVIDER_WORKER_LOCK`, `CI_POLL_INTERVAL_SECONDS`.

## Proposed split (4 modules)

| New module | Contents (current line ranges in `providers.py`) | ~Lines |
|---|---|---|
| `ait/pm_planning.py` | PM plan/escalation parse+finish, `maybe_create_escalation` (9–558) | ~550 |
| `ait/qa_review.py` | milestones, phase gating, deterministic validation, QA review, work-reply envelope, repo-agent instructions (559–814) | ~255 |
| `ait/provider_calls.py` | provider HTTP calls, connection/model testing, `retry_after_time`, `normalize_github_repo_url` (815–1221) | ~405 |
| `ait/provider_dispatch.py` | auto ordering/selection, claim, dispatch, worker loops, watchdog, CI poll, activity status (1222–1865) | ~640 |

`providers.py` is deleted once empty. Each new module starts with `from ait.common import *` (same as `providers.py`).

## Impact analysis

- **`ait/__init__.py`** — add `from ait import <new_module>` for each new module and add them to `_DOMAIN_MODULES`; remove `providers` import/entry once `providers.py` is deleted. **This is the only file that must change.**
- **All other files** (`main.py`, `http_handler.py`, `chat.py`, `projects.py`, `git_ops.py`, `settings.py`, `provider_registry.py`, `notifications.py`, `prompts.py`, `secrets.py`, `common.py`) — **no changes needed**. They call providers functions by bare name, resolved via the flat-namespace injection. Verified external callers: `http_handler.py` (test_provider_connection, list_provider_models, test_provider_model, reconcile_provider_workers, provider_activity_status), `main.py` (reconcile_provider_workers, watchdog_worker_loop, ci_poll_worker_loop), `chat.py`/`projects.py` (select_auto_provider), `projects.py` (auto_provider_order), `git_ops.py`/`http_handler.py`/`settings.py` (normalize_github_repo_url).
- **Frontend / docs** — no changes.

## Incremental migration plan

Each batch: move one group of functions/constants into a new module, register it in `ait/__init__.py`, delete the moved code from `providers.py`, then verify. The app must remain fully functional after every batch.

### Batch 1 — `ait/provider_calls.py`

- Move: `call_gemini_task`, `normalize_github_repo_url`, `call_openai_compatible_task`, `test_provider_connection`, `list_provider_models`, `test_provider_model`, `retry_after_time`.
- Register in `__init__.py`; remove from `providers.py`.
- Verify: `python -m compileall ait server.py`; start server; hit `/api/providers/...` test endpoints.

### Batch 2 — `ait/provider_dispatch.py`

- Move: `AUTO_ORDER_TTL_SECONDS`, `auto_provider_order`, `sort_key`, `select_auto_provider`, `claim_task_for_provider`, `bump_provider_activity`, `dispatch_one_for_provider`, `PROVIDER_WORKER_LOCK`, `provider_worker_loop`, `reconcile_provider_workers`, `run_watchdog_pass`, `watchdog_worker_loop`, `CI_POLL_INTERVAL_SECONDS`, `run_ci_poll_pass`, `ci_poll_worker_loop`, `provider_activity_status`.
- Register; remove from `providers.py`.
- Verify: compileall; server starts; provider workers reconcile on boot.

### Batch 3 — `ait/qa_review.py`

- Move: `project_min_open_phase`, `task_phase_gated`, `check_project_milestones`, `deterministic_validation`, `build_qa_review_instructions`, `parse_qa_result`, `planning_contract_for_task`, `needs_work_reply_envelope`, `parse_work_reply_envelope`, `build_repo_agent_instructions`.
- Register; remove from `providers.py`.
- Verify: compileall; dispatch a task end-to-end if possible.

### Batch 4 — `ait/pm_planning.py` + delete `providers.py`

- Move remaining functions: `parse_pm_plan_response`, `finish_pm_planning_task`, `parse_escalation_response`, `finish_pm_escalation_task`, `maybe_create_escalation`.
- `providers.py` is now empty — delete the file and remove its import/`_DOMAIN_MODULES` entry from `ait/__init__.py`.
- Verify: compileall; full server smoke test; manual UI pass (chat, task dispatch, settings).

### Batch 5 — docs & version

- Update `documentation/VERSIONS.md`, `README.md`, `documentation/ABOUT.md`, `documentation/DOCUMENTATION.md`; bump version.
- Mark this tracker complete.

## Verification commands

```powershell
python -m compileall -q ait server.py
node --check static/app.js   # frontend untouched, sanity only
# server smoke: start server.py, check /api/status responds, then stop
```

## Resume after interruption

1. Run `git status --short` and inspect the current diff.
2. Read this file and continue from the first unchecked batch.
3. Keep each batch behavior-preserving and verify before moving on.
4. If a batch is partially done, finish or revert that batch before starting the next one.

## Progress checklist

- [x] Batch 1 — `provider_calls.py` (moved `call_gemini_task`, `normalize_github_repo_url`, `call_openai_compatible_task`, `test_provider_connection`, `list_provider_models`, `test_provider_model`, `retry_after_time`; registered in `ait/__init__.py`; verified compileall + namespace resolution + server boot)
- [x] Batch 2 — `provider_dispatch.py` (moved auto ordering/selection, claim, dispatch, worker loops, watchdog, CI poll, activity status + `AUTO_ORDER_CACHE`/`AUTO_ORDER_TTL_SECONDS`/`ACTIVE_PROVIDER_COUNTS`/`PROVIDER_WORKER_LOCK`/`CI_POLL_INTERVAL_SECONDS`; registered in `ait/__init__.py`; verified compileall + namespace resolution + server boot)
- [x] Batch 3 — `qa_review.py` (moved milestones, phase gating, deterministic validation, QA review, work-reply envelope, repo-agent instructions; registered in `ait/__init__.py`; verified compileall + namespace resolution + server boot)
- [x] Batch 4 — `pm_planning.py` + delete `providers.py` (moved remaining PM planning/escalation functions; deleted `providers.py`; registered `pm_planning` in `ait/__init__.py`; verified compileall + namespace resolution + server boot)
- [x] Batch 5 — docs & version (0.59)

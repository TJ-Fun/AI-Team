# Plan: Split `ait/projects.py` (838 lines) into domain modules

Date: 2026-11-21 · Version targets: 0.133 / 0.134

## Current state

`ait/projects.py` mixes **five responsibilities**:

| Lines | Responsibility | Functions |
|---|---|---|
| 9–219 | Routing config + resolution | `_clean_route_*`, `default_route_settings`, `_route_settings_from`, `_routing_entries`, `_with_model_flags`, `get_routing`, `save_routing`, `route_entries_for_role`, `route_combo_key`, `_combo_attempt_counts`, `_entry_retries`, `_combo_exhausted`, `route_entry_for_role`, `cleanup_provider_references` |
| 221–270, 356–474 | Projects CRUD + legacy migration | `migrate_legacy_project_context`, `read_projects`, `paused_project_ids`, `save_projects`, `project_background_history` |
| 273–353, 751–767, 824–838 | Task storage + replies + repo key | `read_tasks`, `read_task`, `write_task`, `next_task_id`, `read_task_replies`, `task_repository_key` |
| 477–718 | Project context documents | `is_project_doc_slug`, `project_doc_title`, `project_phase_doc_slugs`, `project_phase_tracker_slugs`, `project_doc_slugs`, `project_context_file`, `ensure_project_context`, `load_project_context`, `save_project_context_document`, `get_project_context_history`, `project_doc_map_text`, `project_context_for_role` |
| 721–748 | Chat history persistence | `chat_history_path`, `read_chat_history`, `write_chat_history`, `append_chat_notice` |
| 770–821 | Task→route resolution | `task_route_entry`, `task_provider`, `provider_for_role`, `has_untried_alternate` |

## Key architectural fact

`ait/__init__.py` uses a **flat shared namespace**: every public name from every domain module is injected into every other module's globals (`_DOMAIN_MODULES` + `_exported_names`). No code calls `projects.read_projects()` — all call sites use bare function names. **Private (`_`-prefixed) names are NOT injected**, so private helpers must stay in the same module as their callers.

Consequence: the split is pure code motion + `__init__.py` registration. No call-site changes anywhere.

## Target structure

- **`ait/routing.py`** (~285 lines) — all routing config/resolution **including** `task_route_entry`, `task_provider`, `provider_for_role`, `has_untried_alternate` (they use private helpers `_combo_attempt_counts`, `_combo_exhausted`, `route_entries_for_role` — must stay together). Also `cleanup_provider_references`.
- **`ait/tasks_store.py`** (~115 lines) — `read_tasks`, `read_task`, `write_task`, `next_task_id`, `read_task_replies`, `task_repository_key`.
- **`ait/projects_store.py`** (~255 lines) — `migrate_legacy_project_context`, `read_projects`, `paused_project_ids`, `save_projects`, `project_background_history`.
- **`ait/project_context.py`** (~245 lines) — all doc-slug/title/file/ensure/load/save/history/map/context-for-role functions.
- **`ait/chat.py`** (221 → ~256 lines) — absorb the 4 chat-history functions (chat domain, avoids a trivial 35-line module).
- **`ait/projects.py`** — deleted.
- **`ait/__init__.py`** — replace `from ait import projects` with the 4 new modules; add them to `_DOMAIN_MODULES` in the same position (after `settings`/`provider_registry`/`secrets`/`prompts`, before `trackers`).

### Dependency notes (all resolved via namespace injection — no imports needed beyond `from ait.common import *`)

- `routing.py` needs `read_tasks`/`read_task`/`write_task` (tasks_store), `provider_names`/`auto_provider_order`/`configured_providers`/`is_active_provider`/`load_model_flags`/`read_app_config`/`save_app_config` — all injected at runtime.
- `project_context.py` needs `read_projects`, `read_tasks`, `project_services_summary` — injected.
- `tasks_store.py` needs `append_event`, `logger`, `now_iso`, `repository_parts` — injected/common.
- `projects_store.py` needs `PROJECT_CONTEXT_FILES`, `clean_project_services`, `make_version_id`, `parse_version_timestamp` — injected/common.
- Import order in `__init__.py` doesn't matter for runtime calls (injection happens after all imports), but keep new modules adjacent to where `projects` was for readability.

## Batches (each leaves app fully functional)

1. **Batch 1 — routing + task storage (v0.133)**
   - Create `ait/routing.py` (routing block + task-route-resolution block) and `ait/tasks_store.py`.
   - Remove moved code from `projects.py` (it keeps projects CRUD + context docs + chat history, ~520 lines).
   - Update `__init__.py`: add both imports + `_DOMAIN_MODULES` entries; keep `projects` import.
   - Verify: `python -m py_compile ait/*.py`, `python -c "import ait"`, spot-check `get_routing`/`route_entry_for_role`/`read_tasks` resolve.

2. **Batch 2 — projects store + context docs + chat history; delete projects.py (v0.134)**
   - Create `ait/projects_store.py` and `ait/project_context.py`.
   - Move chat-history functions into `ait/chat.py`.
   - Delete `ait/projects.py`; update `__init__.py` (remove `projects`, add the two new modules).
   - Verify: same compile/import checks + confirm no remaining references to `ait.projects`.

## Impact analysis

- **New files:** `ait/routing.py` (~285), `ait/tasks_store.py` (~115), `ait/projects_store.py` (~255), `ait/project_context.py` (~245) — all under the 400-line hard limit.
- **Modified:** `ait/__init__.py` (import list + `_DOMAIN_MODULES`), `ait/chat.py` (+~35 lines), `ait/projects.py` (shrinks then deleted).
- **No changes** to any route module, provider module, or frontend code — namespace injection keeps every public name available.
- **Risk:** low-medium. Main hazard is accidentally moving a private helper away from its caller (mitigated: all `_`-helpers stay inside `routing.py`) or a name collision in `_NAMES` (names are unique — they all came from one file). Compile + import smoke test catches wiring errors.
- **Regression surface:** routing save/load, task dispatch provider selection, project CRUD, context document load/save/history, PM/PO context assembly, chat history.

## Post-step protocol (per batch)

Version bump (ABOUT/README/DOCUMENTATION/VERSIONS), commit + push, then next batch.

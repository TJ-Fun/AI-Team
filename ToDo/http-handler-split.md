# `ait/http_handler.py` split — refactoring tracker

Status: **Batch 3 complete** (2026-11-10). Next: Batch 4 — `routes_settings.py`.
Last updated: 2026-11-10

## Goal

Split `ait/http_handler.py` (1,630 lines) into focused route modules without changing behavior. Work stays in small, independently verifiable batches; the app must remain fully functional after each batch.

## Current state

- Single `Handler(BaseHTTPRequestHandler)` class:
  - Helpers: `log_message`, `send_json`, `serve_file`, `read_json_body` (lines 9–52).
  - `do_GET` (54–293, ~240 lines): config, status, connections, routing, projects, git clone job/status, github-files, documentation-files, local-files, context-history, tasks, task replies, prompts, context-documents, notifications, app-update check, provider-quality, llm-usage, settings logs/config, chat, provider models.
  - `do_POST` (294–1369, ~1,076 lines): tasks create, pm-requests, prompts reset, routing, providers CRUD/order/test-model, connections test/github-repositories, llm-usage reset, git status/pull/push/clone, settings logs/log-level/clear/restart/update/config, task status/provider/validation/sync-local/answer/approve-overview/design-review/approve-merge, project context restore, chat.
  - `do_PUT` (1370–1526, ~157 lines): prompts, providers, connections, context-documents, task overview, task, project.
  - `do_DELETE` (1527–1629, ~103 lines): task, provider, connection, context-document, project context-history, chat.
- The package uses a **flat namespace** (`ait/__init__.py` injects all public names into every module's globals), so extracted route functions are callable from `http_handler.py` by bare name with **no import changes** — only registering the new module in `__init__.py`.
- **Name-collision rule**: every extracted function must have a unique name across all domain modules (e.g. `handle_tasks_post`, not `handle_post`), because `_NAMES.setdefault` keeps only the first definition.

## Key design decisions

- **Dispatch pattern**: each route module exports one function per HTTP method, e.g. `handle_tasks_get(handler, path, query) -> bool`, `handle_tasks_post(handler, path, body) -> bool`, `handle_tasks_put(...)`, `handle_tasks_delete(...)`. Each returns `True` after sending a response; `False` when no route matched. `do_GET`/`do_POST`/`do_PUT`/`do_DELETE` call each module in sequence and fall through to the existing 404.
- **Mechanical transform**: inside extracted blocks, `self.send_json(...)` → `handler.send_json(...)`; every `return self.send_json(...)` → `handler.send_json(...); return True`; bare `return` after a response → `return True`.
- **Chat stays in `http_handler.py`**: the `/api/chat` POST block owns `global ACTIVE_CHAT_COUNT` (defined in `common.py:217`). Extracting it would rebind the counter in the new module's globals and break the `chat_working` flag read at `http_handler.py:98`. Chat GET/DELETE are ~15 lines — not worth the shared-state refactor. Documented exception.
- **Static file serving, config/status GET, and the 404 fallthrough stay** in `http_handler.py`.

## Proposed modules (5 new files, justified by domain)

| Module | Routes | ~Lines |
|---|---|---|
| `ait/routes_tasks.py` | task create/status/provider/validation/sync-local/answer/approve-overview/design-review/approve-merge, pm-requests, task GET/replies, task PUT (overview+task), task DELETE | ~600 |
| `ait/routes_projects.py` | project GET, github-files, local-files, context-history, context-documents GET/PUT/DELETE/restore, restore-context, project PUT/DELETE | ~350 |
| `ait/routes_git.py` | git clone job/status GET, git status/pull/push/clone POST | ~120 |
| `ait/routes_settings.py` | settings config/logs/log-level/clear/restart/update, app-update check, notifications, prompts GET/PUT/reset, documentation-files | ~350 |
| `ait/routes_providers.py` | providers CRUD/order/test-model/models, connections CRUD/test/github-repositories, routing, provider-quality, llm-usage GET/reset | ~400 |

`http_handler.py` keeps: class shell + helpers, static file serving, `/api/config`, `/api/status`, `/api/chat` (all methods), dispatch calls, 404 fallthrough. Estimated final size ~150 lines.

## Impact analysis

- **`ait/__init__.py`** — add `from ait import routes_<domain>` for each new module and add them to `_DOMAIN_MODULES`. **Only file that must change** (besides `http_handler.py` itself).
- **All other files** — **no changes**. Route functions call domain functions by bare name via flat-namespace injection (same as today).
- **Frontend / docs** — no changes; API paths and responses are byte-identical.

## Incremental migration plan

Each batch: create the route module, replace that domain's route blocks in `do_GET`/`do_POST`/`do_PUT`/`do_DELETE` with dispatch calls, register the module in `__init__.py`, verify. App must stay functional after every batch.

### Batch 1 — `ait/routes_tasks.py`

- Move all task + pm-requests routes (GET tasks/replies; POST create/pm-requests/status/provider/validation/sync-local/answer/approve-overview/design-review/approve-merge; PUT overview/task; DELETE task).
- Register; replace blocks with `if handle_tasks_get(self, path, query): return` etc.
- Verify: compileall; namespace check; server boot; create/update/delete a task via API.

### Batch 2 — `ait/routes_projects.py`

- Move project + context-document routes (GET project/github-files/local-files/context-history/context-documents; POST restore-context/context-documents restore; PUT context-documents/project; DELETE context-documents/context-history/project).
- Verify: compileall; server boot; project context endpoints.

### Batch 3 — `ait/routes_git.py`

- Move git routes (GET clone job/status; POST status/pull/push/clone).
- Verify: compileall; server boot; `GET /api/git/{id}/status`.

### Batch 4 — `ait/routes_settings.py`

- Move settings/prompts/notifications/app-update/documentation-files routes.
- Verify: compileall; server boot; `GET /api/settings/config`, `GET /api/prompts`.

### Batch 5 — `ait/routes_providers.py`

- Move provider/connection/routing/llm-usage/provider-quality routes.
- Verify: compileall; server boot; `GET /api/provider-quality`, `GET /api/connections`.

### Batch 6 — docs & version

- Update `documentation/VERSIONS.md`, `README.md`, `documentation/ABOUT.md`, `documentation/DOCUMENTATION.md`; bump version; mark tracker complete.

## Verification commands

```powershell
python -m compileall -q ait server.py
python -c "import ait; print(hasattr(ait.routes_tasks, 'handle_tasks_post'))"
# server smoke: start server.py, check /api/status and domain endpoints, then stop
```

## Resume after interruption

1. `git status --short`; inspect current diff.
2. Read this file; continue from the first unchecked batch.
3. Finish or revert a partially-done batch before starting the next.

## Progress checklist

- [x] Batch 1 — `routes_tasks.py` (moved task + pm-requests routes for GET/POST/PUT/DELETE; registered in `ait/__init__.py`; verified compileall + namespace resolution + server boot; `/api/status`, `/api/tasks`, `/api/tasks/{id}/replies` smoke-tested)
- [x] Batch 2 — `routes_projects.py` (moved project + context-document routes for GET/POST/PUT/DELETE; registered in `ait/__init__.py`; verified compileall + namespace resolution + server boot; `/api/projects` smoke-tested)
- [x] Batch 3 — `routes_git.py` (moved git clone job/status GET and git status/pull/push/clone POST; registered in `ait/__init__.py`; verified compileall + namespace resolution + server boot; `/api/git/{id}/status` smoke-tested)
- [ ] Batch 4 — `routes_settings.py`
- [ ] Batch 5 — `routes_providers.py`
- [ ] Batch 6 — docs & version

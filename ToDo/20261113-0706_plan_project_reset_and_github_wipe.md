# Plan: per-project task reset + GitHub contents wipe

Status: **Pending approval**

## Goal

Add two destructive, strictly per-project actions in **Settings → Projects**:

1. **Clear project tasks** — delete every task belonging to one project, plus that project's task replies and activity-log entries. Keeps chat history, project context documents/history, project config, modules, and the local clone.
2. **Wipe GitHub contents** — empty the project's GitHub repository contents and delete the local clone, while keeping the repository object, project `repository` URL, `local_path`, modules, tasks, chat, and context docs.

## Confirmed scope / constraints

- Both actions are per `project_id`; they must never touch other projects. Tasks with missing/empty `project_id` count as `default` only.
- **Clear project tasks** deletes: `data/tasks/{id}.json` (+ `.bak`/`.tmp`), `data/replies/{id}/`, and `data/logs/activity.jsonl` lines for those task ids. Progress is derived from tasks, so it clears automatically.
- **Wipe GitHub contents** does **not** delete the GitHub repository itself. GitHub does not allow deleting the default branch, so the wipe will:
  - reset the default branch to a single empty orphan commit (no files/history),
  - delete all other branches,
  - delete all tags,
  - delete all releases.
  - Issues, pull requests, Actions data, and repository settings are **not** touched.
- Local clone: delete `local_path` only if it contains `.git`; keep the configured `local_path` value so the project can be re-cloned. Refuse if the path is unsafe (filesystem root, home, `ROOT`, or `DATA`).
- Safety gates for both actions: project must exist; no `In progress` tasks for that project; no running clone job for that project. Wipe also refuses if the local clone has uncommitted changes or unpushed commits, and if another project uses the same canonical repository.
- UI confirmation: Clear tasks uses a danger confirm. Wipe requires typing the exact `owner/repo` name; backend verifies `confirm_text`.

## Current structure

- Project settings UI: `static/js/project-settings.js` (329 lines) renders one editor per project and already has Pull/Push/Clone buttons.
- Project routes: `ait/routes_projects.py` (201 lines) handles `/api/projects*`.
- Task delete logic to reuse: `ait/routes_tasks_core.py:169-199`.
- GitHub API helper: `ait/git_github_api.py` (`github_api`, `repository_parts`, `canonical_github_repository`).
- Local git status/clone jobs: `ait/git_local_ops.py`, `CLONE_JOBS`/`CLONE_TARGETS` in `ait/common.py`.
- In-flight task risk: late provider replies can recreate `data/replies/{id}/` if a task is deleted while running (`ait/provider_dispatch.py:206-232`), hence the `In progress` block.

## Target structure

| File | Contents | Est. lines |
|---|---|---|
| `ait/project_cleanup.py` | Per-project guards, task-id selection, task/reply/activity deletion, local-clone deletion, orchestration for both actions | ~220 |
| `ait/github_repo_wipe.py` | GitHub contents wipe: empty orphan commit on default branch, delete other branches/tags/releases | ~160 |
| `ait/routes_projects.py` | Add `POST /api/projects/{id}/clear-tasks` and `POST /api/projects/{id}/wipe-github` | ~245 |
| `ait/__init__.py` | Import/register the two new modules | +2 |
| `static/js/project-settings.js` | Add two danger buttons per project editor + confirm/API handlers | ~390 (still <400; extract to `project-actions.js` if it grows) |

No pre-refactoring batch is required; `project-settings.js` stays under the 400-line hard limit by keeping the new handlers compact.

## Batches

### Batch 1 — Clear project tasks backend → v0.78
- Create `ait/project_cleanup.py` with `project_task_ids`, `clear_project_tasks`, shared guards (`get_project_or_raise`, `assert_no_active_project_work`).
- Register module in `ait/__init__.py`.
- Add `POST /api/projects/{pid}/clear-tasks` in `routes_projects.py`; body requires `{"confirm": true}`.
- Verify: `python -m compileall ait`, package import, disposable `TEAM_DATA_DIR` smoke test with two projects proving only the target project's tasks/replies/activity are removed.
- Docs → 0.78 → commit + push.

### Batch 2 — GitHub contents wipe backend → v0.79
- Create `ait/github_repo_wipe.py` with paginated branch/tag listing, ref deletion, release deletion, empty-tree/orphan-commit creation, and default-branch force update.
- Extend `ait/project_cleanup.py` with `wipe_project_github` (duplicate-repo guard, clean-clone check, remote wipe, local clone deletion).
- Add `POST /api/projects/{pid}/wipe-github`; body requires `{"confirm_text": "owner/repo"}`.
- Verify: compileall/import; smoke test against a disposable GitHub repo if credentials allow, otherwise unit-level checks of request paths/payloads with mocked `github_api`.
- Docs → 0.79 → commit + push.

### Batch 3 — Settings UI buttons → v0.80
- In `renderProjectEditors`, add a danger-actions row with **Clear tasks** and **Wipe GitHub contents** buttons for saved projects (`d.id`).
- Wire click handlers: danger confirm for clear tasks; type-to-confirm `owner/repo` for wipe; call `refreshBoard()` after success; show returned counts/errors.
- Verify: compileall, JS syntax check, manual UI smoke in Settings → Projects.
- Docs → 0.80 → commit + push.

## Impact analysis

- `ait/__init__.py` — add imports and `_DOMAIN_MODULES` entries for `project_cleanup` and `github_repo_wipe`.
- `ait/routes_projects.py` — two new POST routes only; existing routes unchanged.
- `static/js/project-settings.js` — additive buttons/handlers; existing save/pull/push/clone flows unchanged.
- `static/style.css` — reuse existing `.danger` / `.git-controls`; add a small `.project-danger-actions` rule only if needed.
- `documentation/ABOUT.md` + `documentation/VERSIONS.md` — version bumps per batch.
- No changes to task dispatch, provider workers, chat, or project context storage.

## Risks / notes

- GitHub wipe is irreversible for repository contents; protected branches or branches with open PRs may refuse deletion. The endpoint will return the collected errors and will not delete the local clone unless the remote wipe reports no errors.
- The configured GitHub token needs permission to write git data and delete releases/refs; otherwise the API returns a clear error.
- Deleting the local clone is guarded by `.git` presence and a clean/ahead check to avoid losing uncommitted or unpushed work.

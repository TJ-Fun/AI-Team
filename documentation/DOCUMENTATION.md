# Personal AI Team — Project Documentation

Version 0.50 · Updated 2026-11-10

Repository: [https://github.com/TJ-Fun/AI-Team](https://github.com/TJ-Fun/AI-Team)

## 1. Overview

Personal AI Team is a self-hosted web application for managing a project task board and coordinating AI-assisted work. It has a Python standard-library HTTP server, a browser interface written in HTML/CSS/JavaScript, a provider-backed PM/PO chat, configurable external AI providers, and optional GitHub repository operations.

The application is intended for a trusted, single-user environment. The default listener is `127.0.0.1:8765`; it does not provide user accounts or an authentication layer. Keep it bound to loopback or put it behind an appropriately authenticated reverse proxy. Do not expose the port directly to the public internet.

## 2. Repository map

| Path | Purpose |
|---|---|
| `server.py` | Thin entry point; delegates to the `ait` package. Kept for the systemd unit and existing deployment commands. |
| `ait/` | Application package split from the former monolithic `server.py`. `common.py` holds constants, logging, locks, and shared helpers; `settings.py`, `provider_registry.py`, `secrets.py`, `prompts.py`, `projects.py`, `git_ops.py`, `notifications.py`, `providers.py`, `chat.py`, and `http_handler.py` hold the domain logic; `main.py` starts background workers and the HTTP server. The package shares one flat namespace across modules, mirroring the original single-file layout. |
| `static/index.html` | Application layout, dialogs, forms, and tab structure. |
| `static/app.js` | ES module entry point for browser behavior, API calls, rendering, polling, form submissions, and dialogs. |
| `static/js/` | Shared frontend modules; `core.js` holds state, API, dialog, update-progress, and formatting helpers, and feature modules such as `prompts.js`, `project-context.js`, `repository-viewer.js`, `header.js`, `providers.js`, and `project-settings.js` are initialized by `app.js`. |
| `static/style.css` | Application styling and responsive layouts. |
| `personal-ai-team.service` | Example systemd unit for the `ubuntu` service account. |
| `prompts/` | Built-in shared and role-specific agent prompts. |
| `project_templates/` | Starter documents for project briefs, technical overviews, decisions, and outcomes. |
| `documentation/` | Local documentation, About information, and version history shown by the UI. |
| `requirements.txt` | Python dependency declaration (`cryptography`). |
| `README.md` | Short repository and deployment notes. |

## 3. Architecture and data

The server uses Python's `ThreadingHTTPServer`; the frontend is served from `static/`. The browser communicates with JSON endpoints under `/api/`. Long-running project Git clones are handled as background jobs so the browser can poll for progress and completion without holding the request open.

`TEAM_DATA_DIR` sets the writable application data root. The default is `data/` beside `server.py`; the supplied systemd unit sets it to `/var/lib/personal-ai-team`. The server stores tasks, replies, projects, prompts, per-project context documents and history, chat history, credentials, usage/reset records, activity events, and rotating application logs there. Data is separate from application source so app updates do not replace user content.

Saved credentials are encrypted with Fernet when the `cryptography` dependency is available. The encryption key is generated under `TEAM_DATA_DIR/settings/secret.key` with owner-only permissions. Back up the data directory and key together; losing the key makes saved encrypted credentials unreadable. Provider keys may alternatively be supplied using environment variables such as `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `OMNIROUTE_API_KEY`, `NINEROUTER_API_KEY`, and `GITHUB_TOKEN`.

The supplied service runs as the unprivileged `ubuntu` user, uses `ProtectSystem=strict` and `ProtectHome=read-only`, and grants write access only to listed locations. Unix ownership and systemd sandbox rules both apply.

## 4. Main workflows

### 4.1 Projects and board

1. Select the active project from the selector on the task board. The selector remains outside Settings because switching projects is a frequent board action.
2. Use **Settings → Projects** to add or edit project definitions, repository URLs, local checkout paths, and sub-projects. Use **Project context** on the board to edit that project's durable documents.
3. Use **New task** to create a draft with a role, provider preference, project/sub-project, request, and optional acceptance checks.
4. User-created tasks remain drafts until reviewed and marked Ready. By default, PM/PO-proposed work tasks are created as drafts and released when the user approves the project overview; Settings → Routing → Autonomy can disable this plan-approval gate. Project-overview documents still require user approval. After plan approval, dispatch, QA, repository merge, and guarded local sync continue without further user action unless an autonomy gate or failure requires attention. Deliverable work requires acceptance checks before it can enter the agent queue.
5. Follow status, attempts, agent replies, validation results, and activity from the board/task details. Browser polling refreshes board and runtime status. The notification center surfaces blocked/rework/capacity/CI/GitHub/local-sync/provider issues plus PM/PO decisions (clarification answers, project-overview and design-review approvals) and merge approvals; task notifications open the related task detail.

### 4.2 PM/PO chat

The chat panel is a direct conversation with the PM/PO agent, running on a hosted provider selected in the chat header dropdown (also configurable in **Settings → Routing → Chat window**; Gemini, GroqCloud, OpenRouter, OmniRoute, 9Router, or Nvidia). When a specific provider is selected, a second dropdown next to it overrides the model used for chat only (stored per provider in app configuration); **Auto** uses each provider's configured model. Each project has its own chat history, and every message includes the active project's context documents and task board, so conversations stay scoped to the selected project. Chat replies are advisory; use the **Plan** button to send the current message to PM/PO planning (`/api/pm-requests`). PM/PO may ask clarification questions, propose phased backlogs, or escalate blocked work with design-change proposals and alternatives. Planned work is grouped into phases; later phases start only after earlier phases complete, and plans that produce deliverables should include Documentation tasks per phase. Project-overview documents require user approval before replacing Project context. Escalation responses must include a design change with executable provisional team tasks and at least one alternative, plus a design review; depending on autonomy settings those tasks may run during review or wait as drafts until approval. Approving a design review confirms the provisional tasks and cancels the superseded blocked source task; rejecting it or choosing an alternative cancels unfinished provisional tasks and queues a follow-up PM/PO escalation with the user's feedback so PM/PO can plan the chosen solution. Edit a project's durable context documents from **Project context** on the Project Board. Agent prompts are available in **Settings → Agent prompts** and are retained in application data. PM/PO plans must follow the JSON schema supplied with planning requests; unknown sub-project IDs are safely assigned to the whole project rather than rejecting the entire plan.

### 4.3 Provider connections and routing

1. Open **Settings → Connections** to manage the provider list. Providers can be added from a built-in preset (Gemini, GroqCloud, OpenRouter, OmniRoute, 9Router, Nvidia) or as a custom OpenAI-compatible endpoint (name, base URL, model), removed, and reordered with the up/down arrows. The list order is the default routing order for all agents. Each provider row also has a Model dropdown populated from the provider's own model list (Gemini's model API or the OpenAI-compatible `/models` endpoint); selecting a model saves it for that provider, and OpenAI-compatible providers offer an **Auto** model option. Providers whose endpoint cannot list models show their configured model read-only. The **Test model** button next to each Model dropdown sends a minimal "Hi" prompt to the selected model (`POST /api/providers/{id}/test-model`) and shows the reply in a dialog, confirming the model actually responds. Credentials are added, replaced, tested, or cleared per provider; the GitHub repository connector is listed separately and is not a task provider.
2. Open **Settings → Routing** to set up to four preferred providers per work role and to choose the provider used by the PM/PO chat (including **Auto**). **Auto** is also accepted as a preferred-provider slot: it expands to every active provider ordered by audit score. Unavailable providers are skipped by the dispatcher. The QA role uses its own routing order.
3. When a task's preferred providers are exhausted or a task is set to **Auto**, the dispatcher picks the best provider by audit score (quality outcomes aggregated from task attempts); ties and missing scores fall back to the Connections list order. Provider workers start and stop automatically as providers are added or removed.
4. A task can request automatic selection or a specific provider. Provider attempts and quality outcomes feed the Statistics view. Tasks entering **Needs validation** are sent to QA automatically. Every agent reply must follow a strict format before the app accepts it: PM/PO planning returns the planning JSON schema, QA returns a pass/fail JSON review, repository tasks return a file-change manifest, and all other roles return a JSON envelope with `summary` and `details` fields (malformed replies are rejected and the task falls back to another provider or blocks). QA reviews the acceptance checks, original agent reply, and available repository evidence without modifying files or claiming to run tests. A failed review returns the task to its original work role automatically. After three failed QA reviews the task becomes **Blocked** and is auto-escalated to PM/PO; manually returning it to Ready resets the QA-failure count. Task creation and status transitions are always written to `TEAM_DATA_DIR/logs/activity.jsonl` and to `app.log` when the configured capture level includes INFO. Agent reply text is saved under `TEAM_DATA_DIR/replies/<task_id>/<provider>/` and shown in task details; `app.log` records when that reply file is saved, not the full reply body.

The Routing tab also contains **Autonomy** controls: require approval before PM-planned work tasks start (enabled by default and enforced by project-overview approval, which releases the drafted tasks), allow provisional escalation tasks to run during design review (enabled by default), and require approval before merging validated PRs (disabled by default). These settings are stored in app configuration and enforced by the dispatcher, escalation handler, and GitHub merge post-processing.

### 4.4 GitHub repository work

Projects can point to GitHub repositories and local checkout folders. The GitHub connection configured in Connections is shared by repository listing, clone, pull, push, repository reads, and app updates. Clone operations expose progress and actionable Git errors in the UI/logs. Local Files and Documentation browsing is read-only.

For code and documentation tasks, the app creates a task/attempt branch from the exact repository commit the agent's source snapshot was taken from (falling back to the current default-branch head), commits provider-supplied file manifests where supported, and opens a PR targeting that default branch. Gemini, GroqCloud, OpenRouter, OmniRoute, 9Router, and Nvidia receive a limited source snapshot fetched from GitHub; the app applies their returned file manifest. The manifest may include an optional `feedback` field for blockers or scope concerns; it is stored on the task, logged in activity, and appended to the pull request body. PM/PO planning receives saved project context and board information. No agent reads from or edits the configured local checkout. Repository-changing work and QA are serialized per repository, and eligible work is selected oldest-first within that repository; unrelated repositories may run concurrently. QA reviews the current PR head using a bounded GitHub source snapshot (up to 32 files and 48 KB) plus the original agent response. The app verifies that the reviewed PR head has not changed before merging. A changed PR is queued for another QA pass. The app merges only after QA passes unless merge-approval autonomy is enabled; in that case the task moves to **Needs approval** and waits for the **Approve merge** action. Before merging, the app checks the pull request's GitHub Actions check runs: while any check is still running the merge waits (the task shows a pending CI status), a failed check blocks the merge and raises a notification, and repos without CI merge as before. A background poller re-checks open pull requests every 90 seconds so merges complete automatically once checks turn green.

After a validated PR is merged, the app synchronizes the configured local checkout to the default branch. If the checkout does not exist, the app clones it to a staging directory and installs it only after a successful clone. Existing checkouts are updated only when the GitHub origin matches, the checked-out branch is the repository default branch, the worktree is clean, and a fast-forward is possible. Dirty, divergent, branch-mismatched, or wrong-origin checkouts are left untouched; the task receives a local-sync error notification for user resolution. After resolving the issue, use **Retry local checkout sync** in that task's details to retry the same guarded operation. A failed local sync does not undo a successful GitHub merge. No local sync occurs before QA approves and the PR merges.

Use the configured service user's permitted paths. The example service grants project work under `/home/ubuntu/projects`; it does not grant unrestricted filesystem access.

### 4.5 App updates

The app checks the configured app repository for a newer version (parsed from `documentation/ABOUT.md`) at most every 30 minutes. When a newer version is found, a low-severity notification with an **Update now** button appears in the notification center; clicking it runs the same guarded update flow as **Settings → About → Update App** using the saved configuration. The check is also available on demand via `GET /api/app-update/check` (add `?force=1` to bypass the cache).

The **Settings → About** section contains application update controls:

1. Configure the repository URL and a local checkout directory, then save the configuration.
2. On first update, the app clones to the checkout; later updates require the checkout's Git origin to match the configured repository and normally use a fast-forward-only pull. If that pull cannot fast-forward because the remote branch was force-updated or diverged, the updater fetches `origin` and hard-resets the dedicated checkout to its tracked remote branch before copying files.
3. The updater copies repository files into the configured app working directory. For each file, the newer modification time wins. It skips `.git`, virtual environments, caches, and `node_modules`; it does not delete deployment-only files.
4. After the update response is sent, the process exits and systemd restarts it. The browser waits for the service to answer before reloading.

The service must have both systemd write permission (`ReadWritePaths`) and Unix write permission for the checkout and app working directory. Repository and working directories must not overlap. The app updater does not elevate privileges.

### 4.6 Logs and Statistics

**Settings → Logs** lets an operator set the minimum level stored, filter displayed levels, load recent log entries, and clear logs. Logs are written to `TEAM_DATA_DIR/logs/app.log` and rotated daily with seven backups.

The Settings dialog uses Projects, Connections, Routing, Logs, and About tabs. A shared Close button remains at the bottom of the dialog. Long log and documentation lines wrap within their viewers to avoid horizontal scrolling.

**Statistics** is available directly from the header. Its period selector filters provider request, successful reply, and average response-time summaries. Deliverable QA pass rates are shown from recorded provider quality results, with QA attempts attributed to the QA role. Usage reset changes a display baseline for the selected provider/model and period; it does not delete historical records. Board polling refreshes the open task detail only when that task changes and restores focus and scroll position after the update.

## 5. Installation and deployment (Ubuntu/Oracle Cloud)

The repository includes an example `personal-ai-team.service`; adapt it for the actual host, storage, network access, and Python installation. The commands below document a reproducible installation path, not a complete record of every historical command run on a particular Oracle server. The repository does not contain that host's shell history or reverse-proxy configuration.

### 5.1 Install system prerequisites

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
```

### 5.2 Create the service account and paths

The supplied unit expects an existing `ubuntu` account. Create the writable directories and make the application source owned by that account so the updater can write to it:

```bash
sudo install -d -o ubuntu -g ubuntu -m 0750 /opt/personal-ai-team
sudo install -d -o ubuntu -g ubuntu -m 0750 /var/lib/personal-ai-team
sudo install -d -o ubuntu -g ubuntu -m 0750 /home/ubuntu/projects
```

Clone the repository into the app directory as the service user (or copy a trusted checkout there):

```bash
sudo -u ubuntu git clone https://github.com/TJ-Fun/AI-Team.git /opt/personal-ai-team
```

Create and populate a virtual environment:

```bash
sudo -u ubuntu python3 -m venv /opt/personal-ai-team/.venv
sudo -u ubuntu /opt/personal-ai-team/.venv/bin/python -m pip install --upgrade pip
sudo -u ubuntu /opt/personal-ai-team/.venv/bin/python -m pip install -r /opt/personal-ai-team/requirements.txt
```

For a virtual-environment deployment, edit the unit's `ExecStart` to:

```ini
ExecStart=/opt/personal-ai-team/.venv/bin/python /opt/personal-ai-team/server.py
```

If retaining the unit's `/usr/bin/python3`, install the declared Python dependencies for that interpreter using the host's supported package-management method.

### 5.3 Configure environment and secrets

The supplied unit reads optional settings from `/etc/personal-ai-team.env`. Create it with restrictive permissions and set only the keys needed on this host. Example:

```bash
sudo install -o root -g ubuntu -m 0640 /dev/null /etc/personal-ai-team.env
sudoedit /etc/personal-ai-team.env
```

Recognized provider key names include `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `OMNIROUTE_API_KEY`, `NINEROUTER_API_KEY`, `NVIDIA_API_KEY`, and `GITHUB_TOKEN`. Alternatively, credentials can be entered in **Settings → Connections** and stored encrypted in the app data directory. Do not put real keys in Git, documentation, shell history, or support logs.

The service defaults the HTTP port to `8765`. The Orchestrator chat uses one of the configured hosted providers; no local model runtime is required. The server defaults to listening on loopback; configure any reverse proxy separately and require authentication before allowing remote access.

### 5.4 Install and start systemd service

Install the included unit after reviewing its paths and `ExecStart`:

```bash
sudo install -o root -g root -m 0644 /opt/personal-ai-team/personal-ai-team.service /etc/systemd/system/personal-ai-team.service
sudo systemctl daemon-reload
sudo systemctl enable --now personal-ai-team
sudo systemctl status personal-ai-team --no-pager
```

The example unit contains these key settings:

```ini
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/personal-ai-team
Environment=TEAM_DATA_DIR=/var/lib/personal-ai-team
ExecStart=/usr/bin/python3 /opt/personal-ai-team/server.py
Restart=always
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/var/lib/personal-ai-team /home/ubuntu/projects /opt/personal-ai-team
```

The `ReadWritePaths` line is an explicit systemd sandbox allowlist; Unix directory ownership and modes still control access. After changing the unit, always reload and restart it. Verify the active restrictions with:

```bash
sudo systemctl show personal-ai-team -p ProtectSystem -p ProtectHome -p ReadWritePaths
sudo -u ubuntu test -w /var/lib/personal-ai-team && echo "data directory writable"
sudo -u ubuntu test -w /opt/personal-ai-team && echo "app directory writable"
sudo -u ubuntu test -w /home/ubuntu/projects && echo "project workspace writable"
```

The following service-install and reload commands were provided during the Oracle deployment/update troubleshooting for the existing checkout at `/home/ubuntu/projects/personal-ai-team`:

```bash
sudo install -o root -g root -m 0644 \
  /home/ubuntu/projects/personal-ai-team/personal-ai-team.service \
  /etc/systemd/system/personal-ai-team.service
sudo systemctl daemon-reload
sudo systemctl restart personal-ai-team
sudo systemctl show personal-ai-team -p ProtectSystem -p ReadWritePaths
```

These are the deployment commands recorded in the project guidance, not confirmation that each command was run successfully on the host. The server's full shell history was not available when this documentation was prepared.

If the updater reports `Read-only file system`, install the updated unit at `/etc/systemd/system/personal-ai-team.service`, then run:

```bash
sudo systemctl daemon-reload
sudo systemctl restart personal-ai-team
sudo systemctl show personal-ai-team -p ProtectSystem -p ReadWritePaths
```

Confirm `/opt/personal-ai-team` is in the active `ReadWritePaths`. Changing `chmod`/`chown` cannot override a read-only systemd mount. Conversely, adding a path to `ReadWritePaths` does not fix filesystem ownership or mode errors.

### 5.5 Update an installation

Use **Settings → About → App Update** after configuring the app repository and checkout folder. For manual deployment, fetch a trusted revision and copy/restart only after reviewing the diff. Updating the service unit on disk does not automatically reload systemd; run `daemon-reload` and restart as above when the unit changes.

### 5.6 Logs and diagnostics

```bash
sudo systemctl status personal-ai-team --no-pager
sudo journalctl -u personal-ai-team -n 200 --no-pager
sudo tail -n 200 /var/lib/personal-ai-team/logs/app.log
```

The app's rotating file log and the systemd journal contain different information: use the journal for process startup, sandbox, and restart failures, and the app log for application/API/Git diagnostics.

## 6. Configuration reference

| Setting | Default / source | Purpose |
|---|---|---|
| `TEAM_HOST` | `127.0.0.1` | HTTP listen address. |
| `TEAM_PORT` | `8765` | HTTP listen port. |
| `TEAM_DATA_DIR` | `data/` beside the server | Persistent data, encrypted secrets, and logs. |
| `GEMINI_API_KEY` | optional | Gemini provider credential. |
| `GROQ_API_KEY` | optional | GroqCloud credential. |
| `OPENROUTER_API_KEY` | optional | OpenRouter credential. |
| `OMNIROUTE_API_KEY` | optional | OmniRoute gateway credential. |
| `NINEROUTER_API_KEY` | optional | 9Router gateway credential. |
| `NVIDIA_API_KEY` | optional | Nvidia NIM (build.nvidia.com) credential. |
| `GITHUB_TOKEN` | optional | GitHub repository connector credential. |
| `OMNIROUTE_MODEL` / `NINEROUTER_MODEL` | `auto` | Model or combo name sent to the gateways. |
| `NVIDIA_MODEL` | `meta/llama-3.3-70b-instruct` | Model name sent to the Nvidia NIM endpoint. |
| `OMNIROUTE_BASE_URL` / `NINEROUTER_BASE_URL` | `http://localhost:20128/v1` / `http://localhost:20129/v1` | OpenAI-compatible gateway endpoints. |
| `NVIDIA_BASE_URL` | `https://integrate.api.nvidia.com/v1` | Nvidia NIM OpenAI-compatible endpoint. |

Additional model identifiers and user-agent values can be configured through the corresponding environment variables in `ait/common.py`.

## 7. Operational and security notes

- Run as an unprivileged service account; do not run the app as root.
- Keep the listener private or add a trusted authenticated reverse proxy. The application itself does not authenticate users.
- Restrict systemd writable paths to the app data, intended project checkout workspace, and app installation directory.
- Protect `/etc/personal-ai-team.env`, the generated Fernet key, and backups containing application data.
- Use an adequately scoped GitHub token. The app uses the configured credential for GitHub operations and avoids embedding tokens in remote URLs.
- App updates copy files; they are not a transactional deployment or rollback system. Back up application source and data before major changes.
- Confirm actual deployment paths, service user, Python executable, chat provider selection, and proxy settings before applying commands to a production Oracle host.

## 8. Development and verification

Run locally with Python:

```bash
python3 -m pip install -r requirements.txt
python3 server.py
```

Open `http://127.0.0.1:8765`. For a custom data directory, set `TEAM_DATA_DIR` before starting. The server creates required data subdirectories on startup.

Useful checks after changing server or frontend code:

```bash
python3 -m py_compile server.py ait/*.py
for f in static/app.js static/js/*.js; do node --check "$f"; done
```

There is no dedicated automated test suite declared in the repository. Exercise project/task flows, API credentials, clone failure reporting, app update copy behavior, and mobile layouts against a non-production test installation before deployment.

## 9. Known limitations

- No user accounts or built-in authentication; intended for one trusted operator.
- No built-in reverse proxy, TLS, firewall, or Oracle Cloud security-list provisioning.
- UI documentation and local file browsing are read-only.
- App updates preserve files that are newer by modification time and do not delete files absent from Git; this can retain obsolete deployment files.
- The repository does not record every command historically run on an Oracle server. Use the deployment commands above as a reviewed procedure, then maintain host-specific changes in a protected operations runbook.

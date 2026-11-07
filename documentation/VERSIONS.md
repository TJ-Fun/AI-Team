# Version log

## 0.38 — 2026-11-07

- Switched the browser interface to a dark theme: dark surfaces, borders, form controls, dialogs, chat, statistics, and state badges, with `color-scheme: dark` for native controls and scrollbars.
- Updated documentation to 0.38.

## 0.37 — 2026-11-07

- Added OmniRoute and 9Router as AI providers. Both are self-hosted OpenAI-compatible gateways; the app talks to them through configurable base URLs (`OMNIROUTE_BASE_URL`, `NINEROUTER_BASE_URL`, defaulting to `http://localhost:20128/v1` and `http://localhost:20129/v1`) and model names (`OMNIROUTE_MODEL`, `NINEROUTER_MODEL`, defaulting to `auto`).
- New connection families `omniroute` and `ninerouter` with `OMNIROUTE_API_KEY` / `NINEROUTER_API_KEY` credentials, connection tests against each gateway's `/models` endpoint, and per-provider activity counters exposed in `/api/status`.
- The OpenAI-compatible provider path was generalized into a provider config table shared by task dispatch and PM/PO chat; gateways participate in automatic routing, fallback, statistics, and repository manifest tasks like the existing providers.
- Updated documentation to 0.37.

## 0.36 — 2026-11-07

- Refined all default agent prompts to match the enforced system contracts: PM/PO planning and escalation limits are now stated explicitly, and the JSON-schema requirement is scoped to planning requests so it no longer conflicts with chat mode.
- Frontend, Backend, and Documentation prompts now describe the repository file-change manifest workflow, including the 20-file limit, accepted text extensions, protected lock files, editable scope, and the optional `feedback` field.
- Repository agents can now raise PM/PO feedback through an optional `feedback` field in the file-change JSON manifest; it is stored on the task, logged in activity, and included in the pull request body.
- QA prompt now warns that repository snapshots may be partial and that self-reported checks are claims, not evidence.
- Shared rules now route repository-task feedback through the manifest field instead of a `PM/PO feedback` section.
- UI designer prompt welcomes concrete HTML/CSS snippets for implementation-detail tasks.
- Updated documentation to 0.36.

## 0.35 — 2026-11-07

- Fixed provider activity accounting: GroqCloud dispatches were counted as OpenRouter work; each provider now has its own busy counter and `/api/status` exposes `groq_working`.
- The status bar now shows "GroqCloud working" when a GroqCloud task is in flight.
- Provider worker loops now log dispatch exceptions instead of failing silently.
- `automatic_retries` resets after a successful agent reply, so recovered tasks no longer hit the retry limit prematurely.
- Secret key generation is now guarded by a lock to avoid a first-start race.
- Updated documentation to 0.35.

## 0.34 — 2026-11-07

- Split the monolithic `server.py` (~5,900 lines) into the `ait` package: `common.py` (constants, logging, locks, shared helpers), `settings.py`, `secrets.py`, `prompts.py`, `projects.py`, `git_ops.py`, `notifications.py`, `providers.py`, `chat.py`, `http_handler.py`, and `main.py`.
- `server.py` remains a thin entry point so the systemd unit and existing deployment commands keep working unchanged.
- The package shares one flat namespace across modules, mirroring the original single-file layout; provider busy flags are now read through `provider_activity_status()` so the status API reports live values.
- Updated documentation to 0.34.

## 0.33 — 2026-11-07

- Made plan approval the default gate: PM-planned work tasks are created as drafts and released by project-overview approval unless autonomy disables it.
- Tightened PM escalation design reviews: a design change must include executable provisional team tasks and at least one alternative.
- Approving a design review now cancels the superseded blocked source task automatically; reject/alternative still cancels unfinished provisional tasks and queues PM/PO round 2.
- Updated documentation to 0.33.

## 0.32 — 2026-11-07

- Approving a PM/PO project overview now releases that plan's draft work tasks into the Ready queue, making overview approval the single plan gate when plan approval is required.
- Clarified autonomy copy: plan approval is enforced by the project-overview approval step.
- Updated documentation to 0.32.

## 0.31 — 2026-11-07

- Added a chat **Plan** button that sends the current message to PM/PO planning (`/api/pm-requests`); chat replies remain advisory.
- Added decision notifications for PM/PO clarification questions, project-overview and design-review approvals, and merge approvals; task notifications can open the related task.
- Started the CI/milestone poller so pending-CI merges and milestone notifications run every 90 seconds.
- Closed the escalation loop: escalation team tasks inherit phase/module/planning metadata, design-review approval queues drafted provisional tasks, and the blocked source task records review outcomes.
- Added autonomy controls in Settings → Routing for plan approval, provisional escalation tasks, and merge approval.
- Updated documentation to 0.31.

## 0.30 — 2026-11-07

- Added periodic app self-update checks (every 30 minutes) against the configured repository; a newer version raises a notification with an Update now button that runs the standard update flow.
- Made the notification badge neutral ("All clear" when empty, total count otherwise) and added a per-kind breakdown (issues vs updates) in the notification dialog.
- Added `GET /api/app-update/check` for on-demand update status.

## 0.29 — 2026-11-07

- Added strict reply envelopes for all remaining agent roles; malformed replies are rejected and fall back to another provider.
- PR branches are now based on the exact repository commit the agent's source snapshot was taken from, preventing stale-snapshot overwrites.
- Added GitHub Actions check-run gating: merges wait for pending checks, failed checks block the merge with a notification, and a 90-second poller completes merges automatically once checks pass.
- Removed the unused Orchestrator prompt; the chat is PM/PO-only.
- Documented existing concurrency limits (one task per provider, serialized per repository) and attempt timeouts.

## 0.28 — 2026-11-07

- Turned the chat panel into a direct PM/PO agent conversation with a provider dropdown in the chat header.
- Removed the Send to PM/PO button; planning requests are now handled conversationally and tasks are created from the board.
- Fit all six Settings tabs on one row with smaller tab styling.
- Updated the documentation for the PM/PO chat.

## 0.27 — 2026-11-01

- Removed the local Ollama runtime; the Orchestrator chat now runs on a hosted provider (Gemini, GroqCloud, or OpenRouter) selected in Settings → Routing → Chat window.
- Chat history is stored per project, and every chat request includes the active project's context documents and task board so conversations stay scoped to the selected project.
- Removed the Antigravity provider entirely; Gemini, GroqCloud, and OpenRouter remain for agent tasks, with legacy Antigravity task selections treated as Automatic.
- Added project-scope isolation instructions to the shared agent rules and Orchestrator prompt.
- Updated the documentation for the hosted chat provider and Antigravity removal.

## 0.26 — 2026-11-01

- Automatically queued valid PM/PO work tasks while keeping proposed project overviews behind user approval.
- Added automatic QA review for deliverables in Needs validation, routed through the QA provider settings.
- Returned failed QA reviews to the original work role and blocked tasks after three unsuccessful QA reviews for user intervention.
- Serialized repository-changing tasks and QA per GitHub repository, prioritized QA before new work in that repository, and reviewed the exact PR head before merge.
- Fast-forwarded the configured local checkout after a validated PR merge only when origin, default branch, and clean-worktree checks pass; added actionable local-sync notifications and a guarded task-detail retry action.
- Updated task-detail polling to rerender only after task changes while restoring focus and scroll position.
- Updated QA instructions, workflow documentation, and About version to 0.26.

## 0.25 — 2026-11-01

- Accept valid PM/PO plans with unknown sub-project IDs by assigning those tasks to the whole project, and report precise schema validation errors.
- Clarified PM/PO response schema and sub-project ID instructions; aligned shared agent response guidance with task-provided output formats.
- Added notifications for tasks in Rework needed.
- Logged every task creation and status transition in the activity event log and application log; logged when agent reply files are saved.
- Updated the documentation for task logging, project-specific context, PM/PO plans, and the Agent prompts settings tab.
- Updated the About version to 0.25.

## 0.24 — 2026-11-01

- Removed duplicate Close settings actions from the Projects, Connections, and Routing tabs; the shared Settings Close action remains.
- Wrapped log, version-log, and documentation text to prevent horizontal overflow.
- Removed the unused file-list column from the About document viewer.
- Aligned the Update App and Restart App buttons.
- Updated the About version to 0.24.

## 0.23 — 2026-11-01

- Reorganized Settings into Projects, Connections, Routing, Logs, and About tabs; kept the active-project selector on the main board.
- Added a dedicated Routing tab for per-role provider priority and kept credential management in Connections.
- Added an About page with release metadata and local Version log and Documentation viewers.
- Improved Settings tab visibility, mobile layout, and App Update field styling.
- Kept Update App and Restart App together in About's app update section.
- Expanded Statistics with period-based request, reply-success, and response-time summaries, plus an all-time deliverable QA pass rate.
- Added project documentation covering architecture, workflows, configuration, and Oracle Cloud Ubuntu deployment steps.

Earlier release entries have not been maintained in this file. See the Git commit history for changes before version 0.23.

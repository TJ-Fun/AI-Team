# Version log

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

# Plan: Fix duplicated action buttons in task detail dialog

Date: 2026-11-21 · Version target: 0.129

## Problem

`renderTaskDetailActions` in `static/js/task-detail-actions.js` (lines ~29–66) accumulates all action buttons into `actionHtml`, then appends the `.detail-actions-left` / `.detail-actions-right` wrapper divs **to the same string** and embeds `${actionHtml}` inside the right div. Result: every action button (status actions, chain plan approve/reject, overview actions, update decisions, merge approve) renders twice — once as loose buttons and once inside the right group. Most visible on `chain_plan` tasks ("Reject chain" / "Approve stage plan" duplicated).

Introduced in v0.122 (commit `d531e91`, left/right actions split).

## Fix

Single batch — one file, ~10 lines changed:

1. **`static/js/task-detail-actions.js`** — rename accumulated string to `buttonsHtml` (or keep `actionHtml` for buttons only) and compose the final markup as:
   - `.detail-actions-left`: Delete task + Copy (unchanged)
   - `.detail-actions-right`: `${buttonsHtml}` + Close button
   - No loose buttons outside the two groups.

## Impact analysis

- `static/js/task-detail-actions.js` — only file touched. `bindTaskDetailActions` unchanged (event delegation on `#detail-actions` still works; `data-*` attributes unchanged).
- No backend, no API, no CSS changes (`.detail-actions` flex layout already expects exactly two child groups).
- Affects all task types positively: removes duplication for status actions, overview approve, doc/roadmap/board-delta decisions, merge approval — not just `chain_plan`.

## Verification

- Open a `chain_plan` task in "Needs approval": exactly one row — left: Delete task, Copy; right: Reject chain, Approve stage plan, Close.
- Open a regular task with status actions (e.g. Draft → Ready/Retry): no duplicated buttons.
- Open a `project_overview` "Needs approval" task: Save edits / Approve buttons appear once.
- All buttons still functional (delegated click handler unchanged).

## Post-step protocol

Docs/version bump (VERSIONS.md → 0.129, DOCUMENTATION.md if it mentions the actions row), commit + push.

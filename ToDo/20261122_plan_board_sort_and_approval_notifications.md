# Plan: Board sort by creation time + approval notifications

Date: 2026-11-22 · Version targets: 0.150 / 0.151

## Batch 1 — Board sort: newest first (v0.150)

`static/js/task-board.js` `renderTasks()`:

- Replace the release-based `sortedTasks` comparator (releaseKey/cmpRelease/env_setup/release_order) with a strict `created_at` descending sort; fallback to `updated_at` then `id` for missing timestamps.
- Remove now-unused `releaseKey`/`cmpRelease` helpers.
- Release badges on cards stay (display only); only ordering changes.

Verify: `node --check` not applicable (ES module) — visual check via browser or careful review; no backend change.

Docs: VERSIONS.md 0.150. Commit + push.

## Batch 2 — Notification for approval-waiting tasks (v0.151)

`ait/notifications.py` `get_notifications()`:

- Extend the `status == "Needs approval"` branch: currently only `project_overview`, `design_review`, `chain_plan` (and `merge_approval_required`) produce notifications. Add the doc-update family — `doc_update`, `roadmap_update`, `vision_update`, `board_delta`, `doc_edit` — with per-type titles/messages (e.g. "Document update needs approval — Confirmed outcomes", "Roadmap update needs approval", "Project vision update needs approval", "Board delta needs approval", "Document edit needs approval").
- Use `DOC_UPDATE_TASK_TYPES` constant (already in shared namespace) for the check.
- Severity: medium, kind: "decision", task_id set (Open task button works already).

Verify: `py_compile`; unit-check `get_notifications` with a synthetic Needs-approval doc_update task if feasible.

Docs: VERSIONS.md 0.151; DOCUMENTATION.md §4.1/§4.4 notification sentence updated to include doc-update approvals. Commit + push.

## Post-step protocol

Version bumps (ABOUT/README/DOCUMENTATION headers + VERSIONS.md) → commit + push → next batch.

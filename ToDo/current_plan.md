# Update App version gate + Repair App — plan

Status: **Batch 1 complete** (2026-11-10). Next: Batch 2 — frontend.

## Goal

- **Update App** compares current vs remote version first. Same/higher remote → show "Up to Date", no clone/restart. Newer remote → existing clone + mirror + restart flow.
- **Repair App** (new button, same style, next to Update App) always does clone + mirror + restart regardless of version.
- If the version check fails during Update: block with error, log it, raise a notification.

## Batch 1 — Backend (`ait/routes_settings.py`, `ait/notifications.py`)

1. In `POST /api/settings/update` handler:
   - Read optional `mode` from body (`"update"` default, `"repair"`).
   - `update` mode: call `app_update_status(force=True)` before any git work.
     - `update_available` false → return `200 {"up_to_date": true, "current": ..., "latest": ..., "message": "Up to Date"}`.
     - `error` non-empty or `latest` empty → `logger.error`, `append_event({"type": "app_update_check_failed", ...})`, return `502 {"error": "Could not check for updates: ..."}`.
   - `repair` mode: skip check, proceed as today.
2. In `ait/notifications.py` `get_notifications()`: surface a notification item when the cached `app_update_status()` has a recent `error` (so the failed check raises a notification).
3. Verify: `compileall`, namespace check, server boot, POST update with `mode=repair` smoke (expect config error or proceed), POST update default with mocked/unreachable repo → expect blocked error.
4. Docs (`VERSIONS.md`, `README.md`, `ABOUT.md`, `DOCUMENTATION.md`) → version **0.66** → commit + push.

## Batch 2 — Frontend (`static/index.html`, `static/js/settings.js`, `static/js/notifications.js`)

1. `index.html`: add `<button id="repair-app" class="secondary">Repair App</button>` next to Update App; update the stale section description (mirror sync deletes stale files).
2. `settings.js`:
   - Update handler: if `res.up_to_date` → show "Up to Date" in `#update-status`, skip progress/restart wait.
   - New Repair handler: same validation/confirm/progress flow, POST with `mode: "repair"`, confirm text explains it forces a fresh copy of the repo.
3. `notifications.js`: handle `up_to_date` response gracefully (show message instead of restart wait).
4. Verify: server boot, manual UI check instructions.
5. Docs → version **0.67** → commit + push.

## Batch 3 — Final docs & wrap-up

- Update `ToDo/current_plan.md` status, final version bump if needed, push.

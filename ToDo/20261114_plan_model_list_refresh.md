# Plan: Daily Provider Model List Refresh + Routing Test Button

Date: 2026-11-14

## Goal
- Refresh provider model lists once per day (background worker) with retries.
- Persist model cache in `data/model_cache.json`.
- Flag routing entries whose explicit model is no longer listed; show notification + red dropdown.
- Add per-row "Test" button on routing rows (sends "Hi", shows reply).
- Add "Refresh Model List" button on connection cards after "Test model".

## Decisions (confirmed with user)
- Scheduler: new daemon thread following existing `watchdog_worker_loop` / `ci_poll_worker_loop` pattern.
- Cache file: `data/model_cache.json` — OK.
- Notifications: no auto-clear beyond condition resolution; all events logged via `append_event` + `logger`.
- Test button: routing rows only (connection cards already have "Test model").
- Red marking: border/text only.
- Total refresh failure: do NOT flag models (only flag when a fresh successful list lacks the model).

## Batches
1. `ait/model_refresh.py`: cache load/save, `refresh_provider_models_with_retry`, `refresh_all_provider_models`, `model_refresh_worker_loop` (startup run + 24h interval), wire into `ait/main.py`.
2. `check_routing_model_availability()` in `model_refresh.py`: set/clear `model_unavailable` on routing entries; notification kind `provider_model` in `ait/notifications.py`; `append_event` logging.
3. API: `GET /api/providers/{id}/models` serves cache (with `fetched_at`, `stale`); `POST /api/providers/{id}/refresh-models`; `POST /api/providers/{id}/test-model` (new `test_provider_model` in `provider_calls.py`).
4. Frontend routing rows: Test button after model select, red class `route-model--missing`, click handler.
5. Frontend connection cards: "Refresh Model List" button after "Test model"; CSS for red state.
6. Docs (`documentation/DOCUMENTATION.md`, `VERSIONS.md`), version bump, commits per batch.

## Impact analysis
- `ait/main.py` — start new worker thread.
- `ait/provider_calls.py` — add `test_provider_model()`.
- `ait/routes_providers.py` — new routes; models GET may read cache.
- `ait/notifications.py` — new notification kind.
- `static/js/providers.js` — routing row markup/handlers, connection card button, cache invalidation.
- `static/style.css` — `.route-model--missing` styles.
- `documentation/DOCUMENTATION.md`, `documentation/VERSIONS.md` — docs/version.

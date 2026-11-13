# Plan: header agent status + provider health + visible app version

Status: **Pending approval**

## Goal

Replace the useless header `Chat · <provider>` chip with a clickable agent-health status, show the current app version in the header, remove the per-provider connection text from the Project board, and ensure the notification **Update now** flow shows the same progress bar used in Settings.

## Confirmed decisions

- Agent count includes **AI providers only**; GitHub is excluded.
- "Up" means the provider is configured **and** passes a real connectivity/response check.
- The agent chip is clickable and opens **Settings → Connections**.
- Current app version must be visible without opening Settings.

## Health-check finding

A lightweight check already exists: `test_provider_connection()` in `ait/provider_calls.py`.

- Gemini: `GET /v1beta/models/{model}` metadata.
- OpenRouter: `GET /api/v1/auth/key`.
- OpenAI-compatible providers: `GET {base_url}/models`.

These are metadata/auth endpoints, **not** `generateContent`/`chat/completions`, so they do **not** consume tokens or count as model requests. They are still normal API calls and can count toward provider rate limits, so health checks will be cached and throttled rather than run on every 2s board poll.

## Impact analysis

- `ait/provider_health.py` **new** — cached provider health, parallel refresh, summary for `/api/status`.
- `ait/__init__.py` — register `provider_health`.
- `ait/http_handler.py` — `/api/status` adds `app_version` and cached `agents` summary.
- `ait/routes_providers.py` — add `GET /api/providers/health` refresh endpoint.
- `static/index.html` — replace `.model-chip` with agent/version chip; remove board `#agent-status`.
- `static/js/header.js` — render agent chip + version, throttle health refresh, open Settings → Connections on click; stop writing board agent states.
- `static/js/notifications.js` — verify existing update progress; adjust only if smoke shows a gap.
- `static/style.css` — agent chip states (green/yellow/red/neutral) and version styling.
- `documentation/ABOUT.md`, `documentation/VERSIONS.md`, `documentation/DOCUMENTATION.md` — version/docs updates per batch.

## Batches

### Batch 1 — Backend health + status fields → v0.90
- Add `ait/provider_health.py`: refresh configured AI providers in parallel via `test_provider_connection`, cache `{up, checked_at, error}` per provider, expose `provider_health_status()` and `refresh_provider_health()`.
- `/api/status` returns `app_version` and cached `agents` `{up,total,checked_at,providers}`; GitHub excluded.
- Add `GET /api/providers/health` to force a refresh and return the same summary.
- Verify: `python -m compileall -q ait`; smoke summary functions with disposable data.

### Batch 2 — Header agent status UI → v0.91
- Replace `.model-chip` with `#agent-status-chip` showing colored dot, `Agents X/Y`, and `v{app_version}`.
- `refreshStatus()` renders from `/api/status`; a throttled `GET /api/providers/health` call (≈60s, in-flight guarded) keeps health fresh.
- Click opens Settings and selects Connections via `localStorage.ai_team_settings_tab`.
- Remove board `#agent-status` element and its update; keep CPU/LLM/watchdog behavior.
- Verify/fix notification update progress if needed; `node --check` touched JS.

### Batch 3 — Docs/version final → v0.92
- Update `DOCUMENTATION.md` status/header/provider-health notes; finalize `ABOUT.md`/`VERSIONS.md`.
- Verify: `compileall`, `node --check`, `git status` clean except runtime logs.

## Verification

- `python -m compileall -q ait`
- `node --check` for every touched JS file
- Manual smoke: header shows `Agents X/Y` + version; colors match health; click opens Connections; board no longer shows provider connection sentence; notification update shows progress bar.

# Plan: Per-role Provider+Model routing chains (Option A)

Date: 2026-11-14
Status: Awaiting "Go"

## Goal

Replace the fixed 4-slot provider dropdowns in Settings → Routing with a configurable
ranked list of **route entries** per role. Each entry = Provider + Model.

## Confirmed requirements

- Default **3 rows** per role; user can **add rows** (beyond 10 allowed) or **delete down to 1**.
- Both Provider and Model have an **Auto** option.
- If Provider = **Auto**, all rows below it become inactive (Auto is terminal, expands to
  audit-score order as today). Model is forced to Auto/disabled in that case.
- **Same provider may appear multiple times** (e.g. Gemini·pro, then Gemini·flash).
- Model dropdowns **hide unsuitable models** (embeddings, image, TTS, moderation, etc.).

## Data model change

`data/settings/routing.json` entries change from `["Gemini", ...]` to:

```json
{ "PM/PO": [ {"provider": "Gemini", "model": "gemini-2.5-pro"}, {"provider": "Auto", "model": "auto"} ] }
```

- `model: "auto"` or `""` = use provider's configured default model.
- Migration: `get_routing()` accepts legacy string items → `{"provider": name, "model": "auto"}`.
- Sanity cap: `MAX_ROUTE_ENTRIES = 25` per role (prevents runaway payloads; UI allows well beyond 10).

## Backend changes

### `ait/common.py`
- `MAX_PREFERRED_PROVIDERS` → `MAX_ROUTE_ENTRIES = 25`.
- Add `NON_CHAT_MODEL_PATTERNS` regex list (case-insensitive substring match on model id):
  `embed`, `dall-e`, `dalle`, `stable-diffusion`, `sdxl`, `flux`, `whisper`, `tts`,
  `text-to-speech`, `speech`, `audio`, `moderation`, `rerank`, `guard`, `image`,
  `-vision`-free chat models kept (do NOT block "vision" substring alone — only exact
  non-chat families). Conservative list; easy to extend.

### `ait/projects.py`
- `get_routing()` / `save_routing()`: normalize/validate entry objects; legacy string
  migration; drop dedup so duplicate providers allowed; keep "Auto" terminal semantics
  (entries after an Auto-provider entry are ignored on read, matching UI).
- `selection_order_for_role(role)` → return list of `{provider, model}` pairs:
  - explicit entry → `{"provider": name, "model": entry.model}`
  - `provider == "Auto"` → expand `auto_provider_order()`, each with `model: "auto"`, dedup by provider.
- `provider_for_role()` / `task_provider()`: return the matched **entry** (provider + model
  override) instead of just the name. `tried` set keys on `(provider, model_override)` so a
  failed Gemini·pro does not block a later Gemini·flash entry.
- `has_untried_alternate()`: same key change.

### `ait/provider_registry.py`
- `provider_model(name, override="")`: return override when non-empty/non-"auto".
- `openai_compat_config(name, model_override="")`: use override for `cfg["model"]`.

### `ait/provider_stream.py`
- `call_gemini_task(task, extra_context, model_override="")` → use override in URL model.
- `call_openai_compatible_task(..., model_override="")` → pass into `openai_compat_config`
  / `_openai_payload`.

### `ait/info_requests.py`
- `call_task_provider()`: read `task.get("_route_model")` (stashed at dispatch) and pass
  through to both call paths; include in returned `actual_model`.

### `ait/provider_dispatch.py`
- When claiming a task for a provider, stash the matched route model on the task
  (`task["_route_model"]`), record it in the attempt dict (`"model"` already exists —
  ensure it reflects the override), pop before `write_task` persistence concerns (keep it
  in-memory only or persist harmlessly).

### `ait/provider_calls.py`
- `list_provider_models()`: filter `models` through `NON_CHAT_MODEL_PATTERNS` before
  returning (applies to both Gemini and OpenAI-compatible lists).

### `ait/routes_providers.py`
- `/api/routing` GET: return `max_routes` instead of `max_preferred`; `routing` payload is
  now entry objects.
- `/api/routing` POST: `save_routing` handles new shape (no route change needed beyond
  payload pass-through).

## Frontend changes

### `static/index.html`
- Update Routing panel note text (3 default rows, add/remove, Auto semantics).

### `static/js/providers.js` (~rewrite of routing section, est. +120/-60 lines)
- `renderConnectionsAndRouting()`: per role render a card with N route rows:
  `[rank] [Provider select: Auto + providers] [Model select: Auto + filtered models] [↑][↓][✕]`
  + "+ Add route" button (hidden/disabled at `max_routes`) + hint text.
- Default 3 rows when role has no saved entries.
- Provider change → reset that row's model to "auto", re-render.
- Provider = Auto → disable model select and all rows below (visual dimmed state).
- Model options: fetch `/api/providers/{id}/models` lazily, **cache per provider id** in
  `state.modelCache` to avoid refetching per row; prepend `Auto` option; show
  "provider default (X)" label using `current` from API.
- Remove old swap-to-dedup change handler; new handlers for add/remove/reorder/update.
- Submit sends `state.routing` (entry objects) — backend validates.

### `static/style.css`
- Styles for `.route-card`, `.route-row`, `.route-row--disabled`, rank badge, add/remove
  buttons. Reuse existing `route-role`/`route-order` patterns where possible.

## Impact analysis — files touched

| File | Change |
|---|---|
| `ait/common.py` | constants + model blocklist |
| `ait/projects.py` | routing get/save, selection order, provider_for_role, tried keys |
| `ait/provider_registry.py` | model override params |
| `ait/provider_stream.py` | model override in call paths |
| `ait/info_requests.py` | pass override through call_task_provider |
| `ait/provider_dispatch.py` | stash route model at claim, record in attempt |
| `ait/provider_calls.py` | filter unsuitable models |
| `ait/routes_providers.py` | routing payload field rename |
| `static/index.html` | note text |
| `static/js/providers.js` | routing UI rewrite |
| `static/style.css` | new styles |
| `documentation/VERSIONS.md`, `DOCUMENTATION.md` | per-step updates |

## Execution batches (each leaves app functional)

1. **Backend data model + migration** — common.py constants, projects.py get/save/selection
   (entries internally), provider_for_role returns entry; callers updated minimally.
   routing.json old format still readable. No UI change yet (UI keeps sending strings →
   save_routing normalizes). *Verify: existing routing still works, dispatch unchanged.*
2. **Model override plumbing** — provider_registry/stream/info_requests/dispatch:
   `_route_model` flows to actual API calls; attempts record model. *Verify: task run uses
   provider default when override empty.*
3. **Model filtering** — provider_calls.list_provider_models blocklist. *Verify: dropdown
   API returns filtered list.*
4. **Frontend UI** — index.html note, providers.js rewrite, style.css. *Verify: add/remove/
   reorder/Auto-deactivation/duplicate providers all work; save+reload persists.*
5. **Docs + version bump** — VERSIONS.md, DOCUMENTATION.md.

## Open assumptions (flag if wrong)

- Cap = 25 entries/role (UI allows far more than 10).
- Model blocklist is a conservative substring list, applied server-side.
- When Provider=Auto, Model is disabled (Auto provider can't share one model).
- Duplicate provider entries are distinguished by their model in `tried` tracking; identical
  provider+model duplicates are allowed but the second is effectively unreachable after the
  first fails (same as today's dedup, just explicit).

# Plan: Split `static/js/providers.js` (850 lines) into focused modules

Date: 2026-11-21 · Version targets: 0.130 / 0.131 / 0.132 (one per batch)

## Current state

`providers.js` is a single `initProviders({ refreshStatus, refreshBoard })` closure (850 lines) mixing four responsibilities:

- **Shared helpers** — `isProviderConnected`, `providerKindLabel`, `chatProviderOptions`, `populateProviderSelects`, `setConnectionsSuccess` (lines 4–57).
- **Connections UI** — `renderConnectionsAndRouting` (provider rows + GitHub row), `refreshProviderModels`, `#connection-list` click/change listeners, `#add-provider` (lines 59–152, 300–340, 467–646, 775–824).
- **Routing UI** — `defaultRouteProvider/Rows`, `routeRowsFor`, `ensureRouteModelCache`, `routeModelOptionsHtml`, `renderRoutingRoles`, `#routing-roles` change/click listeners, `routingForm` submit (lines 154–298, 648–773, 826–847).
- **Chat provider/model selects** — `renderChatModelSelect`, `renderChatProviderSelect`, three change listeners (lines 342–433).
- **Orchestrator** — `reloadSettingsData` (435–465) and the returned API object (848).

## Consumers (must keep working unchanged)

- `app.js` — `initProviders(...)`, destructures `reloadSettingsData`, `setConnectionsSuccess`, `populateProviderSelects`; passes whole `providersApi` to `loadConfig`.
- `js/header.js` `loadConfig(providers)` — uses `chatProviderOptions`, `renderChatModelSelect`, `populateProviderSelects`.
- `js/settings.js` — uses `reloadSettingsData`, `setConnectionsSuccess`.
- `js/task-detail.js` — uses `populateProviderSelects`.

**Constraint:** `initProviders` must keep returning the exact same API object shape. No consumer changes needed.

## Design

New modules under `static/js/`, each a factory `initX(deps)` returning its functions; `providers.js` becomes a thin orchestrator that builds a shared `deps` object and re-exports the composed API.

- **`provider-shared.js`** (~60 lines, pure functions, no DOM binding): `isProviderConnected(state, name)`, `providerKindLabel(kind)`, `chatProviderOptions(state, selected)`, `populateProviderSelects(state)`, `setConnectionsSuccess(message)`. All take `state` explicitly — no closure magic.
- **`provider-chat-select.js`** (~110 lines): `initChatProviderSelect(deps)` → `{ renderChatModelSelect, renderChatProviderSelect }`; binds the 3 chat listeners internally. Deps: `state` (via core import), `api`, `appAlert`, `chatProviderOptions`, `isProviderConnected`.
- **`provider-routing.js`** (~280 lines): `initProviderRouting(deps)` → `{ defaultRouteProvider, routeRowsFor, ensureRouteModelCache, renderRoutingRoles }`; binds `#routing-roles` listeners + `routingForm` submit internally. Deps include `reloadSettingsData` (lazy — see below), `refreshBoard`, `refreshStatus`.
- **`provider-connections.js`** (~330 lines): `initProviderConnections(deps)` → `{ renderConnectionsList, refreshProviderModels, bindConnections }`; binds `#connection-list` listeners + `#add-provider`. Deps include `ensureRouteModelCache` + `renderRoutingRoles` (from routing module — injected to avoid circular imports).
- **`providers.js`** (~90 lines): imports shared helpers + the three factories; defines `reloadSettingsData` (calls each module's render fn); calls factories in order **routing → connections → chat** (connections needs routing's `ensureRouteModelCache`/`renderRoutingRoles`); returns the same API object as today.

### Cross-module wiring notes

- `renderConnectionsAndRouting` currently calls `renderRoutingRoles()` + `refreshProviderModels()`. It becomes an orchestrator-level function in `providers.js` (or connections module receiving `renderRoutingRoles` via deps) — decide at execution; simplest is keeping a small `renderConnectionsAndRouting` in `providers.js`.
- `reloadSettingsData` is referenced by event handlers inside modules but defined in `providers.js`. Pass it as a dep **after** module init is impossible (listeners bind during init) — solve by passing a lazy wrapper `() => reloadSettingsData()` or declaring `reloadSettingsData` as a hoisted `async function` in `providers.js` before factory calls (function declarations hoist; inner refs to module consts resolve at call time, which is always post-init). Use the hoisted-function approach — zero API change.
- All modules import `{ $, escapeHtml, state, api, appAlert, appConfirm }` from `./core.js` directly (same as today); only genuinely shared *functions* go through `deps`.

## Batches (each leaves app fully functional, same exported API)

1. **Batch 1 — shared helpers + chat select (v0.130)**
   - Create `provider-shared.js` + `provider-chat-select.js`.
   - `providers.js`: delete moved code, import factories, wire deps, keep return shape (`chatProviderOptions`, `isProviderConnected`, `populateProviderSelects`, `setConnectionsSuccess`, `renderChatModelSelect`, `renderChatProviderSelect` now come from modules).
   - Verify: chat provider/model dropdowns work; Settings → Connections/Routing unchanged; task provider select populates.

2. **Batch 2 — routing UI (v0.131)**
   - Create `provider-routing.js`; move routing render + all `#routing-roles`/`routingForm` logic.
   - `providers.js` shrinks to ~570 lines.
   - Verify: add/remove/reorder routes, per-route settings, Test button, accordion toggles, Save routing + autonomy checkboxes.

3. **Batch 3 — connections UI (v0.132)**
   - Create `provider-connections.js`; move connection list render, model selects, all `#connection-list`/`#add-provider` handlers.
   - `providers.js` final ~90 lines: orchestrator + `reloadSettingsData` + `renderConnectionsAndRouting` + API return.
   - Verify: add/remove/reorder providers, save/test/clear credentials, model select + refresh + test-model, GitHub row.

## Impact analysis

- **New files:** `provider-shared.js`, `provider-chat-select.js`, `provider-routing.js`, `provider-connections.js` — all < 400 lines (connections ~330, routing ~280).
- **Modified:** `static/js/providers.js` only (plus docs/version files). **No changes** to `app.js`, `header.js`, `settings.js`, `task-detail.js`, `index.html`, or any backend code.
- **Risk:** low — pure code motion inside one closure; the only subtlety is dep wiring order and the hoisted `reloadSettingsData`. DOM ids, `data-*` attributes, and API calls are copied verbatim.
- **Regression surface:** Settings → Connections and Routing tabs, chat provider/model selectors in header, task detail provider select, provider order persistence.

## Post-step protocol (per batch)

Docs note in DOCUMENTATION.md repository map if it lists JS modules (check), version bump (ABOUT/README/DOCUMENTATION/VERSIONS), commit + push, then next batch.

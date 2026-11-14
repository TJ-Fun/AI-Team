# Plan: Per-route timeout/retries/stream settings + accordion Routing UI

Date: 2026-11-14
Status: Awaiting "Go"

## Confirmed requirements

- Role cards in Settings → Routing become **accordions, all collapsed by default**
  (header shows role name + route count).
- Each provider/model route row gets 4 new settings:
  - **Timeout** (minutes, default 15) — per-attempt limit.
  - **Retries** (default 2) — retries of the SAME combo after a failure.
  - **Retry delay** (minutes, default 5) — wait between retry attempts of the same combo.
  - **Stream** (checkbox, default ON) — ON = SSE streaming, timer resets per received
    chunk (idle semantics); OFF = blocking call, hard deadline (no answer in time = fail).
- **Retry chain (replaces global `MAX_AUTOMATIC_RETRIES` + 2–6h/2–24h backoffs):**
  - ALL failures (timeout, provider API errors, malformed replies) consume the combo's
    retries.
  - Combo exhausted (initial attempt + `retries` retries used) → next combo in role order,
    dispatched immediately.
  - Last combo in the role's route list exhausted → task **Blocked** + PM/PO escalation.
- Applies to routed tasks (`provider == "Automatic"/"Auto"` and QA workflow). Pinned-provider
  tasks keep existing behavior.

## Data model

Route entry becomes:

```json
{"provider": "Gemini", "model": "gemini-2.5-pro", "timeout_seconds": 900,
 "retries": 2, "retry_delay_seconds": 300, "stream": true}
```

- `model: "auto"` unchanged; `stream` bool; Auto-provider expansion entries get defaults.
- Migration: `_routing_entries()` in `ait/projects.py` fills missing fields with defaults
  for legacy `{provider, model}` and string entries. No file rewrite needed.
- Bounds in `save_routing`/normalization: timeout 60–86400 s, retries 0–10,
  retry_delay 0–86400 s, stream coerced to bool.

## Backend changes

### `ait/common.py`
- Add `DEFAULT_ROUTE_TIMEOUT_SECONDS = 900`, `DEFAULT_ROUTE_RETRIES = 2`,
  `DEFAULT_ROUTE_RETRY_DELAY_SECONDS = 300`, `STREAM_HARD_CAP_FACTOR = 8`.
- Remove `MAX_AUTOMATIC_RETRIES` usage (constant may stay unused or be deleted).

### `ait/projects.py`
- `_routing_entries()`: normalize the 4 new fields with defaults + bounds.
- New helper `route_combo_key(entry)` → `(provider, model)`; and
  `combo_attempts_used(task, role, key, cycle_key)` → count of attempts in the current
  routing cycle matching the key (uses `route_model`, default `"auto"`).
- `provider_for_role()` / `task_provider()` / `has_untried_alternate()`: replace the
  `tried` set with **exhaustion** logic — a combo is exhausted when
  `attempts_used > entry.retries` (i.e. all retries consumed). Pick first configured,
  non-exhausted entry in route order. Auto-expanded entries use default retries.
- `route_entries_for_role()` already returns entries; ensure new fields present.

### `ait/provider_dispatch.py`
- `claim_task_for_provider()`: stash `_route_timeout`, `_route_stream` on task (like
  `_route_model`); snapshot `timeout_seconds`, `stream`, `retries`, `retry_delay_seconds`
  onto the attempt dict.
- `dispatch_one_for_provider()` error path (routed tasks only): compute combo exhaustion
  after recording the failed attempt:
  - retries remain → `Waiting for capacity`, `retry_after = now + retry_delay_seconds`
    (use provider `retry_after` when later, e.g. 429).
  - exhausted → non-exhausted combo exists → `Ready` (immediate fallback);
    none → `Blocked` + escalation.
  - Pinned-provider tasks: keep current retryable/fallbackable logic.
- `run_watchdog_pass()` rework:
  - In-progress timeout uses the active attempt's `timeout_seconds` snapshot
    (fallback `DEFAULT_ROUTE_TIMEOUT_SECONDS`).
  - On timeout: same exhaustion decision as above (delay = attempt's
    `retry_delay_seconds`).
  - `Waiting for capacity` branch: on `retry_after` → `Ready` only (drop
    `automatic_retries` increment and cycle-reset logic; exhaustion ordering replaces it).
  - Remove `MAX_AUTOMATIC_RETRIES` / `automatic_retries` writes (keep reading legacy field
    harmlessly or clean up on success path).
- Watchdog hard cap: `attempt_timeout_seconds` recorded at claim =
  `timeout_seconds * STREAM_HARD_CAP_FACTOR` when stream ON (preserves today's
  15m-idle ⇒ 2h-cap), else `timeout_seconds`.

### `ait/provider_stream.py`
- `call_gemini_task()` / `call_openai_compatible_task()`: accept `idle_timeout` and
  `stream_enabled` params (defaults = current constants/True).
- Stream OFF → use the existing blocking (non-SSE) call path with
  `timeout=timeout_seconds`.
- Stream ON → SSE with `urlopen(timeout=idle_timeout)` (per-chunk idle reset, unchanged).

### `ait/info_requests.py`
- `call_task_provider()`: read `_route_timeout`/`_route_stream` from task, pass through;
  include effective timeout/stream in returned meta if useful for logging.

## Frontend changes

### `static/js/providers.js`
- `renderRoutingRoles()`: wrap each role card in an accordion — clickable header
  (chevron ▸/▾) toggling a body div; collapsed by default; header shows
  `Role name · N routes`. Open state kept in `state.routingOpen = {}` (session only).
- Each `.route-row` gains a settings line (`.route-settings`) with 4 inputs:
  Timeout (min), Retries, Retry delay (min), Stream checkbox — bound to
  `entry.timeout_seconds/60`, `entry.retries`, `entry.retry_delay_seconds/60`, `entry.stream`.
- `defaultRouteRows()` entries include the 4 defaults.
- Change/click handlers extended for the new inputs (re-render on change).
- Save payload unchanged shape-wise (entries now carry extra fields; backend validates).

### `static/index.html`
- Update Routing note: mention per-route timeout/retries/retry-delay/Stream and
  accordion behavior.

### `static/style.css`
- `.route-accordion`, `.route-accordion-head`, `.route-accordion-body`,
  `.route-settings` grid, small number inputs, stream checkbox styling.

## Impact analysis — files touched

| File | Change |
|---|---|
| `ait/common.py` | new defaults + hard-cap factor; drop MAX_AUTOMATIC_RETRIES |
| `ait/projects.py` | entry normalization, exhaustion helpers, provider selection |
| `ait/provider_dispatch.py` | claim stash, error-path retry chain, watchdog rework |
| `ait/provider_stream.py` | per-call idle timeout + stream on/off |
| `ait/info_requests.py` | pass route timeout/stream through |
| `static/js/providers.js` | accordions + per-row settings inputs |
| `static/index.html` | note text |
| `static/style.css` | accordion + settings styles |
| `documentation/VERSIONS.md`, `DOCUMENTATION.md`, `ABOUT.md`, `README.md` | v0.98 |

`provider_dispatch.py` is already ~665 lines (over the 400 limit). Batch 1 extracts
`run_watchdog_pass` + watchdog state into `ait/provider_watchdog.py` (pure move; flat
namespace makes this low-risk) before the rework.

## Execution batches

1. **Pre-refactor + data model** — extract watchdog to `ait/provider_watchdog.py`
   (no behavior change); add new entry fields/defaults/bounds in `common.py` +
   `projects.py`; exhaustion helpers. *Verify: app imports, routing save/load round-trip.*
2. **Dispatch + stream plumbing** — claim stash, error-path retry chain, watchdog rework,
   per-call timeout/stream params. *Verify: syntax + smoke test of exhaustion logic.*
3. **Frontend** — accordions + per-row settings UI. *Verify: node --check, manual UI pass.*
4. **Docs + version 0.98 + commits.**

## Open assumptions (flag if wrong)

- `retries` = retries AFTER the first attempt (default 2 ⇒ up to 3 tries per combo).
- Moving to the next combo after exhaustion is immediate (no delay); retry delay applies
  only between attempts of the same combo.
- Provider `Retry-After` (e.g. 429) is honored as a minimum on top of retry delay.
- Stream ON keeps a hidden hard cap = timeout × 8 on the watchdog (≈ today's 2h at 15m
  timeout) so a trickling stream cannot run forever.
- Pinned-provider (non-routed) tasks keep the existing retry behavior.

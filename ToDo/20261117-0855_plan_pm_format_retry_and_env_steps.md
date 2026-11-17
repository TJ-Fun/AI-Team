# Plan: PM/PO format auto-retry + env_setup steps prompt fix

## Problem
- PM agent emits `kind: "env_setup"` tasks without the required `steps` array, so `clean_env_steps` raises and the whole plan is rejected as "Rework needed".
- Root cause: `PM_PLANNING_CONTRACT` shows the task JSON schema without `steps`; the env_setup requirement is only a trailing prose sentence.
- On any contract parse failure there is no automatic retry; the user must manually requeue.

## Confirmed decisions
- Providers are pinned per agent (no "Automatic" routing complexity), so a simple requeue to "Ready" is sufficient; no provider/model pinning.
- Auto-reply includes explicit format requirements plus the validation error.
- Also fix the prompt and add a tolerant parser fallback for missing env steps.

## Impact analysis
- `ait/common.py` - `PM_PLANNING_CONTRACT` text only.
- `ait/pm_planning_parse.py` - env branch: tolerate missing/invalid `steps`.
- `ait/format_retry.py` (NEW, ~60 lines) - shared `maybe_format_retry(task, provider, error_msg)` helper.
- `ait/pm_planning_finish.py`, `ait/doc_updates.py` - call helper in exception paths of `finish_pm_planning_task`, `finish_pm_escalation_task`, `finish_doc_update_task`, `finish_roadmap_update_task`.
- `ait/provider_stream.py` - `_task_prompt`: append `task["format_retry_feedback"]` when present.
- `documentation/ABOUT.md` - version bump per step.

## Batches

### Step 1 - Prompt fix (common.py)
- Rewrite `PM_PLANNING_CONTRACT` task example to include `"kind"` and `"steps"` fields explicitly.
- Add hard rule: every `env_setup` task MUST include `steps` (1-10 objects with title/instructions/expected); plans violating this are invalid.
- Emphasize: reply with ONLY the JSON object, no markdown.
- Version bump, docs note, commit, push.

### Step 2 - Parser fallback (pm_planning_parse.py)
- In the `is_env` branch: if `steps` missing/not a list, synthesize one step `{title: <task title>, instructions: <request>, expected: ""}` and append a warning instead of raising.
- If `steps` is a list but individual items invalid, still raise (genuinely malformed).
- Manual `create_env_task` API path (`clean_env_steps` direct use) stays strict - unchanged.
- Version bump, commit, push.

### Step 3 - Auto format-retry mechanism
- New `ait/format_retry.py`: `MAX_FORMAT_RETRIES = 2`; `maybe_format_retry(task, provider, msg) -> tuple[bool, str]`.
  - If `task["format_retries"] < 2`: increment counter, set `task["format_retry_feedback"]` to "Your previous reply was rejected: {msg}. Reply again with ONLY the required JSON object in exactly the format described - no markdown, no extra text.", set `status = "Ready"`, pop `planning_error`/`block_reason`, append activity entry, `DISPATCH_EVENT.set()`, return `(True, msg)`.
  - Else return `(False, msg)` so the caller falls through to "Rework needed".
- Wire into exception handlers of all four `finish_*` functions.
- Version bump, commit, push.

### Step 4 - Prompt injection (provider_stream.py)
- In `_task_prompt`, after the failed-validation block: if `task.get("format_retry_feedback")`, append it.
- Clear `format_retry_feedback` on successful parse so it does not leak into later attempts.
- Version bump, update docs, commit, push.

## Verification
- Manual test: feed `parse_pm_plan_response` a plan JSON with an env task lacking steps; expect a warning plus synthesized step, no exception.
- Simulate finish failure: task returns "Ready" with `format_retry_feedback`; second failure returns "Rework needed".
- `python -m compileall ait` or the existing test suite if present.

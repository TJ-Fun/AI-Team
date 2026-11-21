# Plan: Repo-manifest truncation + format-error classification (T-0026)

Date: 2026-11-22 · Version targets: 0.152 / 0.153 / 0.154

Context: T-0026 (Backend developer, repo task) failed with "Nvidia API error 503: Agent must return a JSON file-change manifest". The agent's manifest reply was truncated mid-JSON. Two bugs: (1) the `finish_reason=length` truncation check skips repo-manifest tasks (`needs_work_reply_envelope` is False for them, no planning contract); (2) `apply_repository_manifest` `ValueError`s are wrapped as `ProviderFailure(503, error_kind="api")` → retryable Waiting-for-capacity loop instead of actionable format retry.

## Batch 1 — Classification + truncation coverage (v0.152)

`ait/provider_dispatch.py`:

1. Wrap the `apply_repository_manifest(task, reply, provider, attempt_num)` call (line ~358): catch `ValueError` → `raise ProviderFailure(provider, 400, str(exc), error_kind="format")`. All manifest validation failures (truncated JSON, >20 files, bad path, bad extension, oversize content) become format errors → `maybe_format_retry` feeds the exact error back to the agent.
2. Extend the `finish_reason` truncation check (line ~254): fire when `finish in ("length","max_tokens")` AND (`planning_contract_for_task(task)` OR `needs_work_reply_envelope(task)` OR repo-manifest expected). Repo-manifest expected = project has `repository` AND `assigned_role` in ("Frontend developer","Backend developer","Documentation") AND not `qa_active` — same condition as the `apply_repository_manifest` call site. Extract a small helper `_expects_repo_manifest(task)` to avoid duplicating the condition.

Verify: `py_compile`; simulate: manifest ValueError → ProviderFailure format; finish_reason=length on repo task → format error.

Docs: VERSIONS.md 0.152; DOCUMENTATION.md §4.4 provider-call paragraph updated (manifest failures are format errors; truncation check covers manifests). Commit + push.

## Batch 2 — Split guidance in repo instructions (v0.153)

`ait/qa_review.py` `build_repo_agent_instructions`:

- Append to the manifest contract text: the reply must fit the provider output limit; return complete contents only for files that must change; if the full change set cannot fit, return the most critical files and describe the remaining work in the `feedback` field so PM/PO can queue a follow-up task.
- No parser changes — `feedback` field already exists and is stored on the task.

Verify: `py_compile`; confirm instruction string renders.

Docs: VERSIONS.md 0.153. Commit + push.

## Batch 3 — Docs polish (v0.154)

- DOCUMENTATION.md: document the manifest format-retry behavior + split guidance in §4.4.
- Version bumps all four files to 0.154. Commit + push.

## Post-step protocol

Version bumps → commit + push → next batch. Single "Go" authorizes all three.

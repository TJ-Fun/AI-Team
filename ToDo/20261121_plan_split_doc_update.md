# Plan: Split doc_update into one task per document

Date: 2026-11-21 · Version targets: 0.146 / 0.147

Context: `doc_update` tasks require all four PM-writable documents in a single reply (~40–80k chars), which exceeds OpenAI-compatible `max_tokens` (8192) — T-0019 failed this way. Approved approach: **one task per document**.

## Design

Keep `task_type: "doc_update"` and add a `doc_slug` field. All downstream machinery (dispatch finish branch, `decide_doc_update`, `apply_doc_update`, task-detail approve/reject UI, update-payload rendering) already works on a flat `{slug: text}` payload — **no frontend or apply-path changes needed**.

- **New tasks** carry `doc_slug` ∈ `DOC_UPDATE_SLUGS` (`project_brief`, `technical_overview`, `decisions`, `outcomes`).
- **Legacy tasks** without `doc_slug` (e.g. in-flight T-0019) keep the old 4-document contract and parser — no breakage.

## Impact analysis

| File | Change |
|---|---|
| `ait/common.py` | Add `doc_update_contract(slug)` — per-slug JSON contract asking for `{"type":"doc_update","summary","documents":{"<slug>":"complete Markdown ..."}}`; keep `DOC_UPDATE_CONTRACT` for legacy. |
| `ait/qa_review.py` | `planning_contract_for_task`: `doc_update` → `doc_update_contract(task["doc_slug"])` when present, else `DOC_UPDATE_CONTRACT`. |
| `ait/doc_updates.py` | `parse_doc_update_response(reply, slug=None)`: slug → require `documents[slug]` non-empty ≤20k, return `{slug: text}` (ignore extra keys); slug None → existing all-4 behavior. `finish_doc_update_task` passes `task.get("doc_slug")`. |
| `ait/routes_tasks_pm.py` | `kind="docs"` → loop `DOC_UPDATE_SLUGS`, create 4 Ready tasks with per-doc title/request/`doc_slug`; respond `{"tasks": [...]}` (frontend ignores body, just refreshes board). |
| `static/js/*` | None — update section renders `payload.documents \|\| payload` generically; approve/reject unchanged. |
| `DOCUMENTATION.md`, `VERSIONS.md`, version headers | Per-batch protocol. |

Per-doc request text (shared template + slug focus): review current docs/board, propose updated **{doc title}** only, `info_requests` for on-request docs, confirmed facts/assumptions, ≤20,000 chars, never `project_vision`.

## Batch 1 — Backend split (v0.146)

1. `ait/common.py`: `doc_update_contract(slug)` (+ slug→label map reuse `project_doc_title` is in project_context — contract text can embed slug literally; validate slug ∈ DOC_UPDATE_SLUGS, fallback to generic).
2. `ait/qa_review.py`: contract selection by `doc_slug`.
3. `ait/doc_updates.py`: `parse_doc_update_response` slug param + `finish_doc_update_task` wiring.
4. `ait/routes_tasks_pm.py`: create 4 tasks for `kind="docs"`.
5. Verify: `py_compile`; unit-check parser for single-slug, legacy 4-slug, and missing-slug replies.
6. Docs: VERSIONS.md 0.146; DOCUMENTATION.md note that Update docs queues four tasks.
7. Commit + push.

## Batch 2 — Docs polish (v0.147)

1. `DOCUMENTATION.md`: document the per-document update flow (4 tasks, independent approvals, legacy single-task behavior for pre-split tasks).
2. Version bumps all four files + VERSIONS.md 0.147.
3. Commit + push.

## Post-step protocol

Bump all four version files → VERSIONS.md entry → commit + push → next batch.

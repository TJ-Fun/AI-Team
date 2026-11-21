# Plan: Tolerant info_requests parsing + explicit contract wording

Date: 2026-11-21 · Version target: 0.139

## Problem

Task T-0019 (`doc_update`, provider Nvidia/nemotron-3-ultra) failed with "Document update reply must have type 'doc_update'" → Rework needed. The model replied three times with `{"type":"info_requests","slugs":[...]}` (once `"requests"`), but `extract_info_requests` only accepts `{"info_requests":[...]}`. The requests were never detected, so each reply was treated as final, failed `parse_doc_update_response`, and burned all format retries.

## Fix

### Batch 1 — parser + contracts (v0.139)

1. `ait/info_requests.py` — `extract_info_requests`: after the existing `info_requests` list check, also accept `obj.get("type") == "info_requests"` and read the slug array from the first list-valued key among `slugs`, `requests`, `documents`, `docs`. Same slug validation/dedup/cap applies. (~15 lines)
2. `ait/common.py` — make the info_requests instruction explicit in the three contracts that mention it (`DOC_UPDATE_CONTRACT`, `ROADMAP_UPDATE_CONTRACT`, `PROJECT_DOCS_UPDATE_CONTRACT`): replace "Request any on-request project documents you need via info_requests before answering." with wording that shows the literal shape, e.g. `To request documents, reply with ONLY {"info_requests":["slug", ...]} — no other fields — then continue after the documents are provided.`
3. Verify: `python -m py_compile ait/info_requests.py ait/common.py`; quick unit check that all three reply shapes extract slugs and a `doc_update` reply still returns `[]`.
4. Docs: VERSIONS.md 0.139 entry; DOCUMENTATION.md §4.2/§4.3 mention of info_requests updated if wording changes are user-visible (minor — likely just VERSIONS).
5. Commit + push.

## Impact analysis

- **Modified:** `ait/info_requests.py` (extractor only — `call_task_provider` loop unchanged), `ait/common.py` (3 contract strings), `documentation/VERSIONS.md`.
- **No call-site changes** — flat namespace, same function signature.
- **Regression surface:** info_requests detection for all task types (pm_planning, doc_update, roadmap_update, doc_edit, chain tasks). Tolerant parsing only *adds* accepted shapes; the canonical `{"info_requests":[...]}` path is untouched. Risk: a `doc_update`/`doc_edit` reply that happens to contain `"type":"info_requests"` plus a stray list key — impossible in practice since those contracts use different `type` values.
- **Note:** T-0019 itself stays Rework needed; user can re-run "Update docs" after deploy.

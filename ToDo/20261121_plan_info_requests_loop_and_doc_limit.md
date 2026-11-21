# Plan: Fix info_requests loop + raise doc size limit (Option C)

Date: 2026-11-21 · Version targets: 0.148 / 0.149

Context: T-0022 showed two issues — (1) the `info_requests` loop resends all cached docs every round (~100k chars context), dropped slugs look "new" again, and tracker/vision slugs are requestable noise; (2) a valid ~25k-char `outcomes` reply was rejected by `MAX_DOC_CHARS = 20000`.

## Batch 1 — info_requests loop fix (v0.148)

`ait/info_requests.py`:

1. **Incremental context** — track `sent_slugs`; each round's `extra_context` contains full text only for *newly* provided docs plus a compact header listing all provided slugs ("Already provided — do not request again: …"). Replaces `requested_documents_text(cache)` full-resend.
2. **Dropped-slug feedback** — add `extract_info_requests_full(reply) -> (accepted, dropped)` (valid slugs over `INFO_REQUEST_MAX_DOCS` or filtered-out); keep `extract_info_requests` as a wrapper for existing callers. When slugs are dropped/filtered, append a note to `extra_context`: "Not provided (limit/unknown): … — do not request them again."
3. **Requestable-slug filter** — exclude `project_vision` (auto-provided to PM/PO, user-owned) and tracker slugs (`roadmap_tracker`, `phase_*_tracker`, auto-generated) from `info_requests` for all callers; they are returned in the dropped list so the model is told why.

`ait/common.py`: no constant changes needed (reuse `PHASE_TRACKER_DOC_RE`, `ROADMAP_TRACKER_DOC_SLUG`).

Verify: `py_compile`; unit-check extract variants, incremental context text, filter behavior.

Docs: VERSIONS.md 0.148; DOCUMENTATION.md §4.4 info_requests sentence updated. Commit + push.

## Batch 2 — Raise doc size limit (v0.149)

1. `ait/doc_updates.py`: `MAX_DOC_CHARS` 20000 → 30000.
2. `ait/info_requests.py`: `requested_documents_text` per-doc truncation `body[:20000]` → `body[:MAX_DOC_CHARS]`.
3. `ait/common.py` + `ait/doc_updates.py` contracts: update "under 20,000 characters" → "under 30,000 characters" in `DOC_UPDATE_CONTRACT`, `doc_update_contract`, `PROJECT_DOCS_UPDATE_CONTRACT`, `ROADMAP_UPDATE_CONTRACT` (and any other contract mentioning the limit — grep first).
4. Docs: VERSIONS.md 0.149; DOCUMENTATION.md mentions of the 20k limit updated. Commit + push.

## Post-step protocol

Version bumps (ABOUT/README/DOCUMENTATION headers + VERSIONS.md) → commit + push → next batch.

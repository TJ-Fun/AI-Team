# Plan: `project_vision` doc + staged PM replan chains + doc-maintenance improvements

**Status: EXECUTED — all 5 batches shipped (v0.111–v0.115, commits through b03f5cf).**

## Goal
1. Add `project_vision` — PM-maintained user↔PM direction document (auto for PM/PO, on-request others).
2. Replace single-shot "Update roadmap" with a **chain of trackable PM tasks** staged as: Review → Vision → Docs → Roadmap → Board delta. App-side chaining; every step is a normal task row (trackable, retryable).
3. Doc-maintenance extras: auto-append `outcomes` on task completion, "last updated" staleness in doc map. (`decisions` already auto-appends on approved design changes.)

## Approved design decisions
- Doc updates inside an approved chain stage **auto-apply**; stage report shows what changed (snapshots allow restore).
- Board-delta stage: PM proposes full mutation list → **one approval per stage** → apply.
- Vision/Roadmap stage *content* still requires approval before applying (it's the deliverable, not routine sync).
- Chaining is **app-driven**: when a chain task reaches Done, app creates the next stage task. PM stays stateless.
- PM chain tasks get all context docs injected (not just brief+master_plan) so PM checks old decisions and doesn't repeat answered questions.

## Mechanics (per stage)
1. Stage **plan task** (PM/PO): PM returns chain plan — bounded subtasks + approach. User approves plan.
2. App creates **execution subtasks** sequentially (chain_id, stage, seq; next stays Draft until previous Done).
3. Subtasks do the work: Review→report; Vision/Docs→doc writes (auto-apply); Roadmap→plan proposal.
4. Stage **gate**: report/proposal → approve (vision, roadmap, board-delta) or auto-complete (review, docs).
5. App creates next stage plan task. Failure = task Blocked, chain stalls visibly, retry via existing task retry.

## Impact analysis

### Batch 1 — `project_vision` registration
- `project_templates/project_vision.md` — NEW template.
- `ait/common.py`: `PROJECT_CONTEXT_FILES`, `PROJECT_DOC_DESCRIPTIONS`, `ROLE_AUTO_DOCS["PM/PO"]`, `PM_PLANNING_CONTRACT` (add to project_overview), `DOC_UPDATE_CONTRACT` (five docs).
- `ait/pm_planning_parse.py`, `ait/chat.py`: overview validation — add slug.
- `ait/doc_updates.py`: `DOC_UPDATE_SLUGS`.
- `prompts/pm_po.md`: PM maintains vision; consults it in planning/roadmap.
- Verify `static/js/project-context.js` renders generically.

### Batch 2 — Doc maintenance
- Auto-append `outcomes` line when a deliverable task reaches Done (hook in task completion path).
- Doc map: add "last updated" per doc (needs `updated_at` tracking on context saves — check `save_project_context_document`).

### Batch 3 — Chain infrastructure
- Task schema: `chain_id`, `chain_stage`, `chain_seq` fields.
- App-side chain driver: on chain task Done → create next (in dispatcher/completion hook).
- Chain plan contract: PM planning variant returning PM/PO subtasks bound to chain.
- "Update roadmap" button → creates Stage-1 (Review) plan task instead of single roadmap_update. Keep old path? (Decision: replace; roadmap_update becomes the Roadmap *stage* gate.)

### Batch 4 — Stages Review/Vision/Docs/Roadmap
- Review stage: analysis report task type.
- Vision stage: vision proposal → approval → apply.
- Docs stage: subtask doc writes auto-apply via `save_project_context_document`.
- Roadmap stage: reuse `roadmap_update` approval machinery.

### Batch 5 — Board delta
- New payload type: mutations [{op: cancel|supersede|create, task_id, ...}].
- Approval task → apply mutations (cancel/supersede respect in-progress guards; create = normal task creation).
- PM prompt rules: never repeat answered questions; check decisions/outcomes first.

## Risks
- Approval-model change (auto-apply docs) is the biggest behavioral shift — contained to chain tasks only.
- Board mutations on Done tasks: supersede only, never delete; in-progress tasks block the stage.
- Token cost: chain stages multiply PM calls — acceptable for major replans only (manual trigger).

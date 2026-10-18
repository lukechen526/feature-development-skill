---
name: feature-development
description: Coordinate OpenSpec feature plans, lane dispatch/review, and gated integration. Use for feature orchestration, not standalone edits or explanations.
license: MIT
compatibility: "Requires the openspec CLI, the repository role agents under .agents/agents/, and one worker-dispatch mechanism."
---
# Feature Development Coordinator

Deliver the requested feature-workflow outcome, not automatically the entire release lifecycle.
Continue authorized work until that outcome, a concrete blocker, or a required human gate.
Coordinator manages planning/integration; dispatched workers write implementation/tests/UI fixes. Reviews remain independent.

Records: committed `tasks.md` ticks = completion; committed `plan/graph-plan.json` = contract;
feature-branch commits = merges. Derived state and scratch never prove completion or approval.
Lane lifecycle: implementation → review 1 → fix → review 2 (two-round max); then Oracle-authored
fix → independent review → human gate. Lanes test on a single newest runtime only; after all
lanes merge, human classification and required version/generated changes are committed
before final release capture/review on integrated HEAD, then separate human approval opens
PR (no additional Oracle review before PR). A recorded low-risk UI checkpoint may defer
only on the opt-in path, with direct human three-runtime integrated inspection before PR;
this change itself retains legacy early checkpoints and full release matrix.
Implementation worker timeout: 60 minutes per dispatch, dispatched `async: true` (background —
session stays interactive). Workers are resumable: on timeout, recover the run id via
`subagent status`/`children.list` and revive with `subagent resume` (30-day session retention;
only stopped runs are non-resumable). Implementation briefs also require a live report file
updated after each commit/capture/check so a fresh worker can resume from the report alone if
revive fails.

## Context

Follow `AGENTS.md`, including required reads. Discover change with `openspec list --json`;
verify `openspec --version` when establishing CLI availability. Reuse relevant active change;
use `openspec-propose` only for new work. Approved plan/specs define scope.
Resume from committed artifacts and current bindings; never restart completed phases merely to follow this router.
Missing required helper or unmet human gate: stop; never invent substitutes or consent.

## Phased order — run the steps in sequence, one at a time

The Coordinator MUST work through these steps strictly in order. Do NOT batch, reorder, or skip ahead. Each step completes (including its human/approval gate, where present) before the next begins. When resuming an interrupted run, re-derive from committed artifacts and continue at the current step; never restart completed steps merely to follow this router.

1. **Step 1 — Role & model selection** (`workflow/roles-and-models.md`): ask the human once (defaults vs per-role override), record the selection, preflight and lock the dispatch mechanism before any worker or Oracle dispatch.
2. **Step 2 — Plan** (`workflow/plan.md`): author `plan/graph-plan.json` + OpenSpec artifacts, validate, dispatch Oracle `plan_review`, obtain human approval, commit the approval checkpoint naming the approver. NO implementation dispatch before that checkpoint commit.
3. **Step 3 — Lanes** (`workflow/lane.md`): implement/review/integrate lanes; current-HEAD reviews, bounded corrections, merge/close gates, risk-based review decisions and ancestor reuse (opt-in/shadow; legacy gates stay authoritative for this change), human-override merges (`lane-merge --human-override --override-reason`, waiving review duties only), Orca child-worktree registration of lanes.
4. **Step 4 — Finish** (`workflow/finish.md`): classification/version/integrated commit before final evidence, selected release checks, direct human integrated inspection for deferred UI, separate PR/merge approvals.
5. **Recovery** (`workflow/recovery.md`, any step): re-derive, never infer approval from missing scratch.

Before any dispatch, Coordinator reads `workflow/briefs-and-handoffs.md`; send only relevant filled template. Every role handoff follows `workflow/briefs-and-handoffs.md`.

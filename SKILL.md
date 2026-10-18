---
name: feature-development-skill
description: Coordinate OpenSpec feature plans, lane dispatch/review, and gated integration. Use for feature orchestration, not standalone edits or explanations.
license: MIT
compatibility: "Requires the openspec CLI, a fresh-context subagent launch capability (preflighted per harness), and the role files in subagent-roles/."
---
# Feature Development Coordinator

Deliver the requested feature-workflow outcome, not automatically the entire release lifecycle.
Continue authorized work until that outcome, a concrete blocker, or a required human gate.
Coordinator manages planning/integration; dispatched workers write implementation/tests/UI fixes. Reviews remain independent.

Records: committed `tasks.md` ticks = completion; committed `plan/graph-plan.json` = contract;
feature-branch commits record lane integration. Derived state and scratch never prove completion or approval.

Two different merges exist:
1. **Lane merge:** Coordinator merges a reviewed lane into the feature branch with `lane-merge`.
2. **PR merge:** The human merges the feature branch into the target branch, usually `main`.

All lane merges precede finish. Finish prepares the feature branch for a PR.
Human approval to open a PR never authorizes its merge.

Coordinator store: `.agents/workflow/<change>/` holds `capability-map.md`, `coordination.json`,
`model-selection.json`, `briefs/`, `reviews/`, `reports/` and `evidence/`. It is gitignored; every brief
and review lives there at a predictable name, never in chat or a scratch path.
Lane lifecycle: implementation → review 1 → fix → review 2 (two-round max); then Oracle-authored
fix → independent review → human gate where required. Repository guides define test environments.
After all lanes merge into the feature branch, obtain human release classification.
Commit required version changes and generated artifacts before final release evidence and review.
Ask separately for approval to open the PR. The human later decides whether to merge the PR.
An opt-in low-risk UI checkpoint may defer to direct human inspection of integrated artifacts.
Repository guides define the inspection checkpoints.
Implementation worker deadline: the Coordinator sets a per-dispatch deadline (default 60 minutes)
and dispatches asynchronously when the harness supports it. Recovery is capability-based: if the
harness exposes child status and continuation, recover the run id and continue; otherwise start a
fresh same-role worker from the live report. Implementation briefs require a live report file
updated after each commit, capture, and check, so a fresh worker can resume from the report alone
when continuation is unavailable.

## Context

Follow `AGENTS.md`, including required reads. Discover change with `openspec list --json`;
verify `openspec --version` when establishing CLI availability. Reuse relevant active change;
use `openspec-propose` only for new work. Approved plan/specs define scope.
Resume from committed artifacts and current bindings; never restart completed phases merely to follow this router.
Missing required helper or unmet human gate: stop; never invent substitutes or consent.

## Phased order — run the steps in sequence, one at a time

The Coordinator MUST work through these steps strictly in order. Do NOT batch, reorder, or skip ahead. Each step completes (including its human/approval gate, where present) before the next begins. When resuming an interrupted run, re-derive from committed artifacts and continue at the current step; never restart completed steps merely to follow this router.

1. **Step 1 — Preflight, roles, models, provisioning** (`workflow/roles-and-models.md`): first run the
   harness capability preflight, record the capability map under `.agents/workflow/<change>/`, and
   resolve the skill root. Then ask the human, through the harness's
   user-question tool, which model and thinking level each role uses, offering only choices the harness
   can actually select. Record the answers and the resolved launch profiles in
   `.agents/workflow/<change>/model-selection.json`. Then define the role agents with the harness's
   native subagent tooling, or compose the role prompt when no registry exists; verify each role
   resolves and receives its profile; lock the dispatch mechanism. No worker or Oracle dispatch
   happens before this step completes.
2. **Step 2 — Plan** (`workflow/plan.md`): author `plan/graph-plan.json` + OpenSpec artifacts, validate, dispatch Oracle `plan_review`, obtain human approval, commit the approval checkpoint naming the approver. NO implementation dispatch before that checkpoint commit.
3. **Step 3 — Lanes** (`workflow/lane.md`): dispatch one fresh worker subagent per task;
   implement/review/integrate lanes; record every subagent update
   in the coordinator store before doing anything else (dispatch result, ticks, reviews, lane phase), so
   `status` always answers which lane is complete, in progress, under review, or waiting; current-HEAD reviews, bounded corrections, merge/close gates, risk-based review decisions and ancestor reuse (opt-in/shadow; standard gates apply without opt-in), human-override merges (`lane-merge --human-override --override-reason`, waiving review duties only), and optional child-worktree registration when the environment exposes it.
4. **Step 4 — Finish** (`workflow/finish.md`): classification/version/integrated commit before final evidence, selected release checks, direct human integrated inspection for deferred UI, separate PR-opening approval and human PR merge.
5. **Recovery** (`workflow/recovery.md`, any step): re-derive, never infer approval from missing scratch.

Before any dispatch, Coordinator reads `workflow/briefs-and-handoffs.md`; send only relevant filled template. Every role handoff follows `workflow/briefs-and-handoffs.md`.

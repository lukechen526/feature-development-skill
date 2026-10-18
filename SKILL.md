---
name: feature-development-skill
description: Coordinate an OpenSpec feature from approved plan through isolated lane implementation, independent review, gated integration, and release preparation. Use when a feature needs multi-task orchestration, plan/lane reviews, and human approval checkpoints; not for standalone edits or explanations.
license: MIT
compatibility: "Requires the openspec CLI, a fresh-context subagent launch capability (preflighted per harness), and the role files in subagent-roles/."
---
# Multi-Phase Feature Development Workflow

## What this skill does

This skill runs feature work as a sequence of gated phases instead of a single edit session:

1. **Preflight and provision** — inspect how subagents actually launch in this harness, record the
   harness capability map, choose one role/model/thinking profile per role, and define the role agents.
2. **Plan** — turn an OpenSpec change into an approved `plan/graph-plan.json`, get an independent Oracle
   plan review, and record explicit human approval before any implementation begins.
3. **Lanes** — implement each task in an isolated lane worktree, run independent code/test/UI reviews,
   correct within a two-round limit, escalate to Oracle-authored fixes when needed, and merge only
   reviewed lanes into the feature branch.
4. **Finish** — obtain the human release classification, prepare version/generated artifacts, capture
   release evidence on the committed integrated `HEAD`, run required human inspections, and ask
   separately for approval to open the PR.
5. **Recover** — resume from committed artifacts and recorded bindings; never reconstruct approval from
   scratch or chat history.

The Coordinator plans, dispatches, records, and gates. Dispatched subagents write implementation, tests,
UI fixes, reviews, and Oracle advisories. Reviews stay independent of the author. The Coordinator never
patches its own implementation or approves its own work.

## When to use it

Use this skill when a feature has multiple tasks or lanes, needs an approved plan and independent
reviews, or must pass human release gates before a PR. It is designed for OpenSpec changes and for
repositories whose testing, release, and inspection rules live in their own guides.

Do not use it for standalone edits, one-off explanations, or changes that do not need planning, lane
isolation, independent review, and gated integration.

## Roles

| Role ID | Responsibility | Authority |
|---|---|---|
| Coordinator | Plan, dispatch, record, review gates, lane merges, finish sequence | No implementation patches; no self-approval |
| `implementer` | Implementation, tests, ticks, live report | Writes only the assigned lane worktree |
| `code` | Implementation/task completeness; ordinary lanes also cover test adequacy | Read-only review; never patches |
| `test` | Test adequacy, coverage, quality, fixtures, mutation testing | Read-only review; high-risk or selected test duty |
| `ui` | Supplied rendered-UI evidence against approved requirements | Read-only review; never launches the app |
| `oracle` | Plan review, mediation, authorized bounded fixes | Advice by default; authors only with explicit authorization |
| `runner` | Long-running checks, builds, captures, external polling | Observations only; never issues a verdict |
| Human | Model choices, approvals, release classification, PR merge | Sole source of consent |

## Workflow at a glance

```text
Preflight → Plan + Oracle review + human approval
          → Lanes: implement → review → fix → review 2 → mediate/merge
          → Finish: classify → prepare/commit → capture → inspect → pr-check
          → ask to open PR → human merges PR
```

Two different merges exist:

1. **Lane merge:** the Coordinator merges a reviewed lane into the feature branch with `lane-merge`.
2. **PR merge:** the human merges the feature branch into the target branch, usually `main`.

All lane merges precede finish. Finish prepares the feature branch for a PR. Human approval to open a PR
never authorizes its merge.

## What proves completion and approval

- Committed `tasks.md` ticks prove task completion.
- The committed `plan/graph-plan.json` is the plan contract.
- Feature-branch commits record lane integration.
- Recorded reviews, human gates, and finish artifacts prove their own decisions.
- Derived state, dashboards, chat history, and scratch files never prove completion or approval.

## Coordinator store

`.agents/workflow/` is gitignored and holds coordinator-owned artifacts:

- repo-level `capability-map.md` and `model-selection.json` (written once per repository and reused by
  every change; a different selection requires an explicit human-approved update);
- per-change `<change>/plan-reviews.json`, `coordination.json`, `briefs/`, `reviews/`, `reports/`, and
  `evidence/`.

Every brief and review lives at a predictable path under that store, never in chat or a scratch path. A
later session must be able to find them without asking the Coordinator.

## Lane lifecycle

Implementation → review 1 → fix → review 2 (two-round maximum). If review 2 still returns
`MORE WORK`, the Coordinator records Oracle mediation, the Oracle authors the fix, a different
independent reviewer passes it, and the human gate runs where required. Repository guides define test
environments.

Implementation dispatches have a deadline (default 60 minutes) and run asynchronously when the harness
supports it. Recovery is capability-based: continue the child when the harness exposes status and
continuation; otherwise start a fresh same-role worker from the live report. Implementation briefs
require a live report updated after every commit, capture, and check so a fresh worker can resume from
the report alone.

## Phased order — run the steps in sequence, one at a time

The Coordinator MUST work through these steps strictly in order. Do NOT batch, reorder, or skip ahead.
Each step completes (including its human/approval gate, where present) before the next begins. When
resuming an interrupted run, re-derive from committed artifacts and continue at the current step; never
restart completed steps merely to follow this router.

1. **Step 1 — Preflight, roles, models, provisioning** (`workflow/preflight-and-roles.md`): run the harness
   capability preflight once per repository/harness, record the repo-level capability map at
   `.agents/workflow/capability-map.md`, and resolve the skill root. Ask the human, through the harness's
   user-question tool, which model and thinking level each role uses, offering only choices the harness
   can actually select. Record the answers once in the repo-level
   `.agents/workflow/model-selection.json`. Define the role agents with the harness's native subagent
   tooling, or compose the role prompt when no registry exists; verify each role receives its profile;
   lock the dispatch mechanism. No role dispatch happens before this step completes.
2. **Step 2 — Plan** (`workflow/plan.md`): author `plan/graph-plan.json` plus the OpenSpec artifacts,
   validate them, record the Oracle plan-review dispatch with `plan-dispatch`, record its verdict with
   `plan-review`, obtain explicit human approval, and commit the approval checkpoint naming the
   approver. NO implementation dispatch before that checkpoint commit.
3. **Step 3 — Lanes** (`workflow/lane.md`):
   - dispatch one fresh implementer subagent per task;
   - implement, review, correct, and integrate lanes;
   - record every dispatch result, tick, review, and lane phase in the coordinator store before doing
     anything else, so `status` always answers which lane is complete, in progress, under review, or
     waiting;
   - run current-`HEAD` reviews, bounded corrections, merge/close gates, risk-based review decisions,
     ancestor reuse (opt-in/shadow), and human-override merges (`lane-merge --human-override
     --override-reason`, waiving review duties only);
   - delegate long checks or external polling to the read-only Runner when appropriate; Runner reports
     observations and never issues a verdict.
4. **Step 4 — Finish** (`workflow/finish.md`): obtain the human release classification, apply
   version/generated changes and commit the preparation, then run `finish-record classify` with the
   selected checks, capture, required human inspections, and `pr-check`. Ask separately for approval to
   open the PR; the human decides whether to merge it.
5. **Recovery** (`workflow/recovery.md`, any step): re-derive from committed artifacts and recorded
   bindings; never infer approval from missing scratch or chat history.

Before any dispatch, the Coordinator reads `workflow/briefs-and-handoffs.md` and sends only the relevant
filled template. Every role handoff follows that contract.

## Context and stopping rules

Follow `AGENTS.md`, including required reads. Discover the change with `openspec list --json`; verify
`openspec --version` when establishing CLI availability. Reuse a relevant active change; use
`openspec-propose` only for new work. Approved plan/specs define scope.

Resume from committed artifacts and current bindings; never restart completed phases merely to follow
this router. If a required helper is missing or a human gate is unmet, stop and report it; never invent
substitutes or consent.

## Non-negotiable gates

- No implementation dispatch before the approved-plan checkpoint commit.
- Every required review must be independent and must bind the exact current lane `HEAD`.
- The Coordinator never patches or approves its own implementation; reviewers never patch reviewed work;
  the Oracle never self-approves.
- No lane merge without the required current reviews, ticks, evidence, and human gates.
- The Runner never issues `PASS`/`MORE WORK` and never satisfies a review duty.
- No agent opens or merges a PR without the separate human approval required by the finish phase.
- Missing or stale records block; they are never replaced by chat history.

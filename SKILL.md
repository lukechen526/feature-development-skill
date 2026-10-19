---
name: feature-development-skill
description: Coordinate an OpenSpec feature from approved plan through isolated lane implementation, independent review, gated integration, release preparation, and PR follow-up. Use when a feature needs multi-task orchestration, plan/lane reviews, and human approval checkpoints; not for standalone edits or explanations.
license: MIT
compatibility: "Requires Python 3.9+, Git with worktree support, the OpenSpec CLI, and a harness that supports fresh-context subagents, working-directory selection, and role prompt injection."
---
# Multi-Phase Feature Development Workflow

## What this skill does

This skill runs feature work as a sequence of gated phases instead of a single edit session:

1. **Preflight and provision** — inspect how subagents actually launch in this harness, record the
   harness capability map, choose one role/model/thinking profile per role, and define the role agents.
2. **Plan** — obtain an Oracle artifact review for each OpenSpec change, then a separate Oracle review
   of the repo-level `.agents/workflow/plan-graph.json`. Obtain human approval before implementation.
3. **Lanes** — implement each task in an isolated lane worktree, run independent code/test/UI reviews,
   correct within a two-round limit, escalate to Oracle-authored fixes when needed, and merge only
   reviewed lanes into the feature branch.
4. **Finish** — obtain the human release classification, prepare version/generated artifacts, capture
   release evidence on the committed integrated `HEAD`, run required human inspections, and ask
   separately for approval to open the PR. After opening, monitor PR-triggered automated checks and bot code-review comments.
   Route actionable findings through corrections and independent review. Sync and archive included OpenSpec changes before human PR merge.

Recovery is an exception path, not another phase. Use the [recovery triggers](workflow/recovery.md#when-to-use-recovery) after interruption or uncertain state.

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
| `coordinator` | Plan, dispatch, record, review gates, lane merges, finish sequence | No implementation patches; no self-approval |
| [`implementer`](subagent-roles/implementer.md) | Implementation, tests, ticks, live report | Writes only the assigned lane or finish-preparation worktree |
| [`code-reviewer`](subagent-roles/code-reviewer.md) | Implementation/task completeness; ordinary lanes also cover test adequacy | Read-only review; never patches |
| [`test-reviewer`](subagent-roles/test-reviewer.md) | Test adequacy, coverage, quality, fixtures, mutation testing | Read-only review; high-risk lanes |
| [`ui-reviewer`](subagent-roles/ui-reviewer.md) | Supplied rendered-UI evidence against approved requirements | Read-only review; never launches the app |
| [`oracle`](subagent-roles/oracle.md) | Plan review, mediation, authorized bounded fixes | Advice by default; authors only with explicit authorization |
| [`runner`](subagent-roles/runner.md) | Long-running checks, builds, captures, external polling | Observations only; never issues a verdict |
| Human | Model choices, approvals, release classification, PR merge | Sole source of consent |

Command syntax and flags: [helper command reference](workflow/helper-commands.md).

## Workflow at a glance

```text
Preflight → each change: OpenSpec artifact review → repo graph review → human approval
          → Lanes: implement → review → fix → review 2 → mediate/merge
          → Finish: classify → prepare/commit → capture → inspect → pr-check
          → ask to open PR → PR checks + bot reviews → fixes/rechecks
          → sync/archive OpenSpec → PR rechecks → human merges PR
```

Two different merges exist:

1. **Lane merge:** the Coordinator merges a reviewed lane into the feature branch with `lane-merge`.
2. **PR merge:** the human merges the feature branch into the target branch, usually `main`.

All lanes included in a PR merge before finish. Record finish separately for every included change at the final integrated HEAD.
Unrelated changes in the repository graph need not finish for that PR.
Finish includes post-PR follow-up. Human approval to open a PR never authorizes its merge.

## What proves completion and approval

- Committed `tasks.md` ticks prove task completion.
- The local `.agents/workflow/plan-graph.json` defines the repo-level plan; recorded reviews bind its digests.
- OpenSpec artifacts and task ticks remain committed; the graph, coordination file, and approval record never are.
- The repo-level `plan-approval.json` record binds the committed artifact digests and the reviewed graph digest.
- Feature-branch commits record lane integration.
- Recorded reviews, human gates, and finish artifacts prove their own decisions.
- Derived state, dashboards, chat history, and scratch files never prove completion or approval.

## Coordinator store

`.agents/workflow/` is gitignored and holds coordinator-owned artifacts:

- repo-level `capability-map.md` and `model-selection.json` (written once per repository and reused by
  every change; a different selection requires an explicit human-approved update);
- repo-level `plan-graph.json` and `coordination.json` with `changes.<change>.lanes.<lane>.tasks.<task>`;
- repo-level `plan-approval.json` naming the approver, HEAD, graph digest, and artifact digests;
- repo-level `plan-graph-review.json` and `reviews/` for the separate graph review;
- per-change `<change>/plan-reviews.json`, `briefs/`, `reviews/`, `reports/`, and `evidence/`.

The graph, coordination file, and approval record live together in this ignored store. None is committed.
The graph and coordination file group tasks under lanes and lanes under changes.
Use `--change <change>` for change-specific commands when the graph contains multiple changes.
One Coordinator owns the shared store across all changes. Only the Coordinator writes coordination.
A bounded Oracle planning correction may temporarily own graph writes while the Coordinator pauses graph mutations.
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
resuming an interrupted run, follow [recovery routing](#recovery-routing) before further mutations.
Resume the owning phase at its first incomplete operation; never restart completed steps merely to follow this router.

1. **Step 1 — Preflight, roles, models, provisioning** ([workflow/preflight-and-roles.md](workflow/preflight-and-roles.md)): run the harness
   capability preflight once per repository/harness, record the repo-level capability map at
   `.agents/workflow/capability-map.md`, and resolve the skill root. Ask the human, through the harness's
   user-question tool, which model and thinking level each role uses, offering only choices the harness
   can actually select. Record the answers once in the repo-level
   `.agents/workflow/model-selection.json`. Define the role agents with the harness's native subagent
   tooling, or compose the role prompt when no registry exists; verify each role receives its profile;
   lock the dispatch mechanism. No role dispatch happens before this step completes.
2. **Step 2 — Plan** ([workflow/plan.md](workflow/plan.md)): author each change's OpenSpec artifacts.
   Record each Oracle artifact review with `plan-dispatch` / `plan-review --scope change`.
   After every included change passes, author and validate the local repo-level graph.
   Record a separate Oracle graph review with `plan-dispatch` / `plan-review --scope graph`.
   Obtain human approval, commit only the OpenSpec artifacts, and record the approval with `plan-approve`.
   Never commit the graph, coordination file, or approval record. NO implementation dispatch before that
   committed and recorded state.
3. **Step 3 — Lanes** ([workflow/lane.md](workflow/lane.md)):
   - dispatch one fresh implementer subagent per task;
   - implement, review, correct, and integrate lanes;
   - record every dispatch result, tick, review, and lane phase in the coordinator store before doing
     anything else, so `status` always answers which lane is complete, in progress, under review, or
     waiting;
   - run direct current-`HEAD` reviews, bounded corrections, and merge/close gates;
   - follow the explicit [Human Override policy](workflow/lane.md#human-override) for human-authorized exceptions;
   - delegate long checks or external polling to the read-only Runner when appropriate; Runner reports
     observations and never issues a verdict.
4. **Step 4 — Finish** ([workflow/finish.md](workflow/finish.md)): obtain the human release classification, apply
   version/generated changes and commit the preparation, then run `finish-record classify` with the
   selected checks, capture, required human inspections, and `pr-check`. Ask separately for approval to
   open the PR. After opening, monitor PR-triggered checks and bot code-review comments using the
   [post-PR follow-up](workflow/finish.md#post-pr-follow-up). Correct actionable findings and recheck the updated PR.
   [Sync and archive OpenSpec artifacts](workflow/finish.md#openspec-sync-and-archive) on the feature branch before the final merge handoff.
   Recheck the archival push. Later corrections amend archived changes through newly approved tasks and lanes.
   Use finish-scoped dispatch for preparation and monitoring after lane integration; never reopen completed lanes for release work.
   The human decides whether to merge the PR.

Before any dispatch, the Coordinator reads [workflow/briefs-and-handoffs.md](workflow/briefs-and-handoffs.md) and sends only the relevant
filled template. Every role handoff follows that contract.

## Recovery routing

Recovery can interrupt any phase, including post-PR follow-up. It is not Step 5 and does not follow successful finish.
Enter [recovery](workflow/recovery.md#when-to-use-recovery) before further affected actions when:

- a Coordinator session resumes after an interruption;
- a worker stops, times out, or disappears with uncertain ownership or outcome;
- expected local objects, review records, or approval bindings are unexpectedly missing, unreadable, stale, or inconsistent with the actual work;
- a dispatch, merge, cleanup, or record write partially succeeds, leaving its outcome uncertain.

Pause the affected work. Pause all coordinator writes if the shared objects are unreliable.
Reconcile local records, Git facts, and worker ownership using the recovery guide.
Resume the owning phase only when the state is reliable and the next incomplete operation is known.
Missing approval remains a gate in that phase; recovery never grants it. Unresolved ownership or inconsistent state requires the human.

Normal review corrections, known check failures, and planned human approval waits stay in their phase.
They do not trigger recovery while the records and execution state remain trustworthy.
Legacy migration sections apply only when older records need conversion, not on every run.

## Context and stopping rules

Follow `AGENTS.md`, including required reads. Discover the change with `openspec list --json`; verify
`openspec --version` when establishing CLI availability. Reuse a relevant active change; use
`openspec-propose` only for new work. Approved plan/specs define scope.

Resume from the local plan/coordination objects, committed OpenSpec artifacts, and current bindings.
Use [recovery routing](#recovery-routing) after an interruption; never restart completed phases merely to follow this router. If a required helper is missing or a human gate is unmet, stop and report it; never invent
substitutes or consent.

## Non-negotiable gates

- No implementation dispatch before the approved plan is committed and the approval is recorded.
- Every required review must be independent and must bind the exact current lane `HEAD`.
- The Coordinator never patches or approves its own implementation; reviewers never patch reviewed work;
  the Oracle never self-approves.
- No lane merge without the required current reviews, ticks, evidence, and human gates.
- The Runner never issues `PASS`/`MORE WORK` and never satisfies a review duty.
- No agent opens or merges a PR without the separate human approval required by the finish phase.
- Missing or stale records block; they are never replaced by chat history.

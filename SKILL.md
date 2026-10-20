---
name: feature-development-skill
description: Coordinate an OpenSpec feature from approved plan through isolated lane implementation, independent review, gated integration, release preparation, and PR follow-up. Use when a feature needs multi-task orchestration, plan/lane reviews, and human approval checkpoints; not for standalone edits or explanations.
license: MIT
compatibility: "Requires Python 3.9+, Git with worktree support, the OpenSpec CLI, and a harness that supports fresh-context subagents, working-directory selection, and role prompt injection."
---
# Feature Development Workflow

## When to use it

Use this skill for multi-task OpenSpec feature work that needs coordination,
independent review, and human approval gates. Repository guides supply the
project-specific testing, release, and inspection rules.

Skip this skill for standalone edits, one-off explanations, or work that does
not need this coordination.

## Workflow stages and steps

Read and follow each linked guide before starting that phase. Complete each phase
and its gates before starting the next. Do not batch, reorder, or skip phase gates.

1. **[Preflight and roles](references/preflight-and-roles.md).** Verify harness
   capabilities, resolve the skill root, obtain human-selected role/model/thinking
   profiles, and provision roles. Complete preflight before any role dispatch.
2. **[Plan](references/plan.md).** Author the OpenSpec artifacts. Obtain separate
   Oracle reviews of each change's artifacts and then the whole repository graph.
   Obtain human approval, commit the approved artifacts, and record approval with
   [plan-approve](references/helper-commands.md#plan-approval) in
   `.agents/workflow/plan-approval.json` before implementation.
3. **[Lanes](references/lane.md).** Before opening or merging a lane, the
   Coordinator must confirm `status` reports `plan_approval.state` as `current`;
   the helper records and displays the approval but does not enforce it in
   `lane-open` or `lane-merge`. Dispatch isolated, scoped implementation per task.
   Obtain independent reviews of the current lane `HEAD`, correct findings within
   the documented review limits, and integrate only through the lane gates.
4. **[Finish](references/finish.md).** Start after every lane of every change in the
   repository graph integrates. Finish is repository-level: one record at
   `.agents/workflow/finish.json` covers the PR, which may include one or more
   OpenSpec changes. Obtain human release classification, commit release preparation,
   and complete required evidence, reviews, and human inspections. Before PR consent,
   the Coordinator verifies the finish review record at
   `.agents/workflow/finish/reviews/finish-<role>-<head>.md` binds its verdict,
   exact HEAD, inputs/evidence revision, limitations, and reviewer identity;
   `pr-check` does not validate it. Obtain separate consent to open the PR,
   recorded in `.agents/workflow/finish/pr-follow-up.md` (human decision,
   timestamp, HEAD, included changes) and distinct from merge consent; only the
   human merges. Resolve post-PR findings through corrections, independent review,
   and renewed checks. Sync and archive included OpenSpec changes, recheck the PR,
   and hand off to the human for PR merge.

Before every dispatch, read [briefs and handoffs](references/briefs-and-handoffs.md)
and send the relevant filled template. Use [the helper](scripts/featdev.py) through
[the invocation contract and CLI reference](references/helper-commands.md#invocation-and-selection).
That contract requires execution from the consuming feature checkout, not the
installed skill directory.

## Recovery

Use [recovery](references/recovery.md) after interruption, uncertain worker
ownership, missing/stale/inconsistent records, or partially completed operations.
Pause affected mutations. Pause all Coordinator writes when shared records are
unreliable. Reconcile records, Git facts, and worker ownership before resuming the
owning phase's first incomplete operation. Stop for the human if reconciliation
fails. Recovery never recreates consent or restarts completed work. It is an
exception path, not a fifth stage.

## Non-negotiables

- Implementation requires human-approved committed planning artifacts and recorded approval.
- Model, thinking, and role-profile selection is repo-level only in
  `.agents/workflow/model-selection.json`. Never create a per-lane or per-worktree
  profile, and never put a lane worktree or per-dispatch path in a profile; the
  worktree is execution metadata in the brief and coordination.
- Keep implementation isolated and scoped. Required reviews must be independent
  and bind the exact current lane `HEAD`, subject only to documented exceptions.
- The Coordinator dispatches, records, and gates; it never patches implementation.
  Reviewers remain read-only. Authors, including Oracle authors, never self-approve.
  Runner observations never satisfy review duties or supply verdicts.
- Never fabricate completion, evidence, verdicts, or consent. Complete required
  task, review, evidence, and human gates. Only documented exceptions apply,
  including [Human Override and Oracle stand-in](references/lane.md#human-override).
- Missing or stale records block affected work. Chat never replaces those records.
  Stop and report missing required helpers or unmet human gates; never invent substitutes.
- PR-opening consent is a separate human decision recorded in
  `.agents/workflow/finish/pr-follow-up.md`; it is not merge consent. Only the
  human merges the PR.

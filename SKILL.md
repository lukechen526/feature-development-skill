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

Read and follow each linked guide before starting that phase. Complete each phase and its gates before starting the next, except for an explicit
[advance human waiver](references/lane.md#advance-human-waiver).

1. **[Preflight and roles](references/preflight-and-roles.md).** Verify harness
   capabilities, resolve the skill root, obtain human-selected role/model/thinking
   profiles, and provision roles. Complete preflight before any role dispatch.
2. **[Plan](references/plan.md).** Turn the human's feature intent into OpenSpec
   artifacts, reusing an existing change when one matches. Obtain separate Oracle
   reviews of each change's artifacts and then the whole repository graph.
   Obtain human approval or a scoped advance waiver, commit the artifacts, and record approval or waiver with
   [the appropriate command](references/helper-commands.md#plan-approval) before implementation.
3. **[Lanes](references/lane.md).** Opening a lane needs current plan approval or a current waiver covering that lane.
   Dispatch isolated, scoped implementation per task, obtain independent reviews of
   the current lane `HEAD`, correct findings within the documented review limits, and
   integrate through the lane gates or an explicit human override. Verify current approval before merge unless waived;
   `lane-merge` does not enforce it.
4. **[Finish](references/finish.md).** Start after every lane of every change in the
   repository graph integrates. Finish is repository-level: one record at
   `.agents/workflow/finish.json` covers the PR. Obtain human release classification,
   commit release preparation, and complete required evidence, reviews, and human
   inspections. Obtain separate, one-time consent to open the PR, recorded in
   `.agents/workflow/finish/pr-follow-up.md` and distinct from merge consent. Resolve
   post-PR findings through corrections, independent review, and renewed checks, then
   sync and archive included OpenSpec changes and hand off for the human's PR merge.

Before every dispatch, read [briefs and handoffs](references/briefs-and-handoffs.md)
and send the relevant filled template. Use [the helper](scripts/featdev.py) through
[the invocation contract and CLI reference](references/helper-commands.md#invocation-and-selection).
That contract requires execution from the consuming feature checkout, not the
installed skill directory.

## Recovery

Use [recovery](references/recovery.md) after interruption, uncertain worker
ownership, missing/stale/inconsistent records, or partially completed operations.
Pause affected mutations, reconcile records, Git facts, and worker ownership, then
resume the owning phase's first incomplete operation or stop for the human. Recovery
is an exception path, not a fifth stage: it never recreates consent or restarts
completed work.

## Human waiver precedence

An explicit human waiver may cover named human gates in advance for selected lanes
or all lanes. Follow [advance human waiver](references/lane.md#advance-human-waiver).
It takes precedence over conflicting pre-PR human-gate instructions in this skill.
It does not waive technical gates, grant consent, or extend to the pre-PR check or PR decisions.

## Lane override precedence

An explicit human decision for a named lane and candidate HEAD may waive any
lane review or merge rule in this skill. Record the exact waived gates and reason
as described in [Human Override](references/lane.md#human-override). Ordinary
approval does not waive other gates. This exception does not apply to other phases.

## Non-negotiables

- Implementation requires committed planning artifacts and recorded plan approval or an applicable plan-approval waiver.
- Model, thinking, and role-profile selection is repo-level only in
  `.agents/workflow/model-selection.json`. Never create a per-lane or per-worktree
  profile, and never put a lane worktree or per-dispatch path in one.
- Keep implementation isolated and scoped. Required reviews must be independent
  and bind the exact current lane `HEAD`, subject only to documented exceptions.
- The Coordinator dispatches, records, and gates; it never patches implementation.
  Reviewers remain read-only. Authors, including Oracle authors, never self-approve.
  Runner observations never satisfy review duties or supply verdicts.
- Never fabricate completion, evidence, verdicts, or consent. Complete required
  task, review, evidence, and human gates unless explicitly waived under the documented human waiver rules. Only documented exceptions apply,
  including [Human Override and Oracle stand-in](references/lane.md#human-override).
- Missing or stale records block affected work unless explicitly waived under the documented human waiver rules. Chat never replaces those records.
  Stop and report missing required helpers or unmet human gates; never invent substitutes.
- PR-opening consent is a separate, one-time human decision recorded in
  `.agents/workflow/finish/pr-follow-up.md`; it is not merge consent, and only the
  human merges the PR.

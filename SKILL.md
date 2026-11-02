---
name: feature-development-skill
description: Coordinate OpenSpec feature planning, isolated implementation lanes, independent reviews, release preparation, and PR follow-up with human approval gates.
license: MIT
---
# Feature Development Workflow

Use for features needing several coordinated tasks and independent review. For a small isolated edit, use the repository's normal workflow.

## Workflow

1. **Draft the plan.** Clarify the human's intent, then dispatch an Oracle to author or update the OpenSpec changes, acceptance criteria, and tasks. The Coordinator defines lanes, not OpenSpec content. See [planning](references/plan.md).
2. **Review and approve the initial plan.** Have a different, independent, read-only Oracle review the artifacts and lane plan together and return `PASS` or `MORE WORK`. Route artifact corrections to the authoring Oracle until the first `PASS`, then obtain human approval and commit the artifacts. Later human-requested plan changes need neither Oracle re-review nor another plan approval; record the initiating request and have the authoring Oracle update the artifacts.
3. **Open lanes, implement, review, integrate.** Use isolated worktrees, one writer per lane, and independent reviewers. Batch related tasks when useful. Correct findings and integrate after applicable technical and human gates pass. See [lanes](references/lane.md).
4. **Prepare and open the PR.** Complete release preparation, repository checks, required independent reviews and human inspections. Obtain the human's release classification and separate consent to open the PR. See [finish](references/finish.md).
5. **Address PR comments.** Monitor checks and reviews, resolve actionable findings through scoped implementation and independent review, then sync and archive completed OpenSpec changes. Continue until ready or explicitly hand monitoring back to the human.
6. **Hand off for human merge.** Present checks, findings, and mergeability. The human authorizes and performs the PR merge; agents never merge it.

## Boundaries

- The Coordinator coordinates planning, dispatches, records, and integrates; it does not author OpenSpec changes. A scoped Oracle authors them; implementation authors work in separate worktrees. Reviewers are independent of the work they approve and stay read-only. Runner output is evidence, not a verdict.
- Keep existing human gates: model choices, initial plan approval, UI/sensitive lane checkpoints, release classification, repository-required inspections, PR-opening consent, and final PR merge. Honor explicit [waivers, deferrals, and overrides](references/lane.md#human-decisions); do not infer them.
- Committed task ticks establish completion. Current review/evidence bindings establish technical readiness. Neither grants human consent.
- Follow repository instructions for tests, releases, environments, and evidence. Renew affected reviews and evidence when inputs change; do not repeat unaffected checks without reason.
- The Coordinator may reconstruct gitignored workflow artifacts using the current schema, including during breaking migrations. Preserve supported facts and mark unknowns; never fabricate approvals, verdicts, or evidence. See [recovery](references/recovery.md).

## Operating references

Start with [roles and capabilities](references/preflight-and-roles.md). Use [scoped briefs](references/briefs-and-handoffs.md) suited to the work; templates are examples. This skill authorizes scoped subagent delegation.

Run the [helper](scripts/featdev.py) from the consuming feature checkout, not the installed skill directory. [Commands](references/helper-commands.md), [current record schemas](references/workflow-records.md), and a [worked example](references/minimal-workflow.md) cover the mechanics. Read references as needed for the current phase.

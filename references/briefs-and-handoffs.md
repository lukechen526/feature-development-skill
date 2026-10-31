# Scoped briefs and handoffs

Briefs establish what a worker owns and what success means. Adapt the format to the task; no fixed field list or template is mandatory.

Supply the role, assigned tasks or review remit, applicable repository instructions, planning sources, acceptance criteria, owned paths and exclusions, absolute worktree, relevant base/candidate SHA, selected model settings, check/capture ownership, and report/evidence locations. Include a deadline appropriate to the work. Pass this contract and the relevant role prompt, without copying unrelated Coordinator history.

Independent reviewers inspect source artifacts and diffs directly. Implementation corrections may reuse a coherent session with updated findings and candidate SHA. Clarification does not authorize scope expansion or reviewer edits.

## Ownership

- The Coordinator owns planning and workflow records, dispatch, integration, and human decisions. It never patches implementation.
- Implementers write only in the assigned worktree and designated report/evidence paths.
- Reviewers stay read-only and cannot approve their own work. A Runner reports observations, never verdicts.
- Record actual dispatch outcomes and human decisions. Unknown results remain unknown.
- Keep role/model choices repo-level; put execution paths in briefs and coordination.

## Storage

Use `.agents/workflow/` for local records, briefs, reports, and evidence. The graph, coordination, initial review and approval, and finish records have the canonical paths in [record schemas](workflow-records.md). Brief/report filenames may describe the lane, role, task, or candidate; retain history rather than overwriting a previous verdict.

Keep lane evidence separate from integrated release evidence. Reports should identify the producing SHA, check commands/results, findings, limitations, and evidence paths. Large logs belong in files; handoffs carry decisive results and pointers.

Resolve and export `FEATDEV_SKILL_ROOT` in shells that invoke the helper. Workers do not assume the Coordinator's shell environment. Coordinator commands run from the consuming feature checkout; implementation, tests, task ticks, and commits run in the assigned worktree.

## Example implementation brief

> Implement tasks 1.1 and 1.2 sequentially in the lane worktree at the supplied absolute path. Follow the proposal, design, specs, and repository instructions. Own the listed API paths; exclude storage migrations. Run the focused checks, commit each completed task and its checkbox, and report commits, results, evidence, and limitations. One task-level dispatch at a time may use this same session.

## Example review brief

> Review the candidate SHA against its base, assigned tasks, and acceptance criteria. You are the independent code reviewer; cover test adequacy for this ordinary-risk lane. Stay read-only. Write PASS or MORE WORK with concrete findings and closure criteria to the supplied report path.

## Example Runner brief

> Run the specified integration check against the candidate SHA in the supplied worktree. You own this execution; other workers will inspect the result. Report command, inputs, outcome, logs, and limitations. Do not provide a review verdict.

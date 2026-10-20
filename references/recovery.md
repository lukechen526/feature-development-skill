# Recovery

Command syntax: [helper command reference](helper-commands.md).
Resolve and export `FEATDEV_SKILL_ROOT` to the absolute installed skill path before any helper command; never hardcode the location ([preflight](preflight-and-roles.md)).

## When to use recovery

Recovery is an exception path inside the current phase, not a final phase after finish.
The Coordinator enters it before further affected actions when any of these conditions applies:

| Trigger | Entry point |
|---|---|
| A Coordinator session resumes after an interruption | Before another dispatch, record mutation, merge, cleanup, or PR action |
| A worker stops, times out, or disappears with uncertain ownership or outcome | Before continuation or replacement in that worktree |
| Expected local objects or decision records are unexpectedly missing, unreadable, stale, or inconsistent with the actual work | Before trusting those records or changing the affected work |
| A dispatch, merge, cleanup, or record write partially succeeds | Before retrying the operation |

Routine new-repository setup is not recovery. Neither are normal review corrections, known failed checks, or planned human approval waits.
Those remain in their owning phase while records and execution state are trustworthy.
An evidence refresh already prescribed by the phase guide does not require restarting the workflow.

## Reconcile and resume

1. Pause the affected lane or phase.
2. Pause all coordinator writes if the shared local objects are unreliable.
3. Identify the interrupted phase, change, lane, and last confirmed operation.
4. Read the retained local records: `.agents/workflow/plan-graph.json`, `coordination.json`, `plan-approval.json`, `plan-graph-review.json`, the change's `plan-reviews.json`, briefs, reports, reviews (including finish reviews), evidence, and `pr-follow-up.md`.
5. Compare those records with committed OpenSpec artifacts, task ticks, and surviving worktrees/refs.
6. Establish the actual status and ownership of every affected worker.
7. Repair the interrupted operation or renew only the affected missing evidence and decisions.
   Lost or unreadable review ledgers, reports, or evidence require re-review or re-capture; surviving code, task ticks, or summaries are not evidence.
   Resolve implementation conflicts in the lane worktree with fresh review, never as unreviewed feature-branch edits.
   For finish workers, inspect the repo-level `.agents/workflow/finish.json` dispatch list and its worktree/HEAD binding before resuming or replacing a worker.
   For Oracle planning authors, establish graph ownership before any graph mutation or independent review.
8. Refresh coordination and inspect status after reconciliation.
   `status` reports plan-approval state and open intents, but does not verify that review ledgers, reports, evidence, finish reviews, or `pr-follow-up.md` exist; inspect those paths directly.
9. Resume the owning phase at its first incomplete operation.

Recovery ends when records and execution state agree, worker ownership is known, and the next action is identified.
A missing approval remains a gate in the owning phase. Request it there; recovery never substitutes for consent.
If the state cannot be reconciled or worker ownership remains unknown, stop for the human.
Do not replay completed phases, redispatch completed tasks, or repeat a successful merge merely to recreate the normal sequence.

## Binding and ownership rules

- Missing dispatch records require reconciliation of the worker outcome before another launch. Never assume the old worker stopped.
- Missing or stale review records require a fresh independent current-HEAD verdict. Never infer approval from code or task ticks.
- A lost review ledger, review report, or evidence set is a missing record: re-review or re-capture it. Never rebuild it from surviving code, task ticks, or summaries.
- After the actual re-review, record a lost planning verdict with a fresh `plan-dispatch` intent and then `plan-review`, including the verdict, `--head <sha>`, and `--report <path>`; a report path alone does not restore the ledger.
- Missing finish reviews require a fresh independent review at the exact integrated HEAD. A lost PR-opening consent record requires renewed human consent, not an inference from an opened PR.
- Unprovable human acceptance requires a renewed checkpoint. Silence, chat recollection, and green checks are not substitutes.
- Before same-worktree redispatch, prove the prior session is absent, stopped, or released. Never start a second writer.
- A retained pass counts only with its exact reviewed HEAD, current lane contract, and valid evidence bindings.
- Human authorization and plan-bound grants cannot be reconstructed from ancestry or equal trees.
- A deferred checkpoint is not approval. Retain its exact eligibility and binding or perform the early human checkpoint.
- Missing final hands-on inspection records require the human to repeat the inspection. Oracle stand-in cannot replace it.
- Confirm operation results before retrying; never double-dispatch, double-merge, or double-remove.

The local graph, coordination file, plan-approval record, review ledgers, reports, evidence, and PR follow-up are not reconstructible from Git alone. Preserve them together.
Back up the entire `.agents/workflow/` tree before recovery, migration, cleanup, or any destructive repair; it holds every repo-level object, ledger, brief, report, review, and evidence set listed in [Set locations](briefs-and-handoffs.md#set-locations-no-ad-hoc-paths).
Use the conditional sections below only when their specific legacy-record condition applies.

## Repository-object migration

Use this section only when upgrading older per-change storage to the current repo-level objects.
Do not run migration during routine setup, every session restart, or ordinary lane recovery.

The authoritative local files are `.agents/workflow/plan-graph.json` and `.agents/workflow/coordination.json`.
The graph uses `schema_version: 3`; coordination uses `schema_version: 2`.
Both nest `changes → lanes → tasks` and neither is committed.
Migration collapses a legacy lane's `ui` and `screen` flags into `ui = ui or screen`; affected lanes need fresh reviews and gates.
Retained per-change legacy plans remain readable until the shared local graph exists.
The helper imports retained legacy ledgers on the first shared coordination write.
Once a shared object exists, legacy files never replace its entries.

1. Pause dispatches before migration.
2. Back up the entire `.agents/workflow/` tree; the legacy plans, coordination records, and approval record are only part of it.
3. Run `python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" migrate-repo` from the feature checkout.
4. Inspect the imported change, lane, task, review, dispatch, and approval records.
5. Obtain current OpenSpec artifact passes for each affected change.
6. Obtain a separate Oracle repo graph pass after every included change's artifact review passes.
7. Obtain human approval of the local graph and affected OpenSpec artifacts.
8. Commit only affected OpenSpec artifacts, then record the approval with `plan-approve`; never commit a repo object.
9. Renew affected lane reviews, plan-bound grants, release evidence, and human inspections.

Retired `review_mode` fields are dropped; remove them from the plan before validation.
Legacy per-change finish records are not migrated. Finish is repo-level, so after every lane in the graph integrates, re-record classification, evidence, inspections, and PR consent in `.agents/workflow/finish.json` and `.agents/workflow/finish/`.

Migration retains source files, reports, worker IDs, and dispatch keys.
It carries recorded approval bindings forward only when the exact legacy commit proves the same contract.
Grant scope and consent remain unchanged; unverifiable bindings require renewed review or approval.
Retries add missing entries without replacing newer shared records. Each file write is atomic.
A graph write followed by a failed coordination write is recoverable by rerunning migration.
Legacy graph-only plan verdicts do not count as OpenSpec artifact verdicts in the new two-stage flow.
No migration command commits files or invents approvals.
For rollback, restore matching source and runtime backups before using the older helper.
Do not overwrite newer progress with an older backup without reconciliation.

Legacy input paths are `openspec/changes/<change>/plan/graph-plan.json` and
`.agents/workflow/<change>/coordination.json`. New work never creates them.
Git retains historical OpenSpec artifacts and ticks, but a lost local record requires a backup or renewed planning, review, approval, or re-capture—never inferred consent. A surviving graph or coordination entry is no substitute for a lost ledger, report, or evidence set.

## Finish records

Use this section when resuming finish with an older record that lacks the current preparation binding.
Normal changes to release inputs follow restaging in [finish.md](finish.md), without restarting completed phases.

Existing finish records without a preparation artifact require `finish-record restage`.
Restage reuses the stored classification; supply the new preparation receipt and its committed input
paths and repeat release checks and required human inspections. Use `checkpoint:` in new human inspection artifacts. See [finish.md](finish.md) for the record format and [Finish records](helper-commands.md#finish-records) for restage flags.

Preserve lane reports and evidence under the change's absolute reports and evidence directories; finish reports and evidence live under the `.agents/workflow/finish/` tree. Their canonical paths are in [Set locations](briefs-and-handoffs.md#set-locations-no-ad-hoc-paths).
Recovery checks for finish also cover the repo-level finish review at
`.agents/workflow/finish/reviews/finish-<role>-<head>.md`, with its verdict, exact integrated HEAD,
inputs/evidence revision, limitations, and reviewer identity, and
`.agents/workflow/finish/pr-follow-up.md` with its PR URL, current HEAD, finding dispositions,
outstanding items, and PR-opening consent naming the human, timestamp, HEAD, and included changes.
PR-opening consent is separate from release classification and lane merge consent.
`pr-check` does not validate finish reviews, PR-opening consent, or PR follow-up, so a missing or stale record needs fresh review, re-capture, or renewed human consent.

Block on missing/stale plan approval or review binding, a missing finish review or PR-opening consent
needed for the next action, unknown worker, unapproved changed plan, failed required check,
unresolved Oracle escalation, or unmet human gate. Account for all workers/worktrees/sessions/temp resources before stopping.

Prefer completion events; bound fallback polling/output. Reuse preflight only while mechanism/model/level/environment/evidence bindings match.

## Retired review exceptions

Use this section only when retained records contain the removed skipped-duty or ancestor-review exceptions.
New workflows do not enter this branch.

Required reviewers now follow lane risk and UI flags only.
Old skipped-duty decisions and carried-forward ancestor verdicts cannot satisfy current review gates.
Obtain fresh direct current-HEAD verdicts for affected active lanes.
If a closed legacy lane cannot meet the new gates through normal commands, stop for a human-approved record repair.
Never fabricate a pass, reopen completed tasks, or reset review history to bypass the gate.

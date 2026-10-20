# Recovery

Command syntax: [helper command reference](helper-commands.md).

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
4. Read the retained local plan, plan-approval record, coordination records, reports, and approval bindings.
5. Compare those records with committed OpenSpec artifacts, task ticks, and surviving worktrees/refs.
6. Establish the actual status and ownership of every affected worker.
7. Repair the interrupted operation or renew only the affected missing evidence and decisions.
   Resolve implementation conflicts in the lane worktree with fresh review, never as unreviewed feature-branch edits.
   For finish workers, inspect `finish_dispatches` and its worktree/HEAD binding before resuming or replacing a worker.
   For Oracle planning authors, establish graph ownership before any graph mutation or independent review.
8. Refresh coordination and inspect status after reconciliation.
9. Resume the owning phase at its first incomplete operation.

Recovery ends when records and execution state agree, worker ownership is known, and the next action is identified.
A missing approval remains a gate in the owning phase. Request it there; recovery never substitutes for consent.
If the state cannot be reconciled or worker ownership remains unknown, stop for the human.
Do not replay completed phases, redispatch completed tasks, or repeat a successful merge merely to recreate the normal sequence.

## Binding and ownership rules

- Missing dispatch records require reconciliation of the worker outcome before another launch. Never assume the old worker stopped.
- Missing or stale review records require a fresh independent current-HEAD verdict. Never infer approval from code or task ticks.
- Unprovable human acceptance requires a renewed checkpoint. Silence, chat recollection, and green checks are not substitutes.
- Before same-worktree redispatch, prove the prior session is absent, stopped, or released. Never start a second writer.
- A retained pass counts only with its exact reviewed HEAD, current lane contract, and valid evidence bindings.
- Human authorization and combined-Oracle approvals cannot be reconstructed from ancestry or equal trees.
- A deferred checkpoint is not approval. Retain its exact eligibility and binding or perform the early human checkpoint.
- Missing final hands-on inspection records require the human to repeat the inspection. Oracle stand-in cannot replace it.
- Confirm operation results before retrying; never double-dispatch, double-merge, or double-remove.

The local graph, coordination file, and approval record are not reconstructible from Git alone. Preserve them together.
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
2. Back up the existing plans, coordination records, and approval record together.
3. Run `python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" migrate-repo` from the feature checkout.
4. Inspect the imported change, lane, task, review, dispatch, and approval records.
5. Obtain current OpenSpec artifact passes for each affected change.
6. Obtain a separate Oracle repo graph pass after every included change's artifact review passes.
7. Obtain human approval of the local graph and affected OpenSpec artifacts.
8. Commit only affected OpenSpec artifacts, then record the approval with `plan-approve`; never commit a repo object.
9. Renew affected lane reviews, plan-bound grants, release evidence, and human inspections.

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
Git retains historical OpenSpec artifacts and ticks, but cannot reconstruct a lost local graph or ledger.
Lost local objects require a backup or renewed planning/review/approval—not inferred consent.

## Finish records

Use this section when resuming finish with an older record that lacks the current preparation binding.
Normal changes to release inputs follow restaging in [finish.md](finish.md), without restarting completed phases.

Existing finish records without a preparation artifact require `finish-record restage`.
Restage reuses the stored classification; supply the new preparation receipt and its committed input
paths. Pass `--check` to replace the declared check set. Repeat release checks and required human
inspections. Use `checkpoint:` in new human inspection artifacts. See [finish.md](finish.md) for the record format.

Preserve reports/evidence under absolute change directory.
Block on missing/stale plan approval or review binding, unknown worker, unapproved changed plan, failed required check,
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

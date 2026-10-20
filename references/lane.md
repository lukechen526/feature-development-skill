# Lane phase

Follow the approved [plan](plan.md). Implement assigned tasks, review the current lane HEAD, resolve findings, then integrate and close.
Use [helper-commands.md](helper-commands.md) for command syntax. This document defines workflow order, ownership, and gates.

## Records and progress

- OpenSpec artifacts and `tasks.md` are committed. Task definitions are the approved implementation scope.
- `.agents/workflow/plan-graph.json` and `coordination.json` are local, gitignored repo-level objects.
- Both objects group `changes → lanes → tasks`. Lane and task IDs are local to a change.
- Each change's `plan-reviews.json` records Oracle review of its OpenSpec artifacts.
- Repo-level `plan-graph-review.json` records the separate graph review, after all included artifact passes.
- Briefs, reports, and evidence use the locations in [briefs-and-handoffs.md](briefs-and-handoffs.md#set-locations-no-ad-hoc-paths).

One Coordinator writes the shared coordination file across all changes. The Coordinator authors the local graph.
Workers edit neither repo-level object. Other changes' records must survive every update.
Lane worktrees read the owner's shared graph; they do not receive private or committed copies.

The Coordinator records every subagent update before proceeding:

1. Record the exact dispatch outcome and real harness run ID.
2. Verify the reported commits, task ticks, and evidence.
3. Record any review verdict, mediation, or human checkpoint against its actual inputs.
4. Run [sync](helper-commands.md#status-and-task-status) to save progress.
5. Read status and report the current result or blocker.

Sync reads lane-ref ticks for in-flight progress and feature-branch ticks for integrated completion.
Only feature-branch ticks satisfy cross-lane prerequisites. Same-lane prerequisites use the lane's committed ticks.
A completed task is never redispatched as pending merely because its tick is not yet merged.
Use task-status for a blocker that sync cannot infer. It does not replace committed completion.
Status and dashboards are derived per-change views, not approval records. Chat history is never the source of progress or consent.

## Worktrees and readiness

Open the lane with [lane-open](helper-commands.md#lane-open) before its first implementation dispatch.
The default worktree is `~/.agents/worktrees/<repo>/<branch>/<change>/<lane>/`, with ref `featdev/<change>-<lane>`.
`<repo>` is the URL-escaped resolved full feature-checkout path, not its folder name.
For example, `/clients/alice/app` becomes `%2Fclients%2Falice%2Fapp`, including the escaped leading separator.
The escaped path must fit the filesystem limit for one directory name.
`FEATDEV_LANE_WORKTREE_ROOT` permits an absolute Coordinator-controlled root override.
A recorded worktree remains authoritative for that lane's lifetime, including recovery and cleanup.
Optional environment-specific worktree registration is best-effort; the workflow does not depend on it.

Allow one writer per lane worktree. Parallel lanes must have disjoint writes or explicit sequencing.
Implementation, corrections, checks, task edits, and commits run in the named lane worktree, never the feature checkout.
The feature checkout receives implementation through lane integration only.
Every brief names the OpenSpec change, lane, task or review round, absolute worktree, base, and HEAD.

For wrong-tree work:

1. Stop the worker.
2. Preserve unrelated work.
3. Obtain any required reset or discard authorization before cleanup.
4. Correct the brief and redispatch only after worktree ownership is clear.

## Implementation

Use the [implementation brief](briefs-and-handoffs.md#implementation). The Coordinator never patches implementation or approves its own work.
The selected role profile controls the actual harness model and thinking settings. Helper dispatch records need only role, task, and execution metadata.

### One fresh Implementer per task

A fresh Implementer implements exactly one task ID. Different tasks never share an implementation session.
Corrections may continue the same task's session only when its brief and ownership remain valid.

1. Write the scoped brief under the coordinator store.
2. Record a [dispatch intent](helper-commands.md#dispatch) with a new unique key.
3. Launch the Implementer with the selected profile and absolute lane worktree.
4. Run the assigned focused checks after implementation.
5. Mark only the assigned checkbox directly in the lane's `tasks.md`.
6. Commit the completed work and checkbox edit.
7. Update the live report with the commit, checks, output, evidence, and limitations.
8. Return the actual result and leave a clean lane worktree.

Task edits change checkbox state only. Never alter task IDs, wording, notes, nested bullets, or another lane/change's tasks.
The Coordinator verifies the result, closes that exact dispatch, and runs sync before dispatching the next task.
A new task, correction dispatch, or replacement session needs a new key. Only a retry of the same recorded intent retains its key.

### Deadlines and recovery

Implementation dispatches run asynchronously when supported, with a 60-minute deadline or the shorter harness limit.
The Implementer updates its live report after every commit, check, and capture.
On interruption, the Coordinator checks the prior worker's status before continuation or replacement.
Unknown worker ownership requires a human decision; never start a second writer.
A fresh replacement reads the report and approved sources, not the Coordinator's chat history.
Extend work only with concrete progress. Reset or discard actions require the applicable authorization.

## Reviews

### Start conditions and required reviewers

Start review only after every assigned task is implemented and its checkbox is committed.
The Coordinator verifies clean worktrees, current HEAD, required evidence, and the full scoped lane diff before dispatch.
This is a procedural check, not an approval or a generated review package.

| Lane flags or mode | Required independent review |
|---|---|
| Ordinary risk | `code-reviewer` covers implementation, task completeness, and test adequacy |
| High risk | `code-reviewer` covers implementation and task completeness; separate `test-reviewer` covers test adequacy |
| `ui: true` | Add `ui-reviewer` inspection of supplied rendered evidence; the UI `PASS` and human gate each require current evidence |
| Eligible `combined-single-lane` | Independent Oracle covers code, tests, and whole-change implementation coherence; the UI review and human gate still apply |

Use the [review brief](briefs-and-handoffs.md#review). Reviewers read approved sources, the lane diff, and relevant dependencies directly.
Every assigned acceptance criterion and completed task needs review coverage.
Reviewers stay read-only. They report findings to the Coordinator; the Implementer or explicitly authorized Oracle makes corrections.
The UI reviewer ingests supplied screenshots, manifests, and metadata. It never builds, launches, or captures the application.

Only direct verdicts for the current lane HEAD satisfy review gates.
Earlier verdicts remain superseded history. An unchanged role still performs an independent impact check after a new commit.
Record actual verdicts and report paths with [lane-review](helper-commands.md#lane-review) before requesting corrections.

### Checks, evidence, and runtimes

Repository guides define relevant checks, commands, execution owners, environments, and evidence formats.
The lane uses the repository's primary environment. The full release matrix runs after all included lanes merge, during [finish](finish.md).
This skill fixes no platform or runtime version.

- The Implementer runs the assigned focused checks and records exact commands and results.
- Reviewers independently run relevant checks at the reviewed HEAD, within their assigned remit.
- The brief assigns one execution owner to each build, capture, or long-running check to avoid duplicate execution.
- A read-only Runner may execute a supplied long command or poll an external result. Its report is evidence, never a review verdict.
- Screenshots and other evidence bind the producing commit, revision, and relevant inputs.
- Later writes or regeneration invalidate affected evidence. Missing, stale, or incomplete evidence cannot support a pass.
- An existing check result is reusable only under the repository's evidence rules. Never label an old result as a fresh run.
- Prefer completion events and deadlines. Bound fallback polling and returned output.

Keep full logs and visual artifacts in the evidence store. Handoffs contain decisive results and paths.
Do not replace reviewer judgment with deterministic summaries, generated review packets, digests, or fabricated verdict files.

### Verdicts and correction batches

A reviewer returns `PASS` only when the assigned remit has no material issue; otherwise it returns `MORE WORK`.
Findings use shared stable IDs, severity, location, a reachable failure, and a concrete closure check.
Review the full lane diff, then expand only for a specific dependency or requirement concern.
Avoid unrelated cleanup, hypothetical hardening, and duplicate findings.

Collect all first-pass verdicts before building one deduplicated correction batch.
An uncovered criterion or unjustified task tick requires more work.
Record the current commit, lane contract digest, evidence revision, report path, and limitations.
After corrections, each required role returns a fresh current-HEAD verdict. An unaffected role may perform a short impact check.
The two-round limit and unresolved findings continue under [Escalation](#escalation).

### Human checkpoints

A declared `ui` or `sensitive` flag requires a human checkpoint. High risk alone does not add one.
Repository guides define the states, visual variants, environments, and acceptance criteria the human must inspect.
Record the actual human decision against the current lane commit and evidence revision with [human-gate](helper-commands.md#human-gate).
UI lanes require current evidence for that checkpoint.

Missing, rejected, revoked, stale, or wrong-commit approval blocks integration. Silence and green automation never imply consent.
Other independent ready lanes may continue while one lane waits for a human.

An explicit [human-defer](helper-commands.md#human-defer) can move an eligible checkpoint to integrated inspection:

- The lane has UI work, ordinary risk, and no sensitive flag.
- The Coordinator records bounded, reversible impact and the excluded sensitive effects.
- Combined mode is not active.
- The deferral binds current lane HEAD, evidence revision, and required UI evidence.

Deferral is not approval and does not waive any independent review.
Finish must declare appropriate direct human inspection checkpoints covering the deferred work.
A changed integrated HEAD requires fresh inspection. An Oracle cannot perform those final hands-on inspections for the human.

## Escalation

The ordinary cycle is fixed: implementation → review 1 → correction → review 2.
Review rounds persist across commits; a new commit does not reset the count.

1. Record every first-pass verdict.
2. Send the deduplicated correction batch to the Implementer.
3. Obtain fresh current-HEAD verdicts from all required roles after correction.
4. Record Oracle mediation if the second review still requests work.
5. Dispatch an explicitly authorized Oracle to author the bounded fix.
6. Obtain an independent current-HEAD review from a different reviewer.
7. Complete the required human checkpoint before integration.

The helper refuses further review records after two rounds without [lane-mediate](helper-commands.md#lane-mediate).
One further Oracle-authored pass is allowed. Unresolved work then goes to the human; never silently extend the loop.
Every correction retains the plan-required checks, reviewer independence, evidence bindings, and human gates.

A focused technical consultation is read-only unless edit authority is explicitly granted.
It does not reset rounds, approve a merge, or authorize new scope.
Pause dependent edits for conflicting requirements or unclear closure criteria.
Only an [explicit human override](#human-override) can waive review duties; agent preference cannot.

## Human Override

The human retains authority, but the helper has no general bypass for every gate.
The Coordinator records the actual human decision; it never invents or infers an exception.

### Review-duty override

A human may explicitly direct integration despite missing or negative code, test, or UI review verdicts.
Use the [human-override form of lane-merge](helper-commands.md#human-override) with the human's reason.
Prefer completing normal reviews; the exception does not turn a failed or missing review into `PASS`.
For combined mode, use an approved move to standard mode before waiving the exact combined Oracle review.

The override cannot waive:

- Completion and ownership of assigned tasks, or preservation of other task definitions and ticks.
- Clean worktrees, a valid lane ref, candidate identity, or base/ancestry validation.
- Required UI evidence or the current human checkpoint.
- Authorization scope, current bindings, or one-use grant rules.
- Release checks, final human inspections, PR-opening consent, or the human's PR merge decision.

Record an override in this order:

1. Obtain an explicit human instruction identifying the lane, intended integration, and accepted review exception.
2. Verify the current candidate HEAD and all non-waivable gates.
3. Record any separately required human checkpoint.
4. Run the override integration with the human's reason.
5. Preserve the override reason, timestamp, and waived review gates in the merge record.

A changed candidate needs a renewed decision. The Coordinator does not reuse an earlier instruction for different work.
The override affects lane integration only. It is not final release approval and does not reset review history.

### Oracle stand-in for a lane checkpoint

The human may grant an independent Oracle authority to judge a specific lane human checkpoint.
This delegates the acceptance judgment; it does not waive that judgment or any required technical review.

The grant must be human-origin, recorded, scoped to the checkpoint, and bound to the approved selected-change plan.
A current Oracle stand-in must inspect the supplied acceptance checklist and evidence, then return an actual `PASS`.
Technical correctness alone is insufficient. A failed stand-in leaves the gate closed and the grant unconsumed.
Record this with the [Oracle stand-in form of human-gate](helper-commands.md#oracle-stand-in).

Each grant is consumed once for one lane checkpoint. It cannot authorize another lane or the PR merge.
Rejection, revocation, changed inputs, or a stale binding requires fresh human acceptance or a renewed grant.
A grant never removes independent code/test/UI reviews or creates an otherwise unrequired Oracle release review.
Final hands-on release inspections remain human-only, as specified in [finish](finish.md).

## Merge and close

Normal integration requires current independent reviewer passes, current UI evidence where `ui: true`, and the bound human checkpoint.
The [merge command reference](helper-commands.md#lane-merge-and-lane-close) contains syntax, not authority to waive those gates.

The helper validates:

- The lane worktree/ref and feature branch are consistent, with clean required trees.
- The merge base is unique and ancestral to both heads.
- Candidate task definitions match the approved feature-branch definitions.
- Newly added completion ticks match this lane's assigned tasks, excluding inherited completed tasks.
- No other change's task file or another lane's completion state was modified.
- Review, local-plan, evidence, and human-checkpoint bindings are current.

A standard merge preserves the reviewed candidate SHA. Do not rebase a passed lane merely because another lane merged first.
For conflicts, the helper aborts the feature-branch merge. Merge the feature branch into the lane worktree instead.
An Implementer resolves conflicts there and commits the result. Preserve earlier reviewed commits as ancestors; do not rebase them away.
The changed lane HEAD requires fresh independent reviews and affected human gates under the normal review/escalation budget.
Retry integration only after those gates pass. Never resolve implementation conflicts directly on the feature branch.
Interrupted bookkeeping after an already successful merge follows [recovery](recovery.md); it never grants approval to unreviewed resolutions.
Close only after integration is recorded. The recorded worktree remains authoritative for cleanup.

In combined mode, the Oracle's [implementation review](helper-commands.md#combined-oracle-review) binds the exact base, candidate, acceptance, and evidence.
Integration is fast-forward-only to that candidate. Changed inputs invalidate approval.
The combined `final` duty means implementation coherence, not release approval.

After every change included in the PR has integrated, follow [finish](finish.md).
Version/generated changes, release evidence, final inspections, and PR follow-up belong there.
Approval to open a PR never authorizes the later human PR merge.

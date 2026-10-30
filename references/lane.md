# Lane phase

Follow the approved [plan](plan.md). Implement assigned tasks, review the current lane HEAD, resolve findings, then integrate and close.
Use [helper-commands.md](helper-commands.md) for command syntax. This document defines workflow order, ownership, and gates.
Resolve and export `FEATDEV_SKILL_ROOT` to the absolute installed skill path before any command; never hardcode the location ([preflight](preflight-and-roles.md)).

## Records and progress

- OpenSpec artifacts and `tasks.md` are committed. Task definitions are the approved implementation scope.
- `.agents/workflow/plan-graph.json` and `coordination.json` are local, gitignored repo-level objects.
- Both objects group `changes → lanes → tasks`. Lane and task IDs are local to a change.
- Each change's `plan-reviews.json` records Oracle review of its OpenSpec artifacts.
- Repo-level `plan-graph-review.json` records the separate graph review, after all included artifact passes.
- `.agents/workflow/model-selection.json` and its model/thinking/role profiles are repo-level only. No lane worktree or per-dispatch path is stored in a profile; the worktree is execution metadata in the brief and coordination.
- `.agents/workflow/plan-approval.json` records the human plan approval. Status reports approval or waiver as `current`, `waived`, `stale`, or `missing`; lane-open refuses unless it is current or a current plan waiver covers the lane; lane-merge does not enforce it. The Coordinator verifies current approval before merge unless the human explicitly waives it.
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
Readiness and status compute eligibility from feature-branch ticks for same-lane and cross-lane dependencies alike. A tick present only in the lane worktree stays displayed blocked until integrated; implementation may continue while the display catches up.
Only feature-branch ticks satisfy cross-lane prerequisites.
A completed task is never redispatched as pending merely because its tick is not yet merged.
Use task-status for a blocker that sync cannot infer. It does not replace committed completion.
Status and dashboards are derived per-change views, not approval records. Chat history is never the source of progress or consent.

## Worktrees and readiness

Open the lane with [lane-open](helper-commands.md#lane-open) before its first implementation dispatch.
The default worktree is `~/.agents/worktrees/<repo>/<branch>/<change>/<lane>/`, with ref `featdev/<change>-<lane>`.
`<repo>` is `repo-` followed by the SHA-256 hex digest of the resolved full feature-checkout path. It is a fixed-length, filesystem-safe name; different checkouts with the same folder name remain distinct. Existing recorded worktrees keep their original paths, including older URL-escaped paths.
`FEATDEV_LANE_WORKTREE_ROOT` overrides that root and must be an absolute path. The helper resolves a relative value against the current directory, which can silently place new lanes elsewhere; set an absolute path or leave it unset.
A recorded worktree remains authoritative for that lane's lifetime, including recovery and cleanup.
Optional environment-specific worktree registration is best-effort; the workflow does not depend on it.

Allow one writer per lane worktree. Parallel lanes must have disjoint writes or explicit sequencing.
`dispatch` refuses an open same-role intent without a new `--key`, but a new key bypasses that guard. A new key is not authorization for a second writer: the Coordinator completes or transfers the current writer's dispatch before starting another writer in that worktree.
Implementation, corrections, checks, task edits, and commits run in the named lane worktree, never the feature checkout; the feature checkout receives implementation through lane integration only.
Every brief names Change, Lane (or `Scope: finish`), Task or round, Worktree (absolute), Base, HEAD, Profile, Contract (absolute path to [briefs-and-handoffs.md](briefs-and-handoffs.md)), and Deadline.

For wrong-tree work:

1. Stop the worker.
2. Preserve unrelated work.
3. Obtain any required reset or discard authorization before cleanup.
4. Correct the brief and redispatch only after worktree ownership is clear.

## Implementation

Use the [implementation brief](briefs-and-handoffs.md#implementation). The Coordinator never patches implementation or approves its own work.
The selected repo-level profile controls the actual harness model and thinking settings; the helper records role, task, and execution metadata.

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

| Lane flags or mode | Required independent review |
|---|---|
| Ordinary risk | `code-reviewer` covers implementation, task completeness, and test adequacy |
| High risk | `code-reviewer` covers implementation and task completeness; separate `test-reviewer` covers test adequacy |
| `ui: true` | Add `ui-reviewer` inspection of supplied rendered evidence; the UI `PASS` and human gate each require current evidence |

Use the [review brief](briefs-and-handoffs.md#review). Reviewers read approved sources, the lane diff, and relevant dependencies directly.
Every assigned acceptance criterion and completed task needs review coverage.
Reviewers stay read-only. They report findings to the Coordinator; the Implementer or a write-access Oracle, dispatched after mediation, makes corrections.
The UI reviewer ingests supplied screenshots, manifests, and metadata. It never builds, launches, or captures the application.

Oracle-authored fixes and Oracle stand-ins have no provenance flag: the Coordinator verifies reviewer independence for an authored fix and stand-in independence for a checkpoint, then records the ID in the report, brief, grant, or checklist.

Only direct verdicts for the current lane HEAD satisfy review gates.
Earlier verdicts remain superseded history. An unaffected role performs a short independent impact check after a new commit. Compare the previous reviewed commit with the new commit. Confirm that the correction does not affect the role's criteria or evidence. Record a fresh current-HEAD verdict. Do not repeat the full review when the impact check finds no affected input.
Record actual verdicts and report paths with [lane-review](helper-commands.md#lane-review) before requesting corrections.

### Checks, evidence, and runtimes

Repository guides define relevant checks, commands, execution owners, environments, and evidence formats.
The lane uses the repository's primary environment. The full release matrix runs after all included lanes merge, during [finish](finish.md).
This skill fixes no platform or runtime version.

- The Implementer runs the assigned focused checks and records exact commands and results.
- Assign one execution owner to each expensive check. Reviewers examine its command, inputs, result, and provenance. They run another check only for a specific concern, a stale result, or a repository requirement. Reviewer judgment remains independent.
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
For initial review, read the full lane diff. For correction review, read the correction and affected regressions. For an unaffected role, use a short impact check. Expand only for a specific dependency or requirement concern.
Avoid unrelated cleanup, hypothetical hardening, and duplicate findings.

Collect all first-pass verdicts before building one deduplicated correction batch. Complete the batch before the next review dispatch. Do not start a separate review cycle for each finding.
An uncovered criterion or unjustified task tick requires more work.
Record the current commit, lane contract digest, evidence revision, report path, and limitations.
A reviewer or Runner reply states the verdict or status, exact SHA, finding IDs, report path, and evidence revision; it carries no machine-readable verdict footer.
After corrections, each required role returns a fresh current-HEAD verdict. Use correction review for affected roles and a short impact check for unaffected roles. Review the correction and affected regressions. Keep access to the full lane diff, but read unchanged files again only for a specific concern.
The two-round limit and unresolved findings continue under [Escalation](#escalation).

### Human checkpoints

A declared `ui` or `sensitive` flag requires a human checkpoint. High risk alone does not add one.
Repository guides define the states, visual variants, environments, and acceptance criteria the human must inspect.
Record the actual human decision against the current lane commit and evidence revision with [human-gate](helper-commands.md#human-gate).
UI lanes require current evidence for that checkpoint.

Missing, rejected, revoked, stale, or wrong-commit approval blocks integration unless an applicable advance waiver explicitly covers the checkpoint. Silence and green automation never imply consent.
Other independent ready lanes may continue while one lane waits for a human.

An explicit [human-defer](helper-commands.md#human-defer) can move an eligible checkpoint to integrated inspection:

- The lane has UI work, ordinary risk, and no sensitive flag.
- The rationale must cover the bounded impact, the reversible path, and each excluded effect named in the [human-defer command](helper-commands.md#human-defer); the helper stores it without validating it, and its fixed flags are not evidence.
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
5. The Coordinator dispatches a write-access Oracle to author the bounded fix, after the recorded mediation. The helper refuses that write dispatch without the mediation record.
6. Obtain an independent current-HEAD review from a different reviewer.
7. Complete the required human checkpoint before integration.

The helper refuses a review record that would be round three or later for a role unless a current [lane-mediate](helper-commands.md#lane-mediate) record covers that role. It imposes no round cap after mediation.
The workflow allows exactly one further Oracle-authored pass after mediation. If unresolved work remains after that pass, stop for a human decision; never silently extend the loop.
Every correction retains the plan-required checks, reviewer independence, evidence bindings, and human gates.

A focused technical consultation is read-only unless edit authority is explicitly granted.
It does not reset rounds, approve a merge, or authorize new scope.
Pause dependent edits for conflicting requirements or unclear closure criteria.
Only an [explicit human override](#human-override) or applicable [advance waiver](#advance-human-waiver) can waive lane review or merge rules; agent preference cannot.

## Advance human waiver

The human may explicitly waive any named human gate before lane integration, including plan approval, intake confirmation, model-selection questions, lane checkpoints, and human escalation decisions. The human may name individual gates or all pre-integration human gates. The human selects either all lanes or exact `change/lane` pairs. A lane-scoped waiver covers a shared planning decision only for those lanes; other lanes still need the decision. This waiver may cover human decisions before the pre-PR check, including release classification. The pre-PR check, its required inspections, PR-opening consent, and PR merge remain separate and cannot use it.

Before relying on an advance waiver, the Coordinator records the human's actual decision in the repo-level `.agents/workflow/coordination.json`, alongside lane human decisions. Add an `advance_waivers` array at the top level. Record a unique ID, selected gates, exact `change/lane` scope or `all`, reason, decision date, state, and any limits or expiry. If the file does not exist and no legacy coordination needs migration, create it with `schema_version: 2`, the absolute `feature_checkout`, an empty `changes` object, and the array. Otherwise preserve the existing object and all its entries; migrate legacy data first if required. Only the Coordinator writes this record. The helper preserves the array but does not validate the decision, scope, expiry, or revocation.

The human may revoke or narrow a waiver. Record that decision in `advance_waivers` before further use. Remove or replace a revoked or narrowed plan waiver in `.agents/workflow/plan-approval.json`. Never use an obsolete waiver. A waiver is not approval, a review verdict, or evidence. Do not infer it from absence or silence.

For an eligible human decision, verify the recorded waiver covers the specific gate and lane. Record the waiver ID, use, affected lane, and inputs with that gate's existing decision record: plan decisions in `plan-approval.json`, model choices in `model-selection.json`, lane checkpoints in `coordination.json`, and release classification in `finish.json`. Use a distinct `waiver` or `waived` field; never represent a waiver as approval. Make only the decision needed to proceed within the human's stated feature scope. Stop if the decision needs facts that are unavailable or conflicts with another instruction. Do not fabricate answers, acceptance, or authorization. Continue independent technical reviews, evidence checks, and other non-human gates. Never use a waiver to discard data or bypass repository policy without explicit authority.

For plan approval, complete both Oracle review stages and commit the artifacts. Record the waiver using `plan-waive --reason` and its exact lane scope as described in [plan approval](helper-commands.md#plan-approval). The record binds the reviewed graph; a changed graph requires a fresh record under the still-applicable human waiver. Do not call the waived decision an approval.

For a lane human checkpoint, run the [human override](helper-commands.md#human-override) with `--waive human-gate`, the actual current candidate HEAD, and a reason identifying the waiver ID in `coordination.json`. Add `--waive plan-approval` only when that gate is also waived. Do not select other gates unless the human expressly waived them. The helper records `state: waived` in the lane human-gate record and the actual merged candidate in the merge record. It does not claim the human approved either. A changed candidate may use the same active advance waiver only within its recorded scope.

At finish, list every waiver use for human review. An applicable waiver may cover release classification, but the Coordinator records the actual classification as a waiver use, not human approval. Run the normal pre-PR checks, required release inspections, and consent steps. An advance waiver never authorizes opening or merging a PR.

## Human Override

A human may explicitly waive any lane review or merge rule in this skill.
The decision takes precedence over conflicting lane review and merge instructions.
An ordinary approval is not an override. The Coordinator must not invent consent,
a review verdict, task completion, or evidence.

1. Obtain an explicit decision naming the lane, candidate HEAD, intended merge, reason, and each waived gate.
2. Verify the candidate HEAD matches the human decision.
3. Run the [human-override form of lane-merge](helper-commands.md#human-override), naming each waived gate.
4. Preserve the decision and exact waived duties in the reason and merge record.

The helper records the selected gate categories. Gates not selected still apply.
The Coordinator applies the same decision to rules the helper does not enforce.
The override does not waive release checks, final inspections, PR-opening consent,
or the human's PR merge decision. A changed candidate requires a new decision.
A waived review is not a PASS. An unfinished task remains unfinished.
Git may still refuse a merge, and the helper must identify the candidate and target.
An override does not authorize discarding uncommitted changes.

### Oracle stand-in for a lane checkpoint

The human may grant an independent Oracle authority to judge a specific lane human checkpoint.
This delegates the acceptance judgment; it does not waive that judgment or any required technical review.

The grant must be human-origin, recorded, scoped to the listed lane IDs at the checkpoint, and bound to the approved selected-change plan.
A current Oracle stand-in must inspect the supplied acceptance checklist and evidence, then return an actual `PASS`.
Technical correctness alone is insufficient. A failed stand-in leaves the gate closed and the grant unconsumed.
The Coordinator verifies the stand-in is independent of the implementation and affected reviews, and records the stand-in worker ID in the stand-in report or brief; the helper has no stand-in independence flag.
Record this with the [Oracle stand-in form of human-gate](helper-commands.md#oracle-stand-in).

Each grant is consumed once for one lane checkpoint. It cannot authorize another lane or the PR merge. A grant lists lane IDs; `all` is refused.
Rejection, revocation, changed inputs, or a stale binding requires fresh human acceptance or a renewed grant.
A grant never removes independent code/test/UI reviews or creates an otherwise unrequired Oracle release review.
Final hands-on release inspections remain human-only, as specified in [finish](finish.md).

## Merge and close

Normal integration requires current independent reviewer passes, current UI evidence where `ui: true`, and the bound human checkpoint. An explicit human override may waive these requirements.
Before merge, verify plan approval is current ([Records and progress](#records-and-progress)) and that the recorded reviewer evidence is sound unless explicitly waived; `lane-merge` enforces neither.
The [merge command reference](helper-commands.md#lane-merge-and-lane-close) contains syntax; only the human grants an override.

The helper validates:

- The lane worktree/ref and feature branch are consistent, with clean required trees.
- The merge base is unique and ancestral to both heads.
- Candidate task definitions match the approved feature-branch definitions.
- Newly added completion ticks match this lane's assigned tasks, excluding inherited completed tasks.
- No other change's task file or another lane's completion state was modified.
- Review, local-plan, UI-evidence, and human-checkpoint bindings are current.

Among role evidence, the standard merge check revalidates only the `ui-reviewer` evidence path and digest; it confirms other review reports exist, but not their evidence content or report hashes. The Coordinator verifies every other role's evidence and each report hash before merge.

A standard merge preserves the reviewed candidate SHA. Do not rebase a passed lane merely because another lane merged first.
For conflicts, the helper aborts the feature-branch merge. Merge the feature branch into the lane worktree instead.
An Implementer resolves conflicts there and commits the result. Preserve earlier reviewed commits as ancestors; do not rebase them away.
The changed lane HEAD requires fresh independent reviews and affected human gates under the normal review/escalation budget.
Retry integration only after those gates pass. Never resolve implementation conflicts directly on the feature branch.
Interrupted bookkeeping after an already successful merge follows [recovery](recovery.md); it never grants approval to unreviewed resolutions.
Close only after integration is recorded. `lane-close` also accepts a lane whose ticks are complete without a merge record, so the Coordinator verifies the recorded merge (phase `merged` or a matching merge record) before closing. The recorded worktree remains authoritative for cleanup.

After every change included in the PR has integrated, follow [finish](finish.md), including PR-opening consent, which is separate from release classification and from lane merge consent. Approval to open a PR never authorizes the later human PR merge.

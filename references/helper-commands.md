# Helper command reference

This is the canonical command reference for `scripts/featdev.py`.
The [planning](plan.md), [lane](lane.md), [finish](finish.md), and [recovery](recovery.md) guides own workflow order and authority.
Commands record events and validate bindings. They never launch agents, perform reviews, grant consent, or open/merge PRs.

## Invocation and selection

Use Python 3.9 or later. The helper uses only the standard library.
Resolve and export `FEATDEV_SKILL_ROOT` to the absolute installed skill path before running any command ([preflight](preflight-and-roles.md)); never hardcode an installation path.
Run coordinator commands from the consuming feature checkout. Workers run only commands named in their scoped brief.
Pass `--change <change>` before the subcommand for change-specific operations when several changes exist.
Repo graph review uses `--scope graph` without `--change`.
Lane and task IDs are local to a change. New dispatches require unique keys.
Angle-bracket terms are placeholders. Square brackets mark optional arguments.
Quote multiword verdicts, such as `"MORE WORK"`.
Use a command's `--help` for the complete parser flags.

The repo-level `.agents/workflow/plan-graph.json`, `coordination.json`, `plan-approval.json`, and `plan-graph-review.json` are local and gitignored; artifact reviews use `.agents/workflow/<change>/plan-reviews.json`. No runtime object or review/approval record is committed. Every path is canonical in [Set locations](briefs-and-handoffs.md#set-locations-no-ad-hoc-paths).
Back up the entire `.agents/workflow/` tree before migration, cleanup, or destructive repair; Git cannot reconstruct these local objects.

## Command index

| Command | Effect |
|---|---|
| `migrate-repo` | import retained per-change records into the two ignored repo objects; never commit them |
| `plan-validate <plan.json> [--tasks <tasks.md>]` | validate every change; `--change` before the subcommand selects one entry |
| `plan-graph <plan.json>` | read-only; `--format md` or `--format mermaid` |
| `plan-dispatch …` | record an Oracle intent: `--scope change` for artifacts, then `--scope graph` for the repo graph |
| `plan-review …` | record the corresponding scoped verdict; graph scope requires every included artifact PASS |
| `plan-approve …` | record human approval after current artifact and graph PASS |
| `plan-waive …` | record an advance plan approval waiver for selected or all lanes |
| `dispatch [<lane>] <role> …` | record lane intent or `--scope finish` before worker starts |
| `dispatch-done [<lane>] <role> …` | record lane or finish session/outcome |
| `lane-open <lane>` | create/recover worktree/ref |
| `lane-review <lane> <role> <verdict> …` | record SHA-bound `PASS`/`MORE WORK` |
| `lane-mediate <lane> …` | record Oracle mediation for further round/bounded merge |
| `human-gate <lane> …` | record commit/evidence-bound checkpoint |
| `human-defer <lane> …` | explicit low-risk UI checkpoint deferral to integrated inspection |
| `lane-merge <lane>` | validate base, ticks, plan-defined tasks, reviews, and gates; merge |
| `lane-close <lane>` | remove a worktree/ref (Coordinator verifies a recorded merge first) |
| `sync` | refresh lane/task status from lane refs (Coordinator checkout only) |
| `task-status <lane> <task> --status …` | record a coordinator-only blocked/status reason |
| `status [--json]` | tasks/lanes/readiness/reviews/plan approval/open dispatches |
| `dashboard [--serve]` | live page over `coordination.json`; `--serve` re-reads it on every request and the page reloads every 2 s |
| `dashboard [--output <path>]` | offline state dashboard |
| `finish-record <action> …` | record release classification, evidence, inspection, and readiness |
| `--self-check` | developer-only built-in checks in isolated temporary repositories |

## Finish-scoped dispatch

Use the existing dispatch commands with `--scope finish`, no lane positional, and no `--change`.
Finish records (classification, preparation, evidence, inspections) and finish dispatch intents both live in the repo-level `.agents/workflow/finish.json`, separately from lane dispatches.
Every lane of every change in the repository graph must already be integrated. Completed lanes stay closed.
`--worktree` is accepted only with `--scope finish`.

Every finish brief follows the [contract's field list](briefs-and-handoffs.md#context) with `Scope: finish` and the included changes; brief, report, evidence, and review paths are in [Set locations](briefs-and-handoffs.md#set-locations-no-ad-hoc-paths).

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" dispatch --scope finish implementer \
  --task release-preparation --worktree <absolute-separate-worktree> --key <key>
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" dispatch-done --scope finish implementer \
  --key <key> --worker-run-id <worker-id> --result ok
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" dispatch --scope finish runner \
  --task pr-monitoring --key <key>
```

Authoring requires a separate worktree in the same repository, starting exactly at the integrated
feature HEAD. The helper refuses a dirty tracked tree, an author worktree at any other HEAD, a
worktree from another repository, and a second active writer in the same worktree.
Read-only workers default to the feature checkout; preparation reviewers may inspect a separate
candidate descending from that HEAD.
Profile selection is repo-level and applies as described under [dispatch](#dispatch). The helper records role, task, and execution metadata; it never reads the model-selection file or a role profile.
Record completion with the exact key. A changed feature HEAD or scope requires a new intent.
`status` shows open finish intents and their current/stale bindings. Recovery must establish worker ownership before retry.

## Change artifact review

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change <change> plan-dispatch \
  --scope change --role oracle --task plan \
  --report .agents/workflow/<change>/reviews/artifacts-oracle-c<cycle>-r<round>.md
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change <change> plan-review \
  --scope change --role oracle <PASS|MORE WORK> \
  --head <feature-head-sha> \
  --report .agents/workflow/<change>/reviews/artifacts-oracle-c<cycle>-r<round>.md
```

Record intent before launching the actual Oracle. Record its verdict only after reading the actual report.
Pair the report with `.agents/workflow/<change>/briefs/artifacts-oracle-c<cycle>-r<round>.md`.
This scope reviews one change's OpenSpec artifacts, not the graph. See [Oracle stage 1](plan.md#oracle-stage-1-each-changes-openspec-artifacts).

## Oracle planning corrections

After two `MORE WORK` verdicts, the Coordinator may authorize a bounded Oracle authoring handoff.
The brief identifies the exact graph or artifact paths, report, and exclusions. Coordination is never writable by the Oracle.

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-dispatch --scope graph --access write --role oracle \
  --task graph-correction --key <author-key> --report <correction-report>
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change <change> plan-dispatch --scope change --access write --role oracle \
  --task artifact-correction --key <author-key> --report <correction-report>
```

Pause other graph writers until the Oracle finishes. Verify its actual outcome and completed report before independent review.
A fresh read-only `plan-dispatch` records completion of the preceding author intent from that report.
Use a new `--key`, `--task plan` (or omit `--task`), and a new report:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-dispatch --scope graph --role oracle --task plan \
  --key <new-key> --report .agents/workflow/reviews/plan-graph-oracle-c<cycle>-r<round>.md
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change <change> plan-dispatch --scope change --role oracle --task plan \
  --key <new-key> --report .agents/workflow/<change>/reviews/artifacts-oracle-c<cycle>-r<round>.md
```

The author intent cannot supply a verdict. Launch a different Oracle for the new review.
Record the third verdict with the correction report as `--mediation`:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-review --scope graph --role oracle <PASS|MORE WORK> \
  --head <feature-head-sha> \
  --report .agents/workflow/reviews/plan-graph-oracle-c<cycle>-r<round>.md \
  --mediation <correction-report>
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change <change> plan-review \
  --scope change --role oracle <PASS|MORE WORK> \
  --head <feature-head-sha> \
  --report .agents/workflow/<change>/reviews/artifacts-oracle-c<cycle>-r<round>.md \
  --mediation <correction-report>
```

A third or later `plan-review` in the same cycle requires `--mediation`. The workflow treats this as
escalation, not an open-ended loop: resolve remaining work with the human.
A stale intent requires a fresh unique `--key`; never reuse a dispatch key for different inputs.

## Repo graph review

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-dispatch --scope graph --role oracle --task plan \
  --report .agents/workflow/reviews/plan-graph-oracle-c<cycle>-r<round>.md
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-review --scope graph --role oracle <PASS|MORE WORK> \
  --head <feature-head-sha> \
  --report .agents/workflow/reviews/plan-graph-oracle-c<cycle>-r<round>.md
```

Every included change needs a current artifact `PASS` first. The helper enforces that at graph
dispatch, graph verdict, and plan approval by binding the graph digest to each change's artifact digest.
Pair the report with `.agents/workflow/briefs/plan-graph-oracle-c<cycle>-r<round>.md`.
Launch a separate Oracle between intent and verdict commands.
This scope covers the entire local graph; `--change` is refused. See [Oracle stage 2](plan.md#oracle-stage-2-the-repo-level-graph).

## Plan approval

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-approve [--approver <label>]
```

Run after both Oracle stages pass, after the human approves the artifacts and the current repo graph, and after committing the OpenSpec artifacts.
It requires a current artifact `PASS` for every included change and a current graph `PASS` bound to the
current artifact and graph digests. It records the approver, current HEAD, graph digest, and per-change
artifact digests in `.agents/workflow/plan-approval.json`.
It refuses uncommitted `openspec/changes` content, a missing/stale graph `PASS`, or a missing/stale artifact `PASS`.
It records the human decision; it never grants approval. `status` shows whether the record is current, stale, or missing.

For an explicit [advance waiver](lane.md#advance-human-waiver) of plan approval, use one of:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-waive --reason "<human decision and reason>" --lane <change>/<lane> [--lane <change>/<lane>]
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-waive --reason "<human decision and reason>" --all-lanes
```

The helper stores the waiver in `.agents/workflow/plan-approval.json`, the same file as an approval. The waiver uses distinct fields and replaces the prior plan decision. It records `waived`, not `current` approval. It binds the current reviewed graph and artifacts. The Coordinator verifies the advance waiver in `coordination.json` before invoking the command and includes its ID in the reason. A new graph requires a new record. Only covered lanes may open under this waiver.

`lane-open` refuses unless plan approval is current or a current waiver covers the lane. `lane-merge` does not enforce it; the Coordinator checks it before merge ([lane records](lane.md#records-and-progress)).

## Plan validation and rendering

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-validate .agents/workflow/plan-graph.json \
  [--tasks <tasks.md>]
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-graph .agents/workflow/plan-graph.json \
  [--format <md|mermaid>]
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change <change> plan-validate .agents/workflow/plan-graph.json \
  --tasks openspec/changes/<change>/tasks.md
```

The default covers all graph entries. Pass `--change <change>` before the subcommand to select one entry.
`--tasks <tasks.md>` belongs to `plan-validate` only; with several changes it also requires `--change`.

## lane-open

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change <change> lane-open <lane-id>
```

`lane-open` requires current approval or a current plan waiver covering the lane.
Set `FEATDEV_LANE_WORKTREE_ROOT` only to an absolute path; a relative value can silently place new lanes elsewhere ([worktree rules](lane.md#worktrees-and-readiness)).

## dispatch

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" \
  --change <change> dispatch <lane-id> <role> \
    --task <task-id> [--key <key>] \
  [--access <read-only|write>]
```

Use role IDs `implementer`, `code-reviewer`, `test-reviewer`, `ui-reviewer`, `oracle`, or `runner`.
The `coordinator` profile belongs to the current session and is not dispatchable.
Every brief follows the [contract's field list](briefs-and-handoffs.md#context) and names the contract's absolute path.
The Coordinator resolves model and thinking settings from the selected repo-level role profile for the
actual harness launch; profiles live only in `.agents/workflow/model-selection.json`, and never contain a lane worktree or any other per-dispatch path.
The helper records role, task, and dispatch metadata; it never reads the repo-level model-selection file.
`dispatch` refuses an open same-role intent unless you pass a new `--key`. A new key bypasses that
guard and is not authorization for a second writer. The Coordinator must ensure one writer per
worktree: complete, stop, or transfer the open dispatch before starting another writer.

## dispatch-done

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" \
  --change <change> dispatch-done <lane-id> <role> --key <dispatch-key> \
  [--worker-run-id <worker-run-id>] [--tokens <n>] [--cost <x>] [--result <ok|failed>]
```

Supply the exact dispatch key and real harness run ID. Do not fabricate worker IDs.
A new task or correction requires a new key; retries of the same intent retain its key.
A reviewer or Runner reply states the verdict or status, exact SHA, finding IDs or exit status, report
path, and evidence revision; it carries no machine-readable footer.

## lane-review

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" \
  --change <change> lane-review <lane-id> <role> <PASS|MORE WORK> \
  --commit <head-sha> --report <report-path> \
  [--evidence-revision <rev>] [--evidence <path>]
```

Only `--commit` and `--report` are generally required. UI lanes require current evidence for the `ui-reviewer` `PASS`.
Only direct current-HEAD verdicts satisfy gates; earlier verdicts are history.
For a standard lane review, the default evidence revision is `r<round>`. The same commit retains its round; a new commit advances the evidence revision.
Standard review uses `code-reviewer`; an ordinary lane combines implementation and test-adequacy
duties, while a high-risk lane adds `test-reviewer` for test adequacy.
For an Oracle-authored fix or an Oracle stand-in, the Coordinator verifies independence and records the actual reviewer or stand-in worker ID in the report or brief; no helper flag records it.
The helper refuses a round after the second for a role unless a current `lane-mediate` record covers
that role; there is no cap after mediation. Stop for a human decision if work remains.

## human-gate

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" \
  --change <change> human-gate <lane-id> \
  --state <waiting|approved|rejected|revoked> --commit <head-sha> --evidence-revision <rev> [--evidence <path>]
```

The state binds the current lane HEAD and evidence revision; a changed HEAD needs a fresh decision.
Only the human records an interactive approval. The Oracle stand-in form requires a recorded human
grant; see [oracle-stand-in](#oracle-stand-in).

## human-defer

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" \
  --change <change> human-defer <lane-id> \
  --commit <head-sha> --evidence-revision <rev> \
  --rationale "bounded: <impact scope>; reversible: <reversal path>; excluded: safety, privacy, sharing, data-integrity, content-correctness" \
  --risk-assessment low --evidence <path>
```

Only a UI lane with ordinary risk and no sensitive flag is eligible.
`--evidence` is required for a UI lane: the deferred human gate must bind current rendered evidence.
The helper stores the supplied rationale plus fixed `bounded`, `reversible`, and `excluded_effects` values; it does not validate the rationale. The rationale must enumerate the bounded impact, the reversible path, and each excluded sensitive effect named in the command above. The fixed flags are not evidence.
Deferral is not approval and waives no independent review.

## oracle-stand-in

Obtain the human's explicit authorization before recording a grant.
The Coordinator records that actual decision in a JSON file under the change's ignored evidence directory.
This template defines the format; it does not grant authority. Replace every placeholder with the authorized value.

```json
{
  "id": "<unique-grant-id>",
  "scope": ["<lane-id>"],
  "granted_by": "human",
  "granted_at": "<actual-grant-timestamp>",
  "plan_sha256": "<selected-change-plan-digest>"
}
```

- `id`: a non-empty identifier unique to this human grant.
- `scope`: a non-empty list of authorized lane IDs in the selected change; `all` is refused.
- `granted_by`: the literal `human`; an agent cannot originate the authorization.
- `granted_at`: the actual authorization timestamp, in ISO-8601.
- `plan_sha256`: copy the top-level `plan_sha256` from after the plan is approved, never `plan_approval.plan_sha256` or a lane's `lane_plan_sha256`, which bind different inputs:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change <change> status --json
```

The selected-change plan must match the plan at lane opening; reconcile a changed plan before obtaining a renewed grant.
A grant is consumed once for one lane checkpoint.
Use separate files for the grant, the human acceptance checklist, and the independent Oracle's stand-in report.
The Coordinator records the stand-in's worker ID in one of those files and verifies its independence; the helper has no provenance flag.
Record the checkpoint only after the Oracle returns `PASS`:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" \
  --change <change> human-gate <lane-id> \
  --state approved --commit <head-sha> --evidence-revision <rev> \
  --approved-by oracle --authorization <grant.json> --stand-in-report <report.md> \
  --stand-in-verdict PASS --checklist <acceptance.md>
```

This is the authorized form of `human-gate`, not a separate command. A grant never supplies the Oracle acceptance verdict itself.

## human-override

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change <change> lane-merge <lane-id> \
  --human-override --override-head <candidate-sha> \
  --waive reviews --waive tasks --override-reason "<human decision and exact waived duties>"
```

Repeat `--waive` for each gate the human explicitly waives. Choices are
`reviews` (including review evidence), `tasks` (completion and task ownership),
`human-gate` (including checkpoint evidence and authorization), `ancestry`,
`clean-trees`, and `plan-approval` (Coordinator-enforced). With no `--waive`,
only `reviews` is waived. Unselected gates still apply. The human may also waive
other lane review and merge rules in the reason; the Coordinator must apply that
decision. The helper records selected categories, reason, candidate, and timestamp.

A changed candidate requires a renewed decision unless an active advance waiver explicitly covers it. For an advance waiver, pass the actual current candidate and cite the waiver ID in `coordination.json` in the reason. The helper still identifies the
candidate and target, and Git can refuse the merge. A dirty lane's uncommitted
changes are not included. The helper does not fabricate task completion or verdicts.

## lane-merge-and-lane-close

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change <change> lane-merge <lane-id>
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change <change> lane-close <lane-id>
```

Before merge, the Coordinator verifies `.agents/workflow/plan-approval.json` is current for the approved plan unless the human explicitly waives it; `lane-merge` does not enforce it.
Among role evidence, the merge check revalidates only the `ui-reviewer` evidence path and digest. It
confirms that other review report files exist, but not their evidence content or report hashes.
The Coordinator verifies every other role's evidence and each report hash before merge.
`lane-close` accepts a lane whose ticks are complete without a merge record. The Coordinator verifies
the recorded merge (phase `merged` or a matching merge record) before closing.

## lane-mediate

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change <change> lane-mediate <lane> \
  --report <oracle-mediation-report> [--role <reviewer-role>]
```

Mediation records an Oracle decision. It does not author a fix or grant human approval.
See [lane-review](#lane-review) for the round limit, and [correction and escalation](lane.md#escalation) for the workflow rule.

## Status and task status

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change <change> sync
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change <change> task-status <lane> <task> \
  --status <pending|in_progress|done|blocked> [--reason <reason>]
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change <change> status [--json]
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change <change> dashboard [--output <path>]
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change <change> dashboard --serve
```

Sync and task-status writes belong to the Coordinator. A `done` claim never replaces a committed task tick.
Status JSON and dashboards are per-change views of the shared objects, not independent sources of approval.
Before the first plan commit, they show draft planning with no ready lanes or completed tasks.
Draft checkboxes never satisfy implementation gates; review and human approval still precede the plan commit.

## Finish records

Use the order and human gates in [finish](finish.md). Repository guides define labels, checks, inputs, and checkpoints.
Finish is repository-level and covers every change in the repository graph; do not pass `--change`.
Finish records and finish dispatch intents both live in `.agents/workflow/finish.json`; finish briefs, reports, reviews, evidence, and `pr-follow-up.md` use the canonical paths in [Set locations](briefs-and-handoffs.md#set-locations-no-ad-hoc-paths).
PR-opening consent is recorded in `pr-follow-up.md` with the human, timestamp, exact integrated HEAD, and included changes; it is separate from release classification and from PR-merge consent.
`pr-check` does not validate plan approval, finish reviews, PR-opening consent, or the PR follow-up
record; the Coordinator verifies each before relying on it.

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" finish-record classify \
  --classification <label> [--version <label>] \
  --artifact <release-preparation-receipt> \
  --input <tracked-path> [--input <tracked-path> ...] \
  --check <selected-check> [--check <selected-check> ...] \
  [--inspection <inspection-checkpoint> ...]
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" finish-record capture \
  --check <selected-check> --artifact <check-receipt> \
  --input <tracked-path> [--input <tracked-path> ...]
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" finish-record inspect \
  --check <inspection-checkpoint> --artifact <human-inspection-record>
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" finish-record pr-check
```

`classify` requires the human release classification, a preparation artifact, and at least one
`--input`. Every `--input` at classify is a tracked preparation input; there is no separate
check-input field. A release with no preparation still needs a tracked release input, for example `message.py`.
The workflow requires the selected check set at classify, although the parser permits omission; the helper records only the declared set and runs no check. When a set is declared, `capture` refuses a check that was not declared.
`--inspection` declares each required direct human inspection; a deferred `ui` checkpoint requires at
least one declared integrated inspection.
`capture` and `inspect` each take exactly one `--check`. `classify` and `restage` accept repeated
`--check` and `--inspection` values, and `--input` is repeatable wherever inputs are accepted.
`pr-check` requires captured evidence for every selected check, matches a declared check set against
it, and refuses a missing or rejected inspection.

Changing a preparation artifact or any declared input requires restage, followed by renewed evidence and inspections:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" finish-record restage \
  --artifact <release-preparation-receipt> \
  --input <tracked-path> [--input <tracked-path> ...] \
  [--classification <label>] [--version <label>] \
  [--check <selected-check> ...] [--inspection <inspection-checkpoint> ...]
```

Restage retains the stored classification when `--classification` is omitted, clears final evidence
and human inspections, and preserves inspection checkpoint names unless new `--inspection` values
replace them. Repeated `--check` values replace the declared check set. Omitting `--version` clears the
stored expected version, so pass `--version` when the repository uses one.
No finish action grants PR-opening or PR-merge consent.

## Migration

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" migrate-repo
```

This imports legacy plans and ledgers without deleting their sources or committing the local objects.
Back up the entire `.agents/workflow/` tree first.
Follow the review and approval steps in [recovery](recovery.md#repository-object-migration).

## Developer self-check

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --self-check
```

The self-check uses isolated repositories. It does not launch real agents or perform human approvals.

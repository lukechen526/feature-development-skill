# Helper command reference

This is the canonical command reference for `bin/featdev.py`.
The [planning](plan.md), [lane](lane.md), [finish](finish.md), and [recovery](recovery.md) guides own workflow order and authority.
Commands record events and validate bindings. They never launch agents, perform reviews, grant consent, or open/merge PRs.

## Invocation and selection

Use Python 3.9 or later. The helper uses only the standard library.
Resolve `FEATDEV_SKILL_ROOT` through [preflight](preflight-and-roles.md); never hardcode an installation path.
Run coordinator commands from the consuming feature checkout. Workers run only commands named in their scoped brief.
Pass `--change <change>` for change-specific operations when several changes exist.
Repo graph review uses `--scope graph` without `--change`.
Lane and task IDs are local to a change. New dispatches require unique keys.
Angle-bracket terms are placeholders. Square brackets mark optional arguments.
Quote multiword verdicts, such as `"MORE WORK"`.
Use a command's `--help` for the complete parser flags.

The repo-level `.agents/workflow/plan-graph.json` and `coordination.json` are local and gitignored.
Artifact reviews use `.agents/workflow/<change>/plan-reviews.json`.
Repo graph reviews use `.agents/workflow/plan-graph-review.json`.
Plan approval uses `.agents/workflow/plan-approval.json`.
No runtime object or review/approval record is committed.

## Command index

| Command | Effect |
|---|---|
| `migrate-repo` | import retained per-change records into the two ignored repo objects; never commit them |
| `plan-validate <plan.json> [--tasks <tasks.md>]` | validate every change; global `--change` selects one entry |
| `plan-graph <plan.json>` | read-only; `--format md` or `--format mermaid` |
| `plan-dispatch …` | record an Oracle intent: `--scope change` for artifacts, then `--scope graph` for the repo graph |
| `plan-review …` | record the corresponding scoped verdict; graph scope requires every included artifact PASS |
| `plan-approve …` | record human approval of committed artifacts and the reviewed repo graph |
| `dispatch [<lane>] <role> …` | record lane intent or `--scope finish` before worker starts |
| `dispatch-done [<lane>] <role> …` | record lane or finish session/outcome |
| `lane-open <lane>` | create/recover worktree/ref |
| `lane-review <lane> <role> <verdict> …` | record SHA-bound `PASS`/`MORE WORK` |
| `lane-mediate <lane> …` | record Oracle mediation for further round/bounded merge |
| `human-gate <lane> …` | record commit/evidence-bound checkpoint |
| `human-defer <lane> …` | explicit low-risk UI checkpoint deferral to integrated inspection |
| `lane-merge <lane>` | validate base/ticks/source/plan/reviews/gates; merge |
| `lane-close <lane>` | remove merged worktree/ref |
| `sync` | refresh lane/task status from lane refs (Coordinator checkout only) |
| `task-status <lane> <task> --status …` | record a coordinator-only blocked/status reason |
| `status [--json]` | tasks/lanes/readiness/reviews/open dispatches |
| `dashboard [--serve]` | live page over `coordination.json`; `--serve` re-reads it on every request and the page reloads every 2 s |
| `dashboard [--output <path>]` | offline state dashboard |
| `finish-record <action> …` | record release classification, evidence, inspection, and readiness |
| `--self-check` | developer-only built-in checks in isolated temporary repositories |

## Finish-scoped dispatch

Use the existing dispatch commands with `--scope finish` and no lane positional.
Finish records live in `changes.<change>.finish_dispatches`, separately from lane dispatches.
Every included lane must already be integrated. Completed lanes stay closed.

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> dispatch --scope finish implementer \
  --task release-preparation --worktree <separate-worktree> --key <key>
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> dispatch-done --scope finish implementer \
  --key <key> --worker-run-id <worker-id> --result ok
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> dispatch --scope finish runner \
  --task pr-monitoring --key <key>
```

Authoring requires a separate worktree in the same repository, starting at the integrated feature HEAD.
Read-only workers default to the feature checkout; preparation reviewers may inspect a separate candidate descending from that HEAD.
Apply the selected role profile at harness launch.
Record completion with the exact key. A changed feature HEAD or scope requires a new intent.
`status` shows open finish intents and their current/stale bindings. Recovery must establish worker ownership before retry.

## Change artifact review

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> plan-dispatch   --scope change --role oracle     --report .agents/workflow/<change>/reviews/artifacts-oracle-r<round>.md

python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> plan-review   --scope change --role oracle <PASS|MORE WORK> --head <feature-head-sha>   --report .agents/workflow/<change>/reviews/artifacts-oracle-r<round>.md
```

Record intent before launching the actual Oracle. Record its verdict only after reading the actual report.
This scope reviews one change's OpenSpec artifacts, not the graph. See [Oracle stage 1](plan.md#oracle-stage-1-each-changes-openspec-artifacts).

## Oracle planning corrections

After two `MORE WORK` verdicts, the Coordinator may authorize a bounded Oracle authoring handoff.
The brief identifies the exact graph or artifact paths, report, and exclusions. Coordination is never writable by the Oracle.

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" plan-dispatch --scope graph --access write --role oracle \
  --task graph-correction --key <author-key> --report <correction-report>
```

Pause other graph writers until the Oracle finishes. Verify its actual outcome and completed report before independent review.
A fresh read-only `plan-dispatch` records completion of the preceding author intent from that report.
The author intent cannot supply a verdict. Launch a different Oracle for the new review.
Record the third verdict with `plan-review --scope graph --mediation <correction-report>`.
Artifact correction uses `--scope change --change <change>` instead.

## Repo graph review

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" plan-dispatch   --scope graph --role oracle     --report .agents/workflow/reviews/plan-graph-oracle-r<round>.md
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" plan-review   --scope graph --role oracle <PASS|MORE WORK> --head <feature-head-sha>   --report .agents/workflow/reviews/plan-graph-oracle-r<round>.md
```

Every included change needs a current artifact `PASS` first. Launch a separate Oracle between intent and verdict commands.
This scope covers the entire local graph; `--change` is refused. See [Oracle stage 2](plan.md#oracle-stage-2-the-repo-level-graph).

## Plan approval

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" plan-approve [--approver <label>]
```

Run after the human approves the artifacts and the current repo graph PASS, and after committing the OpenSpec artifacts.
It records the approver, current HEAD, graph digest, and per-change artifact digests in
`.agents/workflow/plan-approval.json`. It refuses uncommitted `openspec/changes` content or a missing/stale graph PASS.
It records consent; it never grants approval. `status` shows whether the record is current, stale, or missing.

## Plan validation and rendering

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py"   plan-validate .agents/workflow/plan-graph.json
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py"   plan-graph .agents/workflow/plan-graph.json --format md
```

The default covers all graph entries. Global `--change <change>` selects one entry.
With several changes, `--tasks <tasks.md>` also requires `--change`.

## lane-open

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> lane-open <lane-id>
```

## dispatch

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" \
  --change <change> dispatch <lane-id> <role> \
    --task <task-id> [--key <key>] \
  [--access <read-only|write>] [--acceptance <path>] [--evidence <path>]
```

Use role IDs `implementer`, `code-reviewer`, `test-reviewer`, `ui-reviewer`, `oracle`, or `runner`.
The `coordinator` profile belongs to the current session and is not dispatchable.
The Coordinator resolves model/thinking settings from the selected role profile for the actual harness launch.
The helper records roles and tasks only; it never reads the repo-level model-selection file.
Combined Oracle intents snapshot the supplied acceptance and evidence paths/hashes before launch.

## dispatch-done

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" \
  --change <change> dispatch-done <lane-id> <role> --key <dispatch-key> \
  [--worker-run-id <worker-run-id>] [--tokens <n>] [--cost <x>] [--result <ok|failed>]
```

Supply the exact dispatch key and real harness run ID. Do not fabricate worker IDs.
A new task or correction requires a new key; retries of the same intent retain its key.

## lane-review

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" \
  --change <change> lane-review <lane-id> <role> <PASS|MORE WORK> \
  --commit <head-sha> --report <absolute-report-path> \
  [--evidence-revision <rev>] [--evidence <path>]
```

Only `--commit` and `--report` are generally required. UI lanes require current evidence for the `ui-reviewer` `PASS`.
Only direct current-HEAD verdicts satisfy gates; earlier verdicts are history.
The default evidence revision is `r<round>`. The same commit retains its round; a new commit advances it.
An ordinary lane uses `code-reviewer` for implementation and test adequacy. High-risk lanes also need `test-reviewer`.

## human-gate

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" \
  --change <change> human-gate <lane-id> \
  --state <waiting|approved|rejected|revoked> --commit <head-sha> --evidence-revision <rev> [--evidence <path>]
```

## human-defer

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" \
  --change <change> human-defer <lane-id> \
  --commit <head-sha> --evidence-revision <rev> \
  --rationale "<bounded, reversible, low-risk; excluded sensitive effects>" \
  --risk-assessment low --evidence <path>
```

`--evidence` is required for a UI lane: the deferred human gate must bind current rendered evidence.

## oracle-stand-in

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" \
  --change <change> human-gate <lane-id> \
  --state approved --commit <head-sha> --evidence-revision <rev> \
  --approved-by oracle --authorization <grant.json> --stand-in-report <report.md> \
  --stand-in-verdict PASS --checklist <acceptance.md>
```

This is the authorized form of `human-gate`, not a separate command. A grant never supplies the Oracle acceptance verdict itself.

## human-override

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> lane-merge <lane-id> \
  --human-override --override-reason "<the human decision>"
```

This is an explicit-human-decision form of `lane-merge`. It waives only review duties, never ticks, clean trees, or human checkpoints.

## lane-merge-and-lane-close

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> lane-merge <lane-id>
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> lane-close <lane-id>
```

## Combined Oracle review

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> lane-review <lane> code-reviewer PASS \
  --combined --commit <candidate-sha> --report <report-path> \
  --oracle-provenance <real-reviewer-id> --feature-base <feature-base> --review-base <whole-change-base> \
  --acceptance <acceptance-path> --evidence <evidence-path> --evidence-revision <revision>
```

The Oracle dispatch must already bind the same acceptance and evidence inputs.
The [lane guide](lane.md#merge-and-close) owns eligibility, independence, and exact-candidate integration rules.
This implementation review does not replace either planning review stage.

## lane-mediate

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> lane-mediate <lane> \
  --report <oracle-mediation-report> [--role <reviewer-role>]
```

Mediation records an Oracle decision. It does not author a fix or grant human approval.
See [correction and escalation](lane.md#escalation).

## Status and task status

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> sync
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> task-status <lane> <task> \
  --status <pending|in_progress|done|blocked> [--reason <reason>]
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> status [--json]
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> dashboard [--output <path>]
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> dashboard --serve
```

Sync and task-status writes belong to the Coordinator. A `done` claim never replaces a committed task tick.
Status JSON and dashboards are per-change views of the shared objects, not independent sources of approval.

## Finish records

Use the order and human gates in [finish](finish.md). Repository guides define labels, checks, inputs, and checkpoints.

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> finish-record classify \
  --classification <label> [--version <label>] --artifact <preparation-receipt> --input <tracked-path> \
  --check <selected-check> [--inspection <checkpoint>]
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> finish-record capture \
  --check <selected-check> --artifact <check-receipt> --input <tracked-path>
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> finish-record inspect \
  --check <checkpoint> --artifact <human-inspection-record>
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> finish-record pr-check
```

Repeat `--input`, `--check`, and `--inspection` where the corresponding action accepts multiple values.
Each capture/inspection action selects exactly one `--check`. Use `pr-check` to validate readiness after captures.
Preparation changes require restaging, followed by renewed evidence and inspections:

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> finish-record restage \
  --artifact <preparation-receipt> --input <tracked-path> [--classification <label>]
```

Restage retains classification when omitted. Repeated `--check` or `--inspection` replaces those selected requirements.
No finish action grants PR-opening or PR-merge consent.

## Migration

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" migrate-repo
```

This imports legacy plans and ledgers without deleting their sources or committing the local objects.
Follow the backup, review, and approval steps in [recovery](recovery.md#repository-object-migration).

## Developer self-check

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --self-check
```

The self-check uses isolated repositories. It does not launch real agents or perform human approvals.

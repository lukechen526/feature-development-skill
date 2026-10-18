# Lane phase

Use approved cohesive lane boundaries (`plan.md`). Implement/tick every assigned task,
review complete lane diff at current `HEAD`, merge bound passing work, close.

## Derived state

Committed records:
- `openspec/changes/<change>/tasks.md`: top-level task identity/completion.
- `openspec/changes/<change>/plan/graph-plan.json`: assignments, dependencies, risk/UI flags.

Coordinator store, gitignored, under `<feature-checkout>/.agents/workflow/<change>/`:
- Derived state is rebuilt in memory from committed artifacts. There is no persisted `state.json`.
- `coordination.json`: the definitive coordinator store. Holds each lane's `status`, `status_reason`,
  per-task state (`pending | in_progress | done | blocked`), `worktree` and `worktree_present`,
  `review_status` (`state`, `round`, `reviewer`, reviewed commit, report path), `escalation`
  (`required`, `rounds`, `state`, `decision`, options), dispatches, reviews, authorizations, human gates
  and merge records. A top-level `subagents` list tracks every dispatched subagent: `run_id`, `role`,
  `lane`, `task`, `model`, `level`, `access`, `worktree`, `status`, `result`, timestamps.
- `model-selection.json`: per-role model/thinking answers and the provisioning mechanism.
- `briefs/`: one file per dispatch, named `<lane>-<role>-<task-or-round>.md`.
- `reviews/`: one report per review, named `<lane>-<role>-r<round>.md`.
- `reports/`: lane live reports, named `<lane>-report.md`.

Every artifact a later session needs lives here. A brief or review at any other path is a Coordinator error.
The store is created on demand. `state-derive` ignores `.agents/workflow/` through the local Git exclude file.

Only `featdev.py` writes state; atomic/idempotent. Lost state: re-derive.
Lost coordination: re-review/renew approval, never lose committed completion.

No initialization. After approved planning commit:

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> state-derive <change>
```

Default source: committed plan/tasks at feature `HEAD`; record source SHA.
Use `--ref <committed-ref>` for lane candidate.
Refresh after plan/task-artifact commits and before first lane mutation.

## Commands

Standard-library CLI: `"$FEATDEV_SKILL_ROOT/bin/featdev.py"`; no repository-specific constants.
`FEATDEV_SKILL_ROOT` is resolved by the Step 0 preflight (`roles-and-models.md`) and never hardcoded.
Commands discover state; pass global `--change <change>` when ambiguous. No run id.
Use command `--help` for flags; `…` below denotes required flags.

| Command | Effect |
|---|---|
| `plan-validate <plan.json> [--tasks <tasks.md>]` | shape, task bijection, references, cycles |
| `plan-graph <plan.json>` | read-only; `--format md` or `--format mermaid` |
| `state-derive <change> [--ref <committed-ref>]` | refresh committed view |
| `task-tick <lane> <task>` | tick assigned top-level task; refuse foreign/unknown/nested IDs |
| `dispatch <lane> <role> …` | record intent before worker starts |
| `dispatch-done <lane> <role> …` | record session/outcome |
| `lane-open <lane>` | create/recover worktree/ref |
| `lane-review <lane> <role> <verdict> …` | record SHA-bound `PASS`/`MORE WORK` |
| `review-decision <lane> …` | record Coordinator review-duty decision (code/test/ui; opt-in/shadow) |
| `review-reuse <lane> <role> …` | record assessed ancestor-review reuse with intervening-diff binding |
| `lane-mediate <lane> …` | record Oracle mediation for further round/bounded merge |
| `human-gate <lane> …` | record commit/evidence-bound checkpoint |
| `lane-merge <lane>` | validate base/ticks/source/plan/reviews/gates; merge |
| `lane-close <lane>` | remove merged worktree/ref |
| `status [--json]` | tasks/lanes/readiness/reviews/open dispatches |
| `dashboard [--serve]` | live page over `coordination.json`; `--serve` re-reads it on every request and the page reloads every 2 s |
| `dashboard [--output <path>]` | offline state dashboard |

Combined lane integration uses the guarded `--ff-only` path described under Merge and close below.
Repository guides own test selection, commands, evidence formats, and reuse rules.
Briefs name the exact focused checks and their execution owners.
If affected checks cannot be determined, use the repository's documented fallback or ask the human.

## Readiness and worktrees

### Lane isolation

Before any lane implementation dispatch, Coordinator runs:

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> lane-open <lane-id>
```

On first open, `lane-open` creates a new isolated git worktree from feature `HEAD`:
`~/.agents/worktrees/<repo>/<branch>/<change>/<lane-id>/`, ref `featdev/<change>-<lane-id>`.
Worktree root: `FEATDEV_LANE_WORKTREE_ROOT` (absolute path) overrides the default; it is
always a location under Coordinator control, never relocated per environment.

Optional worktree registration: when the environment exposes a worktree-tracking command, the
Coordinator may register the new lane worktree as a child of the feature worktree while the worktree
stays at its `~/.agents/worktrees` location. Registration is best-effort and never blocks the
workflow; failures print a note. The workflow itself never depends on a specific tracker.

A lane's recorded worktree path is authoritative for its lifetime. This setup is mandatory;
recovery reuses the lane worktree/ref, never the feature checkout.
Coordinator owns lane worktrees. Allow one writer per worktree.
Keep parallel writes disjoint; serialize overlap through plan or Coordinator.

Perform all lane implementation edits, commands, checks, ticks, and commits in the lane worktree.
Deliver reports/evidence to the absolute paths in the brief; these artifacts do not authorize feature-checkout edits.
The feature checkout receives lane implementation only through `lane-merge`.

### Coordinator duties

After tick/review/gate/merge/correction/recovery, recompute readiness; dispatch ready lanes within capacity.

### Status updates (mandatory on every subagent update)

A worker result, a review verdict, a failed dispatch, a timeout, or a correction batch is not complete
until the Coordinator has recorded it. On every update, in this order:

1. Close or update the dispatch record (`dispatch-done` with the run id and result).
2. Record the task ticks that the update added (`state-derive` after reading the lane worktree).
3. Record reviews, mediations and human gates with the exact commit, report path and revision.
4. Write the lane phase: `writing`, `reviewing`, `fixing`, `awaiting-gate`, `merging`, `merged`.
5. Refresh the dashboard or status view the human reads, and state the change in one line.

Lane progress before merge is committed on the lane ref, not on the feature branch. `status --json` reports
it per lane as `inflight` (for example `in-flight only (lane ref): 4/11`); `dashboard` shows it in its own
column. The feature-branch view stays unchecked until `lane-merge`, which is what makes ticks authoritative.
To inspect a lane candidate directly, run `state-derive <change> --ref featdev/<change>-<lane>`.

The coordinator refreshes it with `featdev.py sync` after every subagent update.
Sync reads feature-branch ticks for merged work and lane-ref ticks for unmerged work. It writes `done`, `in_progress`, `pending` or `blocked` per task and the
lane status (`pending | in_progress | blocked | done`) into `coordination.json`. Blocked cases that a sync
cannot infer are recorded with `task-status <lane> <task> --status blocked --reason "<why>"`.

Ownership is enforced, not just documented: `sync` and `task-status` refuse to run outside the feature
checkout, so a worker in a lane worktree cannot write the coordinator store. Workers only tick tasks.

Read the store, never chat history. When the human asks "how is lane X", when a new session starts, and
before every dispatch, decision or merge, read `coordination.json` (or `featdev.py status --json`, which
reads it) and answer from that. Chat history, memory of earlier turns, dashboard screenshots and derived
`state.json` are not status sources; they can be stale or incomplete.

Never leave a subagent update only in chat. `featdev status --json` must answer, at any moment, which
lane is complete, which is in progress, which is under review, and what each lane is waiting for. When a
record and an artifact disagree, the artifact wins and the record is corrected; never the reverse.
Require feature-branch ticks for cross-lane prerequisites; use own ticks for same-lane prerequisites.
All assigned tasks ticked: complete, never redispatch as ready.

Before reviewer dispatch, confirm clean lane worktree, all assigned top-level ticks, required reports/evidence bindings,
check outcomes, current base/`HEAD`. Return missing items to Implementer.
Procedural check only: no approval, generated review package, or round change.

Name the lane worktree's absolute path as `Worktree:` in every lane implementation/review brief (`briefs-and-handoffs.md`).
A wrong-tree brief is a Coordinator error: correct it before dispatch.
If wrong-tree work occurred, stop the worker and discard that work, including commits; preserve unrelated work.
Redispatch with the corrected lane-worktree brief; never accept wrong-tree implementation.

### Implementer duties

Read the full brief; verify the working directory and lane ref before implementation.
Work only in the named lane worktree, including corrections and authorized Oracle fixes.
Read canonical sources anywhere; source reads do not authorize writes there.
Never edit the feature checkout; stop and report any wrong-tree work.

### Reviewer duties

Review the lane diff at the current lane-worktree `HEAD` named in the brief.
Run independent checks there; keep review read-only and never patch reviewed work.

## Implementation

Fill `briefs-and-handoffs.md` implementation template; apply its handoff-context rules. Write the filled
brief to `.agents/workflow/<change>/briefs/<lane>-<role>-<task>.md` and hand the worker that absolute path.
Record intent before starting worker:

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" \
  --change <change> dispatch <lane-id> <role> \
  --model <provider/model> --level <level> --task <task-id> [--key <key>] \
  [--access <read-only|write>] [--acceptance <path>] [--evidence <path>]
```

For a combined Oracle dispatch, pass the final acceptance and evidence paths so the intent
snapshots their canonical paths and hashes before launch; standard dispatches may omit them.

Implement every assigned task using relevant canonical sources.

### One fresh worker subagent per task (mandatory)

Dispatch a **new** implementer subagent for each task ID in a lane. One worker never implements two tasks.

1. Record the dispatch, write the brief for that single task, launch a fresh session.
2. The worker implements only that task, commits it, ticks it, updates the report, and stops.
3. The Coordinator verifies the commit and tick, closes the dispatch, runs `sync`, then dispatches the
   next task to a new worker.

Reuse a session only for a correction to the same task, and only while the brief is unchanged. Never reuse a
worker across tasks to save time: a failure must stay inside one task, and each brief must be read fresh.
Parallel workers are not allowed inside one lane; they share a worktree and would collide.
After each task: focused checks, exact output, genuine completion tick committed with work.
The ONLY permitted `tasks.md` change is flipping your assigned tasks' checkboxes — never add
notes, sub-bullets, or wording edits there; records belong in the lane report:

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" \
  --change <change> task-tick <lane-id> <task-id>
```

Leave clean tree; report under Coordinator's absolute change path. Keep the report current
throughout the dispatch: update it after every commit, capture, or check so a fresh worker
can resume from the report alone if the session is lost. Assign one execution owner per check. Commit code/ticks/version/generated files before expensive final capture.
Later writes invalidate affected evidence; full logs stay in files, handoffs contain decisive results/paths.

Each dispatch receives a unique key. Keep that key with the harness run ID.
Use `--key` only to retry the same recorded dispatch. Use a new key for a correction or another task.

After worker returns, Coordinator closes the exact dispatch:

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" \
  --change <change> dispatch-done <lane-id> <role> --key <dispatch-key> \
  [--worker-run-id <worker-run-id>] [--tokens <n>] [--cost <x>] [--result <ok|failed>]
```

Writer deadline: 60 minutes per implementation dispatch (or the preflighted harness limit, whichever is
shorter); require commits that a fresh worker can continue from. On deadline, inspect progress. If the
harness supports continuation, continue the child; otherwise dispatch a fresh same-role worker from the
live report. Extend only with commit/gate progress; otherwise reset to the last verified commit or
consult the Oracle about the technical gap before redispatching. Never exceed applicable reset
authorization.

## Testing and evidence

- Run assigned checks: static, focused tests, broader suites. Record exact commands/output.
- Bind tests/screenshots/evidence to producing commit/revision. Later commits/corrections/regeneration stale affected evidence;
  stale evidence cannot support `PASS` or gates.
- Reviewers independently run relevant checks at reviewed `HEAD`. Missing/stale/under-covering evidence
  or unproven claims: `MORE WORK`.
- Reuse only with matching evidence content, reviewed dependencies, commits, hashes; label reuse, never fresh run.
- Prefer completion events/deadlines; bound fallback polling/output.
  Cache preflight only while mechanism/model/level/environment/evidence bindings match; retain live initial availability check.

## Risk-based review decisions (opt-in/shadow)

The Coordinator records a per-lane review-duty decision for code, test, and UI duties
(`full`, `focused`, or `skip`) with risk factors, acceptance coverage, checks, and rationale:

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> review-decision <lane-id> \
  --code <full|focused|skip> --test <full|focused|skip> --ui <full|focused|skip> \
  --risk-factor <factor>… --acceptance-coverage "<criteria the duties cover>" \
  --check "<runnable check>"… --rationale "<why>" [--opt-in]
```

- `skip` is explicit and never labeled `PASS`; status and dashboard display it as `skipped`.
- High-risk lanes cannot skip code or test review; a lane without the `ui`/`screen` flags has
  no UI duty and must record `--ui skip`.
- Without `--opt-in` the record is shadow-only — persisted and
  displayed, but lane-merge keeps the standard review gates. With `--opt-in` the
  change's review policy becomes `risk-based` and lane-merge honors the selected duties.
- A missing decision record, or recording without `--opt-in`, always falls back to the
  standard review gate. Combined single-lane mode refuses decision records entirely.

## Ancestor-review reuse (opt-in/shadow)

An earlier ordinary-agent `PASS` at an ancestor commit can satisfy a later HEAD only when the
Coordinator records the assessed intervening change:

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> review-reuse <lane-id> <role> \
  --ancestor <ancestor-sha> --intervening-diff <diff-file> \
  --assessment "<what the intervening change does not touch>" \
  [--evidence-inputs-changed yes|no]
```

- The reuse is bound to the intervening-diff digest and unchanged evidence inputs; status
  displays it as `reused`, never a fresh `PASS`.
- Affected or uncertain changes (or changed evidence inputs) require fresh re-review instead.
- Non-ancestor verdicts are invalid; reuse cannot chain through another reuse record.
- Human authorization and combined-Oracle approvals stay exact-bound and are never reusable.

## Targeted review at lane HEAD

Start only after every lane task is implemented/ticked. Use `briefs-and-handoffs.md` review template;
include changed files and all plan flags. Read worktree/approved sources/evidence directly.
Never consume generated review packages, deterministic preflight results, digests, or verdict files.

Cover every assigned acceptance criterion: code checks implementation/every tick;
high-risk test reviewer checks test adequacy; UI reviewer checks rendered acceptance.
Uncovered criterion/unjustified tick: `MORE WORK`.
Start full lane diff/named dependencies; expand only for concrete failure/requirement violation.
Risk-calibrated reachable issues only; no defensive coding/unnecessary complexity.
Added-code finding must name prevented failure.

Required passes: ordinary = combined code/test; high = separate independent code and test;
`ui: true` adds rendered-UI review of supplied screenshots (UI reviewer ingests evidence only — never launches the application); `screen: true` adds current changed-screen evidence before merge.

Coordinator records verdict before correction:

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" \
  --change <change> lane-review <lane-id> <role> <PASS|MORE WORK> \
  --commit <head-sha> --report <absolute-report-path> \
  [--evidence-revision <rev>] [--evidence <path>]
```

Only `--commit`/`--report` required generally.
Revision defaults to `r<round>`; same commit reuses recorded revision, new commit advances round.
`screen: true` refuses `PASS` without current evidence.
Store the report under `.agents/workflow/<change>/reviews/<lane>-<role>-r<round>.md` and record that
absolute path with `lane-review`. Record while reviewed `HEAD` is current. Ancestor verdict: superseded history, not approval.
Non-ancestor: refused, no record. Rounds persist across commits.
Status/dashboard expose role, SHA, round, current/superseded state, selected duties, and reuse.
Every correction round keeps the selected duties and any reuse assessment visible in status,
dashboard, and the PR summary; a missing decision record falls back to the standard gate.

Use shared stable findings; collect all first-pass verdicts into one deduplicated correction batch.

## Human checkpoint

Plan `ui`, `screen`, or `sensitive` requires checkpoint; flags are declared, never inferred from diff.
Definitions: `plan.md`. No such flag: `human-gate` refuses. Screen lanes also require `--evidence`.
Repository guides define the required UI states, environments, visual variants, and capture commands.
Keep the plan-required reviews and human checkpoints. Record any repository-authorized waiver explicitly.
A lane merge integrates work into the feature branch. It never authorizes a PR merge into the target branch.

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" \
  --change <change> human-gate <lane-id> \
  --state <waiting|approved|rejected|revoked> --commit <head-sha> --evidence-revision <rev> [--evidence <path>]
```

Recorded human authorization may permit independent Oracle stand-in:

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" \
  --change <change> human-gate <lane-id> \
  --state approved --commit <head-sha> --evidence-revision <rev> \
  --approved-by oracle --authorization <grant.json> --stand-in-report <report.md> \
  --stand-in-verdict PASS --checklist <acceptance.md>
```

Grant must be human-origin, recorded, name checkpoint/all approved-plan checkpoints, and match approved plan revision.
Consume once; never reuse for second lane.
Stand-in must cover checklist with `PASS`; technical-only review insufficient.
`MORE WORK`: gate closed, grant unconsumed.
Target-branch merge never authorizable; helper never performs it.

Authorization never waives a check or independent review that the risk decision requires:
the interactive and authorized paths run the same selected non-human checks, and the authorized
gate still gets its own current Oracle stand-in acceptance judgment. No separate technical
pre-PR Oracle pass is invented when the normal finish path does not require one (see `finish.md`).

Missing/stale/revoked/unbound/wrong-commit approval blocks merge.
Later writes/corrections/stale evidence require affected reviews and fresh human approval.
Rejected/revoked checkpoint suspends grant; renewal requires new grant, never reapply old grant after correction.
Status/dashboard show required flag and approved/blocked reason.
Never infer consent; unrelated ready lanes continue.

## Correction and escalation

**Lane round lifecycle (fixed):** implementation → review 1 → implementation fix → review 2.
That is the two-round maximum. If review 2 still yields `MORE WORK`, the escalation path is
fixed and mandatory:

1. `lane-mediate` records Oracle mediation (helper refuses further review records without it).
2. **Oracle authors the fix** — dispatch Oracle with `--access write`; the implementer does
   not write another round.
3. A **different independent reviewer** must PASS the Oracle-authored fix.
4. Human gate, then merge.

Round-by-round detail:

1. Round 1: implementation plus required reviews.
2. `MORE WORK`: one complete deduplicated batch; Implementer records every ID `fixed`/`disputed` with evidence.
3. Round 2: affected checks/substantive reviews; every unaffected required role independently impact-checks current `HEAD`.
   All required roles return fresh verdicts; never carry ancestor `PASS`.
4. Review 2 `MORE WORK` = two-round limit reached: helper refuses further review records until
   `lane-mediate` records Oracle mediation.
5. Then Oracle-authored fix (step 2 above) + independent review (step 3). One further Oracle
   pass allowed; unresolved work goes to human.

Correction rounds never relax the selected duties: every round repeats the selected non-human
checks under the current decision record (or the standard gate when no record exists), and an
Oracle-authored fix always receives independent review regardless of any selected duty.

Conflicting requirement/unclear closure: pause dependent edits; focused read-only Oracle question.
No round reset/edit authorization. Advice/review dispatches remain `read-only`.
Coordinator never patches/approves own implementation; reviewers never patch reviewed work; Oracle never self-approves.

## Testing runtimes

Repository guides select the primary environment for lane checks and evidence.
Run the repository's full release matrix only after all lanes merge into the feature branch.
See `finish.md` for the release sequence. No platform or runtime version is fixed by this skill.

## Merge and close

Require every flag-required reviewer `PASS` at same current lane `HEAD`, current bound human approval where required,
and existing required screen evidence.

Under an explicit `risk-based` policy with a valid recorded decision, lane-merge honors the
selected duties: a duty recorded as `skip` is not required (persisted as a selected duty beside
the merge, never a `PASS` label), and an assessed ancestor reuse satisfies that duty. Everything
else still binds: task ticks and tick ownership, clean trees, plan/base and ancestry validation,
candidate tick-state equality, current required evidence, human checkpoints, and the merge
itself. Missing or tampered decision records fall back to the standard gate. Decision-driven
relaxation is opt-in per change (`--opt-in`).

### Human override

A recorded human decision trumps the review gates only. When the human explicitly instructs a
merge against the review gates, Coordinator may run:

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> lane-merge <lane-id> \
  --human-override --override-reason "<the human decision>"
```

- `--override-reason` is mandatory and must name the human decision; an unexplained override
  cannot be recorded.
- The override waives review duties only. It can never waive clean-tree, task-tick
  ownership/completeness, lane-base/ancestry validation, current required evidence, or human
  authorization integrity — those checks still run and block.
- The override (reason, timestamp, skipped gates) is persisted in the coordination record
  beside the merge for audit. Override remains a human-prerogative escape hatch for review
  duties, not a substitute for the workflow; prefer completing the gates whenever feasible.

### Standard merge checks

Resolve base without scratch as single output:

```bash
git merge-base --all <lane-ref> <feature-branch-HEAD>
```

Base must equal lane-opening commit and remain ancestor of both heads, including after earlier lane merges.
Missing ref, absent/non-unique base, failed ancestry: block for reconciliation.
Derive candidate state; source SHA must equal candidate `HEAD`.
Require exact set equality: newly added ticks since base = candidate-derived checked lane tasks = assigned lane tasks.
Other task-status changes/mismatches block. Exclude inherited earlier-lane ticks.

Feature branch moved: non-fast-forward merge preserving reviewed SHA/tree; never rebase passed lane.

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> lane-merge <lane-id>
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change <change> lane-close <lane-id>
```

Combined mode: Oracle supplies the lane code/test and complete-change implementation review.
Require current tasks, ticks, UI review, human checkpoints, and candidate acceptance/evidence.
Fast-forward the lane into the feature branch at the exact Oracle-reviewed candidate.
Changed pre-merge base, candidate, acceptance, evidence, or eligibility invalidates that approval.
Fallback requires an approved mode change and fresh standard review.

After this lane merge, run `finish.md` on the feature branch.
Classification, version changes, generated artifacts, and final release evidence happen there.
Later release-preparation commits do not undo a recorded lane merge or approve the new release evidence.
The combined record's `final` duty means implementation coherence, not final release approval.
PR-opening approval and the human's PR merge into the target branch remain separate.

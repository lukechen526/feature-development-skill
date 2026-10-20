# Dispatch briefs and shared handoff contract

All roles read this contract. Approved sources and repository rules override briefs.

## Context

Use fresh scoped briefs from Coordinator's [briefs-and-handoffs.md](briefs-and-handoffs.md); never fork/copy Coordinator history.
Preserve role/tasks, criteria, dependencies, owned seams, exclusions, reviewer remit, sources,
change, lane or finish scope, worktree/base/`HEAD`, profile, contract, deadline, check owner,
report/evidence paths/revision, receipt fields.

Every lane brief names `Change`, `Lane`, `Task/round`, `Worktree`, `Base`, `HEAD`, `Profile`, `Contract`,
and `Deadline`. A finish brief names `Change`, `Scope: finish`, `Task`, `Worktree`, `Integrated HEAD`,
owned paths, `Profile`, `Contract`, and `Deadline`. The contract is always this file's absolute path.
The worktree is execution metadata in the brief and coordination; it never belongs in a role profile.

Reuse same-lane session only while coherent; send changed SHA/findings/files/checks/evidence.
Stale context: fresh session, same brief/report pointers.
Keep Coordinator informed of direct Reviewer/Implementer clarification through selected mechanism.
Clarification never authorizes review-time edits, expands scope, hides corrections, or replaces verdicts.
Resolve conflicting requirements before dependent edits.
Read-only Oracle consultation answers one uncertainty; never resets rounds.

A new dispatch `--key` bypasses the helper's open same-role guard. A new key records a new intent; it is
never authorization for a second writer. The Coordinator ensures one writer per worktree and closes the
prior intent before replacement.

Helper syntax lives in [helper-commands.md](helper-commands.md). Briefs select the relevant command and fill every placeholder.
Export `FEATDEV_SKILL_ROOT` to the resolved absolute skill path before running any helper command; see
[preflight](preflight-and-roles.md). Briefs supply resolved values so workers do not inherit the
Coordinator's shell.

## Repository instructions

Before planning or scoped work, look for applicable agent instruction files.
Check the repository root and directories that govern the assigned paths.
Common examples include `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`,
`.github/instructions/*.instructions.md`, and `.cursor/rules/*`. These are examples, not required filenames.
Follow each file's scope and the harness's instruction precedence. Do not combine unrelated scoped rules.
The Coordinator lists discovered applicable files in the brief's Sources; workers check for additional instructions within their assigned scope.
If no agent instruction file exists, use the repository's relevant guides and approved brief.
A missing example filename alone never blocks work. Ask the human when required rules are missing or conflicting.

## Set locations (no ad-hoc paths)

Every handoff artifact has one home, under the coordinator store:

| Artifact | Path |
|---|---|
| Preflight record | `.agents/workflow/preflight.json` |
| Capability map | `.agents/workflow/capability-map.md` |
| Model selection (repo-level) | `.agents/workflow/model-selection.json` |
| Local repository plan (gitignored) | `.agents/workflow/plan-graph.json` |
| Repository coordination | `.agents/workflow/coordination.json` |
| Plan approval | `.agents/workflow/plan-approval.json` |
| Finish record | `.agents/workflow/finish.json` |
| Filled lane brief | `.agents/workflow/<change>/briefs/<lane>-<role>-<task-or-round>.md` |
| Artifact-review brief | `.agents/workflow/<change>/briefs/artifacts-oracle-c<cycle>-r<round>.md` |
| Repo graph brief | `.agents/workflow/briefs/plan-graph-oracle-c<cycle>-r<round>.md` |
| Finish brief | `.agents/workflow/finish/briefs/finish-<role>-<task>.md` |
| Finish report | `.agents/workflow/finish/reports/finish-<role>-<task>.md` |
| Lane live report | `.agents/workflow/<change>/reports/<lane>-report.md` |
| Runner report | `.agents/workflow/<change>/reports/<lane>-runner-<task>.md` |
| Review report | `.agents/workflow/<change>/reviews/<lane>-<role>-r<round>.md` |
| Finish review | `.agents/workflow/finish/reviews/finish-<role>-<head>.md` |
| Artifact-review ledger | `.agents/workflow/<change>/plan-reviews.json` |
| Graph-review ledger | `.agents/workflow/plan-graph-review.json` |
| Oracle artifact review | `.agents/workflow/<change>/reviews/artifacts-oracle-c<cycle>-r<round>.md` |
| Oracle repo graph review | `.agents/workflow/reviews/plan-graph-oracle-c<cycle>-r<round>.md` |
| Lane evidence | `.agents/workflow/<change>/evidence/<lane>/<revision>/` |
| Finish evidence | `.agents/workflow/finish/evidence/<revision>/` |
| PR follow-up and opening consent | `.agents/workflow/finish/pr-follow-up.md` |

The Coordinator writes the brief before dispatch and passes its absolute path, together with the
capability map's absolute path. The worker writes the report at the named path, updating it after every
commit and check. A later session must find every artifact by pattern, without asking the Coordinator.
Missing directory: create it, do not relocate it.
Every brief identifies the OpenSpec change, lane or finish scope, task or review round, worktree, base
or integrated HEAD, profile, contract, and deadline.
The plan and coordination entries use `changes.<change>.lanes.<lane>.tasks.<task>`.
Workers normally read the shared plan without editing it. Only a bounded Oracle planning correction may write it.
Only the Coordinator writes coordination. During Oracle graph authorship, the Coordinator pauses graph mutations.
Finish briefs replace lane identity with finish scope, change, integrated HEAD, and explicit owned paths.
Finish briefs use `.agents/workflow/finish/briefs/finish-<role>-<task>.md`; reports use
`.agents/workflow/finish/reports/finish-<role>-<task>.md`.
Preparation authors receive a separate worktree; read-only finish workers may use the feature checkout.

Model selection and role profiles are repo-level. Never place the file or a profile under a lane
worktree or `.agents/workflow/<change>/`, and never put a lane worktree or another per-dispatch path
in a profile. The absolute worktree belongs only in the brief and coordination.

Lane evidence binds a candidate lane revision: `.agents/workflow/<change>/evidence/<lane>/<revision>/`.
Finish evidence binds the integrated release revision: `.agents/workflow/finish/evidence/<revision>/`.
Never reuse candidate evidence as final release evidence. Planning report files live under the matching
`reviews/` directory (per-change artifact reviews under the change, graph reviews at repo level) and
include the cycle and round so a later cycle cannot overwrite them.

`plan-approve` records approval at `.agents/workflow/plan-approval.json`; `status` reports whether it is
current, stale, or missing. `lane-open` and `lane-merge` do not enforce it. The Coordinator verifies a
current approval for the approved plan before opening a lane and before merging one.

`FEATDEV_LANE_WORKTREE_ROOT` must be an absolute path. A relative value resolves against the
Coordinator's current working directory, so never rely on a relative setting.

PR-opening consent is recorded in `.agents/workflow/finish/pr-follow-up.md` with the human, timestamp,
exact integrated HEAD, and included changes. It is separate from release classification and later
PR-merge consent. A finish review is recorded at
`.agents/workflow/finish/reviews/finish-<role>-<head>.md` with the verdict, exact HEAD,
inputs/evidence revision, limitations, and reviewer identity.

Back up the entire `.agents/workflow/` tree before migration, cleanup, or any destructive repair. The
repo-level objects and per-change records are local and gitignored; Git cannot reconstruct them.

## Coordinator verification

The helper records and validates bindings, but the Coordinator must verify the following. A recorded
binding is not evidence that the check was done.

- Plan approval: verify a current `.agents/workflow/plan-approval.json` before opening a lane and before
  merge. `lane-open` and `lane-merge` do not reject a stale or missing record.
- Review rounds: `lane-review` refuses a round after the second unless `lane-mediate` covers it; there
  is no cap after mediation. Require exactly one further Oracle-authored pass, then a human decision on
  any remaining work.
- Override: record the exact waived duties in the reason and merge record; the helper stores skipped
  gates only as a generic reviews entry.
- Oracle independence: for an Oracle-authored fix or an Oracle stand-in, verify independence and record
  the actual reviewer or stand-in ID in the report or brief; there is no helper flag for it.
- Human deferral: `human-defer` stores the rationale plus fixed `bounded`/`reversible` values and a
  fixed `excluded_effects` list; it does not validate the rationale. Require the rationale to enumerate
  each excluded effect; the fixed fields are not evidence.
- Single writer: a new dispatch key bypasses the open same-role guard. It is never authorization for a
  second writer; ensure the prior worker is stopped or released before replacement.
- Merge evidence: for ordinary role reviews, merge revalidates only UI evidence paths and digests.
  Other role evidence and report hashes are the Coordinator's check.
- Close: `lane-close` accepts a lane whose ticks are complete. Verify a recorded merge before closing.
- Doc-defined records: plan approval, finish reviews, PR-opening consent, and PR follow-up are
  documentation-defined decisions; the helper records their bindings but not the judgment. `pr-check`
  does not validate finish reviews, PR-opening consent, or the PR follow-up record. The Coordinator
  verifies each record's current HEAD, inputs, and approval scope before relying on it.

## Output

Send outcome (`PASS`/`MORE WORK`/`fixed`/`disputed`), exact SHA, task/finding IDs,
decisions, check status, report/evidence paths/revision, uncertainty, harness receipts.
Reviewers and Runners return verdict/status, exact SHA, finding IDs or exit status, report path, and
evidence revision — never only a path. Keep the report authoritative and add no machine footer.
Full reports/logs stay in files. Missing token/cost telemetry = `unknown`, never inferred.

Material finding: stable ID | P0/P1/P2 | location | trigger | violated requirement/criterion |
minimal fix | observable closure check. Consolidate one root cause.
One-line handoffs allowed; reports retain evidence/limitations.

Optional handoff-compression tooling may shorten the handoff text only; it never replaces evidence,
paths, or exact SHAs.
Never change selected models, roles, rules, or evidence.
Read exact paths; return decisive output. Avoid duplicate reports/status dumps and unbounded logs.


Coordinator only: send filled template, never catalog. Apply this file's handoff-context rules.
Use exact task IDs, absolute paths, full SHAs, runnable commands; reference approved sections, never copy.
Missing inputs block; never invent evidence. Plain instructions, not review packages/approval records.

## Implementation

```text
Role: Implementer | Change: <change> | lane: <id> | task: <id>
Role source: <absolute path to the role file> | Profile: <id or prompt_sha256>
Contract: <absolute path to references/briefs-and-handoffs.md> | Deadline: <ISO-8601 timestamp or duration>
Capability map: <absolute path to capability-map.md> | sha256: <hash>
Worktree: <absolute path> | Base: <SHA> | HEAD: <SHA>
Sources: <role brief; repository rules; plan; relevant spec/design sections>
Scope: <owned files/seams; dependencies; explicit exclusions>
Acceptance: <criterion ID: observable result, for the assigned task>
Checks: <criterion ID: command; execution owner; required evidence>
Deliver: <report path; evidence directory/revision; required receipt fields>
```

Require completion of the assigned task, committed work and tick, and a clean tree. Specify behavior, not speculative machinery.

## Finish-scoped briefs

Finish scope replaces lane identity with `Scope: finish`, the included changes, the exact integrated HEAD,
and explicit owned paths. Use these templates for preparation authors, reviewers, and Runners.
Finish briefs use `.agents/workflow/finish/briefs/finish-<role>-<task>.md`; reports use
`.agents/workflow/finish/reports/finish-<role>-<task>.md`; reviews use
`.agents/workflow/finish/reviews/finish-<role>-<head>.md`; evidence uses
`.agents/workflow/finish/evidence/<revision>/`.

Preparation author:

```text
Role: Implementer (preparation author) | Included changes: <changes> | Scope: finish | Task: <task>
Role source: <absolute path to the role file> | Profile: <id or prompt_sha256>
Contract: <absolute path to references/briefs-and-handoffs.md> | Deadline: <ISO-8601 timestamp or duration>
Worktree: <absolute path> | Integrated HEAD: <SHA>
Owned paths: <explicit release-preparation paths>
Sources: <human release classification; repository release/version guides; approved artifacts>
Scope: <version/generated changes allowed by repository guides; explicit exclusions>
Acceptance: <preparation result required by repository guides>
Checks: <selected checks; execution owner>
Report: .agents/workflow/finish/reports/finish-implementer-<task>.md
Evidence: .agents/workflow/finish/evidence/<revision>/
Deliver: <report path; preparation receipt>
```

Finish reviewer:

```text
Role: <code-reviewer|test-reviewer|ui-reviewer> | Included changes: <changes> | Scope: finish | Task: <task>
Role source: <absolute path to the role file> | Profile: <id or prompt_sha256>
Contract: <absolute path to references/briefs-and-handoffs.md> | Deadline: <ISO-8601 timestamp or duration>
Worktree: <absolute path or feature checkout> | Integrated HEAD: <SHA>
Owned paths: read-only
Sources: <repository review rules; release inputs; evidence revision; inspection requirements>
Remit: <required review duties; explicit exclusions>
Evidence inputs: <paths; revision; inputs; independent checks to run>
Report: .agents/workflow/finish/reports/finish-<role>-<task>.md
Review: .agents/workflow/finish/reviews/finish-<role>-<head>.md
Evidence: .agents/workflow/finish/evidence/<revision>/
Deliver: <report path; verdict; exact HEAD; evidence revision; reviewer identity; limitations>
```

Finish Runner:

```text
Role: Runner | Included changes: <changes> | Scope: finish | Task: <task>
Role source: <absolute path to assets/runner.md> | Profile: <id or prompt_sha256>
Contract: <absolute path to references/briefs-and-handoffs.md> | Deadline: <ISO-8601 timestamp or duration>
Worktree: <absolute path> | Integrated HEAD: <SHA>
Owned paths: read-only
Command or poll target: <exact command / URL / device>
Completion condition: <what ends the run>
Cadence: <progress report interval>
Retry policy: <none, or bounded retries>
Report: .agents/workflow/finish/reports/finish-runner-<task>.md
Evidence: .agents/workflow/finish/evidence/<revision>/
Deliver: <report path; status or exit code; evidence revision; limitations>
```

A finish reviewer records verdict, exact HEAD, inputs/evidence revision, limitations, and reviewer
identity in its finish review. A finish Runner does not edit release artifacts or issue a verdict; its
report is evidence. Do not use task ticks or `lane-merge` for preparation-only work.

## Long-process Runner

```text
Role: Runner | Change: <change> | lane: <id> | task: <id>
Role source: <absolute path to assets/runner.md> | Profile: <id or prompt_sha256>
Contract: <absolute path to references/briefs-and-handoffs.md> | Deadline: <ISO-8601 timestamp or duration>
Capability map: <absolute path to capability-map.md> | sha256: <hash>
Worktree: <absolute path> | Base: <SHA> | HEAD: <SHA>
Command or poll target: <exact command / URL / device>
Completion condition: <what ends the run>
Cadence: <progress report interval>
Retry policy: <none, or bounded retries>
Deliver: <report path (reports/<lane>-runner-<task>.md); evidence/log directory/revision; exit code; limitations>
```

Runner executes exactly the supplied command or polling target. It does not implement, edit, or issue a
verdict. Its report is evidence input; it never satisfies a review duty.

## Review

```text
Role: <code-reviewer|test-reviewer|ui-reviewer> | Change: <change> | lane: <id> | mode: <initial/correction/impact> | round: <n>
Role source: <absolute path to the role file> | Profile: <id or prompt_sha256>
Contract: <absolute path to references/briefs-and-handoffs.md> | Deadline: <ISO-8601 timestamp or duration>
Capability map: <absolute path to capability-map.md> | sha256: <hash>
Worktree: <absolute path> | Base: <SHA> | HEAD: <SHA> | review range: <base SHA>..<HEAD SHA>
UI reviewer briefs: evidence-ingest only — never launch/build/capture; judge supplied screenshots/manifests.
Sources: <role brief; rules; plan; relevant spec/design; task definitions>
Remit: <assigned criterion IDs; risk/UI/sensitive flags; exclusions>
Required reviews: <roles selected by lane risk/UI flags; current-HEAD remit and checks>
Evidence: <paths; revision; check results/provenance; independent checks to run>
Prior findings: <none, or report path + IDs + closure checks>
Deliver: <report path; PASS or MORE WORK; exact HEAD; finding IDs; evidence revision; limitations>
```

The review role line is `code-reviewer`, `test-reviewer`, or `ui-reviewer`; never `oracle`. Ordinary
lanes dispatch `code-reviewer` for the code/test review; high-risk lanes dispatch separate
`code-reviewer` and `test-reviewer` reviews.

For an Oracle-authored fix or an Oracle stand-in, the Coordinator verifies independence and records the
actual reviewer or stand-in ID in the report or brief; no helper flag records it.

Correction/impact: include previous reviewed SHA, correction range, full lane range.
Preserve full-lane access; investigate relevant dependencies only.
Keep lane candidate evidence separate from integrated release evidence.

## Correction continuation

```text
Role: <implementer or authorized oracle> | Change: <change> | lane: <id> | task: <id> | round: <n>
Role source: <absolute path to the role file> | Profile: <id or prompt_sha256>
Contract: <absolute path to references/briefs-and-handoffs.md> | Deadline: <ISO-8601 timestamp or duration>
Worktree: <absolute path> | Base: <SHA> | previous reviewed SHA: <SHA> | current HEAD: <SHA>
Findings: <report paths; stable IDs; agreed closure checks>
Changed inputs: <only changed sources, evidence, or decisions>
Verify: <affected commands and evidence to refresh>
Deliver: <report path; new SHA; fixed/disputed per ID with evidence>
```

Wait for complete first-pass batch. Resolve disagreements before dependent edits.
After two review rounds, `lane-review` refuses a third round without `lane-mediate`; there is no cap
after mediation. Require exactly one further Oracle-authored pass, then send unresolved work to the
human. A new dispatch key never authorizes a second writer.

## Oracle consultation

```text
Operation: technical_resolution (read-only) | Change: <change> | lane: <id> | Task/round: <id or round> | HEAD: <SHA>
Role source: <absolute path to the role file> | Profile: <id or prompt_sha256>
Contract: <absolute path to references/briefs-and-handoffs.md> | Deadline: <ISO-8601 timestamp or duration>
Capability map: <absolute path to capability-map.md> | sha256: <hash>
Worktree: <absolute path> | Base: <SHA>
Decision needed: <one concrete question>
Sources: <relevant requirement, code, and finding paths>
Conflict or failure: <exact evidence; attempted approaches and results>
Deliver: <report path; decision and rationale; minimal correction; closure check>
```

OpenSpec artifact review: identify one change; supply proposal/design/spec/tasks, not the repo graph.
Repo graph review: supply the whole local graph and every included change's current artifact verdict and sources.
Run graph review only after every included change passes artifact review. Use separate fresh Oracle contexts and report paths.
Use the canonical artifact-review and repo graph brief paths from [Set locations](#set-locations-no-ad-hoc-paths).
Every `plan-review` command includes the verdict, `--head`, and `--report`.
A correction follow-up records `plan-dispatch --scope graph --role oracle --task plan --key <new-key> --report <new-report>`;
the artifact form uses `--scope change --change <change>` with the same fields.
Record the intent before launching the Oracle, and record the verdict only after reading the actual report.
Implementation review: supply the selected change/lane contract digest alongside commit and evidence bindings.
Consultation never authorizes edits or resets rounds.
Explicitly authorized Oracle fix: implementation template; record `dispatch --access write`.
For a standard Oracle-authored fix or an Oracle stand-in, verify independence and record the actual
reviewer/stand-in ID in the report or brief; no helper flag records it.

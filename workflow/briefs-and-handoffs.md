# Dispatch briefs and shared handoff contract

All roles read this contract. Approved sources and repository rules override briefs.

## Context

Use fresh scoped briefs from Coordinator's [briefs-and-handoffs.md](briefs-and-handoffs.md); never fork/copy Coordinator history.
Preserve role/tasks, criteria, dependencies, owned seams, exclusions, reviewer remit, sources,
worktree/base/`HEAD`, check owner, report/evidence paths/revision, receipt fields.

Reuse same-lane session only while coherent; send changed SHA/findings/files/checks/evidence.
Stale context: fresh session, same brief/report pointers.
Keep Coordinator informed of direct Reviewer/Implementer clarification through selected mechanism.
Clarification never authorizes review-time edits, expands scope, hides corrections, or replaces verdicts.
Resolve conflicting requirements before dependent edits.
Read-only Oracle consultation answers one uncertainty; never resets rounds.

Helper syntax lives in [helper-commands.md](helper-commands.md). Briefs select the relevant command and fill every placeholder.

## Set locations (no ad-hoc paths)

Every handoff artifact has one home, under the coordinator store:

| Artifact | Path |
|---|---|
| Capability map | `.agents/workflow/capability-map.md` |
| Model selection | `.agents/workflow/model-selection.json` |
| Local repository plan (gitignored) | `.agents/workflow/plan-graph.json` |
| Repository coordination | `.agents/workflow/coordination.json` |
| Filled brief | `.agents/workflow/<change>/briefs/<lane>-<role>-<task-or-round>.md` |
| Lane live report | `.agents/workflow/<change>/reports/<lane>-report.md` |
| Runner report | `.agents/workflow/<change>/reports/<lane>-runner-<task>.md` |
| Review report | `.agents/workflow/<change>/reviews/<lane>-<role>-r<round>.md` |
| Artifact-review ledger | `.agents/workflow/<change>/plan-reviews.json` |
| Graph-review ledger | `.agents/workflow/plan-graph-review.json` |
| Oracle artifact review | `.agents/workflow/<change>/reviews/artifacts-oracle-r<round>.md` |
| Oracle repo graph review | `.agents/workflow/reviews/plan-graph-oracle-r<round>.md` |
| Evidence and logs | `.agents/workflow/<change>/evidence/<lane>/<revision>/` |

The Coordinator writes the brief before dispatch and passes its absolute path, together with the
capability map's absolute path. The worker writes the report at the named path, updating it after every
commit and check. A later session must find every artifact by pattern, without asking the Coordinator.
Missing directory: create it, do not relocate it.
Every brief identifies the OpenSpec change, lane, and task or review round.
The plan and coordination entries use `changes.<change>.lanes.<lane>.tasks.<task>`.
Workers normally read the shared plan without editing it. Only a bounded Oracle planning correction may write it.
Only the Coordinator writes coordination. During Oracle graph authorship, the Coordinator pauses graph mutations.
Finish briefs replace lane/task identity with finish scope, change, integrated HEAD, and explicit owned paths.
Use predictable `briefs/finish-<role>-<task>.md` and `reports/finish-<role>-<task>.md` paths.
Preparation authors receive a separate worktree; read-only finish workers may use the feature checkout.

## Output

Send outcome (`PASS`/`MORE WORK`/`fixed`/`disputed`), exact SHA, task/finding IDs,
decisions, check status, report/evidence paths/revision, uncertainty, harness receipts.
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
Role: Implementer | lane: <id> | tasks: <ids>
Role source: <absolute path to the role file> | profile: <id or sha256>
Capability map: <absolute path to capability-map.md> | sha256: <hash>
Worktree: <absolute path> | base: <SHA> | starting HEAD: <SHA>
Sources: <role brief; repository rules; plan; relevant spec/design sections>
Scope: <owned files/seams; dependencies; explicit exclusions>
Acceptance: <criterion ID: observable result, for every assigned task>
Checks: <criterion ID: command; execution owner; required evidence>
Deliver: <report path; evidence directory/revision; required receipt fields>
```

Require all assigned tasks, committed work/ticks, clean tree. Specify behavior, not speculative machinery.

## Long-process Runner

```text
Role: Runner | lane: <id> | task: <id>
Role source: <absolute path to subagent-roles/runner.md> | profile: <id or sha256>
Capability map: <absolute path to capability-map.md> | sha256: <hash>
Worktree: <absolute path> | HEAD: <SHA>
Command or poll target: <exact command / URL / device>
Completion condition: <what ends the run>
Cadence: <progress report interval>
Retry policy: <none, or bounded retries>
Deliver: <reports/<lane>-runner-<task>.md; evidence/log directory; exit code; limitations>
```

Runner executes exactly the supplied command or polling target. It does not implement, edit, or issue a
verdict. Its report is evidence input; it never satisfies a review duty.

## Review

```text
Role: <code-reviewer/test-reviewer/ui-reviewer/oracle> | lane: <id> | mode: <initial/correction/impact>
Role source: <absolute path to the role file> | profile: <id or sha256>
Capability map: <absolute path to capability-map.md> | sha256: <hash>
UI reviewer briefs: evidence-ingest only — never launch/build/capture; judge supplied screenshots/manifests.
Worktree: <absolute path> | review range: <base SHA>..<HEAD SHA>
Sources: <role brief; rules; plan; relevant spec/design; task definitions>
Remit: <assigned criterion IDs; risk/UI/sensitive flags; exclusions>
Required reviews: <roles selected by lane risk/UI flags; current-HEAD remit and checks>
Evidence: <paths; revision; check results/provenance; independent checks to run>
Prior findings: <none, or report path + IDs + closure checks>
Deliver: <report path; PASS or MORE WORK; limitations>
```

The review role ID is `code-reviewer`, `test-reviewer`, or `ui-reviewer`. Ordinary lanes dispatch `code-reviewer` for the combined code/test
review; high-risk lanes dispatch separate `code-reviewer` and `test-reviewer` reviews.
Combined Oracle review is recorded with `role=code-reviewer`.
Correction/impact: include previous reviewed SHA, correction range, full lane range.
Preserve full-lane access; investigate relevant dependencies only.
Combined Oracle: whole-change target base, feature/lane base, code/test/complete-change implementation duties, provenance, candidate acceptance
and evidence files; dispatch with both paths so their canonical paths and hashes are snapshotted before launch.
These are lane candidate inputs, not final release approval. Keep integrated release evidence in separate files.

## Correction continuation

```text
Lane: <id> | previous reviewed SHA: <SHA> | current HEAD: <SHA>
Role source: <absolute path to the role file> | profile: <id or sha256>
Capability map: <absolute path to capability-map.md> | sha256: <hash>
Findings: <report paths; stable IDs; agreed closure checks>
Changed inputs: <only changed sources, evidence, or decisions>
Verify: <affected commands and evidence to refresh>
Deliver: <new SHA; fixed/disputed per ID with evidence; report path>
```

Wait for complete first-pass batch. Resolve disagreements before dependent edits.

## Oracle consultation

```text
Operation: technical_resolution (read-only) | lane: <id> | HEAD: <SHA>
Role source: <absolute path to the role file> | profile: <id or sha256>
Capability map: <absolute path to capability-map.md> | sha256: <hash>
Decision needed: <one concrete question>
Sources: <relevant requirement, code, and finding paths>
Conflict or failure: <exact evidence; attempted approaches and results>
Deliver: <decision and rationale; minimal correction; closure check; report path>
```

OpenSpec artifact review: identify one change; supply proposal/design/spec/tasks, not the repo graph.
Repo graph review: supply the whole local graph and every included change's current artifact verdict and sources.
Run graph review only after every included change passes artifact review. Use separate fresh Oracle contexts and report paths.
Implementation review: supply the selected change/lane contract digest alongside commit and evidence bindings.
Consultation never authorizes edits or resets rounds.
Explicitly authorized Oracle fix: implementation template; record `dispatch --access write`.

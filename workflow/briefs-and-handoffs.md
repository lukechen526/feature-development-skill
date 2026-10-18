# Dispatch briefs and shared handoff contract

All roles read this contract. Approved sources and repository rules override briefs.

## Context

Use fresh scoped briefs from Coordinator's `briefs-and-handoffs.md`; never fork/copy Coordinator history.
Preserve role/tasks, criteria, dependencies, owned seams, exclusions, reviewer remit, sources,
worktree/base/`HEAD`, check owner, report/evidence paths/revision, receipt fields.

Reuse same-lane session only while coherent; send changed SHA/findings/files/checks/evidence.
Stale context: fresh session, same brief/report pointers.
Keep Coordinator informed of direct Reviewer/Implementer clarification through selected mechanism.
Clarification never authorizes review-time edits, expands scope, hides corrections, or replaces verdicts.
Resolve conflicting requirements before dependent edits.
Read-only Oracle consultation answers one uncertainty; never resets rounds.

## Set locations (no ad-hoc paths)

Every handoff artifact has one home, under the coordinator store:

| Artifact | Path |
|---|---|
| Filled brief | `.agents/workflow/<change>/briefs/<lane>-<role>-<task-or-round>.md` |
| Lane live report | `.agents/workflow/<change>/reports/<lane>-report.md` |
| Review report | `.agents/workflow/<change>/reviews/<lane>-<role>-r<round>.md` |
| Oracle plan review | `.agents/workflow/<change>/reviews/plan-<role>-r<round>.md` |
| Evidence and logs | `.agents/workflow/<change>/evidence/<lane>/<revision>/` |

The Coordinator writes the brief before dispatch and passes its absolute path. The worker writes the
report at the named path, updating it after every commit and check. A later session must find every
artifact by pattern, without asking the Coordinator. Missing directory: create it, do not relocate it.

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
Worktree: <absolute path> | base: <SHA> | starting HEAD: <SHA>
Sources: <role brief; repository rules; plan; relevant spec/design sections>
Scope: <owned files/seams; dependencies; explicit exclusions>
Acceptance: <criterion ID: observable result, for every assigned task>
Checks: <criterion ID: command; execution owner; required evidence>
Deliver: <report path; evidence directory/revision; required receipt fields>
```

Require all assigned tasks, committed work/ticks, clean tree. Specify behavior, not speculative machinery.

## Review

```text
Role: <code/test/UI/Oracle> | lane: <id> | mode: <initial/correction/impact>
Role source: <absolute path to the role file> | profile: <id or sha256>
UI reviewer briefs: evidence-ingest only — never launch/build/capture; judge supplied screenshots/manifests.
Worktree: <absolute path> | review range: <base SHA>..<HEAD SHA>
Sources: <role brief; rules; plan; relevant spec/design; task definitions>
Remit: <assigned criterion IDs; risk/UI/screen flags; exclusions>
Selected duties: <recorded review-decision modes for code/test/ui, plus any ancestor-reuse
assessment the reviewer must verify; omit only when the standard gate applies>
Evidence: <paths; revision; check results/provenance; independent checks to run>
Prior findings: <none, or report path + IDs + closure checks>
Deliver: <report path; PASS or MORE WORK; limitations>
```

Correction/impact: include previous reviewed SHA, correction range, full lane range.
Preserve full-lane access; investigate relevant dependencies only.
Combined Oracle: whole-change target base, feature/lane base, code/test/complete-change implementation duties, provenance, candidate acceptance
and evidence files; dispatch with both paths so their canonical paths and hashes are snapshotted before launch.
These are lane candidate inputs, not final release approval. Keep integrated release evidence in separate files.

## Correction continuation

```text
Lane: <id> | previous reviewed SHA: <SHA> | current HEAD: <SHA>
Role source: <absolute path to the role file> | profile: <id or sha256>
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
Decision needed: <one concrete question>
Sources: <relevant requirement, code, and finding paths>
Conflict or failure: <exact evidence; attempted approaches and results>
Deliver: <decision and rationale; minimal correction; closure check; report path>
```

Plan review: review template + proposal/design/spec/tasks/graph.
Consultation never authorizes edits or resets rounds.
Explicitly authorized Oracle fix: implementation template; record `dispatch --access write`.

# Plan phase

The Coordinator owns planning. Planning starts from the human's request, not from an existing OpenSpec change: the Coordinator turns that intent into reviewed OpenSpec artifacts, then into the lane graph. No implementation begins before both Oracle review stages and human approval or an applicable advance waiver.

## Objects and ownership

- Active OpenSpec artifacts live under `openspec/changes/<change>/`: proposal, design, delta specs, and `tasks.md`.
- Archived changes retain their identity in the local graph. Approved corrective work may amend their archived artifacts.
- The repo-level graph is `.agents/workflow/plan-graph.json`.
- The repo-level runtime store is `.agents/workflow/coordination.json`.
- The plan-approval record is `.agents/workflow/plan-approval.json`.
- These repo-level objects are local and gitignored. None is a committed artifact.
- The graph and coordination file group `changes → lanes → tasks`; the Coordinator preserves unrelated entries during updates.
- Only the Coordinator writes coordination. Oracle may temporarily own bounded graph corrections under the exception below.

## Required order

1. Receive the human's feature intent and clarify scope, behavior, and non-goals.
2. Choose the included changes: reuse a matching existing change or create one with `openspec-propose`.
3. Author each included change's OpenSpec artifacts from that intent.
4. Obtain an independent Oracle artifact verdict for each change.
5. Complete every included change's artifact corrections before graph review.
6. Build or update the repo-level graph from the reviewed change artifacts.
7. Obtain a separate independent Oracle verdict for the whole repo-level graph.
8. Obtain human approval of the artifacts and graph, or verify an applicable advance waiver.
9. Commit only the reviewed OpenSpec artifacts.
10. Record the human approval with `plan-approve`, or the scoped waiver with `plan-waive`.
11. Start implementation from that committed and recorded state.

OpenSpec edits remain uncommitted during both review stages. No intermediate artifact commit occurs between Oracle correction cycles.
The graph, coordination file, and approval record remain gitignored throughout; approval never changes their storage policy.
The approval record is not a Git object; it binds the committed artifact digests and the reviewed graph digest.

## From user intent to change artifacts

The Plan phase assumes no OpenSpec change exists. The Coordinator reads the human's request, resolves material ambiguity with the human, and writes the artifacts that define the feature.

### Intake and clarification

Restate the intended behavior, affected surfaces, and non-goals, and confirm them with the human before authoring.
Resolve scope boundaries, user-visible behavior, compatibility and data constraints, and what would make the change unacceptable.
Ask when a requirement is missing, conflicting, or untestable; never substitute a plausible guess.
Return the request to the human when it is too small to need this workflow or is really several unrelated features.

### Choose the included changes

Run `openspec list --json`. Reuse a matching change rather than duplicating it; use `openspec-propose` when none matches.
For a reused change, read its current artifacts and resolve gaps before review.
Use several changes only when each delivers independently reviewable behavior; otherwise keep one.
Every later review, lane, and finish record covers exactly the included changes.

### Author the artifacts

For each included change, author under `openspec/changes/<change>/`:

- `proposal.md`: why the change is needed and what it changes.
- `design.md`: the chosen approach, alternatives, and trade-offs implementation must respect.
- delta specs: the requirements the change adds, modifies, or removes, each with observable acceptance criteria or scenarios, expressed against the applicable baseline specifications.
- `tasks.md`: unique top-level checklist IDs that implement the specifications; nested checkboxes annotate their parent task.

Acceptance criteria must be observable and testable by an independent reviewer, and tasks must map back to them so review coverage is checkable.
The artifacts are the implementation contract: do not start implementation or build the lane graph before both Oracle stages and human approval or an applicable advance waiver.

### Oracle stage 1: each change's OpenSpec artifacts

Dispatch a fresh Oracle with operation `openspec_review` for each change.
Its remit is requirements, proposal/design/spec consistency, scope, task completeness, acceptance criteria, and testability.
This review does not include the repo-level graph or any legacy per-change graph.
It can run before `plan-graph.json` exists.

Record intent before the actual launch, and record the verdict only after reading the actual report: see the [helper command reference](helper-commands.md#change-artifact-review).

Each change has its own ledger: `.agents/workflow/<change>/plan-reviews.json`.
The helper binds the verdict to each reviewed artifact file and its digest, and to the review HEAD, never to the graph.
A `PASS` covers the files it reviewed. Pass `--file` for each changed file, or omit it to cover every file in the change.
An edit drops that file's coverage and leaves the other files covered, so the next review covers the changed file and its related requirements only.
A `MORE WORK` verdict drops coverage for the files it names; a later `PASS` restores the files it covers.
Name a deleted artifact with `--file` to review the removal; the record lists it in `removed`.
An open correction cycle blocks graph review, plan approval, and lane opening until a `PASS` covers every file that cycle named.
A reviewed edit must be committed before those gates open again: lane worktrees read the feature HEAD, not the working tree.
Task completion marks alone do not change any artifact digest.
An earlier graph-only verdict never satisfies this stage.
Every artifact file needs a current `PASS` before Oracle graph review and before plan approval.
A ledger from before per-file review still counts while its recorded fold digest matches the files on disk; otherwise one full review restores coverage.

## Author the repo-level graph

The graph translates reviewed artifacts into cohesive lanes, ownership, dependencies, risk flags, and task assignments.
Split lanes for independent work, ownership conflicts, risk boundaries, or unreviewably broad diffs—not parallelism alone.
Each change contains one or more lanes. Each lane contains one or more tasks.

```json
{
  "schema_version": 3,
  "changes": {
    "example-change": {
      "lanes": {
        "store": {
          "risk": "high", "ui": false, "sensitive": false, "depends_on": [],
          "tasks": {"1.1": {"deps": []}, "1.2": {"deps": ["1.1"]}}
        },
        "ui": {
          "risk": "ordinary", "ui": true, "sensitive": false, "depends_on": ["store"],
          "tasks": {"2.1": {"deps": ["1.2"]}}
        }
      }
    }
  }
}
```

| Field | Required | Rule |
|---|---|---|
| `schema_version` | yes | `3` |
| `changes` | yes | non-empty object keyed by OpenSpec change directory name |
| `changes.<change>.lanes` | yes | non-empty lane-id object |
| `lanes.<lane>.risk` | yes | `ordinary` or `high` |
| `lanes.<lane>.ui` | yes | boolean; user-facing appearance/interaction, including rendered evidence and human review |
| `lanes.<lane>.sensitive` | yes | boolean; content correctness, safety, privacy, other sensitive content |
| `lanes.<lane>.depends_on` | no | lane IDs in the same change; default none |
| `lanes.<lane>.tasks` | yes | non-empty task-id object; nesting defines ownership |
| `tasks.<task>.deps` | yes | task IDs in the same change; empty allowed |

Lane fields are relative to `changes.<change>`.
Task IDs are unique across lanes within a change and match that change's top-level `tasks.md` IDs exactly.
Different changes can reuse lane IDs and task IDs. Dependencies do not cross change boundaries.
Missing gate flags and empty change/lane/task collections are refused.
`schema_version: 2` is retired: convert each lane with `ui = ui or screen`, remove `screen`, and set `schema_version: 3`.
Roughly 2–4 tasks per lane is guidance, not a validation rule.

Declared flags drive gates:
- `risk: high` requires separate `code-reviewer` and `test-reviewer` verdicts; ordinary lanes combine those duties.
- `ui: true` requires `ui-reviewer` inspection of supplied rendered evidence, a human checkpoint, and current evidence for both.
- `sensitive: true` requires a human checkpoint, subject to the documented deferral rules.

### Structural validation

Validation checks nested shape, task-ID bijection, references, and dependency cycles.
Lane cycle checks include edges implied by cross-lane task dependencies; otherwise an acyclic task graph can deadlock integration.
Oracle judgment—not structural validation—assesses boundaries, ownership, risk, and coverage.
Run it with the [helper command reference](helper-commands.md#plan-validation-and-rendering).

### Oracle stage 2: the repo-level graph

Start only after all included changes pass stage 1.
Dispatch a separate fresh Oracle with operation `plan_graph_review`.
Its remit is whole-repository planning: change-to-lane/task mapping, cohesive ownership, overlaps between changes,
dependencies, risk/UI flags, review duties, and complete coverage of the reviewed artifacts.
It uses the reviewed OpenSpec artifacts as inputs; it does not silently revise their approved requirements.

Graph review has no `--change` selector: see the [helper command reference](helper-commands.md#repo-graph-review).
The repo-level ledger is `.agents/workflow/plan-graph-review.json`, separate from every change's artifact ledger.
The helper requires current artifact coverage for every graph entry at dispatch and verdict recording.
The graph verdict binds `.agents/workflow/plan-graph.json` alone.
An artifact edit does not invalidate the graph review; the per-file artifact review covers it.
A lane, task, ownership, dependency, `risk`, `ui`, or `sensitive` edit changes the graph:
obtain a fresh graph review and human approval before renewed implementation.

## Corrections and approval

Each change's artifact review and the repo graph review have separate two-round correction cycles.
Later planning work after a `PASS` starts a new cycle. Preserve every prior review in the ledger.
Each Oracle planning dispatch has a canonical brief and report pair: artifact review at
`.agents/workflow/<change>/briefs/artifacts-oracle-c<cycle>-r<round>.md` and
`.agents/workflow/<change>/reviews/artifacts-oracle-c<cycle>-r<round>.md`; repo graph review at
`.agents/workflow/briefs/plan-graph-oracle-c<cycle>-r<round>.md` and
`.agents/workflow/reviews/plan-graph-oracle-c<cycle>-r<round>.md`.
The `c<cycle>` and `r<round>` components prevent overwriting an earlier report when later planning work starts a new cycle or a correction review starts a new round.
Edits after `MORE WORK` remain in the same cycle; changing a commit or digest never resets that cycle.
A `PASS` that covers only some of the files an open `MORE WORK` named does not close the cycle or restart its round count.
A third review within that cycle requires `--mediation <report>` or escalation to the human.
After a second `MORE WORK`, authorize bounded Oracle authorship or escalate to the human.

For Oracle authorship, record `plan-dispatch --access write` before the handoff.
The authorization identifies the exact graph or OpenSpec artifact paths and the correction report.
Only this bounded planning exception permits an Oracle to write the local graph directly.
Pause Coordinator graph writes and other graph authors until the Oracle stops or releases ownership.
The Oracle never writes coordination or approval ledgers. The Coordinator records the outcome.
Read the completed report before recording a fresh read-only follow-up with a new key and a new report; it uses `--task plan` (or omits `--task`), never the author's correction task. See [planning correction commands](helper-commands.md#oracle-planning-corrections).

That intent closes the author record; a different Oracle performs the independent review with `plan-review`.
The author cannot approve its own correction.
A stale intent requires a fresh unique `--key`; never reuse a dispatch ID for different inputs.

After both stages pass, obtain explicit human approval of the artifacts and local graph, unless an applicable [advance waiver](lane.md#advance-human-waiver) covers plan approval.
Commit the OpenSpec artifacts first, then record the approval with `plan-approve` or the waiver with `plan-waive` ([approval command](helper-commands.md#plan-approval)).
Committing a contract-changing artifact edit after its review does not satisfy the artifact requirement:
the changed digest invalidates the earlier PASS and requires a fresh artifact review.
Never stage `plan-graph.json`, `coordination.json`, or their review/approval records; the helper records the decision but never grants human approval or commits these files.

Plan-approval state and its enforcement are defined once in [lane records](lane.md#records-and-progress).

A plan amendment is a file review, not a planning cycle. Review the changed artifacts with `--file`; coverage then restores and the recorded approval is current again while `plan-graph.json` does not change.
A change to lanes, task ownership, dependencies, `risk`, `ui`, or `sensitive` edits the graph. Obtain a fresh graph review and human approval recorded with `plan-approve`, or an applicable waiver recorded with `plan-waive`, before renewed implementation.
Lane review and human-gate bindings include the lane contract digest; material lane edits require fresh verdicts.
Unrelated changes or lanes do not invalidate an unchanged ordinary lane's review binding.
Plan-bound grants retain their exact selected-change bindings.
Back up the entire `.agents/workflow/` tree; Git cannot reconstruct these local objects.

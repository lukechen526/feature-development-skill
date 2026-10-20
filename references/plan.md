# Plan phase

The Coordinator owns planning. No implementation begins before both Oracle review stages and explicit human approval.

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

1. Discover the included OpenSpec changes.
2. Author each change's OpenSpec artifacts.
3. Obtain an independent Oracle artifact verdict for each change.
4. Complete every included change's artifact corrections before graph review.
5. Build or update the repo-level graph from the reviewed change artifacts.
6. Obtain a separate independent Oracle verdict for the whole repo-level graph.
7. Obtain the human's explicit approval of the artifacts and graph.
8. Commit only the approved OpenSpec artifacts.
9. Record the human approval with `plan-approve`.
10. Start implementation from that committed and recorded state.

OpenSpec edits remain uncommitted during both review stages. No intermediate artifact commit occurs between Oracle correction cycles.
The graph, coordination file, and approval record remain gitignored throughout; approval never changes their storage policy.
The approval record is not a Git object; it binds the committed artifact digests and the reviewed graph digest.

## Discover and author change artifacts

Run `openspec list --json`. Reuse relevant changes or use `openspec-propose` for a new change.
Read each change's proposal, design, delta specs, tasks, and applicable baseline requirements.
Resolve material ambiguity with the human.
Only unique top-level checklist IDs identify tasks. Nested checkboxes annotate their parent tasks.

### Oracle stage 1: each change's OpenSpec artifacts

Dispatch a fresh Oracle with operation `openspec_review` for each change.
Its remit is requirements, proposal/design/spec consistency, scope, task completeness, acceptance criteria, and testability.
This review does not include the repo-level graph or any legacy per-change graph.
It can run before `plan-graph.json` exists.

Record intent before the actual launch, and record the verdict only after reading the actual report: see the [helper command reference](helper-commands.md#change-artifact-review).

Each change has its own ledger: `.agents/workflow/<change>/plan-reviews.json`.
The helper binds the verdict to the OpenSpec artifact digest and review HEAD, not to the graph.
Artifact edits invalidate the corresponding verdict. Task completion marks alone do not change the artifact contract digest.
An earlier graph-only verdict never satisfies this stage.
Every included change needs a current `PASS` before Oracle graph review starts.

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
The helper requires current artifact passes for every graph entry at dispatch and verdict recording.
The graph verdict binds the whole graph and the reviewed artifact digests.
Changing either input invalidates the graph review. Changed artifact contracts return to stage 1 first.

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

After both stages pass, obtain explicit human approval of the artifacts and local graph.
Commit the OpenSpec artifacts first, then record the approval with `plan-approve` ([approval command](helper-commands.md#plan-approval)).
Committing a contract-changing artifact edit after its review does not satisfy the artifact requirement:
the changed digest invalidates the earlier PASS and requires a fresh artifact review.
Never stage `plan-graph.json`, `coordination.json`, or their review/approval records; the helper records the decision but never grants human approval or commits these files.

Plan-approval state and its enforcement are defined once in [lane records](lane.md#records-and-progress).

Plan amendments require affected artifact review, then repo graph review, then human approval recorded with `plan-approve` before renewed implementation.
Lane review and human-gate bindings include the lane contract digest; material lane edits require fresh verdicts.
Unrelated changes or lanes do not invalidate an unchanged ordinary lane's review binding.
Plan-bound grants retain their exact selected-change bindings.
Back up the entire `.agents/workflow/` tree; Git cannot reconstruct these local objects.

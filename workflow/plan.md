# Plan phase

Coordinator alone authors plan and commits approval checkpoint; no implementation worker before approval.
Plan is graph; `status` derives readiness from committed plan/ticks, never a second scheduler.

## Commit timing

Planning artifacts (`plan/graph-plan.json`, delta specs, design.md, tasks.md edits) are UNCOMMITTED while under review. Commit order is strictly:

1. Author artifacts; validate locally.
2. Dispatch Oracle `plan_review` against the working tree.
3. Obtain explicit human approval of the artifacts/plan.
4. ONLY THEN commit: first the artifacts, then the approval checkpoint naming the approver.

Never commit artifacts before the Oracle verdict, and never commit an approval checkpoint before human approval. If Oracle returns `MORE WORK`, revise the working tree and repeat from step 2 — nothing is committed between cycles.

## Discover

Run `openspec list --json`; reuse relevant active change or use `openspec-propose`.
Read matching baseline and change proposal/delta/design/tasks. Change directory name is sole identifier; no run id.
Resolve material ambiguity with human.

Keep normal OpenSpec checklist. Only unique top-level checklist IDs identify tasks;
nested bullets annotate parent, never become plan tasks.

Group shared behavior/files/checks under one writer/review.
Split only independent work, ownership conflicts, risk boundaries, or unreviewably broad diffs—not parallelism alone.
Prepare lane criteria, sources, dependencies, exclusions, check owners, reviewer remits in briefs/committed tasks;
never copy Coordinator history.

## Author `plan/graph-plan.json`

Committed contract only; no run-scoped paths/evidence.

```json
{
  "schema_version": 1,
  "change": "meal-logging",
  "lanes": {
    "store": {
      "risk": "high",
      "ui": false,
      "screen": false,
      "sensitive": false,
      "depends_on": []
    },
    "screen": {
      "risk": "ordinary",
      "ui": true,
      "screen": true,
      "sensitive": false,
      "depends_on": ["store"]
    }
  },
  "tasks": {
    "1.1": { "lane": "store", "deps": [] },
    "1.2": { "lane": "store", "deps": ["1.1"] },
    "2.1": { "lane": "screen", "deps": ["1.2"] }
  }
}
```

| Field | Required | Rule |
|---|---|---|
| `change` | yes | change directory name, parent of `plan/` |
| `review_mode` | no | default `standard`; eligible `combined-single-lane` |
| `schema_version` | no | accepted, ignored |
| `lanes` | yes | lane-id object |
| `lanes.<id>.risk` | yes | `ordinary` or `high` |
| `lanes.<id>.ui` | yes | boolean; user-facing appearance/interaction |
| `lanes.<id>.screen` | yes | boolean; rendered-screen evidence needed |
| `lanes.<id>.sensitive` | yes | boolean; user-facing wording/content correctness, safety, privacy, medical sensitivity |
| `lanes.<id>.depends_on` | no | lane IDs; default none |
| `tasks` | yes | task-id object |
| `tasks.<id>.lane` | yes | owning lane ID |
| `tasks.<id>.deps` | yes | task IDs; empty allowed |

Missing flags refused, never silently false. Empty lanes legal; extra keys ignored.
Roughly 2–4 tasks/lane is reviewability guidance, not validation rule.

Declared flags drive gates, never inferred from diff:
- `risk: high`: separate code/test reviews; ordinary combines them.
- `ui: true`: rendered-UI review of supplied screenshots/manifests (UI reviewer ingests evidence only; never launches a simulator).
- `screen: true`: current changed-screen evidence before merge.
- Any `ui`/`screen`/`sensitive`: per-lane human checkpoint.

`combined-single-lane`: exactly one ordinary-risk lane.
Independent Oracle covers lane code/test plus complete-change final duty.
UI/human gates, acceptance evidence, independence, risk classification remain.
Unknown/ineligible mode fails validation. Equal trees never replace exact reviewed commit.

## Validate

```bash
python3 .agents/skills/feature-development-skill/bin/featdev.py \
  plan-validate openspec/changes/<change>/plan/graph-plan.json --tasks openspec/changes/<change>/tasks.md
python3 .agents/skills/feature-development-skill/bin/featdev.py \
  plan-graph openspec/changes/<change>/plan/graph-plan.json --format md
```

Checks: shape/change-name; exact top-level task-ID bijection; all task/lane references resolve;
task/lane graphs acyclic. Failure returns actionable line.
Structure only: Oracle/human assess lane boundaries, risk, assignments.

## Oracle review

Dispatch `plan_review` using fresh review brief: OpenSpec/graph, exact base/`HEAD`, boundaries,
criteria, prior findings. Return `PASS`/`MORE WORK`; the Oracle writes the full report to
`.agents/workflow/<change>/reviews/plan-<role>-r<round>.md` and returns that path. A verdict that exists
only in chat is not recorded.
Maximum two Coordinator-driven reviews. Second `MORE WORK`: Oracle authors revision or escalate to human;
never third Coordinator-driven cycle.

## Approval

Run strict validation against the (still-uncommitted) artifacts; obtain explicit human approval of artifacts/plan. Then commit in the order defined in **Commit timing** above: artifacts first, then the approval checkpoint commit naming approver: `approved by human`, or `approved by Oracle` under current stand-in authorization. Commit is approval record; no approval file/digest/plan-hash lock.

Plan hash/revision is staleness stamp, not lock.
Changed committed plan requires fresh re-approval commit; derivation then updates stamp/rebuilds view, never refuses merely changed hash.

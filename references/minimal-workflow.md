# Worked example: a reviewed greeting feature

This walkthrough follows a feature from an OpenSpec requirement through implementation, independent review, and PR preparation.
It shows what the Coordinator records and where real agents and the human must act.
For realistic feature requests and when to use the skill, start with the [README](../README.md#when-to-use-it).

The feature is deliberately tiny so the workflow stays readable.
A real one-function change would rarely justify this much coordination.
Normally, use the skill for features with several tasks, dependencies, or explicit review and approval requirements.

## Request and acceptance criteria

Example request:

> Use feature-development-skill to implement the hello-message change.
> Provide message.greeting(), returning exactly hello.
> Use the project's focused check and release rules.
> Review the specification and plan before asking me to approve implementation.

| Criterion | How it is checked |
|---|---|
| `message.greeting()` returns exactly `hello` | A Python assertion runs in the lane and again on the integrated feature branch. |
| Implementation matches the approved task | A separate code reviewer checks the current lane commit and test adequacy. |
| The integrated output is acceptable | The human inspects the output under this fixture's release rules. |

The Coordinator obtains two separate planning verdicts: one for OpenSpec artifacts and one for the lane graph.
The human then approves the plan. Only after that does an Implementer write code in an isolated worktree.
The greeting lane has its own branch and working directory, separate from the Coordinator's feature checkout.
The Implementer runs checks and commits there. The Coordinator integrates that commit only after independent review passes.
The greeting lane is ordinary risk with no UI or sensitive flag, so it requires one combined code/test review.
The fixture's final human output inspection is a release rule, not an automatic requirement for every CLI project.

## Before running commands

This recipe uses one lane and one task in a disposable consuming repository.
It is not a live harness validation result.
Commands record real events; they do not launch agents, perform reviews, or grant consent.
Never use sample text as a review report or human approval.
Do not paste the entire walkthrough as an unattended script: stop at every agent handoff and human decision.

Read [the skill](../SKILL.md) and [the handoff contract](briefs-and-handoffs.md) first.
Stop after any failed command or unmet gate.
This walkthrough instantiates the [canonical helper commands](helper-commands.md).

## 1. Prepare the consuming repository

Use a clean feature checkout with a committed baseline and a configured Git identity.
Use the repository's OpenSpec process to create an uncommitted change named `hello-message`.
Its proposal, design, and delta specs must require a `message.greeting()` function that returns `hello`.
Its testing guide must select the Python assertion below as the fixture's release check.
For this fixture, its release guide requires no version or generated-file changes.
The release guide also requires the human to inspect the integrated command output.

```bash
export FEATDEV_SKILL_ROOT="/absolute/path/to/feature-development-skill"
FEATURE_CHECKOUT="$(pwd -P)"
CHANGE="hello-message"
STORE="$FEATURE_CHECKOUT/.agents/workflow/$CHANGE"
PLAN=".agents/workflow/plan-graph.json"
TASKS="openspec/changes/$CHANGE/tasks.md"
openspec --version
openspec list --json
mkdir -p "$STORE/briefs" "$STORE/reviews" "$STORE/reports" "$STORE/evidence"
```

Complete [preflight and role setup](preflight-and-roles.md) before any launch.
Write the repo-level capability map and human-approved model selection.
The Coordinator reads the selected role profiles for each actual harness launch.
Helper dispatch records take the role and task, not separate model or thinking overrides.
Do not substitute defaults or create a per-change selection file.

## 2. Review change artifacts, then the repo graph

### Review the OpenSpec change

The proposal, design, and delta specs already exist from the consuming repository's OpenSpec process.
The Coordinator writes the task checklist. No graph is required for this review.

```bash
printf '%s\n' '- [ ] 1.1 Implement message.greeting() returning hello.' > "$TASKS"
PLAN_HEAD="$(git rev-parse HEAD)"
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" plan-dispatch   --scope change --role oracle     --report "$STORE/reviews/artifacts-oracle-r1.md"
```

Write `$STORE/briefs/artifacts-oracle-r1.md` using operation `openspec_review`.
Name only this change's proposal, design, delta specs, tasks, review HEAD, role source, profile, and capability map.
Exclude the repo graph. Launch a fresh read-only Oracle through the preflighted harness.
Read its actual report. Only after a real `PASS`, record:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" plan-review   --scope change --role oracle PASS --head "$PLAN_HEAD" --report "$STORE/reviews/artifacts-oracle-r1.md"
```

For multiple included changes, repeat artifact review for each before proceeding.
For `MORE WORK`, use the bounded correction procedure in [the plan phase](plan.md).

### Author and review the repo graph

This fixture starts without a graph. Do not overwrite an existing `.agents/workflow/plan-graph.json`.
For an existing graph, add `changes.hello-message` while preserving every other change.
The graph is local and ignored, just like coordination.

```bash
mkdir -p "$(dirname "$PLAN")"
cat > "$PLAN" <<'JSON'
{
  "schema_version": 3,
  "changes": {
    "hello-message": {
      "lanes": {
        "message": {
          "risk": "ordinary",
          "ui": false,
          "sensitive": false,
          "depends_on": [],
          "tasks": {
            "1.1": {
              "deps": []
            }
          }
        }
      }
    }
  }
}
JSON
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-validate "$PLAN"
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-graph "$PLAN" --format md
mkdir -p "$FEATURE_CHECKOUT/.agents/workflow/reviews" "$FEATURE_CHECKOUT/.agents/workflow/briefs"
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-dispatch   --scope graph --role oracle     --report "$FEATURE_CHECKOUT/.agents/workflow/reviews/plan-graph-oracle-r1.md"
```

Write `.agents/workflow/briefs/plan-graph-oracle-r1.md` using operation `plan_graph_review`.
Supply the whole repo graph, every included change's artifact verdict, and the reviewed source artifacts.
Launch a separate fresh Oracle. Read the actual report. Only after a real graph `PASS`, record:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-review   --scope graph --role oracle PASS --head "$PLAN_HEAD"   --report "$FEATURE_CHECKOUT/.agents/workflow/reviews/plan-graph-oracle-r1.md"
```

The artifact ledger is `$STORE/plan-reviews.json`.
The graph ledger is `.agents/workflow/plan-graph-review.json`.
Neither verdict substitutes for the other or grants human approval.

CAUTION: The following commands record approval; they do not obtain it.
Obtain explicit human approval of the OpenSpec artifacts and local graph before running them.
Commit the artifacts first, then record the approval in the coordinator store.
Never stage a repo-level runtime object.

```bash
git add "openspec/changes/$CHANGE"
git commit -m "docs: add approved greeting artifacts"
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-approve --approver human
```

`plan-approve` writes `.agents/workflow/plan-approval.json` with the approver, current HEAD,
graph digest, and per-change artifact digests. It refuses uncommitted artifact changes or a
missing/stale graph `PASS`.

## 3. Dispatch the implementer

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" lane-open message
WORKTREE="$(python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" status --json | \
  python3 -c 'import json, sys; print(json.load(sys.stdin)["lanes"]["message"]["worktree"])')"
IMPLEMENTER_KEY="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
```

Write `$STORE/briefs/message-implementer-1.1.md` using the implementation template.
Fill every path and command with its resolved value.
Include task `1.1`, the criterion, focused check, lane worktree, base, `HEAD`, role source, profile, and capability map.
Require the live report at `$STORE/reports/message-report.md`.

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" dispatch message implementer \
    --task 1.1 --key "$IMPLEMENTER_KEY"
```

Launch a fresh implementer in `$WORKTREE` through the selected harness.
Save the returned run ID as `IMPLEMENTER_RUN_ID`.
The implementer authors `message.py`; the coordinator never writes that implementation.

The implementer runs these commands in its lane worktree after implementation.
The brief must supply resolved variables; do not assume child sessions inherit the coordinator shell.

```bash
cd "$WORKTREE"
python3 -B -c 'import message; assert message.greeting() == "hello"; print("focused check: PASS")'
```

The implementer changes only task `1.1` from `[ ]` to `[x]` in the lane's `$TASKS`.
All other task IDs, checkbox states, and definitions remain unchanged.

```bash
git add message.py "openspec/changes/$CHANGE/tasks.md"
git commit -m "feat: add greeting and complete task"
git status --short
```

The implementer updates its live report and returns the commit and actual check results.
The coordinator verifies the report, committed tick, and clean lane worktree.
Only after success, the coordinator records:

```bash
cd "$FEATURE_CHECKOUT"
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" dispatch-done message implementer \
  --key "$IMPLEMENTER_KEY" --worker-run-id "$IMPLEMENTER_RUN_ID" --result ok
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" sync
LANE_HEAD="$(git -C "$WORKTREE" rev-parse HEAD)"
```

## 4. Review and integrate the lane

Write `$STORE/briefs/message-code-reviewer-r1.md` using the review template.
Require a combined implementation/test-adequacy review at `LANE_HEAD`.
Name the exact lane range and `$STORE/reviews/message-code-reviewer-r1.md`.

```bash
CODE_KEY="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" dispatch message code-reviewer \
    --task 1.1 --key "$CODE_KEY"
```

Launch a different fresh reviewer in the lane worktree.
Save its actual run ID as `CODE_RUN_ID`.
Verify its receipt and read the report.
After an actual independent `PASS`, record:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" dispatch-done message code-reviewer \
  --key "$CODE_KEY" --worker-run-id "$CODE_RUN_ID" --result ok
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" lane-review message code-reviewer PASS \
  --commit "$LANE_HEAD" --report "$STORE/reviews/message-code-reviewer-r1.md"
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" sync
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" lane-merge message
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" lane-close message
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" sync
```

For `MORE WORK`, record that verdict instead of merging.
Follow [lane correction and escalation](lane.md).
This lane has no UI or sensitive flag, so no per-lane human checkpoint is required.

## 5. Finish on the feature branch

Obtain the human's release classification after the lane merge.
Set `CLASSIFICATION` to the exact chosen label.
Follow the repository's preparation policy before recording it.
For this fixture, no version or generated-file edits are required; integrated `HEAD` is already committed.
If a real repository requires preparation changes, commit them before the helper's `classify` command.

The coordinator writes a factual preparation receipt:

```bash
cd "$FEATURE_CHECKOUT"
cat > "$STORE/evidence/preparation.md" <<EOF
Classification: $CLASSIFICATION
Prepared HEAD: $(git rev-parse HEAD)
Preparation: the fixture policy requires no version or generated-file changes.
Input: message.py
EOF
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" finish-record classify \
  --classification "$CLASSIFICATION" --artifact "$STORE/evidence/preparation.md" \
  --input message.py --check greeting --inspection output
```

Run and record the selected check on committed integrated `HEAD`:

```bash
python3 -B -c 'import message; assert message.greeting() == "hello"; print("release check: PASS")' \
  > "$STORE/evidence/greeting.txt" 2>&1 && \
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" finish-record capture \
  --check greeting --artifact "$STORE/evidence/greeting.txt" --input message.py
```

Ask the human to inspect this output personally:

```bash
python3 -B -c 'import message; print(message.greeting())'
```

Expected output: `hello`.
The human records the real decision in `$STORE/evidence/human-output.md` using [the inspection format](finish.md).
The record must name checkpoint `output`.
No example record substitutes for that decision.

After the human records acceptance:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" finish-record inspect \
  --check output --artifact "$STORE/evidence/human-output.md"
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" finish-record pr-check
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" status --json
```

Ask separately for approval to open the PR.
Follow the repository's PR policy after that approval.
Monitor automated PR checks and bot code-review comments using [post-PR follow-up](finish.md#post-pr-follow-up).
Route actionable findings through implementation and independent review, then recheck the updated PR.
Sync and archive the completed OpenSpec change before human merge using [the finish sequence](finish.md#openspec-sync-and-archive).
Recheck the archival commit on the PR. The human retains the later PR merge decision.

## Monitor or resume this example

Ask the Coordinator for progress while workers run:

> Show the hello-message dashboard.
> Summarize completed work, pending reviews, and blockers.

Use the [status and dashboard commands](helper-commands.md#status-and-task-status) from the feature checkout.
A live dashboard is optional; run its server in a separate terminal.
The dashboard shows recorded progress. It never supplies a review verdict or human approval.

After an interruption, keep the same feature checkout and its local workflow records.
Ask:

> Resume hello-message with feature-development-skill.
> Reconcile records and worker ownership.
> Continue from the first incomplete operation.
> Do not repeat completed tasks or recreate approval from chat history.

The Coordinator follows [recovery](recovery.md) before further affected operations.
Do not restart the walkthrough at step 1 merely because a new session begins.

## Expected records

These paths describe the state before OpenSpec sync and archive.
After archive, the completed task file lives in its verified archive directory instead of the active change directory.
Local records remain in the same coordinator store.

| Location | Expected result |
|---|---|
| `.agents/workflow/capability-map.md` | Actual harness capabilities |
| `.agents/workflow/model-selection.json` | Human-approved repo-level role profiles |
| `.agents/workflow/hello-message/plan-reviews.json` | Current Oracle verdict for OpenSpec artifacts only |
| `.agents/workflow/plan-graph-review.json` | Separate Oracle verdict for the whole repo graph |
| `.agents/workflow/plan-approval.json` | Human plan approval bound to committed artifacts and the reviewed graph |
| `.agents/workflow/plan-graph.json` | Local repo-level change/lane/task graph; never committed |
| `.agents/workflow/coordination.json` | Completed dispatches, reviews, merge, finish, and lane tasks under `changes.hello-message` |
| `openspec/changes/hello-message/tasks.md` | Task `1.1` committed and checked on the feature branch |
| `.agents/workflow/hello-message/reviews/` | Real Oracle and code-review reports |
| `.agents/workflow/hello-message/reports/` | Implementer live report |
| `.agents/workflow/hello-message/evidence/` | Preparation receipt, release-check output, and human inspection record |

`pr-check` must pass without `restage` on this normal path.
For interrupted work or changed inputs, follow [recovery](recovery.md).

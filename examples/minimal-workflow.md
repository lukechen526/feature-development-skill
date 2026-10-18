# Minimal workflow

This recipe uses one ordinary lane and one task in a disposable consuming repository.
It is not a live harness validation result.
Commands record real events; they do not launch agents, perform reviews, or grant consent.
Never use sample text as a review report or human approval.

Read [the skill](../SKILL.md) and [the handoff contract](../workflow/briefs-and-handoffs.md) first.
Stop after any failed command or unmet gate.

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
PLAN="openspec/changes/$CHANGE/plan/graph-plan.json"
TASKS="openspec/changes/$CHANGE/tasks.md"
openspec --version
openspec list --json
mkdir -p "$STORE/briefs" "$STORE/reviews" "$STORE/reports" "$STORE/evidence"
```

Complete [preflight and role setup](../workflow/preflight-and-roles.md) before any launch.
Write the repo-level capability map and human-approved model selection.
Set `ORACLE_MODEL`, `ORACLE_LEVEL`, `IMPLEMENTER_MODEL`, `IMPLEMENTER_LEVEL`, `CODE_MODEL`, and `CODE_LEVEL`
from that selection in the coordinator shell.
Do not substitute defaults or create a per-change selection file.

## 2. Author and review the plan

The coordinator writes these planning artifacts:

```bash
mkdir -p "$(dirname "$PLAN")"
cat > "$PLAN" <<'JSON'
{
  "schema_version": 1,
  "change": "hello-message",
  "lanes": {
    "message": {
      "risk": "ordinary",
      "ui": false,
      "screen": false,
      "sensitive": false,
      "depends_on": []
    }
  },
  "tasks": {
    "1.1": {"lane": "message", "deps": []}
  }
}
JSON
printf '%s\n' '- [ ] 1.1 Implement message.greeting() returning hello.' > "$TASKS"
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" plan-validate "$PLAN" --tasks "$TASKS"
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" plan-graph "$PLAN" --format md
PLAN_HEAD="$(git rev-parse HEAD)"
```

Write the filled Oracle brief to `$STORE/briefs/plan-oracle-r1.md`.
Name the proposal, design, delta specs, tasks, plan, feature `HEAD`, role source, profile, and capability map.
Require the first report to echo role ID, profile hash, worktree, `HEAD`, and contract path.

Record intent before launching a fresh read-only Oracle in the feature checkout:

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" plan-dispatch \
  --role oracle --model "$ORACLE_MODEL" --level "$ORACLE_LEVEL" --task plan \
  --report "$STORE/reviews/plan-oracle-r1.md"
```

Launch the Oracle through the preflighted harness.
Verify its first-report receipt and read its real review report.
Only after an actual `PASS`, record:

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" plan-review \
  --role oracle PASS --head "$PLAN_HEAD" --report "$STORE/reviews/plan-oracle-r1.md"
```

For `MORE WORK`, record that verdict instead.
Follow the bounded correction procedure in [the plan phase](../workflow/plan.md).

CAUTION: The following commits record approval; they do not obtain it.
Obtain explicit human approval before running them.

```bash
git add "openspec/changes/$CHANGE"
git commit -m "docs: add approved greeting plan"
git commit --allow-empty -m "planning checkpoint approved by human"
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" state-derive "$CHANGE"
```

## 3. Dispatch the implementer

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" lane-open message
WORKTREE="$(python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" status --json | \
  python3 -c 'import json, sys; print(json.load(sys.stdin)["lanes"]["message"]["worktree"])')"
IMPLEMENTER_KEY="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
```

Write `$STORE/briefs/message-implementer-1.1.md` using the implementation template.
Fill every path and command with its resolved value.
Include task `1.1`, the criterion, focused check, lane worktree, base, `HEAD`, role source, profile, and capability map.
Require the live report at `$STORE/reports/message-report.md`.

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" dispatch message implementer \
  --model "$IMPLEMENTER_MODEL" --level "$IMPLEMENTER_LEVEL" --task 1.1 --key "$IMPLEMENTER_KEY"
```

Launch a fresh implementer in `$WORKTREE` through the selected harness.
Save the returned run ID as `IMPLEMENTER_RUN_ID`.
The implementer authors `message.py`; the coordinator never writes that implementation.

The implementer runs these commands in its lane worktree after implementation.
The brief must supply resolved variables; do not assume child sessions inherit the coordinator shell.

```bash
cd "$WORKTREE"
python3 -B -c 'import message; assert message.greeting() == "hello"; print("focused check: PASS")'
git add message.py
git commit -m "feat: add greeting"
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" task-tick message 1.1
git add "openspec/changes/$CHANGE/tasks.md"
git commit -m "docs: mark greeting task complete"
git status --short
```

The implementer updates its live report and returns the commit and actual check results.
The coordinator verifies the report, committed tick, and clean lane worktree.
Only after success, the coordinator records:

```bash
cd "$FEATURE_CHECKOUT"
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" dispatch-done message implementer \
  --key "$IMPLEMENTER_KEY" --worker-run-id "$IMPLEMENTER_RUN_ID" --result ok
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" sync
LANE_HEAD="$(git -C "$WORKTREE" rev-parse HEAD)"
```

## 4. Review and integrate the lane

Write `$STORE/briefs/message-code-r1.md` using the review template.
Require a combined implementation/test-adequacy review at `LANE_HEAD`.
Name the exact lane range and `$STORE/reviews/message-code-r1.md`.

```bash
CODE_KEY="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" dispatch message code \
  --model "$CODE_MODEL" --level "$CODE_LEVEL" --task 1.1 --key "$CODE_KEY"
```

Launch a different fresh reviewer in the lane worktree.
Save its actual run ID as `CODE_RUN_ID`.
Verify its receipt and read the report.
After an actual independent `PASS`, record:

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" dispatch-done message code \
  --key "$CODE_KEY" --worker-run-id "$CODE_RUN_ID" --result ok
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" lane-review message code PASS \
  --commit "$LANE_HEAD" --report "$STORE/reviews/message-code-r1.md"
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" sync
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" lane-merge message
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" lane-close message
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" sync
```

For `MORE WORK`, record that verdict instead of merging.
Follow [lane correction and escalation](../workflow/lane.md).
This lane has no UI, screen, or sensitive flag, so no per-lane human checkpoint is required.

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
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" finish-record classify \
  --classification "$CLASSIFICATION" --artifact "$STORE/evidence/preparation.md" \
  --input message.py --check greeting --inspection output
```

Run and record the selected check on committed integrated `HEAD`:

```bash
python3 -B -c 'import message; assert message.greeting() == "hello"; print("release check: PASS")' \
  > "$STORE/evidence/greeting.txt" 2>&1 && \
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" finish-record capture \
  --check greeting --artifact "$STORE/evidence/greeting.txt" --input message.py
```

Ask the human to inspect this output personally:

```bash
python3 -B -c 'import message; print(message.greeting())'
```

Expected output: `hello`.
The human records the real decision in `$STORE/evidence/human-output.md` using [the inspection format](../workflow/finish.md).
The record must name checkpoint `output`.
No example record substitutes for that decision.

After the human records acceptance:

```bash
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" finish-record inspect \
  --check output --artifact "$STORE/evidence/human-output.md"
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" finish-record pr-check
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --change "$CHANGE" status --json
```

Ask separately for approval to open the PR.
Follow the repository's PR policy after that approval.
The human retains the later PR merge decision.

## Expected records

| Location | Expected result |
|---|---|
| `.agents/workflow/capability-map.md` | Actual harness capabilities |
| `.agents/workflow/model-selection.json` | Human-approved repo-level role profiles |
| `.agents/workflow/hello-message/plan-reviews.json` | Completed plan dispatch and current plan review |
| `.agents/workflow/hello-message/coordination.json` | Completed lane dispatches, current review, recorded merge, and finish bindings |
| `openspec/changes/hello-message/tasks.md` | Task `1.1` committed and checked on the feature branch |
| `.agents/workflow/hello-message/reviews/` | Real Oracle and code-review reports |
| `.agents/workflow/hello-message/reports/` | Implementer live report |
| `.agents/workflow/hello-message/evidence/` | Preparation receipt, release-check output, and human inspection record |

`pr-check` must pass without `restage` on this normal path.
For interrupted work or changed inputs, follow [recovery](../workflow/recovery.md).

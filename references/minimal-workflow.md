# Worked workflow

This example illustrates the order of operations. Replace IDs, paths, checks, and SHA placeholders with actual repository values. Helper records never substitute for real worker reports or human decisions.

## Draft and approve

Use the human's request to create `openspec/changes/greeting/` with proposal, design, delta specs, and:

```markdown
## 1. Greeting
- [ ] 1.1 Add the greeting behavior
- [ ] 1.2 Add focused regression coverage
```

Create a [current-schema](workflow-records.md) graph with one ordinary, non-UI, nonsensitive lane `greet` containing those tasks; task 1.2 depends on 1.1. Record role/model choices and verify scoped worker support.

```bash
export FEATDEV_SKILL_ROOT="/absolute/installed/feature-development-skill"
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-validate
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" dispatch --scope plan oracle --task plan --key plan-r1
```

Launch the independent Oracle to review both artifacts and graph. Read its report and record the actual verdict:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" review --scope plan oracle PASS --commit <feature-sha> --report /absolute/oracle.md
```

If the verdict is MORE WORK, correct the findings and repeat with another dispatch/report until the first PASS. Obtain human plan approval, commit artifacts, then record approval:

```bash
git add openspec/changes/greeting
git commit -m "docs: define greeting feature"
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-approve
```

## Implement, review, integrate

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" lane-open greet
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" dispatch greet implementer --task 1.1 --key greeting-1
```

Launch an Implementer in the recorded worktree. It implements task 1.1, checks it, commits its tick and code, and reports. Record the result, then reuse that session for task 1.2 with a new task dispatch:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" dispatch-done greet implementer --key greeting-1 --worker-run-id <worker> --result ok
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" dispatch greet implementer --task 1.2 --key greeting-2
```

After the second task is committed and reported, close its dispatch. Launch a separate read-only code reviewer with both tasks and the candidate diff. Record its actual verdict:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" dispatch-done greet implementer --key greeting-2 --worker-run-id <same-worker> --result ok
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" dispatch greet code-reviewer --task lane-review --key code-r1
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" review greet code-reviewer PASS --commit <lane-sha> --report /absolute/code-review.md
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" dispatch-done greet code-reviewer --key code-r1 --result ok
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" lane-merge greet
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" lane-close greet
```

UI/sensitive lanes also require applicable human checkpoints; high-risk lanes require separate test review. Corrections use focused checks and fresh independent verdicts, without a fixed round cap.

## Human-requested plan change

When the human changes the requested behavior, update the artifacts and lane graph, commit affected artifacts, and record the initiating request and current planning inputs in `coordination.json.plan_amendments`. No Oracle re-review or second plan approval is required. Reconcile active worktrees and renew affected implementation reviews/evidence/checkpoints.

## Prepare, open, follow up, hand off

After all lanes integrate, obtain human release classification. Have a finish-scoped author prepare release artifacts in a separate worktree, review and integrate preparation, then run repository release checks and required human inspections. Use [finish record commands](helper-commands.md#finish-and-status) with actual receipts and committed input paths.

Verify readiness and obtain separate human consent to open the PR. Record it in `finish/pr-follow-up.md`, open the PR, and monitor checks/comments. Resolve actionable findings through scoped implementation and independent review. Sync/archive completed OpenSpec changes, commit and push, then renew affected validation.

Present the final PR state for the human's authorization and merge. Agents never merge the PR.

## Inspect and resume

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" status --json
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" status --format mermaid
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" dashboard
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-validate --workflow
```

For interruptions or breaking schemas, back up local records and use [Coordinator reconstruction](recovery.md). Resume incomplete work; do not recreate known approvals or repeat successful merges just to match an older procedure.

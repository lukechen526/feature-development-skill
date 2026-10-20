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
The greeting lane is ordinary risk with no UI or sensitive flag, so it requires one `code-reviewer` pass covering code and test adequacy. A high-risk lane would add `test-reviewer`.
The fixture's final human output inspection is a release rule, not an automatic requirement for every CLI project.
The human separately decides whether to open the PR and later whether to merge it.

The helper records events and checks bindings.
It does not enforce plan approval at `lane-open` or `lane-merge`, perform reviews, or grant consent.
The Coordinator verifies every human gate and every record this walkthrough says to check.

## Before running commands

This recipe uses one lane and one task in a disposable consuming repository.
It is not a live harness validation result.
Commands record real events; they do not launch agents, perform reviews, or grant consent.
Never use sample text as a review report or human approval.
Do not paste the entire walkthrough as an unattended script: stop at every agent handoff and human decision.

These fences assume one shell session.
If you run a fence in a new shell, re-export `FEATDEV_SKILL_ROOT` and re-assign every variable that fence uses; child sessions never inherit the Coordinator shell.
A fence that changes directory must return to the feature checkout as shown.

This example uses a disposable repository.
Before you modify or repair an existing workflow store, back up the entire `.agents/workflow/` tree outside the checkout; Git cannot reconstruct these local, gitignored objects.

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
export FEATDEV_SKILL_ROOT="<resolved absolute skill path>"
FEATURE_CHECKOUT="$(pwd -P)"
CHANGE="hello-message"
STORE="$FEATURE_CHECKOUT/.agents/workflow/$CHANGE"
FINISH_STORE="$FEATURE_CHECKOUT/.agents/workflow/finish"
PLAN="$FEATURE_CHECKOUT/.agents/workflow/plan-graph.json"
TASKS="openspec/changes/$CHANGE/tasks.md"
CLASSIFICATION="<human release classification>"
IMPLEMENTER_RUN_ID="<implementer run ID from the launch>"
CODE_RUN_ID="<code-reviewer run ID from the launch>"
FINISH_REVIEW_RUN_ID="<finish code-reviewer run ID from the launch>"
openspec --version
openspec list --json
mkdir -p "$STORE/briefs" "$STORE/reviews" "$STORE/reports" \
  "$FINISH_STORE/briefs" "$FINISH_STORE/reports" "$FINISH_STORE/reviews" \
  "$FINISH_STORE/evidence/r1" \
  "$FEATURE_CHECKOUT/.agents/workflow/briefs" \
  "$FEATURE_CHECKOUT/.agents/workflow/reviews"
```

Resolve `FEATDEV_SKILL_ROOT` through [preflight](preflight-and-roles.md); do not hardcode the installation path.
Replace `CLASSIFICATION` and each run ID only with the value the human or the actual harness launch returns.
Do not fabricate a run ID.

Complete [preflight and role setup](preflight-and-roles.md) before any launch.
Write the repo-level capability map and human-approved model selection.
The Coordinator reads the selected role profiles for each actual harness launch.
Helper dispatch records take the role and task, not separate model or thinking overrides.
Do not substitute defaults or create a per-change selection file.

`model-selection.json` and its model, thinking, and role profiles are repo-level only.
Never place the file or a profile under a lane worktree or `.agents/workflow/hello-message/`, and never put a worktree or another per-dispatch path in a profile.
The absolute worktree is execution metadata in the brief and coordination.

## 2. Review change artifacts, then the repo graph

### Review the OpenSpec change

The proposal, design, and delta specs already exist from the consuming repository's OpenSpec process.
The Coordinator writes the task checklist. No graph is required for this review.

```bash
printf '%s\n' '- [ ] 1.1 Implement message.greeting() returning hello.' > "$TASKS"
PLAN_HEAD="$(git rev-parse HEAD)"
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" plan-dispatch \
  --scope change --role oracle --task plan \
  --report "$STORE/reviews/artifacts-oracle-c1-r1.md"
```

Write `$STORE/briefs/artifacts-oracle-c1-r1.md` using operation `openspec_review`.
Name `Change: hello-message`, `Scope: change artifacts`, `Lane: not applicable (planning review)`,
`Task/round: artifacts c1-r1`,
`Worktree: $FEATURE_CHECKOUT` (read-only), `Base`/`HEAD: $PLAN_HEAD`,
`Profile: oracle` from the repo-level selection,
`Contract: <absolute path to references/briefs-and-handoffs.md>`, and `Deadline`.
List only this change's proposal, design, delta specs, tasks, reviewed source artifacts, role source, and capability map.
Exclude the repo graph.
End the brief with `Deliver: $STORE/reviews/artifacts-oracle-c1-r1.md`.
The Oracle writes its actual report there before any `plan-review` command.
Launch a fresh read-only Oracle through the preflighted harness.
Read its actual report. Only after a real `PASS`, record:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" plan-review \
  --scope change --role oracle PASS --head "$PLAN_HEAD" \
  --report "$STORE/reviews/artifacts-oracle-c1-r1.md"
```

Every `plan-review` command includes the verdict, `--head`, and `--report`.
For multiple included changes, repeat artifact review for each before proceeding.
For `MORE WORK`, use the bounded correction procedure in [the plan phase](plan.md).
Record the correction intent with a new key and a new report, for example:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" plan-dispatch \
  --scope change --role oracle --task plan \
  --key "$(python3 -c 'import uuid; print(uuid.uuid4().hex)')" \
  --report "$STORE/reviews/artifacts-oracle-c1-r2.md"
```

After two `MORE WORK` verdicts, a further review requires Oracle mediation.
Require exactly one further Oracle-authored pass and then a human decision on any remaining work; there is no cap after mediation.
The repo graph form replaces `--scope change --change "$CHANGE"` with `--scope graph` and uses a report under `.agents/workflow/reviews/`.

### Author and review the repo graph

This fixture starts without a graph. Do not overwrite an existing `.agents/workflow/plan-graph.json`.
If `$PLAN` already exists, add only `changes.hello-message` and preserve every other change and key.
If it already defines `hello-message`, stop and reconcile rather than overwrite it.
If the existing graph contains other changes, each needs a current artifact `PASS` before graph review; the helper validates every graph entry.
The graph is local and ignored, just like coordination.

```bash
if [ -f "$PLAN" ]; then
  python3 - "$PLAN" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
document = json.loads(path.read_text(encoding="utf-8"))
changes = document.setdefault("changes", {})
if "hello-message" in changes:
    raise SystemExit("plan-graph already defines hello-message; reconcile it instead of overwriting")
changes["hello-message"] = {
    "lanes": {
        "message": {
            "risk": "ordinary",
            "ui": False,
            "sensitive": False,
            "depends_on": [],
            "tasks": {"1.1": {"deps": []}},
        }
    },
}
temporary = path.with_name(path.name + ".tmp")
temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
temporary.replace(path)
PY
else
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
fi
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-validate "$PLAN"
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-graph "$PLAN" --format md
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-dispatch \
  --scope graph --role oracle --task plan \
  --report "$FEATURE_CHECKOUT/.agents/workflow/reviews/plan-graph-oracle-c1-r1.md"
```

Write `$FEATURE_CHECKOUT/.agents/workflow/briefs/plan-graph-oracle-c1-r1.md` using operation `plan_graph_review`.
Name `Change: hello-message` (the included change), `Scope: repo graph`,
`Lane: not applicable (planning review)`, `Task/round: graph c1-r1`,
`Worktree: $FEATURE_CHECKOUT` (read-only), `Base`/`HEAD: $PLAN_HEAD`,
`Profile: oracle` from the repo-level selection,
`Contract: <absolute path to references/briefs-and-handoffs.md>`, and `Deadline`.
Supply the whole repo graph, every included change's artifact verdict, and the reviewed source artifacts.
End the brief with `Deliver: $FEATURE_CHECKOUT/.agents/workflow/reviews/plan-graph-oracle-c1-r1.md`.
The Oracle writes its actual report there before any `plan-review` command.
Launch a separate fresh Oracle. Read the actual report. Only after a real graph `PASS`, record:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-review \
  --scope graph --role oracle PASS --head "$PLAN_HEAD" \
  --report "$FEATURE_CHECKOUT/.agents/workflow/reviews/plan-graph-oracle-c1-r1.md"
```

The artifact ledger is `$STORE/plan-reviews.json`.
The graph ledger is `.agents/workflow/plan-graph-review.json`.
Neither verdict substitutes for the other or grants human approval.

CAUTION: The following commands record approval; they do not obtain it.
Obtain explicit human approval of the OpenSpec artifacts and local graph before running them.
Commit the artifacts first, then record the approval in the repo-level record.
Never stage a repo-level runtime object.

```bash
git add "openspec/changes/$CHANGE"
git commit -m "docs: add approved greeting artifacts"
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-approve --approver human
```

`plan-approve` writes `.agents/workflow/plan-approval.json` with the approver, current HEAD,
graph digest, and per-change artifact digests.
The record is local and gitignored; never commit it.
It refuses uncommitted artifact changes, a missing or stale artifact `PASS`, or a missing or stale graph `PASS`.
`status` reports the record as `current`, `stale`, or `missing`.

`lane-open` and `lane-merge` do not enforce plan approval.
Before opening this lane and again before merging it, the Coordinator must run `status` and confirm
`plan_approval.state` is `current` for the approved plan.

## 3. Dispatch the implementer

Verify current plan approval before opening the lane:

```bash
if ! python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" status --json | \
  python3 -c '
import json, sys
state = json.load(sys.stdin)["plan_approval"]["state"]
print("plan approval:", state)
raise SystemExit(0 if state == "current" else 1)
'; then
  echo "plan approval is not current; stop before lane-open" >&2
  exit 1
fi
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" lane-open message
WORKTREE="$(python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" status --json | \
  python3 -c 'import json, sys; print(json.load(sys.stdin)["lanes"]["message"]["worktree"])')"
LANE_BASE="$(python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" status --json | \
  python3 -c 'import json, sys; print(json.load(sys.stdin)["lanes"]["message"]["base"])')"
LANE_START="$(git -C "$WORKTREE" rev-parse HEAD)"
IMPLEMENTER_KEY="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
```

If you set `FEATDEV_LANE_WORKTREE_ROOT`, use an absolute path.
A relative value resolves against the Coordinator's current directory and can silently place the worktree elsewhere.

Write `$STORE/briefs/message-implementer-1.1.md` using the implementation template.
Name `Change: hello-message`, `Lane: message`, `Task/round: 1.1`, `Worktree: $WORKTREE`,
`Base: $LANE_BASE`, `HEAD: $LANE_START`, `Profile: implementer` from the repo-level selection,
`Contract: <absolute path to references/briefs-and-handoffs.md>`, and `Deadline`.
Fill every path and command with its resolved value.
Include the criterion, focused check, lane worktree, base, `HEAD`, role source, profile, and capability map.
End the brief with `Deliver: $STORE/reports/message-report.md`.
Require the live report at that path.

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" dispatch message implementer \
    --task 1.1 --key "$IMPLEMENTER_KEY"
```

Launch a fresh implementer in `$WORKTREE` through the selected harness.
Set `IMPLEMENTER_RUN_ID` to the exact run ID the launch returns.
The implementer authors `message.py`; the Coordinator never writes that implementation.

A new dispatch `--key` bypasses the helper's open same-role guard, but it is not authorization for a second writer.
Ensure the prior worker is stopped or released before replacement, and keep one writer per worktree.

The implementer runs these commands in its lane worktree after implementation.
The brief must supply resolved variables; do not assume child sessions inherit the Coordinator shell.

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
The Coordinator verifies the report, committed tick, and clean lane worktree.
Only after success, the Coordinator records the dispatch outcome and syncs:

```bash
cd "$FEATURE_CHECKOUT"
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" dispatch-done message implementer \
  --key "$IMPLEMENTER_KEY" --worker-run-id "$IMPLEMENTER_RUN_ID" --result ok
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" sync
LANE_HEAD="$(git -C "$WORKTREE" rev-parse HEAD)"
```

## 4. Review and integrate the lane

Write `$STORE/briefs/message-code-reviewer-r1.md` using the review template.
Name `Change: hello-message`, `Lane: message`, `Task/round: review 1`, `Worktree: $WORKTREE`,
`Base: $LANE_BASE`, `HEAD: $LANE_HEAD`, review range `$LANE_BASE..$LANE_HEAD`,
`Profile: code-reviewer` from the repo-level selection,
`Contract: <absolute path to references/briefs-and-handoffs.md>`, and `Deadline`.
Require a code and test-adequacy review at `LANE_HEAD`.
Name the exact lane range and `Deliver: $STORE/reviews/message-code-reviewer-r1.md`.
This ordinary lane uses one `code-reviewer` for code and test adequacy.
A high-risk lane would add a separate `test-reviewer`.

```bash
CODE_KEY="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" dispatch message code-reviewer \
    --task 1.1 --key "$CODE_KEY"
```

Launch a different fresh reviewer in the lane worktree.
Set `CODE_RUN_ID` to the actual run ID returned by the launch.
Verify its receipt and read the report.
The reviewer reply states the verdict or status, exact SHA, finding IDs, report path, and evidence revision.
It carries no machine-generated footer.
After an actual independent `PASS`, record:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" dispatch-done message code-reviewer \
  --key "$CODE_KEY" --worker-run-id "$CODE_RUN_ID" --result ok
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" lane-review message code-reviewer PASS \
  --commit "$LANE_HEAD" --report "$STORE/reviews/message-code-reviewer-r1.md"
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" sync
```

For ordinary role evidence, merge revalidates only the `ui-reviewer` evidence path and digest.
It does not revalidate the other role evidence or report hashes.
Before merging, the Coordinator verifies this code-review report's hash and evidence and confirms plan approval is still current:

```bash
if ! python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" status --json | \
  python3 -c '
import json, sys
state = json.load(sys.stdin)["plan_approval"]["state"]
print("plan approval:", state)
raise SystemExit(0 if state == "current" else 1)
'; then
  echo "plan approval is not current; stop before merge" >&2
  exit 1
fi
if ! python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" lane-merge message; then
  echo "lane-merge failed; stop and reconcile" >&2
  exit 1
fi
if ! python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" status --json | \
  python3 -c '
import json, sys
lane = json.load(sys.stdin)["lanes"]["message"]
print("lane phase:", lane["phase"])
raise SystemExit(0 if lane["phase"] == "merged" else 1)
'; then
  echo "no recorded merge; do not close the lane" >&2
  exit 1
fi
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" lane-close message
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" sync
```

`lane-close` also accepts a lane whose ticks are complete without a recorded merge.
The Coordinator verifies the recorded merge before closing; the phase check above is that verification.

For `MORE WORK`, record that verdict instead of merging.
Follow [lane correction and escalation](lane.md).
After two review rounds, a further round requires Oracle mediation; there is no cap after mediation.
Require exactly one further Oracle-authored pass, an independent current-HEAD review, and then a human decision on any remaining work.
For a standard Oracle-authored fix or an Oracle stand-in, verify independence and record the actual reviewer or stand-in ID in the report or brief; there is no helper flag for it.

## 5. Finish on the feature branch

```bash
HEAD="$(git rev-parse HEAD)"
```

Confirm every lane of every change in the repository graph is merged and closed, with a clean feature-branch `HEAD`.
Finish is repository-level: one `.agents/workflow/finish.json` record covers this PR, even when the graph contains more than one OpenSpec change.
Do not pass `--change` to any `finish-record` command.
Obtain the human's release classification after the lane merge.
Set `CLASSIFICATION` to the exact chosen label.
Follow the repository's preparation policy before recording it.
For this fixture, no version or generated-file edits are required; integrated `HEAD` is already committed.
If a real repository requires preparation changes, commit them before the helper's `classify` command.

The Coordinator writes a factual preparation receipt under the finish evidence revision.
It records the actual commands, results, and paths:

```bash
mkdir -p "$FINISH_STORE/evidence/r1"
cat > "$FINISH_STORE/evidence/r1/preparation.md" <<EOF
Classification: $CLASSIFICATION
Prepared HEAD: $(git rev-parse HEAD)
Preparation: the fixture policy requires no version or generated-file changes.
Inputs: message.py
Commands: git rev-parse HEAD
Results: HEAD matches the committed integrated feature branch.
Paths: message.py
EOF
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" finish-record classify \
  --classification "$CLASSIFICATION" --artifact "$FINISH_STORE/evidence/r1/preparation.md" \
  --input message.py --check greeting --inspection output
```

`classify` requires at least one `--input` and treats every input as a preparation input.
There is no separate check-input field.
This fixture has no preparation changes, so `message.py` is the tracked release input.
Changing `message.py` after this record requires `finish-record restage` with the new receipt and inputs.
Declare every selected check with `--check`; `classify` records the declared set and runs no check.
This fixture uses no version. In a versioned repository, pass `--version <label>` here; omitting `--version` on a later `restage` clears the expected version.

Run and record the selected check on committed integrated `HEAD`.
The check receipt includes the exact command, result, `HEAD`, and inputs:

```bash
mkdir -p "$FINISH_STORE/evidence/r1"
CHECK_COMMAND="python3 -B -c 'import message; assert message.greeting() == \"hello\"; print(\"release check: PASS\")'"
if eval "$CHECK_COMMAND" > "$FINISH_STORE/evidence/r1/greeting-output.txt" 2>&1; then
  CHECK_RESULT=PASS
else
  CHECK_RESULT=FAIL
fi
{
  printf 'Command: %s\n' "$CHECK_COMMAND"
  printf 'HEAD: %s\n' "$(git rev-parse HEAD)"
  printf 'Inputs: message.py\n'
  printf 'Result: %s\n' "$CHECK_RESULT"
  printf 'Output:\n'
  cat "$FINISH_STORE/evidence/r1/greeting-output.txt"
} > "$FINISH_STORE/evidence/r1/greeting.txt"
if [ "$CHECK_RESULT" != PASS ]; then
  echo "release check failed; stop before capture" >&2
else
  python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" finish-record capture \
    --check greeting --artifact "$FINISH_STORE/evidence/r1/greeting.txt" --input message.py
fi
```

A check outside the declared set is refused.
A failed check blocks the next steps.

### Finish review

Before asking for PR-opening consent, obtain an independent finish-scoped code review at the exact integrated `HEAD`.
Write `$FINISH_STORE/briefs/finish-code-reviewer-release-review.md` using the finish reviewer template.
Name `Included changes: hello-message`, `Scope: finish`, `Task: release-review`,
`Worktree: $FEATURE_CHECKOUT` (read-only), `Owned paths: read-only`, `Integrated HEAD: $HEAD`,
`Profile: code-reviewer` from the repo-level selection,
`Contract: <absolute path to references/briefs-and-handoffs.md>`, and `Deadline`.
List the release inputs and the `r1` evidence as evidence inputs.
End the brief with `Deliver: $FINISH_STORE/reports/finish-code-reviewer-release-review.md` and
`Review: $FINISH_STORE/reviews/finish-code-reviewer-$HEAD.md`.

```bash
FINISH_REVIEW_KEY="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" dispatch --scope finish code-reviewer \
  --task release-review --key "$FINISH_REVIEW_KEY"
```

Launch a fresh read-only reviewer on the feature checkout.
Set `FINISH_REVIEW_RUN_ID` to the actual run ID returned by the launch.
The reviewer writes its report and a finish review record at `$FINISH_STORE/reviews/finish-code-reviewer-$HEAD.md`
using the [finish review record](finish.md#finish-review-record) format.
That record states the verdict (`PASS` or `MORE WORK`), the exact integrated HEAD, the inputs and evidence revision,
any limitations, and the actual reviewer identity.
The reviewer reply also states the verdict or status, exact SHA, finding IDs, report path, and evidence revision; it carries no machine footer.
Only a current `PASS` at this HEAD and revision permits consent.
The Coordinator verifies the finish review record's exact HEAD, evidence revision, and reviewer independence rather than relying on a helper check.
A `MORE WORK` verdict blocks consent; resolve findings and re-review at the new HEAD.

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" dispatch-done --scope finish code-reviewer \
  --key "$FINISH_REVIEW_KEY" --worker-run-id "$FINISH_REVIEW_RUN_ID" --result ok
```

### Human inspection and PR readiness

Ask the human to inspect this output personally:

```bash
python3 -B -c 'import message; print(message.greeting())'
```

Expected output: `hello`.
The human records the real decision at `$FINISH_STORE/evidence/r1/human-output.md` using [the inspection format](finish.md).
The record must name checkpoint `output`.
No example record substitutes for that decision.

After the human records acceptance:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" finish-record inspect \
  --check output --artifact "$FINISH_STORE/evidence/r1/human-output.md"
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" finish-record pr-check
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" --change "$CHANGE" status --json
```

`pr-check` validates only the declared check set, the captured evidence, and the declared human inspections.
It does not validate plan approval, this finish review, PR-opening consent, or `pr-follow-up.md`.
The Coordinator verifies each of those records has the current HEAD, inputs, evidence revision, and approval scope before relying on it.
The finish review must be current at the same HEAD and evidence revision before PR-opening consent is requested.

### PR opening and follow-up

Create `$FINISH_STORE/pr-follow-up.md` when finish starts, at the latest before asking for consent, and list every change included in the PR.
Start the file with the included changes; fill the consent block only after the explicit answer, and the PR URL only after opening.
Ask the human separately for permission to open the PR; release classification and PR-merge consent do not imply it.
Only after an explicit `yes`, record the consent and then open the PR.
The consent record names the human, timestamp, exact integrated HEAD, and included changes:

```markdown
# PR follow-up: hello-message

Included changes: hello-message

## PR-opening consent
Decision: approved to open
Human: <name>
Timestamp: <ISO-8601>
HEAD: <exact integrated HEAD>
Included changes: hello-message

## PR
URL: <PR URL>
HEAD: <current feature HEAD>

## Bot dispositions
| Finding | Disposition | Evidence or reply |
|---|---|---|
| <bot finding> | <actionable, duplicate, outdated, or unsupported> | <fix commit, reply, or reason> |

## Outstanding items
- <item, or none>
```

PR-opening consent is separate from release classification and from the later human PR-merge decision.
PR-opening consent names the exact HEAD at which the PR opens; a HEAD that changes before opening needs renewed consent.
Opening the PR starts post-PR follow-up; it does not complete the workflow.
Monitor automated PR checks and bot code-review comments using [post-PR follow-up](finish.md#post-pr-follow-up).
Route actionable findings through implementation and independent review, then recheck the updated PR.
Record the PR URL, current HEAD, and each bot finding's disposition in `pr-follow-up.md`.
Pending checks are not successful checks, and a failing required check blocks merge readiness.

### Archive and refresh

Sync and archive the completed OpenSpec change before the human merge, using [the finish sequence](finish.md#openspec-sync-and-archive).
After the archival commit, refresh every affected record for the new `HEAD`:

```bash
HEAD="$(git rev-parse HEAD)"
mkdir -p "$FINISH_STORE/evidence/r2"
```

Re-run the affected check with the same command and write the receipt under `$FINISH_STORE/evidence/r2/`.
Record it again:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" finish-record capture \
  --check greeting --artifact "$FINISH_STORE/evidence/r2/greeting.txt" --input message.py
```

This is not `restage`: `message.py` did not change.
If it did, restage first, then recapture.

Refresh the finish review at the new HEAD.
Use the same brief fields with `Integrated HEAD: $HEAD` and the `r2` evidence revision.
Repeat the finish review dispatch with a new key, launch a fresh reviewer, and set `FINISH_REVIEW_RUN_ID` from that launch.
Record the new `$FINISH_STORE/reviews/finish-code-reviewer-$HEAD.md` after an actual `PASS`, then complete the dispatch with the same `dispatch-done` form shown above:

```bash
FINISH_REVIEW_KEY="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" dispatch --scope finish code-reviewer \
  --task release-review --key "$FINISH_REVIEW_KEY"
```

Ask the human to repeat the declared inspection at the archival HEAD and write a fresh record under `$FINISH_STORE/evidence/r2/`.
Record it:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" finish-record inspect \
  --check output --artifact "$FINISH_STORE/evidence/r2/human-output.md"
```

Re-run `finish-record pr-check`:

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" finish-record pr-check
```

Then recheck the updated PR: current required checks, current bot comments, and updated dispositions in `pr-follow-up.md`.
Record the archival HEAD and the updated bot dispositions there.
Only then present the current checks, review dispositions, and mergeability for the human's PR merge decision.

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

These paths describe the state after finish and PR opening, before OpenSpec sync and archive.
After archive, refresh the affected evidence and finish review for the archival HEAD, for example under `.agents/workflow/finish/evidence/r2/`.
The completed task file then lives in its verified archive directory instead of the active change directory.
Local records remain in the same coordinator store.

| Location | Expected result |
|---|---|
| `.agents/workflow/capability-map.md` | Actual harness capabilities |
| `.agents/workflow/preflight.json` | Harness/mechanism record; the repo-level selection copies its harness block |
| `.agents/workflow/model-selection.json` | Human-approved repo-level role profiles; no lane worktree or per-dispatch path |
| `.agents/workflow/hello-message/plan-reviews.json` | Current Oracle verdict for OpenSpec artifacts only |
| `.agents/workflow/plan-graph-review.json` | Separate Oracle verdict for the whole repo graph |
| `.agents/workflow/plan-graph.json` | Local repo-level change/lane/task graph; never committed |
| `.agents/workflow/plan-approval.json` | Human plan approval bound to committed artifacts and the reviewed graph; the Coordinator verifies it is current before lane-open and merge |
| `.agents/workflow/briefs/plan-graph-oracle-c1-r1.md` | Repo graph Oracle brief |
| `.agents/workflow/reviews/plan-graph-oracle-c1-r1.md` | Repo graph Oracle actual report |
| `.agents/workflow/hello-message/briefs/` | Artifact-review, implementation, and lane-review briefs |
| `.agents/workflow/hello-message/reports/` | Implementer live report |
| `.agents/workflow/hello-message/reviews/` | Artifact Oracle report and lane code-review report |
| `.agents/workflow/finish.json` | Repo-level finish classification, preparation, checks, evidence, inspections, and dispatches for the PR |
| `.agents/workflow/finish/evidence/r1/` | Preparation receipt, check receipt, and human output inspection |
| `.agents/workflow/finish/pr-follow-up.md` | PR scope, human PR-opening consent, URL/HEAD, and bot dispositions |
| `.agents/workflow/coordination.json` | Completed dispatches, reviews, merge, finish, and lane tasks under `changes.hello-message` |
| `openspec/changes/hello-message/tasks.md` | Task `1.1` committed and checked on the feature branch |

`pr-check` must pass without `restage` on this normal path.
Archive refresh re-captures evidence but does not change preparation inputs.
For interrupted work or changed inputs, follow [recovery](recovery.md).

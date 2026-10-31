# Helper commands

Use Python 3.9+ and Git. Export `FEATDEV_SKILL_ROOT` to the absolute installed skill path, then invoke `python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" ...` from the consuming feature checkout. Commands record facts and check bindings; they do not launch agents, review work, grant consent, or open/merge PRs.

Use global `--change <id>` for change-specific operations when several changes exist. Plan and finish scopes are repository-level and reject a change selector. Quote the verdict `"MORE WORK"`. `--help` documents all accepted flags.

## Command index

| Command | Purpose |
|---|---|
| `plan-validate [plan.json]` | Validate plan shape, task mapping, references, and cycles; `--workflow` also validates present current-schema records |
| `review [lane] role verdict` | Record a current candidate verdict; `--scope plan` reviews initial artifacts and graph together |
| `plan-approve` | Record actual initial human approval or an explicit scoped waiver |
| `dispatch [lane] role` | Record worker intent for `--scope plan`, `lane` (default), or `finish` |
| `dispatch-done [lane] role` | Record actual worker outcome in the matching scope |
| `lane-open lane` | Create or recover an isolated worktree/ref |
| `lane-merge lane` | Check technical/human gates and integrate the candidate |
| `lane-close lane` | Remove a merged lane's worktree/ref |
| `human-gate lane` | Record acceptance, rejection, revocation, authorized stand-in, or eligible deferral |
| `status` | Read derived state as text, `--json`, or graph `--format md\|mermaid` |
| `dashboard` | Render a dashboard; `--serve` runs its live web server |
| `finish-record action` | Record classification, evidence, inspections, and release readiness |

Retired commands have no aliases. State refresh is derived on reads and gate checks; no sync command is needed. The Coordinator writes blocker annotations and reconstructed records directly using [current schemas](workflow-records.md). Migration is governed by [recovery](recovery.md), not a converter command.

## Initial planning

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-validate
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" dispatch --scope plan oracle --task plan --key initial-r1 --report /absolute/review.md
# Launch the independent Oracle, then read its actual report.
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" review --scope plan oracle PASS --commit <sha> --report /absolute/review.md
# Obtain initial human approval and commit the OpenSpec artifacts first.
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" plan-approve --approver human
```

A planning verdict closes its dispatch. For an interrupted worker or changed review inputs, use `dispatch-done --scope plan oracle --key <key> --result failed` after establishing ownership, then dispatch a replacement. Each `MORE WORK` correction needs a new independent review until the first PASS. No fixed round limit applies.

`plan-approve --waiver <human-decision-and-reason> --lane <change/lane>` (repeat lanes) or `--all-lanes` records a distinct advance waiver. Verify the human's recorded waiver before using it. Later human-requested plan changes use amendment records, not another approval or Oracle dispatch.

## Workers and reviews

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" lane-open api
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" dispatch api implementer --task 1.1 --key task-1
# Launch or continue the implementation session in the lane worktree.
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" dispatch-done api implementer --key task-1 --worker-run-id <id> --result ok
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" review api code-reviewer PASS --commit <candidate-sha> --report /absolute/code-review.md
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" lane-merge api
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" lane-close api
```

Dispatch roles are `implementer`, `code-reviewer`, `test-reviewer`, `ui-reviewer`, `oracle`, `runner`. Implementers default to write access; others default to read-only. Only implementer or an explicitly authorized Oracle may receive `--access write`. Dispatch intent does not grant scope beyond the human request. New dispatches use new keys; retrying the same intent uses its original key. Complete the prior ownership record before replacement or starting a conflicting worker.

Lane reviews require `--commit` and `--report`. UI PASS additionally requires `--evidence <path>`; `--evidence-revision <id>` identifies the evidence version. Read the actual report before recording any verdict. A changed candidate requires fresh current-HEAD reviews; an unaffected role may perform an impact check.

Finish workers use `dispatch --scope finish <role> --task <id> --worktree <absolute-path>`. Authors require a separate clean worktree at integrated HEAD. Finish reviewers may inspect a candidate descending from integrated HEAD. Completion uses the same scope/key. Use `--worker-run-id`, `--tokens`, and `--cost` only for observed values.

## Human decisions

Acceptance uses `human-gate <lane> --state approved --commit <sha> --evidence-revision <rev>` and `--evidence <path>` for UI. Other states are `waiting`, `rejected`, `revoked`.

Deferral uses `--state deferred-to-integrated --rationale <reason> --risk-assessment low`, with the same candidate/evidence bindings. Only ordinary-risk UI without sensitive content qualifies. The explicit rationale must address bounded impact, reversibility, and the absence of content-correctness, safety, privacy, medical, legal, financial, or permission effects. It moves human inspection to finish; it does not grant acceptance.

For a human-authorized Oracle stand-in, add `--approved-by oracle --authorization <grant.json> --stand-in-report <report> --stand-in-verdict PASS --checklist <checklist>`. The JSON grant names `id`, `granted_by: human`, `granted_at`, exact lane ID `scope` list, and the selected-change `plan_sha256` from status. No `all` scope. Keep grant, checklist, and independent report separate. Verify stand-in independence and record its identity. A grant is consumed once after successful integration; it cannot authorize PR merge.

An explicit lane override uses `lane-merge <lane> --human-override --override-head <candidate> --override-reason <human-decision> --waive <gate>`. Repeat `--waive` for exactly the authorized categories: `reviews`, `tasks`, `human-gate`, `ancestry`, `clean-trees`, `plan-approval`. No omitted gate is waived. Review/tick/evidence fabrication remains prohibited.

## Finish and status

```bash
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" finish-record classify --classification patch --artifact /absolute/preparation.md --input package.json --check tests --inspection output
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" finish-record capture --check tests --artifact /absolute/results.md --input src --input package.json
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" finish-record inspect --check output --artifact /absolute/human-inspection.md --input src
python3 "$FEATDEV_SKILL_ROOT/scripts/featdev.py" finish-record pr-check
```

Use repository-specific classifications, checks, and paths. `restage` updates preparation and retains only unaffected results; reuse the existing version explicitly with `--version` when applicable. Inspection artifacts contain the actual human's unique `inspector`, `checkpoint`, and `verdict` fields. A rejected inspection blocks readiness.

`status` never writes workflow records or grants decisions. `dashboard --output <path>` writes a rendered page; `--serve --port 8765 --refresh-seconds 2` serves a live view. Displays are monitors, not proof of consent.

Developer validation: `python3 scripts/featdev.py --self-check` runs isolated fixture tests and documentation link/command checks. It never launches agents or obtains real human approval.

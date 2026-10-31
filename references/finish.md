# Release preparation and PR follow-up

Begin after all lanes of every included OpenSpec change integrate. Finish is repository-level: one `.agents/workflow/finish.json` covers the PR. Do not pass `--change` to finish operations.

## Prepare and open the PR

1. Reconcile task completion, lane integrations, reviews, and human decisions. Summarize any waiver uses.
2. Obtain the human's release classification, or apply an explicit eligible advance waiver. A finish-scoped Implementer prepares versions and generated artifacts in a separate worktree. Integrate its reviewed preparation commit before final captures. Preparation-only work does not reopen implementation tasks or lanes.
3. Run the repository's required release checks and capture committed integrated inputs. Repository instructions define commands, environments, evidence, independent finish reviews, and human inspections. The skill imposes no platform-specific matrix.
4. Record preparation/classification, selected checks, evidence, and required direct human inspections using `finish-record`. A changed input renews affected results; unchanged declared inputs may retain valid results at descendant commits. Run `finish-record pr-check`.
5. Verify required finish reviews, plan authorization, inspections, and readiness yourself: the helper's PR check validates declared release artifacts, not all consent or reviewer judgment.
6. Obtain separate, one-time human consent to open the PR. Record the decision, human/source, timestamp, integrated SHA, and included changes in `.agents/workflow/finish/pr-follow-up.md`, then open it. A changed candidate before opening requires renewed opening consent; later fix/archive pushes do not. Opening consent never authorizes PR merge.

Required final hands-on inspections remain human-only. Oracle stand-ins and advance lane waivers do not replace them. Record finish reviews under `.agents/workflow/finish/reviews/`, with reviewer identity, verdict, base/candidate SHA, applicable criteria, evidence, limitations, and declared inputs. Without declared inputs, the review binds the exact SHA.

## PR comments and checks

Monitor required checks and expected reviews through the repository's stabilization window. Read failures and comments, deduplicate findings, and record each disposition with supporting evidence. Pending checks are not passing checks.

Resolve actionable findings through scoped implementation lanes and independent review. Corrections retain the applicable task, review, and human checkpoints. Human-requested planning changes follow [planning](plan.md) without another planning approval or Oracle review. If a bot finding needs a feature-scope decision, obtain the human's change request rather than silently expanding scope.

Push reviewed corrections, renew affected release validation, and keep monitoring. Record the PR URL, current SHA, check state, comment dispositions, outstanding issues, and consent in `pr-follow-up.md`. If monitoring must end early, explicitly hand the remaining work to the human.

## OpenSpec sync, archive, and human merge

After implementation findings are resolved, sync approved delta specifications into the baseline, validate, and archive every included completed change using the repository's OpenSpec procedure. Avoid applying already-synced deltas twice. Commit and push archival changes, then rerun specification checks and affected validation. Archived changes retain their identity in local workflow records.

Present the final PR SHA, checks, review dispositions, mergeability, and remaining limitations. Stop for the human's merge authorization and actual merge. Neither green automation nor agent review authorizes PR merge.

## Release record operations

- `classify`: after preparation integration, record the human classification, preparation receipt and committed inputs, all selected checks, and required inspection checkpoints.
- `capture`: record one actual check's evidence and complete tracked input dependencies.
- `inspect`: record one human checkpoint. The artifact contains unique `inspector: human`, `checkpoint: <id>`, and `verdict: accept` or `reject` fields. An agent never writes acceptance on the human's behalf.
- `restage`: after changed preparation or release contract, update the record and retain only results still valid for their declared inputs. Omit classification to reuse the recorded decision; pass the existing version when retaining it.
- `pr-check`: verify selected checks and inspections are present, valid, and unchanged. It does not open the PR or grant consent.

See [command syntax](helper-commands.md) and [record schemas](workflow-records.md).

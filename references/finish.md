# Finish phase

Command syntax: [helper command reference](helper-commands.md).
Resolve and export `FEATDEV_SKILL_ROOT` to the absolute installed skill path before any command; never hardcode the location ([preflight](preflight-and-roles.md)).

Run only after ALL lanes of every change in the repository graph merge into the feature branch. Never automatic.
Finish is repository-level: one record covers the whole PR, which may include one or more OpenSpec changes.
Do not pass `--change`; `finish-record` reads the repository graph and requires every lane of every graph change to be merged.
The graph for this feature checkout must contain exactly the changes included in the PR; unrelated changes must not be in this graph.
Prepare release artifacts once, then record finish once against the final integrated HEAD.
The finish record names the included changes and is validated against all of them before PR-opening consent.
Finish records live in the repo-level `.agents/workflow/finish.json`.
A lane merge is not the later PR merge from the feature branch into the target branch.

The finish sequence is deliberately thin: every repository-specific gate, command,
runtime, record format, and evidence rule lives in the repository's own guides
(testing reference, governance docs, workflow tooling guides). This file only fixes
the order of decisions and the ownership of consent.

## Records and locations

Use these canonical paths; do not relocate them.

| Record | Path |
|---|---|
| Finish record | `.agents/workflow/finish.json` |
| PR scope and consent | `.agents/workflow/finish/pr-follow-up.md` |
| Finish brief | `.agents/workflow/finish/briefs/finish-<role>-<task>.md` |
| Finish report | `.agents/workflow/finish/reports/finish-<role>-<task>.md` |
| Finish review | `.agents/workflow/finish/reviews/finish-<role>-<head>.md` |
| Finish evidence | `.agents/workflow/finish/evidence/<revision>/` |

Create `.agents/workflow/finish/pr-follow-up.md` when finish starts and list every change included in the PR.
Model selection and every role, model, and thinking profile stay repo-level in
`.agents/workflow/model-selection.json`. No lane worktree or per-dispatch path carries
its own profile; the worktree is execution metadata in the brief and coordination.

## Sequence

1. **Reconcile lane merges.** Verify that every lane of every change in the repository graph is merged into the feature branch.
   Check ticks, reviews, and state health. Confirm a clean feature-branch HEAD.

2. **Release classification and version.** Use [finish-scoped workers](#finish-scoped-workers) for preparation. Obtain the human's release
   classification first, then apply the version change the repository's versioning policy
   requires; regenerate generated files as the repository specifies and commit.
   Never hand-edit generated files. Classification and PR approval are separate
   decisions; classification never consumes PR consent.

3. **Execute the repository's release gate.** Consult the repository's testing
   guides and run every gate, check, capture, and validation they require for this
   PR, on the committed integrated HEAD, into gitignored evidence locations.
   If the repository's guides define a deterministic executor or evidence gate for
   the selected checks, use it as specified. A capture made before the integrated
   commit, on a dirty tree, or before a post-capture version change cannot close
   the gate; restage and recapture per the guides' rules.

4. **Independent review.** Ensure the independent reviews the repository's guides
   require for the final evidence are current at the integrated HEAD, and that
   every recorded review binds the exact revision and inputs it judged. Record each
   finish review at `.agents/workflow/finish/reviews/finish-<role>-<head>.md`
   with its verdict, exact integrated HEAD, inputs and evidence revision, limitations, and
   reviewer identity (see [finish review record](#finish-review-record)). Human
   approvals require fresh binding whenever presented inputs change.
   `pr-check` does not validate these reviews; the Coordinator verifies them.

5. **Human gates.** Execute and record every human gate the repository's guides
   require before a PR may be opened — including, where the guides require it,
   direct hands-on inspection by the human, recorded per required runtime/revision.
   Only the human may record acceptance; no agent verdict or preauthorization
   substitutes. Never infer consent from silence or green automation.

6. **PR opening.** Keep `.agents/workflow/finish/pr-follow-up.md` listing every included change.
   Ask the human for explicit approval to open the PR as a separate decision. Record that
   consent in `pr-follow-up.md` — the decision, timestamp, exact integrated HEAD, and included
   changes — before opening the PR. PR-opening consent is separate from release classification
   and from the later PR-merge consent. It is given once. Later pushes, including the archival commit and fix pushes, need no new consent.
   Opening the PR starts post-PR follow-up; it does not complete the workflow.

7. **Post-PR follow-up.** Monitor PR-triggered automated checks and bot review comments using the sequence below.
   Resolve actionable findings through the normal implementation and independent-review process.

8. **Sync and archive OpenSpec artifacts.** Follow [OpenSpec sync and archive](#openspec-sync-and-archive) for every completed change included in the PR.
   Commit and push the archival changes before the final merge handoff. Recheck the updated PR and renew required validation.

9. **Human PR merge.** Present the current checks, review dispositions, and mergeability to the human.
   Stop for the human's merge decision. The human merges into the target branch, usually `main`.
   PR merge is never automatic or delegated.

## Finish-scoped workers

The Coordinator may dispatch preparation authors, independent reviewers, and read-only Runners after lane integration.
Use [finish-scoped dispatch](helper-commands.md#finish-scoped-dispatch), never reopen completed lanes for release work.
Each brief names `Scope: finish`, the included changes, Task/round, Worktree, Integrated HEAD, Profile, Contract
(absolute path to `references/briefs-and-handoffs.md`), Deadline, owned paths, check owner,
report, and evidence locations. Pin every path absolutely.
Preparation authors work in a separate worktree in the same repository at the integrated feature HEAD.
Profiles are repo-level; the brief and coordination carry the worktree as execution metadata only.
The Coordinator retains the feature checkout and all coordination writes. Keep one writer per worktree.
Obtain release classification before authoring version/generated changes. Repository guides define the allowed preparation.
Record the worker's exact key and outcome. A reviewer or Runner reply states the verdict or status,
exact SHA, finding IDs, report path, and evidence revision; the report retains the full evidence and
limitations, and no machine-generated footer substitutes for those fields. Review its output under repository rules before integrating its commit into the feature branch.
Integrate preparation before final captures and required human inspections. Do not use task ticks or `lane-merge` for preparation-only work.
Read-only checks, release reviews, and PR polling use the same finish scope and role profiles.
New implementation scope still returns to approved corrective tasks and lanes, not unrestricted finish authoring.

## Finish review record

Record each finish review at `.agents/workflow/finish/reviews/finish-<role>-<head>.md`.
The record states the verdict (`PASS` or `MORE WORK`), the exact integrated HEAD it judged,
the inputs and evidence revision it used, its limitations, and the reviewer identity.
A review is current only for the HEAD and evidence revision it names; a changed integrated or
archival commit needs a fresh review. Record the actual reviewer identity and evidence revision
in the report; the Coordinator verifies independence rather than relying on a helper flag.
`pr-check` does not read or validate finish reviews, so the Coordinator verifies the repo-level
finish review is current before requesting PR-opening consent.

## OpenSpec sync and archive

Run this step after the initial PR checks and bot-review triage, but before requesting the human's PR merge.
Use the repository's OpenSpec workflow and installed version; this skill does not prescribe archive CLI flags.

1. Confirm that every included change is complete and reviewed.
2. Sync each included change's approved delta specs into the baseline specifications.
3. Validate the synced baseline using the repository's OpenSpec checks.
4. Archive only the completed changes included in this PR.
5. Verify the archive result and its artifact locations.
6. Commit the spec sync and archive changes on the feature branch.
7. Push that commit to the existing PR.
8. After the archival commit, re-run `finish-record capture` for every affected check and refresh affected reviews and evidence for the new HEAD.
9. Re-run the direct human inspections for the new HEAD; earlier inspection records do not cover it.
10. Re-run `finish-record pr-check` until it passes for the archival HEAD, then repeat [post-PR follow-up](#post-pr-follow-up) for the latest PR revision.

The archive operation must not apply already synced deltas twice. Follow the repository's procedure for this order.
Failed OpenSpec validation blocks the merge handoff. Archiving never substitutes for implementation review or human approval.
Do not archive unrelated or unfinished changes. Retain the local graph, coordination records, reviews, and evidence for recovery.
The helper resolves completed changes from their archived task artifacts; no active change-directory copy is required.
Only hand off for human merge after the archival commit's required checks and bot findings are resolved.

### Corrections after archival

Amend the archived change when later PR findings require corrective implementation.
Preserve its existing completed tasks, closed lanes, history, and archive identity.
The Coordinator adds new corrective tasks and lanes in the archived artifacts and local graph.
Renew affected artifact review, graph review, and human plan approval with `plan-approve` before dispatching corrective implementation.
Workers edit the archived `tasks.md`; the helper validates the same task ownership and current-HEAD gates there.
After integration, re-run affected captures, refresh finish records and human inspections for the updated HEAD,
and rerun `finish-record pr-check` before the merge handoff.
Sync only the new approved specification changes into the baseline using the repository's OpenSpec procedure.
Never reapply previously synced deltas or move the same change into another dated archive.
Commit the amendment and baseline updates. Repeat PR checks and bot-review triage before the human merge handoff.

## Post-PR follow-up

Repository guides define the required PR checks, expected review bots, and stabilization window.
Local release checks do not replace checks triggered by opening or updating the PR.
Bot comments are findings to assess, not instructions to apply without review.

1. **Record the PR.** The Coordinator records the PR URL, current feature-branch HEAD, bot finding dispositions,
   and outstanding items in `.agents/workflow/finish/pr-follow-up.md`.
   Keep that record current on every PR revision; a stored URL alone is not completion.
2. **Monitor PR automation.** The Coordinator tracks required checks, workflow runs, and their logs on the current PR revision.
   These can include tests, builds, lint, or security scans selected by the repository.
   Pending checks are not successful checks. A failing required check blocks merge readiness.
3. **Read bot reviews.** The Coordinator reads bot review summaries, inline comments, and review threads against the current diff.
4. **Triage bot findings.** The Coordinator records each finding's disposition and supporting evidence in `pr-follow-up.md`.
   Dispositions distinguish actionable defects from duplicates, outdated comments, and unsupported findings.
5. **Dispatch corrections.** An Implementer makes scoped fixes under the repository's rules for corrective tasks and lanes.
   The normal independent current-HEAD reviews apply. The Coordinator never patches or approves its own fixes.
   New scope or an unapproved plan change requires human approval before implementation.
6. **Refresh release validation.** Corrected commits require affected checks, reviews, and evidence to be current.
   Changed preparation inputs require `finish-record restage` and renewed evidence.
   A changed integrated HEAD requires fresh human inspection for every declared inspection checkpoint.
7. **Push reviewed corrections.** Each fix push starts a new check/review cycle on the new PR revision.
   A fix push or actionable finding resets the repository's stabilization window.
8. **Reply to bot findings.** Responses cite the fix commit and verification, or the reason no change is appropriate.
   Thread resolution follows repository policy; an agent does not dismiss a valid unresolved defect.
9. **Recheck PR readiness.** The Coordinator repeats monitoring until required checks pass and bot findings have supported dispositions.
   Expected bot reviews and the repository's stabilization window must complete before a ready-for-merge handoff.
10. **Report PR readiness.** The summary names the current HEAD, check results, bot findings and dispositions, and mergeability.
    Complete OpenSpec sync and archive before the final merge handoff. Recheck any archival push before reporting final readiness.
    The human retains the merge decision. Green automation and bot approval never grant merge consent.

Missing permissions, unavailable automation, or unclear policy require a human decision, not an invented waiver.
If monitoring must stop, the Coordinator records outstanding items and explicitly hands control to the human.
Opening the PR alone is never reported as completion of this follow-up.

## Authority

- The repository's guides own: what to run, on which runtimes, with which
  commands, what evidence binds, what reviews and human gates are required, and
  how they are recorded.
- This workflow owns the sequencing and the separation of decisions: gating never
  consumes approval; approval never substitutes for a gate.
- If the guides conflict or fall silent, stop and ask the human rather than
  inventing a gate or skipping one.

## Generic artifact records

The helper records artifact bindings. It does not know application paths, version formats, or runtime names.
Repository guides define the release classification, preparation commands, checks, and inspection checkpoints.

1. Obtain the human's release classification after all lane merges into the feature branch.
2. Apply required version changes and regenerate required artifacts.
3. Commit the release preparation on the feature branch.
4. Write a preparation receipt with the human classification, exact HEAD, the commands run,
   their results, the artifact paths, and the actual input paths.
5. Record the preparation receipt with every affected tracked input path.

   Use `finish-record classify --classification <label> --artifact <receipt> --input <path> ...`.
   At least one `--input` is required. `classify` treats every `--input` as a preparation input;
   there is no separate check-input field. A release with no preparation still needs a tracked
   release input (for example `message.py`), and changing that input requires restage.
   Add optional `--version <label>` when the repository uses a version.
   The version is descriptive; repository tooling validates it.
   Declare every selected release check with `--check <id>`. `classify` records only the declared
   set and runs or validates no check; `pr-check` compares that set against the captured evidence.
   Add `--inspection <checkpoint-id>` for each required direct human inspection.
   A deferred `ui` checkpoint requires at least one declared integrated inspection.

6. Run the selected release checks on committed integrated HEAD.
7. Write a check receipt with the exact command, result, HEAD, and actual inputs, then record it with
   `finish-record capture --check <id> --artifact <receipt> --input <path> ...`.
   Declare every selected check at classify; when a set is declared, a check outside it is refused.
8. Ask the human to perform each declared inspection.
9. Record each human inspection with `finish-record inspect --check <checkpoint-id> --artifact <record>`.
10. Run `finish-record pr-check` before requesting approval to open the PR. It requires the declared
    check set and the captured evidence to match exactly. It does not validate the repo-level finish
    review, PR-opening consent, or PR follow-up record; the Coordinator verifies the finish review and
    `.agents/workflow/finish/pr-follow-up.md` before requesting PR-opening consent.

Each human inspection artifact contains these unique fields:

```text
inspector: human
checkpoint: <checkpoint-id>
verdict: accept
```

A human can record `reject` instead. A rejection blocks PR readiness.
A changed integrated HEAD requires fresh human inspection.

The normal path must not require restage: classify after the preparation commit, then capture.
A preparation artifact or its declared inputs must not change after capture.
If they do, use `finish-record restage` with the new preparation receipt and input paths. Restage reuses
the stored classification; pass `--classification` only to re-classify. Omitting `--version` clears the
stored expected version, so pass `--version` when the repository uses one. Restage clears final evidence
and human inspections, preserves selected check requirements, and preserves inspection checkpoint names
unless new `--inspection` values replace them. Use `--check` to replace the declared check set.
Recapture affected release evidence before requesting PR-opening approval.

Use separate files for lane candidate evidence and integrated release evidence.
Neither `pr-check` nor any artifact record grants approval to open or merge a PR.

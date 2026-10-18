# Finish phase

Run only after ALL lanes merge into the feature branch. Never automatic.
This applies to standard and combined review modes.
A lane merge is not the later PR merge from the feature branch into the target branch.

The finish sequence is deliberately thin: every repository-specific gate, command,
runtime, record format, and evidence rule lives in the repository's own guides
(testing reference, governance docs, workflow tooling guides). This file only fixes
the order of decisions and the ownership of consent.

## Sequence

1. **Reconcile lane merges.** Verify that every reviewed lane is merged into the feature branch.
   Check ticks, reviews, and state health. Confirm a clean feature-branch HEAD.

2. **Release classification and version.** Obtain the human's release
   classification and apply the version change the repository's versioning policy
   requires; regenerate generated files as the repository specifies and commit.
   Never hand-edit generated files. Classification and PR approval are separate
   decisions; classification never consumes PR consent.

3. **Execute the repository's release gate.** Consult the repository's testing
   guides and run every gate, check, capture, and validation they require for this
   change, on the committed integrated HEAD, into gitignored evidence locations.
   If the repository's guides define a deterministic executor or evidence gate for
   the selected checks, use it as specified. A capture made before the integrated
   commit, on a dirty tree, or before a post-capture version change cannot close
   the gate; restage and recapture per the guides' rules.

4. **Independent review.** Ensure the independent reviews the repository's guides
   require for the final evidence are current at the integrated HEAD, and that
   every recorded review binds the exact revision and inputs it judged. Human
   approvals require fresh binding whenever presented inputs change.

5. **Human gates.** Execute and record every human gate the repository's guides
   require before a PR may be opened — including, where the guides require it,
   direct hands-on inspection by the human, recorded per required runtime/revision.
   Only the human may record acceptance; no agent verdict or preauthorization
   substitutes. Never infer consent from silence or green automation.

6. **PR opening and PR merge.** Ask the human for explicit approval to
   open the PR as a separate decision. Open and babysit per the repository's PR
   policy (watch checks/comments/mergeability for the stabilization window the
   baseline workflow defines; triage bot findings; reset the window on fix pushes
   or actionable findings). Stop for an interactive human merge decision; merge is
   never automatic or delegated. The human merges the PR into the target branch, usually `main`.

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
4. Write a preparation receipt with the human classification, commands, results, and artifact paths.
5. Record the preparation receipt with every affected tracked input path.

   Use `finish-record classify --classification <label> --artifact <receipt> --input <path> ...`.
   Add optional `--version <label>` when the repository uses a version.
   The version is descriptive; repository tooling validates it.
   Add `--inspection <checkpoint-id>` for each required direct human inspection.
   A deferred UI checkpoint requires at least one declared integrated inspection.

6. Run the selected release checks on committed integrated HEAD.
7. Record each result with `finish-record capture --check <id> --artifact <receipt> --input <path> ...`.
8. Ask the human to perform each declared inspection.
9. Record each human inspection with `finish-record inspect --check <checkpoint-id> --artifact <record>`.
10. Run `finish-record pr-check` before requesting approval to open the PR.

Each human inspection artifact contains these unique fields:

```text
inspector: human
checkpoint: <checkpoint-id>
verdict: accept
```

A human can record `reject` instead. A rejection blocks PR readiness.
A changed integrated HEAD requires fresh human inspection.

A preparation artifact or its declared inputs must not change after capture.
Use `finish-record restage` with the new preparation receipt and input paths after such a change.
Restage clears final evidence and human inspections. It preserves selected check requirements.
Restage preserves inspection checkpoint names unless new `--inspection` values replace them.
Recapture affected release evidence before requesting PR-opening approval.

Use separate files for lane candidate evidence and integrated release evidence.
Neither `pr-check` nor any artifact record grants approval to open or merge a PR.

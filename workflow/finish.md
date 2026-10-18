# Finish phase

Run only after ALL lanes have merged. Never automatic.

The finish sequence is deliberately thin: every repository-specific gate, command,
runtime, record format, and evidence rule lives in the repository's own guides
(testing reference, governance docs, workflow tooling guides). This file only fixes
the order of decisions and the ownership of consent.

## Sequence

1. **Reconcile and integrate.** Verify all lane merges, ticks, reviews, and state
   health; confirm a clean integrated HEAD.

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

6. **PR approval, babysitting, merge.** Ask the human for explicit approval to
   open the PR as a separate decision. Open and babysit per the repository's PR
   policy (watch checks/comments/mergeability for the stabilization window the
   baseline workflow defines; triage bot findings; reset the window on fix pushes
   or actionable findings). Stop for an interactive human merge decision; merge is
   never automatic or delegated.

## Authority

- The repository's guides own: what to run, on which runtimes, with which
  commands, what evidence binds, what reviews and human gates are required, and
  how they are recorded.
- This workflow owns the sequencing and the separation of decisions: gating never
  consumes approval; approval never substitutes for a gate.
- If the guides conflict or fall silent, stop and ask the human rather than
  inventing a gate or skipping one.

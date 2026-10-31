# Implementation lanes

Open a lane only after initial plan authorization, with committed planning artifacts and satisfied dependencies. Keep its branch and worktree separate from the Coordinator's feature checkout. One writer owns a lane at a time; independent lanes may run concurrently.

## Implement

Give an Implementer the assigned tasks, owned paths, acceptance criteria, dependencies, checks, and worktree. Record `dispatch` before launch and `dispatch-done` after observing its actual outcome. Related tasks and corrections may share a session; keep task-level dispatch records and accurate ownership.

Implementers run focused checks, mark only assigned task checkboxes, and commit work and ticks in the lane. Plan changes originate from the human and are coordinated under [planning](plan.md). Workers do not silently rewrite task definitions or another lane's completion state.

Use concise reports with commits, checks, evidence, findings, and remaining limitations. On interruption, determine the prior worker's status before replacement. See [recovery](recovery.md).

## Independent review

Start when all assigned tasks are implemented and committed and the lane is clean.

| Lane | Required reviewers |
|---|---|
| Ordinary risk | `code-reviewer`: implementation, completeness, and test adequacy |
| High risk | `code-reviewer` plus separate `test-reviewer` |
| UI | Add `ui-reviewer` with current rendered evidence |

Reviewers stay read-only, review the assigned acceptance criteria and scoped diff, and return `PASS` or `MORE WORK`. Report material issues with location, failure scenario, and closure criteria. Record verdicts using `review` against the actual candidate SHA. Runner observations do not satisfy reviews.

Collect findings into a coherent correction batch. Authors correct them; independent reviewers verify closure. After a changed candidate, affected roles review corrections and regressions; unaffected roles may perform a short impact check and return a fresh current-HEAD verdict. There is no fixed review-round count or mandatory Oracle mediation. Seek consultation or human guidance when corrections stall, requirements conflict, or a decision exceeds scope.

Repository guides define checks and environments. Assign one execution owner to expensive checks or captures and reuse valid results under their evidence rules. Evidence names the producing commit and relevant inputs. A UI `PASS` requires current evidence. Changed inputs invalidate affected results.

## Human decisions

Keep the existing human checkpoints: a `ui` or `sensitive` lane requires human acceptance; high risk alone does not. Bind the actual decision to the candidate commit and evidence revision through `human-gate`. UI acceptance needs current rendered evidence. Silence and green checks are not approval.

The human may explicitly:

- **Waive human gates in advance.** Name gates and exact `change/lane` scope or all lanes, limits, and expiry. Record in `coordination.json.advance_waivers` and identify each use. This may cover intake, model selection, initial plan approval, lane checkpoints, escalation decisions, and release classification. It never replaces technical reviews, required final inspections, PR-opening consent, or PR merge. Record plan waivers through `plan-approve --waiver`; use a lane override for waived lane checkpoints. Revocation takes effect before further use.
- **Defer an eligible checkpoint.** Only ordinary-risk UI work without a sensitive flag qualifies. Record a bounded, reversible rationale and excluded sensitive effects with `human-gate --state deferred-to-integrated`. Finish must include appropriate direct human inspections. Deferral does not waive independent review.
- **Authorize an Oracle stand-in.** A recorded grant names exact lane IDs, checkpoint, and selected-change plan digest. An independent Oracle inspects the acceptance checklist and evidence and returns an actual `PASS`. Record its identity and report; consume the grant once. Changed inputs or revocation require renewed authority. Final release inspections and PR merge remain human-only.
- **Override lane gates.** Explicitly name lane, candidate SHA, intended merge, reason, and waived duties. Record through `lane-merge --human-override`; waive only named gates. A waived review is not a `PASS`, and an unfinished task remains unfinished. Override does not authorize discarding changes or bypass final release/PR gates.

## Integrate and close

Before `lane-merge`, verify current plan authorization, current independent passes, task completion, required evidence, and the applicable human checkpoint or explicit override. Verify every review report and its evidence; the helper rechecks UI evidence but cannot substitute for review judgment or verify human identity.

The helper checks worktree/ref consistency, clean trees, ancestry, task ownership, candidate ticks, and review/gate bindings. Merge preserves the reviewed candidate SHA. If integration conflicts, resolve them in the lane worktree, commit, obtain fresh affected reviews and gates, then retry. The Coordinator does not patch implementation conflicts in the feature checkout.

Close only after a recorded integration, using `lane-close`. Preserve recorded worktree locations during recovery. When every included lane is integrated, proceed to [finish](finish.md).

# Recovery and breaking migrations

Use when resuming interrupted work, worker ownership is uncertain, records are missing or incompatible, or an operation partially completed. Pause affected actions, reconcile records with Git and workers, and resume the first incomplete operation. Do not replay completed implementation or successful integrations.

## Coordinator reconstruction authority

The Coordinator may create, repair, or replace `coordination.json` and any other gitignored workflow artifact using the [current schemas](workflow-records.md), including across breaking changes. No dedicated migration command, legacy compatibility layer, or separate human migration approval is required.

Before replacement:

1. Establish ownership of affected workers and pause them or obtain their release. Never create a second writer because a record disappeared.
2. Back up the existing `.agents/workflow/` tree outside the files being replaced.
3. Inspect Git history, committed OpenSpec artifacts and ticks, branches/worktrees, surviving reports and evidence, live worker state, and explicit human decisions available in the session.
4. Rebuild current-schema records from those sources. Preserve actual worktree locations, integrations, review history, grant consumption/revocation, waiver limits, PR consent, and outstanding findings when supported.
5. Add reconstruction provenance: source paths or decision references, date, transformations, and unresolved unknowns. Run `plan-validate --workflow` and inspect `status` before resuming.

A schema change alone does not invalidate a supported decision, review, or check. Translate its binding only when the underlying candidate, scope, criteria, and evidence remain equivalent. Reconstruct the initial combined Oracle review from surviving evidence; legacy artifact and graph reports together may support it. Preserve those sources and explain the conversion. Do not claim a new Oracle review occurred.

Unknown is a valid reconstruction result. Omit an unsupported approval or verdict and list the gap; never invent a `PASS`, consent, evidence, or worker completion. Explicit human decisions in the current session are usable sources. Code, checkboxes, or ancestry alone cannot establish a review verdict or human approval. Obtain missing judgments only when needed for the next action.

## Interrupted operations

- For a missing dispatch, inspect the actual worker outcome and ownership before recording it or retrying.
- For an uncertain merge, compare recorded candidate and feature ancestry. Preserve a successful integration rather than merging again; unreviewed conflict resolutions still need lane review.
- For missing evidence, recapture only what cannot be supported by surviving evidence under repository rules.
- For an incompatible record, reconstruct it directly rather than forcing the old schema through current commands.
- For human-requested plan changes, record the request and current planning inputs. Do not request repeated planning approval or Oracle review after the initial `PASS`.
- For release records, preserve results with unchanged declared inputs; restage and renew only affected validation. PR-opening consent remains separate from merge consent.

If worker ownership or a required fact cannot be resolved, stop affected work and explain the specific gap. Independent ready work may continue.

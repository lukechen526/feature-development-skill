# Planning

The Coordinator clarifies the human's intent and builds the lane plan; it does not author or edit OpenSpec change content. Dispatch an Oracle with bounded write authority to author the OpenSpec artifacts. Read applicable repository instructions, resolve material ambiguity, and reuse a matching change rather than duplicating it. Use OpenSpec's available creation procedure when a new change is needed.

## Initial plan

Confirm the intended behavior, scope, and non-goals with the human before authoring, unless already established or explicitly waived.

Have the authoring Oracle create or update proposal, design, delta specifications, and `tasks.md` for each included change. Its brief must identify the requested behavior, owned change paths, exclusions, and applicable repository instructions. Make behavior, acceptance criteria, dependencies, and test expectations clear enough to implement and review.

Build `.agents/workflow/plan-graph.json` using the [current schema](workflow-records.md). Group cohesive tasks into lanes according to ownership, dependencies, risk, and reviewability. Parallelism is optional. Validate the graph and its task mapping with `plan-validate`.

After authorship completes, dispatch a different, independent, read-only Oracle with both the artifacts and entire lane plan. Its remit includes requirements, testability, coverage, ownership conflicts, dependencies, and risk/UI/sensitive flags. Use `dispatch --scope plan oracle --task plan`, then record the actual verdict through `review --scope plan oracle ...`.

Route OpenSpec `MORE WORK` findings back to the authoring Oracle, correct Coordinator-owned lane records as needed, and repeat the combined review until the first `PASS`. There is no fixed round limit or mandatory authoring escalation; seek help when progress stalls or requirements conflict. The Oracle never approves its own authored work.

Present the plan for human approval, commit the OpenSpec artifacts, then record that decision with `plan-approve`. An explicitly waived initial approval uses `plan-approve --waiver` with its lane scope; Oracle review is still required. No implementation begins before the initial review and human authorization.

## Later human-requested changes

After the initial Oracle `PASS`, do not require Oracle re-review for subsequent artifact or graph edits. After initial human approval, the human's change request is authorization to update the plan: no second plan approval is needed.

Have the authoring Oracle update the affected OpenSpec artifacts, then commit them, update the graph as needed, and append the initiating human request and current planning inputs to `coordination.json`'s `plan_amendments`. This records authorization already given, not a new approval. Task checkbox edits alone do not change planning inputs. See [amendment records](workflow-records.md#planning-review-and-authorization).

Revalidate structure and reconcile affected lanes before more implementation. Changed task definitions must reach active worktrees. Renew affected lane reviews, evidence, and human checkpoints; unchanged lanes retain valid results. Do not silently expand the human's requested feature.

The graph, coordination, review ledgers, and authorization records stay gitignored. OpenSpec artifacts and task completion ticks are committed.

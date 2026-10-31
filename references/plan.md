# Planning

The Coordinator turns the human's intent into OpenSpec artifacts and a lane plan. Read applicable repository instructions, resolve material ambiguity, and reuse a matching change rather than duplicating it. Use OpenSpec's available creation procedure when a new change is needed.

## Initial plan

Confirm the intended behavior, scope, and non-goals with the human before authoring, unless already established or explicitly waived.

Author proposal, design, delta specifications, and `tasks.md` for each included change. Make behavior, acceptance criteria, dependencies, and test expectations clear enough to implement and review.

Build `.agents/workflow/plan-graph.json` using the [current schema](workflow-records.md). Group cohesive tasks into lanes according to ownership, dependencies, risk, and reviewability. Parallelism is optional. Validate the graph and its task mapping with `plan-validate`.

Dispatch one independent Oracle with both the artifacts and entire lane plan. Its remit includes requirements, testability, coverage, ownership conflicts, dependencies, and risk/UI/sensitive flags. Use `dispatch --scope plan oracle --task plan`, then record the actual verdict through `review --scope plan oracle ...`.

Resolve `MORE WORK` findings and repeat the combined review until the first `PASS`. There is no fixed round limit or mandatory authoring escalation; seek help when progress stalls or requirements conflict. The Oracle never approves its own authored work.

Present the plan for human approval, commit the OpenSpec artifacts, then record that decision with `plan-approve`. An explicitly waived initial approval uses `plan-approve --waiver` with its lane scope; Oracle review is still required. No implementation begins before the initial review and human authorization.

## Later human-requested changes

After the initial Oracle `PASS`, do not require Oracle re-review for subsequent artifact or graph edits. After initial human approval, the human's change request is authorization to update the plan: no second plan approval is needed.

Update and commit the affected OpenSpec artifacts, update the graph as needed, and append the initiating human request and current planning inputs to `coordination.json`'s `plan_amendments`. This records authorization already given, not a new approval. Task checkbox edits alone do not change planning inputs. See [amendment records](workflow-records.md#planning-review-and-authorization).

Revalidate structure and reconcile affected lanes before more implementation. Changed task definitions must reach active worktrees. Renew affected lane reviews, evidence, and human checkpoints; unchanged lanes retain valid results. Do not silently expand the human's requested feature.

The graph, coordination, review ledgers, and authorization records stay gitignored. OpenSpec artifacts and task completion ticks are committed.

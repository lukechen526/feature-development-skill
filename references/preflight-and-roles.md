# Roles and capabilities

Discover applicable repository instructions and confirm Git, Python 3.9+, OpenSpec (`openspec --version`), and a harness capable of scoped subagent launches. Resolve the installed skill root and export `FEATDEV_SKILL_ROOT`; run coordinator helper commands from the consuming feature checkout.

Use the harness's supported launch, model selection, working-directory, message, wait, and stop tools. Verify workers operate in the intended worktree and receive their role and scoped brief. Prefer fresh context for independent reviews; reuse coherent implementation sessions for related tasks and corrections. A launch must provide the intended scope and role boundaries. Report a missing capability rather than pretending it exists.

## Human model selection

Ask the human for model and thinking choices for each role once per repository, unless already specified or explicitly waived. The human may select the same settings for several roles or delegate the choice. Offer supported settings; obtain acknowledgement if the harness cannot honor a requested selection. Reuse the recorded choices until changed or unavailable. Keep selection repo-level, with worktree paths in execution records rather than role profiles.

| Role | Responsibility |
|---|---|
| Coordinator | Current session: planning, ownership, records, integration, and human gates |
| [Implementer](../assets/implementer.md) | Scoped implementation, tests, task ticks, and commits |
| [Code reviewer](../assets/code-reviewer.md) | Correctness and task completeness; test adequacy for ordinary lanes |
| [Test reviewer](../assets/test-reviewer.md) | Separate test adequacy review for high-risk lanes |
| [UI reviewer](../assets/ui-reviewer.md) | Review supplied rendered UI evidence |
| [Oracle](../assets/oracle.md) | Initial combined planning review; optional technical consultation |
| [Runner](../assets/runner.md) | Execute assigned checks or monitor results; no review verdicts |

Record a short capability summary in `preflight.json` and actual role selections in `model-selection.json`, using the [current schemas](workflow-records.md). No exhaustive capability matrix, prompt hash, provisioning sequence, or per-dispatch profile is required.

Record dispatch intent before launch. Use completion events where supported and choose deadlines appropriate to the work and harness. On interruption, establish whether the prior worker still owns the worktree before replacing it. One writer per lane; no overlapping execution that races a review or capture.

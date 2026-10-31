# Feature Development Skill

An agent skill for taking an OpenSpec feature from an idea to a reviewed pull request.

The workflow has six stages:

1. Draft specifications, acceptance criteria, tasks, and an implementation lane plan.
2. Obtain a combined Oracle review until the initial plan passes, then human approval.
3. Implement in isolated lanes, obtain independent reviews, and integrate.
4. Prepare the release and obtain separate consent to open the PR.
5. Address PR checks and comments, then sync and archive completed specifications.
6. Hand the ready PR to the human for the merge decision and merge.

After the initial Oracle `PASS` and human plan approval, human-requested planning changes need neither review again nor another plan approval. The Coordinator records the request and updates the planning artifacts. Affected implementation reviews, evidence, and lane checkpoints still apply.

The Coordinator manages the workflow; separate implementation agents write code and independent reviewers assess it. Related tasks may share a session. Review correction loops follow progress and risk rather than a fixed round count.

## Use

Install this skill in your coding-agent harness and invoke it from the project you want to change:

> Use feature-development-skill to add saved searches. Clarify the expected behavior, prepare the OpenSpec artifacts and lanes, obtain the initial Oracle review, and ask for my plan approval before implementation.

For existing work:

> Use feature-development-skill to resume saved-searches. Reconcile the records, worktrees, and worker ownership, reconstruct older records using the current schema if needed, and continue from the first incomplete operation.

The helper has 12 commands for validation, dispatch, reviews, decisions, worktrees, status, the optional dashboard, and release records. It does not launch agents, perform reviews, grant permission, or open or merge PRs.

Gitignored records can be reconstructed by the Coordinator during breaking migrations. Supported human decisions and review evidence survive schema changes; uncertainty remains explicit. The CLI intentionally removes retired commands rather than maintaining compatibility aliases.

Start at [SKILL.md](SKILL.md). See the [command reference](references/helper-commands.md), [record schemas](references/workflow-records.md), and [worked example](references/minimal-workflow.md).

Licensed under [MIT](LICENSE).

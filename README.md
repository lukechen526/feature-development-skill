# Feature Development Skill

**Build new features with spec-driven development and a coordinated multi-agent workflow.**

This [Agent Skill](https://agentskills.io/specification) helps your coding agent take a feature from an idea to a reviewed pull request.

It uses OpenSpec to define the expected behavior, design, acceptance criteria, and tasks before implementation starts. A Coordinator assigns scoped work to implementation agents and separate review agents. Implementation happens in isolated Git worktrees. You approve the plan, make release decisions, and retain the final PR merge decision.

Install the skill in your coding agent. Run it inside the project you want to change, not inside this skill repository.

## When to use it

Use this skill when a feature needs more coordination than a single edit session:

- The feature spans several parts of a project, such as storage, an API, and a user interface.
- Tasks have dependencies or need separate owners without overlapping writes.
- You want independent review, rather than the implementation agent approving its own work.
- You need explicit approval checkpoints and recorded test or UI evidence.
- Work may span several sessions, so progress, decisions, and recovery must be recorded.

It works for backend, frontend, CLI, and other Git-based projects.
The project's own guides define its technology, test commands, release process, and required environments.

Skip this skill for a quick explanation, a small isolated fix, or work that does not need planning and review gates.
It adds deliberate coordination overhead. It is not an unattended system that ships changes without human approval.

## How the workflow works

**Spec-driven development** means agreeing on what the feature must do before agents write its implementation.
The reviewed specification and task checklist remain the reference for implementation, tests, and review.

| Stage | What happens |
|---|---|
| Set up | The Coordinator reads project instructions, checks the agent harness, and asks you to select models and thinking levels. |
| Specify and plan | OpenSpec artifacts define the feature. A lane plan assigns tasks, dependencies, owners, and risk flags. |
| Review and approve | Separate Oracle agents review each change's artifacts, then the repository plan. You approve both before implementation. |
| Implement | Fresh implementation agents complete scoped tasks in isolated Git worktrees. Independent lanes can progress without sharing a working directory. |
| Review and integrate | Separate reviewers check the current implementation and relevant tests. UI work adds rendered-evidence review and human checkpoints. Passing lanes merge into the feature branch. |
| Prepare the PR | Workers prepare release artifacts and evidence under project rules. You complete required inspections and separately approve opening the PR. |
| Follow through | The Coordinator monitors PR checks and bot findings, routes fixes through review, and syncs and archives completed OpenSpec changes. You decide whether to merge. |

A **lane** groups related tasks in one isolated worktree. Lanes are an ownership boundary, not a requirement to parallelize everything.
An **Oracle** is a specialist agent for planning review, mediation, and explicitly authorized corrections.
The Coordinator manages the workflow; it does not patch implementation or approve its own work.

### Implementation stays in isolated worktrees

Each implementation lane gets its own Git branch and worktree: a separate working directory for that lane's changes.
Implementation agents edit files, run focused checks, update task checkboxes, and commit inside that worktree.
Tasks in the same lane reuse its worktree, with only one writer at a time.
Independent lanes can run in parallel when their file ownership permits it.

The Coordinator keeps the feature checkout separate.
It merges reviewed lane commits into the feature branch only after the required reviews and human checkpoints pass.
Workers do not apply implementation patches directly to that checkout.
Release-preparation authors also use a separate worktree after lane integration.

### What you get

- Committed specifications, task completion marks, and reviewed implementation.
- Local records of task ownership, agent outcomes, reviews, approvals, and release evidence.
- A progress dashboard showing task and lane state, reviews, and blockers.
- A recovery path that resumes incomplete work instead of restarting completed tasks.

The Python helper records decisions and checks their bindings. It does not launch agents, perform reviews, or grant approval.
The coding-agent harness launches the agents. No helper command opens or merges a PR.

## Use

1. Open your project's feature checkout in your coding-agent harness.
2. Ask the agent to use `feature-development-skill` with a feature request or an existing OpenSpec change.
3. Answer its questions about requirements, models, and approvals.
4. Review the plan before authorizing implementation.

You do not need to launch each agent manually or memorize the helper commands.
The Coordinator starts at [SKILL.md](SKILL.md) and follows the phase guides.
It stops at required human decisions; a passing test is not your approval.

### Example: build a feature across several layers

> Use feature-development-skill to add saved searches.
> Users should be able to name, reopen, and delete their saved searches.
> Clarify ownership and access rules with me before implementation.
> Define the OpenSpec requirements and acceptance criteria.
> Propose tasks and lanes for the relevant storage, API, and UI changes.
> Complete the required planning reviews.
> Ask for my approval before implementation.

This is a good fit because one feature spans several parts of the project.
The Coordinator discovers the existing architecture and assigns the required implementation, test, and UI reviews.
It does not assume a framework or database.

### Example: build a backend-only feature

> Use feature-development-skill to add a CSV export for reports.
> Agree on permissions, exported fields, and size limits with me in the spec.
> Follow this repository's test and release rules.
> Complete the required planning reviews.
> Ask for my approval before implementation.

UI review is not required when the approved plan has no UI work.
Review duties follow the plan's risk and UI flags, not a fixed application template.

### Example: implement an existing specification

> Use feature-development-skill for the existing OpenSpec change saved-searches.
> Read its proposal, design, specs, and tasks.
> Resolve any gaps in the specification.
> Complete the planning reviews.
> Ask for my approval before implementation.

The Coordinator reuses the change rather than creating a duplicate.

### Example: resume interrupted work

> Use feature-development-skill to resume saved-searches.
> Reconcile the local records and surviving worktrees.
> Show completed tasks, pending reviews, and blockers.
> Continue from the first incomplete operation without restarting completed work.

Resume from the same feature checkout with its local workflow records intact.
Git alone cannot reconstruct lost reviews, approvals, or the local lane plan.

### Monitor progress

> Show the progress dashboard for saved-searches.
> Summarize anything waiting for my decision.

The [dashboard and status commands](workflow/helper-commands.md#status-and-task-status) show progress from local records.
The dashboard is a monitor, not proof of approval.
For the command-level lifecycle, see the [worked example](examples/minimal-workflow.md).

Licensed under [MIT](LICENSE).

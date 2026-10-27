# Feature Development Skill

**Build new features with spec-driven development and a coordinated multi-agent workflow.**

This [Agent Skill](https://agentskills.io/specification) helps your coding agent take a feature from an idea to a reviewed pull request.

It uses OpenSpec to define the expected behavior, design, acceptance criteria, and tasks before implementation starts. A Coordinator assigns scoped work to implementation agents and separate review agents in isolated Git worktrees.

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

Skip this skill for a quick explanation, a small isolated fix, or work that needs no planning and review gates.
It adds deliberate coordination overhead and is not an unattended system.

## How the workflow works

**Spec-driven development** means agreeing on what the feature must do before agents write its implementation; the reviewed specification and task checklist stay the reference for implementation, tests, and review.

Four gated phases:

1. **Plan** — turn your request into OpenSpec artifacts, then a lane plan; two separate Oracle reviews; then your approval.
2. **Lanes** — one task per fresh implementer in an isolated Git worktree; independent review; gated merge into the feature branch.
3. **Finish** — you set the release classification; one repository-level finish record covers the whole PR under the repository's own evidence, review, and inspection rules.
4. **Follow-up** — PR consent, findings, and archive recorded and rechecked.

You approve the plan, set the release classification, consent to opening the PR, and decide the merge.
A passing test is not your approval.

You can explicitly waive selected human gates in advance for named lanes or all lanes. The workflow distinguishes waivers from approvals in its records and keeps the pre-PR check and PR consent. See [advance human waiver](references/lane.md#advance-human-waiver).

A **lane** groups related tasks in one worktree: an ownership boundary, not a demand to parallelize everything. An **Oracle** is a specialist agent for planning review, mediation, and explicitly authorized corrections. The Coordinator manages the workflow; it does not patch implementation or approve its own work.

Phase detail and gates: [SKILL.md](SKILL.md).

### Implementation stays in isolated worktrees

Each lane gets its own Git branch and worktree. Implementers edit files, run focused checks, tick tasks, and commit there: one writer at a time, and independent lanes can run in parallel when their file ownership permits.

The Coordinator keeps the feature checkout separate and merges reviewed lane commits only after the required reviews and human checkpoints pass. Workers never patch it directly; release preparation also uses a separate worktree.

### What you get

- Committed specifications, task completion marks, and reviewed implementation.
- Local records of task ownership, agent outcomes, reviews, approvals, and release evidence.
- A progress dashboard showing task and lane state, reviews, and blockers.
- A recovery path that resumes incomplete work instead of restarting completed tasks.

The Python helper records decisions and checks their bindings. It never launches agents, performs reviews, grants approval, or opens and merges PRs. The coding-agent harness launches the agents.

## Use

1. Open your project's feature checkout in your coding-agent harness.
2. Ask the agent to use `feature-development-skill` with a feature request or an existing OpenSpec change.
3. Answer its questions about requirements, models, and approvals.
4. Review the plan before authorizing implementation.

You do not need to launch each agent manually or memorize the helper commands.
The Coordinator starts at [SKILL.md](SKILL.md) and follows the phase guides, stopping at required human decisions.

### Example: build a feature across several layers

> Use feature-development-skill to add saved searches.
> Users should be able to name, reopen, and delete their saved searches.
> Clarify ownership and access rules with me before implementation.
> Define the OpenSpec requirements and acceptance criteria.
> Propose tasks and lanes for the relevant storage, API, and UI changes.
> Complete the required planning reviews.
> Ask for my approval before implementation.

This is a good fit because one feature spans several parts of the project.
The Coordinator discovers the existing architecture and assigns the reviews the plan requires; it assumes no framework or database.

### Example: build a backend-only feature

> Use feature-development-skill to add a CSV export for reports.
> Agree on permissions, exported fields, and size limits with me in the spec.
> Follow this repository's test and release rules.
> Complete the required planning reviews.
> Ask for my approval before implementation.

Review duties follow the plan's risk and UI flags, not a fixed application template; no UI work means no UI review.

### Example: implement an existing specification

> Use feature-development-skill for the existing OpenSpec change saved-searches.
> Read its proposal, design, specs, and tasks.
> Resolve any gaps in the specification.
> Complete the planning reviews.
> Ask for my approval before implementation.

### Example: resume interrupted work

> Use feature-development-skill to resume saved-searches.
> Reconcile the local records and surviving worktrees.
> Show completed tasks, pending reviews, and blockers.
> Continue from the first incomplete operation without restarting completed work.

Resume from the same feature checkout with its local records intact; Git alone cannot reconstruct lost reviews, approvals, or the local lane plan.

### Monitor progress

> Show the progress dashboard for saved-searches.
> Summarize anything waiting for my decision.

The [dashboard and status commands](references/helper-commands.md#status-and-task-status) show progress from local records.
The dashboard is a monitor, not proof of approval.
For the command-level lifecycle, see the [worked example](references/minimal-workflow.md).

Licensed under [MIT](LICENSE).

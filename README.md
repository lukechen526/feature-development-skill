# Feature Development Skill

An [Agent Skill](https://agentskills.io/specification) for coordinated OpenSpec feature development.
It separates planning, isolated implementation, independent review, lane integration, and release preparation.
Human approval remains separate from automated checks.

Start with [SKILL.md](SKILL.md) for agent instructions.
Use the [minimal workflow](examples/minimal-workflow.md) for a small command-by-command example.

## Requirements

- Python 3.9 or later for the helper. The helper uses only the standard library.
- Git with worktree support and a configured commit identity.
- The OpenSpec CLI, available as `openspec`.
- A harness that can launch fresh-context subagents with a selected working directory and the supplied role prompt.
- A consuming Git repository with OpenSpec artifacts and repository-owned testing and release guidance.

Model selection, tool restrictions, continuation, and completion notifications depend on the harness.
The [preflight](workflow/preflight-and-roles.md) records supported capabilities and limitations.
No live cross-harness certification is claimed.

## Install

Use your harness's skill installer when it accepts a local skill directory.
Select this repository's root directory, which contains `SKILL.md`.
Preserve the installed directory name: `feature-development-skill`.

For manual installation:

1. Identify the skill-discovery directory documented by your harness.
2. Create a `feature-development-skill` directory there.
3. Copy `SKILL.md`, `LICENSE`, `bin/`, `workflow/`, and `subagent-roles/` into that directory.
4. Copy `README.md` and `examples/` if you want the setup guide and walkthrough available there.
5. Reload skill discovery as your harness requires.

Do not copy `.git/`, `.venv/`, runtime records, or `IMPLEMENTATION-PLAN.md`.
The role files are prompt sources, not preinstalled native agents.
The coordinator provisions or composes them during preflight.

Set the absolute installed path when your harness does not supply the skill root:

```bash
export FEATDEV_SKILL_ROOT="/absolute/path/to/feature-development-skill"
python3 "$FEATDEV_SKILL_ROOT/bin/featdev.py" --help
```

## Use

1. Open the consuming repository's feature checkout in your harness.
2. Ask the agent to use this skill.
3. Answer the model-selection and approval questions.

Example requests:

> Use feature-development-skill to plan and implement the active OpenSpec change.

> Use feature-development-skill to resume this change from its recorded coordination state.

The coordinator starts at [SKILL.md](SKILL.md).
The helper records workflow events; it does not launch agents or grant approval.
No helper command opens or merges a PR.

## Files and runtime records

| Location | Purpose |
|---|---|
| [SKILL.md](SKILL.md) | Skill metadata and workflow entry point |
| [bin/featdev.py](bin/featdev.py) | Standard-library helper and self-check |
| [workflow/](workflow/) | Phase instructions and shared handoff contract |
| [subagent-roles/](subagent-roles/) | Harness-independent role prompts |
| [examples/](examples/) | Human-readable walkthrough |
| `.agents/workflow/` in the consuming repository | Local capability map, model choices, coordination, reports, and evidence |

Treat the installed skill as read-only.
Run workflow commands from the consuming feature checkout, except worker commands assigned to a lane worktree.
The helper locally excludes `.agents/workflow/` from Git when it creates records.
Keep those records in the same checkout for recovery.
Do not publish them as part of the skill.

## Validate this repository

Run these commands from the skill repository:

```bash
python3 -m py_compile bin/featdev.py
python3 bin/featdev.py --self-check
git diff --check
```

The self-check uses temporary Git repositories and an isolated Git configuration.
It checks helper behavior, workflow documentation, and local Markdown link targets.
It does not launch real subagents or perform human approvals.

The official reference validator needs Python 3.11 or later.
Install its development-only dependencies in a separate environment:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/skills-ref validate "$PWD"
```

`requirements-dev.txt` pins the upstream reference validator to a Git revision.
Normal skill use does not require that package or a virtual environment.
GitHub Actions runs validation on Python 3.9 and 3.11.
Only the Python 3.11 job installs the reference validator.

## License

[MIT](LICENSE).

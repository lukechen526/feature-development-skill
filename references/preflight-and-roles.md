# Preflight, roles, models, and dispatch

Command syntax: [helper command reference](helper-commands.md).

The human chooses the model and thinking for every role; there is no fixed default table. The selection is asked once per repository and reused by every change until the human changes it.

## Step 0 — capability preflight (once per repo/harness)

Discover applicable [repository agent instructions](briefs-and-handoffs.md#repository-instructions) before planning or dispatch.
Record their paths for scoped briefs. No particular instruction filename is required.
Verify `openspec --version` when establishing OpenSpec CLI availability.

Do not assume a provider, harness, or agent registry. Inspect the live tool surface and record what it
actually supports; never infer a capability from a tool name, a package manifest, or a previous run.

| Capability | Determine |
|---|---|
| Fresh launch | Can a new subagent start with a fresh context? |
| Fork | Is a history-inheriting launch available? This workflow does not use it. |
| Working directory | Can the launch set the lane worktree as its working directory? |
| Prompt injection | Can the role body be injected as the system or task prompt? |
| Model | Can a model be selected per subagent? |
| Model enumeration | Can the harness list selectable models? |
| Thinking | Can a reasoning or thinking level be selected per subagent? |
| Tools | Can a role's tool set be restricted? |
| Wait / notify | Is completion delivered by an event, or must the Coordinator poll? Record `event` or `poll`. |
| Status | Can a running child be inspected by id? |
| Message / continue | Can an existing child receive a correction? |
| Parent / supervisor message | Can a child send a question or status to its parent, and can the parent message a live child? |
| Resume | Can a timed-out or failed child be resumed? |
| Stop | Can a child be interrupted? |
| Timeout | Can a per-child deadline be set? |
| Structured output | Can the child return a typed receipt? |

Resolve the skill root once, in this order: `FEATDEV_SKILL_ROOT`; the loaded skill's base directory; a
verified project or user skill directory. Require `scripts/featdev.py`, `references/`, and `assets/`.
Record the absolute path, then export it before running any helper command, including commands in nested
shells:

```bash
export FEATDEV_SKILL_ROOT="<resolved absolute skill root>"
```

Invoke the helper as `"$FEATDEV_SKILL_ROOT"/scripts/featdev.py`; never rely on a relative root or an unexpanded variable.

Choose the first mechanism that satisfies all three hard gates: fresh-context launch, a settable working
directory, and role prompt injection:

1. a native named agent definition (registry, manifest, create/update API, or agent directory);
2. a native fresh subagent with a composed prompt;
3. a one-shot orchestration API with per-agent model overrides and a settable working directory;
4. an external agent CLI that accepts a prompt and a working directory;
5. stop when no available mechanism provides a fresh-context launch with a settable working directory and
   role prompt injection. Report the missing capability; never substitute a shared or Coordinator working
   directory.

A mechanism change on a later run is a new preflight. There is no silent fallback.

Step 0 writes two required repo-level records:

- `.agents/workflow/capability-map.md`: the capability map defined below.
- `.agents/workflow/preflight.json`: the harness name and version, the capability matrix, the resolved
  absolute skill root, the absolute featdev path, the capability map path and SHA-256, and the selected
  mechanism.

A different harness or mechanism requires a new preflight that rewrites both records.

The repo-level records are local and gitignored. Back up the entire `.agents/workflow/` tree before
rewriting preflight or selection records; Git cannot reconstruct it.

Template for `.agents/workflow/preflight.json`:

```json
{
  "harness": {
    "name": "<name>",
    "version": "<version>",
    "capabilities": {
      "fresh_launch": true,
      "fork": false,
      "working_directory": true,
      "prompt_injection": true,
      "model_select": false,
      "model_enumeration": false,
      "thinking_select": false,
      "tool_scope": false,
      "wait": "<event|poll>",
      "status": true,
      "message": true,
      "parent_message": true,
      "resume": true,
      "stop": true,
      "timeout": false,
      "structured_output": false
    }
  },
  "skill_root": "<absolute path>",
  "featdev": "<absolute path to scripts/featdev.py>",
  "capability_map": "<absolute path to .agents/workflow/capability-map.md>",
  "capability_map_sha256": "<hash>",
  "mechanism": "<native-agent | composed-prompt | one-shot-orchestration | external-cli>"
}
```

### Record the capability map

Before defining roles, write `.agents/workflow/capability-map.md`: a short, one-page mapping from each generic capability in the table above to the concrete tool or operation this harness exposes.

Give it one row per capability in the Step 0 table, with columns `Harness tool or operation`, `Invocation`, `Supported`, and `Fallback / limitation`.
`Supported` takes exactly one of `yes`, `no`, `unsupported`, or `not exposed`; for Wait / notify record `event` or `poll`. Never leave a blank. If the harness exposes more than one mechanism, name the mechanism the row applies to.

The Coordinator reads the map before every dispatch, and every brief carries its absolute path.

## Step 1 — ask the human (once per repository)

Before any dispatch, ask the human through the harness's user-question tool (or in chat when no
question tool exists). Ask one question per role, in this order:

| Role ID | Role | Question | Role file | Review duty |
|---|---|---|---|---|
| `coordinator` | Coordinator | Which model and thinking level should run the coordinator session? | — (session profile, not dispatchable) | Selects the current coordinator session |
| `implementer` | Implementer | Which model and thinking level should implement tasks and fixes? | [assets/implementer.md](../assets/implementer.md) | Implementation, tests, fixes |
| `code-reviewer` | Code reviewer | Which model and thinking level should review code? | [assets/code-reviewer.md](../assets/code-reviewer.md) | Implementation/task completeness; ordinary lanes also own the code/test review |
| `test-reviewer` | Test reviewer | Which model and thinking level should review test adequacy? | [assets/test-reviewer.md](../assets/test-reviewer.md) | Test adequacy, coverage, quality, mutation testing; high-risk lanes |
| `ui-reviewer` | Rendered-UI reviewer | Which model and thinking level should review supplied UI evidence? | [assets/ui-reviewer.md](../assets/ui-reviewer.md) | Supplied rendered-UI evidence |
| `oracle` | Oracle | Which model and thinking level should review plans, mediate, and author bounded fixes? | [assets/oracle.md](../assets/oracle.md) | Plan review, mediation, authorized fixes |
| `runner` | Runner | Which model and thinking level should run long checks or polling? | [assets/runner.md](../assets/runner.md) | Long checks and polling; observations only, no verdict |

Rules:

1. Offer only the models and thinking levels the preflight reports as selectable, plus a free-text
   option. When model enumeration is unavailable, accept free text and verify each entry against the
   live harness before recording it.
2. Ask per role; do not invent a default and do not fill an answer yourself.
3. If the harness cannot select a model or thinking level, say so, require explicit human acknowledgement, and record it as defined below; never silently substitute a value.
4. The human may answer "same as X" or "you choose"; record that literally with the resolved value.
5. Never infer a role model from another role, from the coordinator session, or from another repository.
   Explicit per-role answers win.

`effective` records the value the harness actually uses for each of `model` and `thinking`: the selected
value when supported, the session default when not. `degraded` is true when either selected value could
not be used. `acknowledged` records the human's explicit acceptance of that degradation, recorded only after the human answers; a degraded role
without acknowledgement blocks dispatch.

## Canonical roles

The table above defines the role IDs. The same ID is used in the role file's `role_id`, `model-selection.json`, the positional role in `dispatch <lane> <role>` and `lane-review <lane> <role> <verdict>`; do not use aliases or alternate spellings.
Ordinary lanes require the `code-reviewer` review, which covers code and test adequacy. High-risk lanes require separate `code-reviewer` and `test-reviewer` reviews. The standard gate requires `ui-reviewer` review only when the plan sets `ui: true`, which also requires current rendered evidence for the UI `PASS` and the human checkpoint. A `ui` or `sensitive` flag requires a human checkpoint, subject to the documented deferral rules.

## Record the selection

Step 1 writes the core object at `.agents/workflow/model-selection.json`; Step 1b adds `profiles`. There is no top-level
`dispatch_mechanism` or `agent_provisioning` field; the mechanism is recorded once in `preflight.json`
and per role in `profiles.<role>.mechanism`.

This file is created in Step 1, after the human answers. Every change reuses
it; do not copy it into `.agents/workflow/<change>/`. A different selection for one change requires an
explicit human decision and an update to this repo-level file.

Copy `harness`, `skill_root`, `capability_map`, and `capability_map_sha256` from
`preflight.json`; do not re-inspect or hand-edit them. The profile hash is `prompt_sha256`.

Template for `.agents/workflow/model-selection.json` (documentation only; this skill ships no such file):

```json
{
  "scope": "repo",
  "asked_at": "<ISO-8601>",
  "asked_with": "question-tool | interview | chat",
  "skill_root": "<absolute path>",
  "capability_map": "<absolute path to .agents/workflow/capability-map.md>",
  "capability_map_sha256": "<hash>",
  "harness": "<copied verbatim from preflight.json: name, version, capabilities>",
  "roles": {
    "coordinator": {
      "model": "<provider/model>",
      "thinking": "<level>",
      "effective": {"model": "<value actually used>", "thinking": "<value actually used>"},
      "degraded": false,
      "acknowledged": false
    }
  },
  "profiles": {
    "implementer": {
      "role_file": "<absolute path to assets/implementer.md>",
      "prompt_sha256": "<hash>",
      "mechanism": "native-agent | composed-prompt | one-shot-orchestration | external-cli",
      "contract_path": "<absolute path to references/briefs-and-handoffs.md>",
      "continuation": "message | resume | fresh-dispatch",
      "gaps": []
    }
  }
}
```

Repeat the `roles` entry for every role in the role table and the `profiles` entry for every dispatchable role (`implementer`, `code-reviewer`, `test-reviewer`, `ui-reviewer`, `oracle`, `runner`). `profiles` is a Step 1b addition; Step 1 writes the other fields.

The file is repo-level and must not contain a lane worktree or any other per-dispatch path; the absolute worktree is supplied in each filled brief and recorded in coordination.

The Coordinator reads this file before every dispatch. It re-asks only when the repository has no selection, the human requests a change, the harness or mechanism changed, or a recorded capability or model is no longer available.

## Step 1b — define the roles with native tooling

A role needs a launch profile before it can be dispatched. Step 1b adds `profiles` to the repo-level
`model-selection.json`; Step 1 already wrote the core fields.

1. **Read the canonical role files** in this skill: `assets/*.md`, each with `name`, `role_id`,
   `description`, a requested capability list, and a prompt body.
2. **Normalize the role file into a profile.** Preserve the prompt body verbatim. Treat the role's tool
   list as requested capabilities and map each to the harness's actual tools; a capability the harness
   lacks is recorded as a gap, not silently dropped. Add the absolute contract path. The role file's
   `role_id` is authoritative; do not rename the role.
3. **Instantiate** using the first mechanism from Step 0:
   - native registry or definition API: create or update the named agent from the role file; set model,
     thinking, and tools only where the preflight says they are supported;
   - no registry: compose the prompt from the role body, the brief, and the absolute contract path, and
     launch a fresh subagent with the lane worktree as its working directory;
   - one-shot orchestration: use the same composition, set the model/provider per agent, and set the
     lane worktree as the working directory. A mechanism without working-directory control does not
     qualify; stop and choose another preflighted mechanism.
4. **Provision idempotently**: get the definition first; create it when absent; update it when the
   canonical file hash changed.
5. **Verify before dispatching**: the child's first report must state its role ID, profile hash
   (`prompt_sha256`), worktree, `HEAD`, and the absolute contract path. A same-named builtin without
   the role prompt is not acceptable. If verification fails, re-dispatch; never accept the work.
6. **Record the mechanism and profile** in `model-selection.json` as
   `profiles.<role>.mechanism` and the profile fields. A different mechanism on a later run is a new
   preflight, not a silent fallback.

Rules:

- Never launch a role whose profile is missing or whose prompt does not include the role body.
- Never edit a harness's agent directory by hand when a definition or provisioning tool exists.
- If no fresh-context launch with a settable working directory and prompt injection works, stop before
  planning work starts and report what is missing.

## Mechanism selection and locking

Select the first preflighted mechanism that supports fresh launch, a settable working directory, and
role prompt injection. The mechanism determines harness behavior, never provider/model notation.
Dispatch-layer failure: stop; never switch mechanism silently. Model failure: recover the locked role
model in the same lane worktree.

Record the role, task, and access using [dispatch](helper-commands.md#dispatch).
The helper does not resolve model settings; the Coordinator applies the selected role profile to the actual harness launch.
Keep the profile id or hash in the brief and repo-level model selection. The selected model and level
are locked per role lineage inside a lane, including replacements.

### Dispatch mode (worker resumability)

Long-running roles (Implementer, Reviewer, Runner, Oracle): dispatch asynchronously when the harness
supports it, with an explicit per-dispatch deadline in the brief. An implementation dispatch uses 60
minutes or the shorter harness limit; review and Runner deadlines follow the brief. Do not poll merely
to wait when completion events wake the parent.

Recovery is capability-based. On deadline, inspect progress. If the harness supports continuation,
continue the child; otherwise launch a fresh same-role worker from the live report. Never start a second
writer in the same worktree. The lane's live report is the fallback context source.

### Native adapter contract

Use the harness's exposed native tools, not a nested CLI. The adapter must provide, where available:
spawn, follow-up or message, completion wait, and stop or close. Tool names and parameters vary by
harness; inspect live schemas. This skill authorizes scoped lane delegation.

Record the dispatch before spawn; the returned agent or session id becomes the worker run id. Every brief follows the contract's field list and names the absolute contract path. Pass the role brief path, the filled task brief, and the profile.

Require implementation edits, tests, task edits, and commits in the lane worktree. Reports and evidence
go to the absolute `.agents/workflow/` paths named in the brief; those paths are the only writes outside
it. Use a fresh context, never a fork of the Coordinator's history. Reuse a coherent correction
session only. Respect live capacity, sandbox, and review-only boundaries.

Preflight the selected model and effort against live supported choices; map provider notation only to
verified equivalents. Never silently inherit the parent's defaults or substitute an unsupported
provider/model selection. Before the mechanism lock, choose the next compatible mechanism or ask the
human for a supported override; after the lock, stop on failure.

# Roles, models, dispatch, provisioning

The human chooses the model and thinking for every role. There is no fixed default table in this skill.
The selection is recorded and reused until the human changes it.

## Step 0 — capability preflight (before asking or dispatching)

Do not assume a provider, harness, or agent registry. Inspect the live tool surface and record what it
actually supports. Do not infer a capability from a tool name, a package manifest, or a previous run.

| Capability | Determine |
|---|---|
| Fresh launch | Can a new subagent start with a fresh context? |
| Fork | Is a history-inheriting launch available? This workflow does not use it. |
| Working directory | Can the launch set the lane worktree as its working directory? |
| Prompt injection | Can the role body be injected as the system or task prompt? |
| Model | Can a model be selected per subagent? Is a model list enumerable? |
| Thinking | Can a reasoning or thinking level be selected per subagent? |
| Tools | Can a role's tool set be restricted? |
| Wait / notify | Is completion delivered by event, or must the Coordinator poll? |
| Status | Can a running child be inspected by id? |
| Message / continue | Can an existing child receive a correction? |
| Resume | Can a timed-out or failed child be resumed? |
| Stop | Can a child be interrupted? |
| Timeout | Can a per-child deadline be set? |
| Structured output | Can the child return a typed receipt? |

Resolve the skill root once, in this order: `FEATDEV_SKILL_ROOT`; the loaded skill's base directory; a
verified project or user skill directory. Require `bin/featdev.py`, `workflow/`, and `subagent-roles/`.
Record the absolute path and pass it to every command and brief.

Choose the first mechanism that satisfies fresh launch, a settable working directory, and role prompt
injection:

1. a native named agent definition (registry, manifest, create/update API, or agent directory);
2. a native fresh subagent with a composed prompt;
3. a one-shot orchestration API with per-agent model overrides (record any absent working directory or
   continuation);
4. an external agent CLI that accepts a prompt and a working directory;
5. stop only when no fresh-context launch exists.

A mechanism change on a later run is a new preflight. There is no silent fallback.

Write the preflight to `.agents/workflow/<change>/preflight.json`, or to the `harness`, `skill_root`,
and `profiles` sections of `model-selection.json` created in Step 1. Include the harness name and
version, the capability matrix, the resolved skill root, the featdev path, and one launch profile per
role: role file, prompt hash, mechanism, working directory, model and thinking requested versus
effective, tool policy, continuation and stop support, gaps, and human acknowledgement.

## Step 1 — ask the human (no implicit defaults)

Before any dispatch, ask the human through the harness's user-question tool (or in chat when no
question tool exists). Ask one question per role, in this order:

| Role | Question |
|---|---|
| Orchestration | Which model and thinking level should run the coordinator session? |
| Implementer | Which model and thinking level should implement tasks and fixes? |
| Code reviewer | Which model and thinking level should review code? |
| Test reviewer | Which model and thinking level should review test adequacy? |
| Rendered-UI reviewer | Which model and thinking level should review supplied UI evidence? |
| Oracle | Which model and thinking level should review plans, mediate, and author bounded fixes? |

Rules:

1. Offer only the models and thinking levels the preflight reports as selectable, plus a free-text
   option.
2. Ask per role; do not invent a default and do not fill an answer yourself.
3. If the harness cannot select a model or thinking level, say so, record the session default as the
   effective value, and ask the human to acknowledge the degradation explicitly.
4. The human may answer "same as X" or "you choose"; record that literally with the resolved value.
5. Never infer a role model from another role, from the coordinator session, or from a previous change
   in another repository. Explicit per-role answers and existing lane model locks win.

## Record the selection

Write the answers to a gitignored file under the coordinator store:

```
<repo>/.agents/workflow/<change>/model-selection.json
```

```json
{
  "change": "<change>",
  "asked_at": "<ISO-8601>",
  "asked_with": "question-tool | interview | chat",
  "skill_root": "<absolute path>",
  "harness": {
    "name": "<name>",
    "version": "<version>",
    "capabilities": {
      "fresh_launch": true,
      "working_directory": true,
      "prompt_injection": true,
      "model_select": false,
      "thinking_select": false,
      "tool_scope": false,
      "wait": "event | poll",
      "status": true,
      "message": true,
      "resume": true,
      "stop": true,
      "timeout": false,
      "structured_output": false
    }
  },
  "roles": {
    "orchestration": { "model": "<provider/model>", "thinking": "<level>", "effective": "<value>" },
    "implementer": { "model": "<provider/model>", "thinking": "<level>", "effective": "<value>" },
    "code_reviewer": { "model": "<provider/model>", "thinking": "<level>", "effective": "<value>" },
    "test_reviewer": { "model": "<provider/model>", "thinking": "<level>", "effective": "<value>" },
    "rendered_ui_reviewer": { "model": "<provider/model>", "thinking": "<level>", "effective": "<value>" },
    "oracle": { "model": "<provider/model>", "thinking": "<level>", "effective": "<value>" }
  },
  "profiles": {
    "implementer": {
      "role_file": "<absolute path to subagent-roles/worker.md>",
      "prompt_sha256": "<hash>",
      "mechanism": "native-agent | composed-prompt | one-shot-orchestration | external-cli",
      "working_directory": "<absolute lane worktree>",
      "continuation": "message | resume | fresh-dispatch",
      "gaps": []
    }
  },
  "dispatch_mechanism": "<mechanism>",
  "agent_provisioning": "<mechanism>"
}
```

Repeat `profiles` for every role defined in Step 1b.

The Coordinator reads this file before every dispatch. It re-asks only when the human requests a
change, when a recorded capability or model is no longer available, or when this change has no recorded
selection. `.agents/workflow/` must be gitignored; `featdev.py state-derive` adds a local Git exclude
rule when it is missing.

Verify each selected model and thinking level against the preflight's live findings. A choice that is
not selectable is recorded with the effective session default and the human's acknowledgement, never
silently substituted.

## Step 1b — define the roles with native tooling

A role needs a launch profile before it can be dispatched. Do not assume the files are installed.

1. **Read the canonical role files** in this skill: `subagent-roles/*.md`, each with `name`, `aliases`,
   `description`, a requested capability list, and a prompt body.
2. **Normalize the role file into a profile.** Preserve the prompt body verbatim. Treat the role's tool
   list as requested capabilities and map each to the harness's actual tools; a capability the harness
   lacks is recorded as a gap, not silently dropped. Add the absolute contract path and the lane
   worktree to the profile.
3. **Instantiate** using the first mechanism from Step 0:
   - native registry or definition API: create or update the named agent from the role file; set model,
     thinking, and tools only where the preflight says they are supported;
   - no registry: compose the prompt from the role body, the brief, and the absolute contract path, and
     launch a fresh subagent with the lane worktree as its working directory;
   - one-shot orchestration: use the same composition and set the model/provider per agent; record that
     working-directory control or continuation may be absent.
4. **Provision idempotently**: get the definition first; create it when absent; update it when the
   canonical file hash changed.
5. **Verify before dispatching**: the child's first report must state its role, profile hash, worktree,
   `HEAD`, and the absolute contract path. A same-named builtin without the role prompt is not
   acceptable. If verification fails, re-dispatch; never accept the work.
6. **Record the mechanism and profile** in `model-selection.json`. A different mechanism on a later run
   is a new preflight, not a silent fallback.

Rules:

- Never launch a role whose profile is missing or whose prompt does not include the role body.
- Never edit a harness's agent directory by hand when a definition or provisioning tool exists.
- If no fresh-context launch works, stop before planning work starts and report what is missing.

## Mechanism selection and locking

Select the first preflighted mechanism that supports fresh launch, a settable working directory, and
role prompt injection. The mechanism determines harness behavior, never provider/model notation.
Dispatch-layer failure: stop; never switch mechanism silently. Model failure: recover the locked role
model in the same lane worktree.

Records `dispatch --model`, `--level`, `--role`, `--access`, and the profile id or hash. The selected
model and level are locked per role lineage inside a lane, including replacements.

### Dispatch mode (worker resumability)

Long-running workers (Implementer, Reviewer, Oracle): dispatch asynchronously when the harness supports
it, with an explicit per-dispatch deadline. Do not poll merely to wait when completion events wake the
parent.

Recovery is capability-based. On deadline, inspect progress. If the harness supports continuation,
continue the child; otherwise launch a fresh same-role worker from the live report. Never start a second
writer in the same worktree. The lane's live report is the fallback context source.

### Native adapter contract

Use the harness's exposed native tools, not a nested CLI. The adapter must provide, where available:
spawn, follow-up or message, completion wait, and stop or close. Tool names and parameters vary by
harness; inspect live schemas. This skill authorizes scoped lane delegation.

Record the dispatch before spawn; the returned agent or session id becomes the worker run id. Pass the
role brief path, the filled task brief, the profile, and the absolute lane worktree; require all writes
there. Use a fresh context, never a fork of the Coordinator's history. Reuse a coherent correction
session only. Respect live capacity, sandbox, and review-only boundaries.

Preflight the selected model and effort against live supported choices; map provider notation only to
verified equivalents. Never silently inherit the parent's defaults or substitute an unsupported
provider/model selection. Before the mechanism lock, choose the next compatible mechanism or ask the
human for a supported override; after the lock, stop on failure.

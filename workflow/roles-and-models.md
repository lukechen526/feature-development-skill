# Roles, models, dispatch, provisioning

The human chooses the model and thinking for every role. There is no fixed default table in this skill.
The selection is asked once per repository, recorded, and reused until the human changes it.

## Step 1 — ask the human (no defaults)

Before any dispatch, ask the human through the harness's user-question tool (for example an
`ask_user` or interview-style prompt). Ask one question per role, in this order:

| Role | Question |
|---|---|
| Orchestration | Which model and thinking level should run the coordinator session? |
| Implementer | Which model and thinking level should implement tasks and fixes? |
| Code reviewer | Which model and thinking level should review code? |
| Test reviewer | Which model and thinking level should review test adequacy? |
| Rendered-UI reviewer | Which model and thinking level should review supplied UI evidence? |
| Oracle | Which model and thinking level should review plans, mediate, and author bounded fixes? |

Rules:

1. Offer the models the harness actually reports as available, plus a free-text option.
2. Ask per role; do not invent a default and do not fill an answer yourself.
3. If the harness has no question tool, ask in chat and record the answer verbatim.
4. The human may answer "same as X" or "you choose"; record that literally with the resolved value.
5. Never infer a role model from a different role, from the coordinator session, or from a previous change
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
  "asked_with": "ask_user | interview | chat",
  "roles": {
    "implementer": { "model": "<provider/model>", "thinking": "<level>" },
    "code_reviewer": { "model": "<provider/model>", "thinking": "<level>" },
    "test_reviewer": { "model": "<provider/model>", "thinking": "<level>" },
    "rendered_ui_reviewer": { "model": "<provider/model>", "thinking": "<level>" },
    "oracle": { "model": "<provider/model>", "thinking": "<level>" }
  },
  "dispatch_mechanism": "<mechanism>",
  "agent_provisioning": "<mechanism>"
}
```

The Coordinator reads this file before every dispatch. It re-asks only when the human requests a change,
when a recorded model is no longer available, or when the change name does not match. `.agents/workflow/`
must be gitignored; `featdev.py state-derive` adds a local Git exclude rule when it is missing.

Verify each selected model against the live list the harness reports. A model that is not offered is
recorded as unavailable with the reason, and the human is asked again.

## Step 1b — provision the roles with native subagent tooling

A role needs an agent definition in the harness before it can be launched. Do not assume the files ship.

1. **Read the canonical role files** in this skill: `subagent-roles/*.md`, each with `name`,
   `description`, `tools`, `thinking` and a prompt body.
2. **Detect the provisioning mechanism** the harness offers, in this order:
   - the package manifest already exposes the agents (`pi.subagents.agents` or `pi-subagents.agents`);
   - the native subagent tool can create or update agents programmatically (for example
     `{ action: "create", options: { config } }` and `{ action: "update" }`);
   - the harness reads a project agent directory;
   - the harness reads a user agent directory.
3. **Provision idempotently**: get the agent first; create it when absent, update it when the canonical
   file changed. Compare a hash of the canonical file with the installed definition, and update on drift.
4. **Verify before dispatching**: the agent resolves by name and reports the expected tools. A same-named
   builtin without the role's tools is not acceptable; namespace the role (for example
   `multi-lane-workflow.codeReviewer`) or stop with a named reason.
5. **Record the mechanism** in `model-selection.json` as `agent_provisioning`, and in the coordination
   record. A different mechanism on a later run is a new preflight, not a silent fallback.

Rules:

- Never launch a role whose agent is missing, incomplete, or lacks `write` where the role produces a report.
- Never edit the harness's agent directory by hand when a provisioning tool exists.
- If no provisioning mechanism works, stop before planning work starts and report what is missing.

## Mechanism selection and locking

Select the first available, preflighted dispatch mechanism before the first worker:

1. native Pi subagents;
2. the invoking harness's native subagents, including Codex;
3. Orca;
4. Herdr.

Mechanism determines harness, never provider/model prefix. Dispatch-layer failure: stop, never switch
mechanism silently. Model failure: recover the locked role model in the same lane worktree.

Records `dispatch --model`, `--level`, `--role`, `--access`, and the provisioning mechanism. The
selected model and level are locked per role lineage inside a lane, including replacements.

### Dispatch mode (worker resumability)

Long-running workers (Implementer, Reviewer, Oracle): dispatch with `async: true` (background, keeps the
session interactive) and an explicit `timeoutMs` (implementers 3600000). Never call `bg_wait` merely to
wait; native completion/attention events wake the parent.

**Timeout recovery is resume-first.** Failed or paused async runs keep their persisted child session for
30 days and are resumable; only runs killed via stop/interrupt are not. On timeout: recover the run id via
`subagent({ action: "status" })` or `children.list`, then `subagent({ action: "resume", id, message })`.
Launch a fresh same-role worker only if revive fails or the human asks; the lane's live report is the
fallback context source.

### Codex native adapter

Use exposed native tools, not a nested CLI: `spawn_agent`, follow-up/message, completion wait, stop/close
as available. Tool names and parameters vary by harness; inspect live schemas. This skill authorizes
scoped lane delegation.

Record `dispatch` before spawn; the returned agent/session ID becomes `dispatch-done --worker-run-id`.
Pass the role-brief path, the filled task brief, and the absolute lane worktree; require all writes there.
Do not assume `.agents/agents/*.md` auto-loads and do not assume spawning creates a worktree.
Use fresh context (`fork_turns: "none"` where exposed); reuse a coherent correction session only.
Respect live capacity, sandbox, and review-only boundaries.

Preflight the selected model and effort against live supported choices; map provider notation only to
verified equivalents. Never silently inherit the parent's defaults or substitute an unsupported
provider/model selection. Before the mechanism lock, choose the next compatible mechanism or ask the human
for a supported override; after the lock, stop on failure.

Reference: [Codex subagents](https://developers.openai.com/codex/subagents/).

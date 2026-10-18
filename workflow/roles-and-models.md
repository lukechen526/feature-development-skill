# Roles, models, dispatch

Skill defaults for role/model/thinking; the human's per-change selection record overrides them.

**Default role/model table** (portable across repos; agent names bind to each repo's agent
definitions — e.g. this repo: `worker`/`reviewer`/`oracle`/`ios-ui-reviewer`):

| Role | Default model (thinking) | Fallback | Responsibility |
|---|---|---|---|
| Implementer | `openai-codex/gpt-6.1-sol:high` | `opencode-go/glm-5.3-flash:high` | writes code, tests, and evidence |
| Runner | `openai-codex/gpt-6-luna:max` | `openrouter/openai/gpt-6-luna:high` | runs long processes (tests, Maestro flows) or polls external resources in the background; reports progress, consults the Coordinator on problems, never implements |
| Code reviewer | `openai-codex/gpt-6.1-sol:medium` | `opencode-go/deepseek-v4.1-flash:high` | reviews implementation |
| Test reviewer | `openai-codex/gpt-6.1-sol:medium` | `opencode-go/deepseek-v4.1-flash:high` | separate pass for high-risk lanes |
| Rendered-UI reviewer | `openai-codex/gpt-6.1-sol:medium` | `opencode-go/deepseek-v4.1-flash:high` | ingests screenshots/manifests only, never launches anything |
| Oracle | `openai-codex/gpt-6.1-sol:max` | `opencode-go/glm-5.3:high` | plan review, mediation, bounded authored fixes |

Primary thinking levels: implementer `high`, all reviewers `medium`, Oracle `max`. Code/test share fallback. Coordinator has no
worker model. The runner takes Coordinator instructions verbatim and is not a dispatch target for
lane implementation roles.

## Selection

Before preflight/worker creation, ask human once: defaults (table above) or per-role override.
Persist the outcome in gitignored `<feature-checkout>/.agents/tmp/featdev-<change>/model-selection.json`; no run id.
Override applies to every matching dispatch; absent overrides, the table's defaults apply.
Explicit human overrides and existing lane model locks take precedence; other role defaults remain unchanged.

Oracle operations: `plan_review`, `technical_resolution`.
Primary level: `max` for both Oracle operations;
fallback `high`/`max` as recorded by selected candidate.

Use the shared handoff contract in `briefs-and-handoffs.md` for fresh briefs/coherent delta continuations.
Perform live initial availability check. Cache successful preflight by mechanism/model/level/environment;
invalidate changed bindings. Prefer completion events, bounded fallback polling; preserve required PR monitoring.

## Locking

Lock selected model/level per role lineage within lane, including replacements.
Record launched model/level in every `dispatch`.

## Mechanism

Select first available, preflighted mechanism before first worker:

1. native Pi subagents;
2. invoking harness's native subagents, including Codex;
3. Orca;
4. Herdr.

Mechanism determines harness, never provider/model prefix.
Dispatch-layer failure: stop, never switch mechanism silently.
Model failure: recover locked role model in same lane worktree.
Role briefs/selected model override mapped agent or mechanism model/level/description.

### Dispatch mode (worker resumability)

Long-running workers (Implementer, Reviewer, Oracle): dispatch with `async: true` (background,
keeps the session interactive) and an explicit `timeoutMs` (implementers 3600000). Never call
`bg_wait` merely to wait — native completion/attention events wake the parent.

**Timeout recovery is resume-first.** Failed/partial/paused async runs keep their persisted
child session for 30 days and are resumable; only runs killed via stop/interrupt (`stopped`)
are not. On timeout: recover the run id via `subagent({ action: "status" })` or
`children.list` — NOT the session-file UUID from the failure notice — then
`subagent({ action: "resume", id: "<run-id>", message: "<continuation>" })`. Launch a fresh
same-role worker only if revive fails (wrong id, missing session) or the human asks; the
lane's live report is the fallback context source in that case.

### Codex native adapter

Use exposed native tools, not a nested CLI: `spawn_agent`, follow-up/message, completion wait, stop/close as available.
Tool names/parameters vary by harness; inspect live schemas. This skill authorizes scoped lane delegation.
Record `dispatch` before spawn; returned agent/session ID becomes `dispatch-done --worker-run-id`.
Pass role-brief path, filled task brief, absolute lane worktree; require all worker commands there.
Do not assume `.agents/agents/*.md` auto-loads or spawning creates a worktree.
Use fresh context (`fork_turns: "none"` where exposed); reuse coherent correction sessions only.
Respect live capacity, sandbox, and review-only boundaries.

Preflight selected model/effort against live supported choices; map provider notation only to verified equivalents.
Never silently inherit parent defaults or substitute unsupported provider/model selections.
Before mechanism lock, choose next compatible mechanism or ask human for supported override; after lock, stop on failure.
Verify custom-agent settings do not override selection. Preserve default role table until human changes it.

Reference: [Codex subagents](https://developers.openai.com/codex/subagents/).

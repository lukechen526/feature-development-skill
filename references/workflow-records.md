# Current workflow records

All paths below are relative to `.agents/workflow/` in the consuming feature checkout. They are local and gitignored. The Coordinator owns them; workers write only assigned reports/evidence. These schemas describe current storage, not a promise of compatibility with older releases. The Coordinator may reconstruct records directly under [recovery](recovery.md).

Unknown facts are omitted or listed in reconstruction `unknowns`; never fill a passing decision with a guessed value. Additional provenance and descriptive fields are allowed. `plan-validate --workflow` checks present record shapes; it does not prove a review occurred, consent was given, or the next gate is ready.

## Lane plan: `plan-graph.json`

```json
{
  "schema_version": 3,
  "changes": {
    "example": {
      "lanes": {
        "api": {
          "risk": "ordinary",
          "ui": false,
          "sensitive": false,
          "depends_on": [],
          "tasks": {"1.1": {"deps": []}, "1.2": {"deps": ["1.1"]}}
        }
      }
    }
  }
}
```

Each collection is nonempty. Task IDs match top-level IDs in the change's `tasks.md` exactly and are unique within the change. Lane/task dependencies refer within the same change and must be acyclic, including lane ordering implied by task dependencies. `depends_on` defaults to empty; task `deps` and all three gate-driving flags are required. Risk is `ordinary` or `high`. UI and sensitive are booleans; their reviewer/human implications are in [lanes](lane.md).

## Coordination: `coordination.json`

```json
{
  "schema_version": 2,
  "feature_checkout": "/absolute/feature-checkout",
  "changes": {
    "example": {
      "lanes": {
        "api": {
          "phase": "created",
          "worktree": null,
          "ref": null,
          "base": null,
          "tasks": {"1.1": {"status": "pending"}, "1.2": {"status": "pending"}},
          "dispatches": [],
          "reviews": [],
          "merge": null,
          "cleanup": null
        }
      },
      "subagents": [],
      "human_gates": {},
      "authorizations": {}
    }
  },
  "advance_waivers": [],
  "plan_amendments": []
}
```

An empty `changes` object is valid before lanes open. Per-change `branch` and `plan_sha256` describe the execution context. Optional per-change `dispatches`, `reviews`, and `worktrees` are arrays; helper operations maintain the necessary records. Preserve unrelated change nodes and top-level fields during edits.

Lane phase is `created`, `writing`, `reviewing`, `revising`, `merged`, `closed`, or reconstructed `unknown`. Task annotations use `pending`, `in_progress`, `done`, `blocked`, or `unknown`; blockers may include a reason. These are annotations: status derives completion from committed ticks and progress from live dispatches, overriding stale claims. Optional lane `status` and `status_reason` describe blockers.

A lane dispatch contains `role`, `task`, unique `key`, `access` (`read-only` or `write`), `started_at`, and `lane_head`. Completion adds `result` (`ok` or `failed`), `worker_run_id`, and optional measured `tokens`/`cost`; absent telemetry remains unknown. Related task dispatches may share a worker ID. Finish dispatches also name `scope: finish`, `worktree`, and `feature_head`.

A lane review contains `role`, `verdict`, `reviewed_commit`, `report`, and `binding` with `commit`, `lane_plan_sha256`, `evidence_revision`, `evidence_path`, and `evidence_sha256`. `round`, `status`, and `gate_eligible` are descriptive; round numbers impose no limit. A current PASS requires the candidate and lane contract to match. UI reviews require valid evidence bytes. Preserve report provenance when reconstructing.

A merge contains `merge_commit` (the integration SHA), `reviewed_head` (candidate SHA) and `base`, with optional `branch`. The lane node retains `ref` and `worktree`. The helper may add override details and inspection requirements. Closed lanes retain the merge record and `cleanup` facts. Integration ancestry and committed ticks must substantiate a reconstructed integration.

Human gates are keyed by lane ID. They contain `state`, `checkpoint`, and `binding` with candidate `commit`, `lane_plan_sha256`, and `evidence_revision`; UI bindings also include evidence path/hash. Authorized stand-ins include authorization and report provenance. Use the helper to record new decisions; reconstruction preserves supported historical decisions. Waivers are distinct from acceptance.

An `advance_waivers` entry records ID, actual human/source, gates, exact `change/lane` scope or all lanes, reason, decision date, state, limits/expiry, and revocation. Verify those limits before each use; the helper cannot authenticate human intent.

## Planning review and authorization

`plan-graph-review.json` is the combined initial review ledger:

```json
{"change": null, "dispatches": [], "reviews": []}
```

A planning dispatch contains `scope: plan`, `role: oracle`, `task: plan`, unique `key`, `head`, `inputs`, `started_at`, optional `report`, and eventual `result`. A planning review adds `verdict`, `report`, `report_sha256`, `dispatch_key`, and `recorded_at`, with the same `head` and `inputs`. Retain each correction review until the first `PASS`; later planning edits do not invalidate that historical PASS.

Planning `inputs` contain `plan_sha256` and `artifacts`. `plan_sha256` is the SHA-256 of Python `json.dumps(structure, sort_keys=True).encode('utf-8')`. The structure maps each change to its lanes' `risk`, `ui`, `sensitive`, `depends_on` and an ordered task list of `id`, `lane`, `deps`. `artifacts` maps each change to relative artifact file names and SHA-256 hashes. Artifact extensions are `.md`, `.yaml`, `.yml`, `.json`; exclude legacy `plan/` directories. Normalize top-level task checkbox state before hashing `tasks.md`. The helper's `_planning_inputs(root)` computes the canonical value, so callers need not reproduce the algorithm.

`plan-approval.json` uses `schema_version: 2`, `scope: repo`, `approver`, `approved_at`, `head`, `initial_review` (the PASS dispatch key), and `inputs`. For an explicit waiver, replace approver/date with `waived_by`/`waived_at` and include `waiver: {reason, lanes, all_lanes}`. Exactly one of a nonempty lane list or `all_lanes: true` defines waiver scope. Initial approval persists across human-requested plan changes.

For later edits, append `{human_request, inputs}` to the top-level `coordination.json.plan_amendments`. `human_request` identifies the actual initiating request; `inputs` are the current canonical planning inputs. Include date and changed paths when useful. This records existing authorization without another approval command. To obtain inputs from Python:

```python
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location("featdev", "/absolute/skill/scripts/featdev.py")
featdev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(featdev)
inputs = featdev._planning_inputs(Path("/absolute/feature-checkout"))
```

## Capability and model records

`preflight.json`: `schema_version: 1`, `harness` (name/version string), `capabilities` (a short object of supported scoped-launch/worktree/model/wait mechanisms and limitations), and `skill_root` (absolute path).

`model-selection.json`: `schema_version: 1`, `scope: repo`, `harness` string, and `roles` keyed by Coordinator and canonical worker role IDs. Each selection records requested and effective model/thinking settings plus the human decision/source, delegation or waiver, and any acknowledged limitation. Worktree paths belong in dispatches, not selections. These records guide harness launches; the helper does not choose models or launch workers.

## Finish: `finish.json`

Uses `schema_version: 1`, absolute `feature_checkout`, `classification`, optional `expected_version`, `classified_at`, `plan_sha256`, `plan_digest_kind: structure`, `included_changes`, and `preparation`. Preparation contains producing `head`, receipt `artifact`/`sha256`, and tracked `inputs`.

`declared_checks` lists selected IDs; `required_checks` maps IDs to input-path lists. `evidence` maps IDs to `binding_kind: inputs`, producing `head`, `artifact`/`sha256`, `inputs`, and `preparation_sha256`.

`required_inspections` lists checkpoint IDs. `inspection` maps checkpoints to producing `head`, artifact/hash, preparation hash, and `verdict: accept|reject`. Inspections with declared `inputs` also use `binding_kind: inputs` and `inspection_inputs`; otherwise exact SHA applies. Human report fields are documented in [finish](finish.md).

Optional finish `dispatches` and `subagents` track workers. The helper creates these records through `finish-record`; reconstructed records use the same bindings and preserve actual evidence.

`finish/pr-follow-up.md` records PR URL, included changes, current SHA, checks, comment dispositions, outstanding issues, and the separate human PR-opening consent/source, timestamp, and consented SHA. It is not PR merge consent.

## Reconstruction provenance

Any reconstructed record may include:

```json
{
  "reconstruction": {
    "at": "2026-10-31T12:00:00Z",
    "sources": ["surviving report path", "human decision reference", "Git SHA"],
    "transformations": ["converted old lane layout to current schema"],
    "unknowns": ["worker outcome could not be established"]
  }
}
```

Do not turn unknowns into ready gates. Recover enough for the next operation; do not recreate every historical field or repeat completed phases merely to satisfy an old format.

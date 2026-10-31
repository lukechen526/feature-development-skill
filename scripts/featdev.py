#!/usr/bin/env python3
"""Record and validate local planning, dispatch, review, integration, and release decisions."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote, urlparse
from typing import Any


FIELD_TABLE = """Plan fields:
  schema_version  3 for repository-level plan-graph.json
  changes         OpenSpec change id -> lanes
  lanes           per change: lane id -> risk, ui, sensitive, depends_on, tasks
  tasks           per lane: task id -> deps (task IDs within the same change)

Validation checks: shape, task-id bijection, reference resolution, acyclicity."""


class PlanError(ValueError):
    """A plan failed one of the four cheap checks."""


class StateError(ValueError):
    """A run state operation was refused."""


def _one_line(value: object) -> str:
    return str(value).replace("\n", " ").replace("\r", " ")


def _utc_timestamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        command = _one_line(self.prog)
        self.exit(2, f"{command}: error: {_one_line(message)}; run {command} --help\n")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"shape: cannot load {path}: {_one_line(exc)}; fix the plan file") from exc
    if not isinstance(value, dict):
        raise PlanError("shape: plan must be a JSON object; change it to an object")
    return value


_REPO_PLAN = ".agents/workflow/plan-graph.json"


def _archive_name_matches(name: str, change: str) -> bool:
    return re.fullmatch(rf"(?:\d{{4}}-\d{{2}}-\d{{2}}-)?{re.escape(change)}", name) is not None


def _openspec_change_directory(root: Path, change: str) -> Path:
    active = root / "openspec" / "changes" / _safe_change(change)
    if (active / "tasks.md").is_file():
        return active
    archive = root / "openspec/changes/archive"
    matches = [path for path in archive.iterdir()
               if _archive_name_matches(path.name, change) and (path / "tasks.md").is_file()] if archive.is_dir() else []
    if len(matches) > 1:
        raise StateError(f"state: multiple archives match change {change!r}; reconcile the archive identity")
    return matches[0] if matches else active


def _change_plans(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Project the repository hierarchy into the existing per-change gate inputs."""
    changes = document.get("changes")
    if document.get("schema_version") == 2 and isinstance(changes, dict):
        raise PlanError(
            "shape: schema_version 2 is retired; convert each lane's ui/screen to one ui flag "
            "(ui = ui or screen), remove screen, and set schema_version 3"
        )
    if document.get("schema_version") != 3 or not isinstance(changes, dict) or not changes:
        raise PlanError("shape: repository plan requires schema_version 3 and a non-empty changes object")
    result = {}
    for change, node in changes.items():
        _safe_change(change)
        if not isinstance(node, dict) or not isinstance(node.get("lanes"), dict) or not node["lanes"]:
            raise PlanError(f"shape: change {change!r} needs one or more lanes")
        lanes, tasks = {}, {}
        for lane_id, lane in node["lanes"].items():
            _safe_component(lane_id, "lane id")
            if not isinstance(lane, dict) or not isinstance(lane.get("tasks"), dict) or not lane["tasks"]:
                raise PlanError(f"shape: {change}/{lane_id} needs one or more tasks")
            lanes[lane_id] = {key: value for key, value in lane.items() if key != "tasks"}
            for task_id, task in lane["tasks"].items():
                if not isinstance(task_id, str) or not task_id.strip() or not isinstance(task, dict):
                    raise PlanError(f"shape: {change}/{lane_id} needs named task objects")
                if task_id in tasks:
                    raise PlanError(f"shape: duplicate task {task_id!r} across lanes in change {change!r}")
                tasks[task_id] = {**task, "lane": lane_id}
        result[change] = {**node, "change": change, "lanes": lanes, "tasks": tasks}
    return result


def _selected_plan(document: dict[str, Any], change: str) -> dict[str, Any]:
    plans = _change_plans(document)
    if change not in plans:
        raise PlanError(f"shape: change {change!r} is not in the repository plan; select a planned change")
    return plans[change]


def _plan_digest(document: dict[str, Any], change: str, text: str) -> str:
    # Other changes must not invalidate this change's review/grant bindings.
    source = json.dumps(_selected_plan(document, change), sort_keys=True) if "changes" in document else text
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _change_plan_at(checkout: Path, commit: str, change: str) -> tuple[dict[str, Any], str, str]:
    """Read the current local plan; the Coordinator reconstructs older schemas."""
    root = _workflow_checkout(checkout)
    path = root / _REPO_PLAN
    if not path.is_file():
        raise StateError("plan: reconstruct .agents/workflow/plan-graph.json using the current schema")
    text = path.read_text(encoding="utf-8")
    document = _load_json(path)
    return _selected_plan(document, change), _plan_digest(document, change, text), _REPO_PLAN


def _lane_plan_digest(plan: dict[str, Any], lane_id: str) -> str:
    value = {"change": plan["change"], "lane": lane_id, "definition": plan["lanes"][lane_id],
             "tasks": {task_id: task for task_id, task in plan["tasks"].items() if task["lane"] == lane_id}}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def _change_directory(plan_path: Path) -> str:
    directory = plan_path.resolve().parent
    return directory.parent.name if directory.name == "plan" else directory.name


def _check_shape(plan: dict[str, Any], plan_path: Path) -> None:
    expected_change = _change_directory(plan_path)
    if plan_path.name != "plan-graph.json" and plan.get("change") != expected_change:
        raise PlanError(
            f"shape: change must be {expected_change!r} for this plan directory; change the plan's change field"
        )

    if "review_mode" in plan:
        raise PlanError("shape: review_mode is retired; remove it (standard review is the only mode)")
    lanes = plan.get("lanes")
    tasks = plan.get("tasks")
    if not isinstance(lanes, dict):
        raise PlanError("shape: lanes must be an object; change lanes to a lane-id object")
    if not isinstance(tasks, dict):
        raise PlanError("shape: tasks must be an object; change tasks to a task-id object")

    for task_id, task in tasks.items():
        if not isinstance(task, dict):
            raise PlanError(f"shape: task {task_id!r} must be an object; change it to a task record")
        if not isinstance(task.get("lane"), str):
            raise PlanError(f"shape: task {task_id!r} lane must be a string; change it to a lane id")
        if not isinstance(task.get("deps"), list):
            raise PlanError(f"shape: task {task_id!r} deps must be a list; change it to a task-id list")

    for lane_id, lane in lanes.items():
        if not isinstance(lane, dict):
            raise PlanError(f"shape: lane {lane_id!r} must be an object; change it to a lane record")
        if lane.get("risk") not in {"high", "ordinary"}:
            raise PlanError(
                f"shape: lane {lane_id!r} risk must be 'high' or 'ordinary'; change the lane risk"
            )
        if "screen" in lane:
            raise PlanError(
                f"shape: lane {lane_id!r} uses the retired screen flag; set ui to ui or screen "
                "and remove screen (schema_version 3)"
            )
        # The gate-driving flags are required, not optional. A missing flag would be
        # coerced to false in the derived lane views, silently dropping the UI review and the
        # human checkpoint the plan flags exist to require.
        for flag in ("ui", "sensitive"):
            if not isinstance(lane.get(flag), bool):
                raise PlanError(
                    f"shape: lane {lane_id!r} {flag} must be true or false; change the lane flag"
                )


def _top_level_task_id_list(text: str) -> list[str]:
    """Ordered top-level task identifiers, duplicates included."""
    return [
        match.group(1)
        for line in text.splitlines()
        if (match := re.match(r"^-\s*\[[ xX]\]\s+(\S+)", line))
    ]


def _task_ids_from_text(text: str) -> set[str]:
    # Only top-level checklist entries are task identifiers. A nested checkbox is a phase or
    # annotation of the task above it (for example a `**Readiness**` bullet), not a task, and
    # reading its leading bold marker as an identifier would invent task ids.
    return set(_top_level_task_id_list(text))


def _check_duplicate_task_ids(text: str, source: str) -> None:
    """Refuse a tasks.md that lists one top-level task identifier more than once.

    The plan bijection compares identifier sets, so a duplicated identifier collapses silently:
    one copy can be ticked while the other stays open and the lane still derives complete.
    """
    counts: dict[str, int] = {}
    for identifier in _top_level_task_id_list(text):
        counts[identifier] = counts.get(identifier, 0) + 1
    duplicates = sorted(identifier for identifier, count in counts.items() if count > 1)
    if duplicates:
        raise PlanError(
            f"task-id duplicates in {source}: {', '.join(duplicates)}; list each top-level task identifier once"
        )


def _checked_task_ids_from_text(text: str) -> list[str]:
    checked: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^-\s*\[[xX]\]\s+(\S+)", line)
        if match and match.group(1) not in checked:
            checked.append(match.group(1))
    return checked


def _normalized_task_definitions(text: str) -> str:
    # A lane owns its own task status and nothing else about the contract. Normalizing every
    # checkbox marker to one placeholder lets a lane's own ticks through while any task
    # definition, prose, or task-set edit still differs from the approved base.
    return "\n".join(
        re.sub(r"^\s*-\s*\[[ xX]\]", "- [marked]", line) for line in text.splitlines()
    )


def _task_ids(tasks_path: Path) -> set[str]:
    try:
        text = tasks_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanError(
            f"task-id bijection: cannot load {tasks_path}: {_one_line(exc)}; provide a readable tasks.md"
        ) from exc
    _check_duplicate_task_ids(text, str(tasks_path))
    return _task_ids_from_text(text)


def _check_task_ids(plan: dict[str, Any], tasks_path: Path) -> None:
    plan_ids = set(plan["tasks"])
    source_ids = _task_ids(tasks_path)
    if plan_ids != source_ids:
        missing = sorted(source_ids - plan_ids)
        extra = sorted(plan_ids - source_ids)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"extra {', '.join(extra)}")
        raise PlanError(
            f"task-id bijection: {'; '.join(details)}; make plan task ids match tasks.md"
        )


def _check_references(plan: dict[str, Any]) -> None:
    lanes = plan["lanes"]
    tasks = plan["tasks"]
    lane_ids = set(lanes)
    task_ids = set(tasks)

    for task_id, task in tasks.items():
        lane_id = task["lane"]
        if lane_id not in lane_ids:
            raise PlanError(
                f"reference resolution: task {task_id!r} names unknown lane {lane_id!r}; change the task lane"
            )
        for dependency in task["deps"]:
            if not isinstance(dependency, str) or dependency not in task_ids:
                raise PlanError(
                    f"reference resolution: task {task_id!r} names unknown task {dependency!r}; change the dependency"
                )

    for lane_id, lane in lanes.items():
        dependencies = lane.get("depends_on", [])
        if not isinstance(dependencies, list):
            raise PlanError(
                f"reference resolution: lane {lane_id!r} depends_on must be a list; change it to lane ids"
            )
        for dependency in dependencies:
            if not isinstance(dependency, str) or dependency not in lane_ids:
                raise PlanError(
                    f"reference resolution: lane {lane_id!r} names unknown lane {dependency!r}; change the dependency"
                )


def _check_acyclic(plan: dict[str, Any]) -> None:
    def check_graph(graph: dict[str, list[Any]], label: str) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise PlanError(
                    f"acyclicity: {label} dependency cycle includes {node!r}; remove the cycle"
                )
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            visit(node)

    check_graph({task_id: task["deps"] for task_id, task in plan["tasks"].items()}, "task")
    effective = {lane_id: set(lane.get("depends_on", [])) for lane_id, lane in plan["lanes"].items()}
    for task in plan["tasks"].values():
        for dependency in task["deps"]:
            owner = plan["tasks"][dependency]["lane"]
            if owner != task["lane"]:
                effective[task["lane"]].add(owner)
    check_graph({lane_id: sorted(deps) for lane_id, deps in effective.items()}, "effective lane")


def validate_plan(plan_path: Path, tasks_path: Path | None = None, change: str | None = None) -> None:
    """Validate all repository changes, or one explicitly selected change."""
    document = _load_json(plan_path)
    plans = _change_plans(document)
    if change is not None:
        plans = {change: _selected_plan(document, change)}
    if tasks_path is not None and len(plans) != 1:
        raise PlanError("task-id bijection: --tasks requires --change when the repository plan contains multiple changes")
    for name, plan in plans.items():
        _check_shape(plan, plan_path)
        default = (_openspec_change_directory(plan_path.resolve().parents[2], name) / "tasks.md"
                   if "changes" in document else plan_path.resolve().parent.parent / "tasks.md")
        _check_task_ids(plan, tasks_path or default)
        _check_references(plan)
        _check_acyclic(plan)


def validate_workflow(root: Path) -> None:
    """Check present workflow records without creating state or inventing missing facts."""
    validate_plan(root / _REPO_PLAN)
    def require(ok: bool, message: str) -> None:
        if not ok:
            raise StateError("workflow: " + message)
    def records(value: object, label: str) -> list[dict[str, Any]]:
        require(isinstance(value, list) and all(isinstance(row, dict) for row in value),
                label + " must be an array of objects")
        return value
    def strings(value: object, label: str) -> None:
        require(isinstance(value, list) and all(isinstance(item, str) for item in value),
                label + " must be an array of strings")
    store = workflow_dir(root)
    plans = _change_plans(_load_json(root / _REPO_PLAN))
    if (store / "coordination.json").exists():
        coordination = _repository_coordination(root)
        for change, node in coordination["changes"].items():
            require(change in plans and isinstance(node, dict), "coordination change is not in the plan")
            require(isinstance(node.get("lanes"), dict), "coordination change needs lanes")
            for field in ("subagents", "dispatches", "reviews", "worktrees"):
                records(node.get(field, []), field)
            for field in ("human_gates", "authorizations"):
                require(isinstance(node.get(field, {}), dict), field + " must be an object")
            for lane_id, lane in node["lanes"].items():
                require(lane_id in plans[change]["lanes"] and isinstance(lane, dict),
                        "coordination lane is not in the plan")
                require(lane.get("phase", "created") in
                        {"created", "writing", "reviewing", "revising", "merged", "closed", "unknown"},
                        "invalid lane phase")
                for field in ("worktree", "ref", "base", "status", "status_reason"):
                    require(lane.get(field) is None or isinstance(lane[field], str), field + " must be a string or null")
                for field in ("dispatches", "reviews"):
                    records(lane.get(field, []), field)
                require(isinstance(lane.get("tasks", {}), dict), "lane tasks must be an object")
                for task_id, task in lane.get("tasks", {}).items():
                    require(task_id in _lane_assigned_tasks(plans[change], lane_id) and isinstance(task, dict)
                            and task.get("status") in (*_TASK_STATES, "unknown"), "invalid lane task state")
                for row in lane.get("reviews", []):
                    require(row.get("role") in _CANONICAL_ROLES and row.get("verdict") in {"PASS", "MORE WORK"}
                            and isinstance(row.get("binding"), dict) and bool(row.get("report")),
                            "review needs canonical role, verdict, binding, and report")
        for row in records(coordination.get("plan_amendments", []), "plan_amendments"):
            require(isinstance(row.get("human_request"), str) and bool(row["human_request"].strip())
                    and isinstance(row.get("inputs"), dict), "amendment needs human_request and inputs")
        records(coordination.get("advance_waivers", []), "advance_waivers")
    if (store / _GRAPH_REVIEW_LEDGER).exists():
        ledger, _ = _load_plan_ledger(None, root)
        for row in records(ledger["dispatches"], "planning dispatches"):
            require(row.get("scope") == "plan" and row.get("role") == "oracle"
                    and isinstance(row.get("inputs"), dict) and bool(row.get("key")),
                    "planning dispatch needs scope, oracle, inputs, key")
        for row in records(ledger["reviews"], "planning reviews"):
            require(row.get("scope") == "plan" and row.get("verdict") in {"PASS", "MORE WORK"}
                    and isinstance(row.get("inputs"), dict) and bool(row.get("report"))
                    and bool(row.get("report_sha256")) and bool(row.get("dispatch_key")),
                    "planning review needs verdict, inputs, report/hash, dispatch_key")
    if (store / _PLAN_APPROVAL).exists():
        approval = _load_json(store / _PLAN_APPROVAL)
        require(approval.get("schema_version") == 2 and approval.get("scope") == "repo"
                and isinstance(approval.get("inputs"), dict) and bool(approval.get("initial_review")),
                "plan approval needs schema_version 2, scope repo, inputs, initial_review")
        require(bool(approval.get("waiver")) or bool(approval.get("approver") and approval.get("approved_at")),
                "plan approval needs a human decision or explicit waiver")
    if (store / "finish.json").exists():
        finish = _load_finish_record(root)
        for field in ("evidence", "inspection", "required_checks", "inspection_inputs"):
            require(isinstance(finish.get(field, {}), dict), "finish " + field + " must be an object")
        for field in ("declared_checks", "required_inspections", "included_changes"):
            strings(finish.get(field, []), "finish " + field)
        records(finish.get("dispatches", []), "finish dispatches")
    for name in ("preflight.json", "model-selection.json"):
        if (store / name).exists():
            value = _load_json(store / name)
            require(value.get("schema_version") == 1, name + " needs schema_version 1")
            require(isinstance(value.get("harness"), str) and bool(value["harness"]), name + " needs harness")
            if name == "preflight.json":
                require(isinstance(value.get("capabilities"), dict), "preflight needs capabilities")
            else:
                require(value.get("scope") == "repo" and isinstance(value.get("roles"), dict),
                        "model selection needs scope repo and roles")
    # Provenance may accompany any reconstructed record; unknowns never become passing gates.
    for path in store.glob("*.json"):
        value = _load_json(path)
        if "reconstruction" in value:
            provenance = value["reconstruction"]
            require(isinstance(provenance, dict) and bool(provenance.get("sources"))
                    and bool(provenance.get("at")), "reconstruction needs sources and at")
            strings(provenance.get("unknowns", []), "reconstruction unknowns")


def _list(value: object) -> str:
    if not value:
        return "none"
    return ", ".join(f"`{item}`" for item in value)  # type: ignore[union-attr]


def _bool(value: object) -> str:
    return str(bool(value)).lower()


def render_markdown(plan: dict[str, Any]) -> str:
    if "changes" in plan:
        return "# Repository plan\n\n" + "\n".join(
            render_markdown(node) for node in _change_plans(plan).values()
        )
    lines = [f"# Plan: `{plan.get('change', '')}`", "", "## Lanes", ""]
    tasks = plan.get("tasks", {})
    lanes = plan.get("lanes", {})
    for lane_id, lane in lanes.items():
        members = [task_id for task_id, task in tasks.items() if task.get("lane") == lane_id]
        lines.append(
            f"- `{lane_id}` — risk: `{lane.get('risk', '')}`; ui: `{_bool(lane.get('ui'))}`; "
            f"sensitive: `{_bool(lane.get('sensitive'))}`; depends_on: {_list(lane.get('depends_on', []))}"
        )
        lines.append(f"  - tasks: {_list(members)}")
    lines.extend(["", "## Tasks", ""])
    for task_id, task in tasks.items():
        lines.append(
            f"- `{task_id}` — lane: `{task.get('lane', '')}`; deps: {_list(task.get('deps', []))}"
        )
    return "\n".join(lines) + "\n"


def _mermaid_id(prefix: str, value: object) -> str:
    return f"{prefix}_{str(value).encode('utf-8').hex()}"


def _mermaid_text(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', "\\\"").replace("\n", " ")


def render_mermaid(plan: dict[str, Any]) -> str:
    if "changes" in plan:
        lines = ["flowchart TD"]
        for change, node in _change_plans(plan).items():
            namespace = _mermaid_id("change", change)
            lines.append(f'  subgraph {namespace}["{_mermaid_text(change)}"]')
            child = render_mermaid(node).splitlines()[1:]
            for line in child:
                line = re.sub(r"\b(lane|task)_([0-9a-f]+)\b", lambda match: namespace + "_" + match[0], line)
                lines.append("  " + line)
            lines.append("  end")
        return "\n".join(lines) + "\n"
    lines = ["flowchart TD"]
    lanes = plan.get("lanes", {})
    tasks = plan.get("tasks", {})
    for lane_id, lane in lanes.items():
        lane_node = _mermaid_id("lane", lane_id)
        dependencies = ", ".join(str(item) for item in lane.get("depends_on", [])) or "none"
        label = (
            f"{lane_id} — risk: {lane.get('risk', '')}; ui: {_bool(lane.get('ui'))}; "
            f"sensitive: {_bool(lane.get('sensitive'))}; depends_on: {dependencies}"
        )
        lines.append(f'  subgraph {lane_node}["{_mermaid_text(label)}"]')
        for task_id, task in tasks.items():
            if task.get("lane") != lane_id:
                continue
            deps = ", ".join(str(item) for item in task.get("deps", [])) or "none"
            task_node = _mermaid_id("task", task_id)
            task_label = f"{task_id}<br/>lane: {lane_id}<br/>deps: {deps}"
            lines.append(f'    {task_node}["{_mermaid_text(task_label)}"]')
        lines.append("  end")

    for lane_id, lane in lanes.items():
        for dependency in lane.get("depends_on", []):
            lines.append(f"  {_mermaid_id('lane', dependency)} --> {_mermaid_id('lane', lane_id)}")
    for task_id, task in tasks.items():
        for dependency in task.get("deps", []):
            lines.append(f"  {_mermaid_id('task', dependency)} --> {_mermaid_id('task', task_id)}")
    return "\n".join(lines) + "\n"


def _safe_change(change: str) -> str:
    if not isinstance(change, str) or not change or change in {".", ".."} or Path(change).name != change:
        raise StateError("state: change must be a non-empty path-safe name; change it")
    return change


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def workflow_dir(root: Path) -> Path:
    """Coordinator-owned store for one repository."""
    return root / ".agents" / "workflow"


def _workflow_checkout(checkout: Path) -> Path:
    root = checkout.resolve()
    if (root / _REPO_PLAN).is_file() or (workflow_dir(root) / "coordination.json").is_file():
        return root
    # A lane worktree has no tracked plan. Its owner is recorded in the shared lane ledger.
    for line in _state_git(root, "worktree", "list", "--porcelain").stdout.splitlines():
        if not line.startswith("worktree "):
            continue
        owner = Path(line[9:]).resolve()
        path = workflow_dir(owner) / "coordination.json"
        if not path.is_file():
            continue
        data = _read_state(path)
        for change in data.get("changes", {}).values():
            for lane in change.get("lanes", {}).values():
                if isinstance(lane, dict) and lane.get("worktree") and Path(lane["worktree"]).resolve() == root:
                    return owner
    return root


def _ensure_workflow_ignored(root: Path) -> None:
    """Ignore the coordinator store without touching a tracked file.

    Writes to .git/info/exclude, which git never tracks, so a clean-tree gate stays clean.
    """
    rule = ".agents/workflow/"
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-path", "info/exclude"],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return
    if result.returncode != 0:
        return
    target = Path(result.stdout.strip())
    if not target.is_absolute():
        target = root / target
    try:
        text = target.read_text(encoding="utf-8") if target.is_file() else ""
    except OSError:
        return
    if rule in {line.strip() for line in text.splitlines()}:
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(("" if text.endswith("\n") or not text else "\n") + rule + "\n")
    except OSError:
        return


def _coordination_path(change: str, checkout: Path | None = None) -> Path:
    _safe_change(change)
    return workflow_dir((checkout or Path.cwd()).resolve()) / "coordination.json"


_FINISH_RECORD = "finish.json"


def finish_record_path(checkout: Path) -> Path:
    """Repo-level finish record shared by every OpenSpec change in the PR."""
    return workflow_dir(checkout.resolve()) / _FINISH_RECORD


def _load_finish_record(checkout: Path) -> dict[str, Any]:
    root = checkout.resolve()
    path = finish_record_path(root)
    if not path.is_file():
        return {"schema_version": 1, "feature_checkout": str(root)}
    value = _read_state(path)
    if value.get("schema_version") != 1:
        raise StateError(f"finish: {path} needs schema_version 1; repair the finish record")
    if value.get("feature_checkout") != str(root):
        raise StateError("finish: finish record belongs to another checkout; reconcile it")
    return value


def _write_finish_record(checkout: Path, value: dict[str, Any]) -> Path:
    root = checkout.resolve()
    value["schema_version"] = 1
    value["feature_checkout"] = str(root)
    path = finish_record_path(root)
    _atomic_write_json(path, value)
    return path


def _finish_changes(checkout: Path) -> list[str]:
    """Every OpenSpec change included in the current repository plan."""
    root = _workflow_checkout(checkout)
    path = root / _REPO_PLAN
    if path.is_file():
        return sorted(_change_plans(_load_json(path)))
    return _active_changes(root)


def _finish_plan_digest(checkout: Path) -> str:
    return _graph_structure_digest(_load_json(_workflow_checkout(checkout) / _REPO_PLAN))


def _finish_integrated(checkout: Path, head: str) -> None:
    """Require every lane of every included change to be merged into the feature branch."""
    root = _workflow_checkout(checkout)
    changes = _finish_changes(root)
    if not changes:
        raise StateError("finish: no OpenSpec changes; author and approve a plan before release")
    repository = _repository_coordination(root)
    for change in changes:
        state = state_derive(change, None, root)
        recorded = repository.get("changes", {}).get(change, {}).get("lanes", {})
        if not isinstance(recorded, dict):
            raise StateError(f"finish: coordination lanes for {change!r} are malformed; repair them")
        lanes = state.get("lanes", {})
        if not lanes:
            raise StateError(f"finish: change {change!r} has no lanes; repair the plan")
        for lane_id, lane in lanes.items():
            merge = recorded.get(lane_id, {}).get("merge") if isinstance(recorded.get(lane_id), dict) else None
            if not isinstance(lane, dict) or lane.get("complete") is not True or not isinstance(merge, dict):
                raise StateError(
                    f"finish: lane {change}/{lane_id} is not merged into the feature branch; "
                    "integrate every lane in every included change first"
                )
            merged_head = merge.get("merge_commit")
            if not isinstance(merged_head, str) or not _is_ancestor(root, merged_head, head):
                raise StateError(
                    f"finish: lane {change}/{lane_id} merge commit is not in integrated HEAD"
                )


def _read_state(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    path = Path(value)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"state: cannot load {path}: {_one_line(exc)}; repair the derived state") from exc
    if not isinstance(value, dict):
        raise StateError("state: state.json must contain an object; re-derive the state")
    return value


def _atomic_write_text(path: Path, value: str) -> None:
    temporary: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise StateError(f"state: cannot write {path}: {_one_line(exc)}; check file permissions") from exc
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _atomic_write_json(path: Path, value: object) -> None:
    temporary: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise StateError(f"state: cannot write {path}: {_one_line(exc)}; check file permissions") from exc
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


_GRAPH_REVIEW_LEDGER = "plan-graph-review.json"
_PLAN_APPROVAL = "plan-approval.json"


def _plan_reviews_path(change: str | None, checkout: Path | None = None) -> Path:
    return workflow_dir((checkout or Path.cwd()).resolve()) / _GRAPH_REVIEW_LEDGER


def _load_plan_ledger(change: str | None, checkout: Path | None = None) -> tuple[dict[str, Any], Path]:
    path = _plan_reviews_path(change, checkout)
    if not path.exists():
        return {
            "change": change,
            "dispatches": [],
            "reviews": [],
        }, path
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"plan: cannot load {path}: {_one_line(exc)}; repair or remove it") from exc
    if not isinstance(value, dict):
        raise StateError(f"plan: {path} must contain a JSON object; repair or remove it")
    if value.get("change") != change:
        raise StateError(f"plan: {path} belongs to another change; reconcile it")
    dispatches = value.setdefault("dispatches", [])
    reviews = value.setdefault("reviews", [])
    if not isinstance(dispatches, list) or not isinstance(reviews, list):
        raise StateError(f"plan: {path} has malformed dispatch/review records; repair it")
    return value, path


def _write_plan_ledger(change: str | None, checkout: Path | None, ledger: dict[str, Any]) -> Path:
    path = _plan_reviews_path(change, checkout)
    _atomic_write_json(path, ledger)
    return path


def _plan_head(repository: Path) -> str:
    result = _run_git(repository, "rev-parse", "HEAD")
    if result.returncode or not result.stdout.strip():
        detail = _one_line(result.stderr.strip() or result.stdout.strip())
        raise StateError(
            f"plan: cannot resolve feature HEAD{(': ' + detail) if detail else ''}; repair the checkout"
        )
    return result.stdout.strip()


def _change_artifact_files(root: Path, change: str) -> tuple[Path, dict[str, str]]:
    """Capture historical review inputs and identify later human-requested amendments."""
    directory = _openspec_change_directory(root, change)
    if not (directory / "tasks.md").is_file():
        raise StateError(f"plan: {directory} needs OpenSpec artifacts including tasks.md")
    files = {}
    for path in sorted(directory.rglob("*")):
        if (not path.is_file() or path.relative_to(directory).parts[0] == "plan"
                or path.suffix.lower() not in {".md", ".yaml", ".yml", ".json"}):
            continue
        data = path.read_bytes()
        if path.name == "tasks.md" and path.parent == directory:
            data = _normalized_task_definitions(data.decode("utf-8")).encode("utf-8")
        files[str(path.relative_to(directory))] = hashlib.sha256(data).hexdigest()
    return directory, files


def _graph_structure_digest(document: dict[str, Any]) -> str:
    """Bind task order and scope explicitly, without prose or artifact contents."""
    structure = {
        change: {
            "lanes": {
                lane_id: {key: lane.get(key) for key in
                          ("risk", "ui", "sensitive", "depends_on")}
                for lane_id, lane in plan["lanes"].items()
            },
            # A list preserves task order even though object keys are canonicalized.
            "tasks": [{"id": task_id, "lane": task["lane"], "deps": task.get("deps", [])}
                      for task_id, task in plan["tasks"].items()],
        }
        for change, plan in _change_plans(document).items()
    }
    return hashlib.sha256(json.dumps(structure, sort_keys=True).encode("utf-8")).hexdigest()


def _planning_inputs(root: Path) -> dict[str, Any]:
    path = root / _REPO_PLAN
    validate_plan(path)
    document = _load_json(path)
    return {"plan_sha256": _graph_structure_digest(document),
            "artifacts": {change: _change_artifact_files(root, change)[1]
                          for change in _change_plans(document)}}


def plan_dispatch(role: str = "oracle", task: str = "plan", key: str | None = None,
                  report: object = None) -> Path:
    """Record one initial review of the artifacts and lane plan together."""
    _require_coordinator_cwd({}, "dispatch --scope plan")
    if role != "oracle" or task != "plan":
        raise StateError("plan: initial review uses role oracle and task plan")
    root = _workflow_checkout(Path.cwd())
    _ensure_workflow_ignored(root)
    inputs = _planning_inputs(root)
    ledger, path = _load_plan_ledger(None, root)
    if any(row.get("verdict") == "PASS" for row in ledger["reviews"]):
        raise StateError("plan: initial Oracle PASS is already recorded; later edits need no re-review")
    report_path = str(Path(str(report)).expanduser().resolve()) if report else None
    intent = {"scope": "plan", "role": role, "task": task,
              "key": key or uuid.uuid4().hex, "head": _plan_head(root),
              "inputs": inputs, "report": report_path, "started_at": _utc_timestamp()}
    for row in ledger["dispatches"]:
        if row.get("key") == intent["key"]:
            if any(row.get(field) != intent[field] for field in ("inputs", "head", "report", "role", "task")):
                raise StateError("plan: dispatch key has a different intent; use a new key")
            return path
    if any(row.get("result") is None for row in ledger["dispatches"]):
        raise StateError("plan: review worker is still open; establish ownership and complete it before replacement")
    ledger["dispatches"].append(intent)
    return _write_plan_ledger(None, root, ledger)


def plan_review(verdict: str, head: str, report: object) -> Path:
    _require_coordinator_cwd({}, "review --scope plan")
    root = _workflow_checkout(Path.cwd())
    ledger, path = _load_plan_ledger(None, root)
    if verdict not in {"PASS", "MORE WORK"}:
        raise StateError("plan: verdict must be PASS or MORE WORK")
    if any(row.get("verdict") == "PASS" for row in ledger["reviews"]):
        raise StateError("plan: initial Oracle PASS is already recorded")
    pending = [row for row in ledger["dispatches"] if row.get("result") is None]
    if len(pending) != 1:
        raise StateError("plan: record one dispatch --scope plan before its review")
    intent = pending[0]
    inputs = _planning_inputs(root)
    reviewed_head = _git_commit(root, head)
    if inputs != intent.get("inputs") or reviewed_head != intent.get("head") or reviewed_head != _plan_head(root):
        raise StateError("plan: review inputs changed; close the old dispatch and review the corrected plan")
    report_path = _review_report(report)
    if intent.get("report") and intent["report"] != report_path:
        raise StateError("plan: review report differs from dispatch report")
    ledger["reviews"].append({"scope": "plan", "role": "oracle", "verdict": verdict,
        "head": reviewed_head, "inputs": inputs, "report": report_path,
        "report_sha256": _path_sha256(Path(report_path)), "dispatch_key": intent["key"],
        "recorded_at": _utc_timestamp()})
    intent.update({"result": "ok", "verdict": verdict})
    return _write_plan_ledger(None, root, ledger)


def _initial_plan_pass(root: Path) -> dict[str, Any] | None:
    ledger, _ = _load_plan_ledger(None, root)
    for row in ledger["reviews"]:
        if row.get("scope") == "plan" and row.get("verdict") == "PASS":
            report = _required_document(row.get("report"), "initial Oracle report")
            if _path_sha256(Path(report)) != row.get("report_sha256"):
                raise StateError("plan: initial Oracle report changed; reconstruct its supported record")
            return row
    return None


def plan_approve(approver: str = "human", waiver: str | None = None,
                 lanes: list[str] | None = None, all_lanes: bool = False) -> Path:
    """Record initial authorization, never manufacture a human decision."""
    _require_coordinator_cwd({}, "plan-approve")
    root = _workflow_checkout(Path.cwd())
    if waiver is not None:
        if not waiver.strip() or bool(lanes) == all_lanes:
            raise StateError("plan: waiver requires a reason and either --lane or --all-lanes")
        plans = _change_plans(_load_json(root / _REPO_PLAN))
        if lanes and any(len(item.split("/")) != 2 or item.split("/")[0] not in plans
                         or item.split("/")[1] not in plans[item.split("/")[0]]["lanes"] for item in lanes):
            raise StateError("plan: waiver lanes must name planned change/lane pairs")
    elif lanes or all_lanes:
        raise StateError("plan: lane scope requires --waiver")
    reviewed = _initial_plan_pass(root)
    if reviewed is None:
        raise StateError("plan: initial combined Oracle PASS is required before approval")
    inputs = _planning_inputs(root)
    if _git_output(root, "status", "--porcelain", "--untracked-files=all", "--", "openspec/changes"):
        raise StateError("plan: commit the OpenSpec artifacts before recording approval")
    record = {"schema_version": 2, "scope": "repo", "approver": approver,
              "approved_at": _utc_timestamp(), "head": _plan_head(root),
              "initial_review": reviewed["dispatch_key"], "inputs": inputs}
    if waiver is not None:
        record["waived_by"] = record.pop("approver")
        record["waived_at"] = record.pop("approved_at")
        record["waiver"] = {"reason": waiver, "lanes": lanes or [], "all_lanes": all_lanes}
    path = workflow_dir(root) / _PLAN_APPROVAL
    _atomic_write_json(path, record)
    return path


def _plan_approval_status(state: dict[str, Any]) -> dict[str, Any]:
    """Initial authorization persists; human-requested amendments are recorded in coordination."""
    root = Path(str(state["feature_checkout"]))
    path = workflow_dir(root) / _PLAN_APPROVAL
    if not path.is_file():
        return {"state": "missing"}
    try:
        record = _load_json(path)
        if record.get("schema_version") != 2:
            raise StateError("reconstruct initial approval using schema_version 2")
        review = _initial_plan_pass(root)
        if review is None or record.get("initial_review") != review.get("dispatch_key"):
            raise StateError("initial Oracle review binding is missing")
        waiver = record.get("waiver")
        if waiver is not None and (not isinstance(waiver, dict) or not waiver.get("reason")
                or not isinstance(waiver.get("lanes"), list)
                or not all(isinstance(lane, str) for lane in waiver["lanes"])
                or not isinstance(waiver.get("all_lanes"), bool)
                or bool(waiver["lanes"]) == waiver["all_lanes"]):
            raise StateError("malformed plan waiver")
        if waiver is None and (not record.get("approver") or not record.get("approved_at")):
            raise StateError("initial human approval is missing")
        inputs = _planning_inputs(root)
        if inputs != record.get("inputs"):
            amendments = _repository_coordination(root).get("plan_amendments", [])
            if not any(isinstance(item, dict) and item.get("human_request")
                       and item.get("inputs") == inputs for item in amendments):
                return {**record, "state": "stale", "detail":
                        "record the initiating human request and current inputs in coordination.plan_amendments"}
        clean = _run_git(root, "status", "--porcelain", "--untracked-files=all", "--", "openspec/changes")
        if clean.returncode or clean.stdout.strip():
            return {**record, "state": "stale", "detail": "commit the changed OpenSpec artifacts"}
        return {**record, "state": "waived" if waiver is not None else "current"}
    except (OSError, ValueError, StateError) as exc:
        return {"state": "unreadable", "detail": _one_line(exc)}


def _require_plan_authorization(state: dict[str, Any], lane_id: str) -> None:
    approval = _plan_approval_status(state)
    waiver = approval.get("waiver")
    if approval.get("state") == "current":
        return
    if (approval.get("state") == "waived" and isinstance(waiver, dict)
            and (waiver.get("all_lanes") or f"{state['change']}/{lane_id}" in waiver.get("lanes", []))):
        return
    raise StateError(f"lane: plan authorization is {approval.get('state')}: "
                     f"{approval.get('detail', 'record initial plan approval or its explicit waiver')}")


def _state_git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repository), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise StateError(f"state: git unavailable: {_one_line(exc)}; use a git checkout") from exc


def _resolve_state_ref(checkout: Path, source_ref: str | None) -> tuple[str, str]:
    ref = source_ref or "HEAD"
    if not isinstance(ref, str) or not ref.strip() or ref.startswith("-") or "\x00" in ref or "\n" in ref or "\r" in ref:
        raise StateError("state: committed ref must be a non-empty git ref; change --ref")
    result = _state_git(checkout, "rev-parse", "--verify", f"{ref}^{{commit}}")
    commit = result.stdout.strip()
    if result.returncode or not commit or len(commit.splitlines()) != 1:
        detail = _one_line(result.stderr.strip() or result.stdout.strip())
        raise StateError(
            f"state: committed ref {ref!r} cannot be resolved{(': ' + detail) if detail else ''}; change --ref"
        )
    return ref, commit


def _committed_file(checkout: Path, commit: str, relative: str) -> str:
    result = _state_git(checkout, "show", f"{commit}:{relative}")
    if result.returncode:
        detail = _one_line(result.stderr.strip() or result.stdout.strip())
        raise StateError(
            f"state: committed artifact {relative} is unavailable at {commit}"
            f"{(': ' + detail) if detail else ''}; commit the artifact first"
        )
    return result.stdout


def _committed_tasks(
    checkout: Path, commit: str, change: str, allow_draft: bool = False,
) -> tuple[str, str]:
    active = f"openspec/changes/{_safe_change(change)}/tasks.md"
    result = _state_git(checkout, "show", f"{commit}:{active}")
    if result.returncode == 0:
        return result.stdout, active
    paths = _state_git(checkout, "ls-tree", "-r", "--name-only", commit, "--", "openspec/changes/archive").stdout.splitlines()
    matches = [path for path in paths if Path(path).name == "tasks.md"
               and _archive_name_matches(Path(path).parent.name, change)]
    if len(matches) > 1:
        raise StateError(f"state: multiple committed archives match change {change!r}; reconcile the archive identity")
    if allow_draft and not matches and (checkout / active).is_file():
        return (checkout / active).read_text(encoding="utf-8"), active
    relative = matches[0] if matches else active
    return _committed_file(checkout, commit, relative), relative


def _check_task_ids_text(plan: dict[str, Any], tasks_text: str, source: str) -> None:
    _check_duplicate_task_ids(tasks_text, source)
    plan_ids = set(plan["tasks"])
    source_ids = _task_ids_from_text(tasks_text)
    if plan_ids != source_ids:
        missing = sorted(source_ids - plan_ids)
        extra = sorted(plan_ids - source_ids)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"extra {', '.join(extra)}")
        raise PlanError(
            f"task-id bijection: {'; '.join(details)}; make plan task ids match {source}"
        )


def _lane_readiness(
    lane_id: str,
    lanes: dict[str, dict[str, Any]],
    plan: dict[str, Any],
    checked_tasks: set[str],
) -> str:
    lane = lanes[lane_id]
    tasks = plan["tasks"]
    assigned = list(lane.get("tasks", []))
    if lane.get("complete"):
        return "complete: every assigned task is ticked"
    lane_dependencies = [
        dependency for dependency in lane.get("depends_on", [])
        if not lanes[dependency].get("complete")
    ]
    if lane_dependencies:
        return "blocked: lane dependencies: " + ", ".join(lane_dependencies)
    candidates = [task_id for task_id in assigned if task_id not in checked_tasks]
    ready = []
    blocked = []
    for task_id in candidates:
        unmet = []
        for dependency in tasks[task_id].get("deps", []):
            dependency_lane = tasks[dependency].get("lane")
            if dependency_lane == lane_id:
                if dependency not in checked_tasks:
                    unmet.append(dependency)
            elif not lanes[dependency_lane].get("complete"):
                unmet.append(dependency)
        if unmet:
            blocked.append(f"{task_id} needs {', '.join(unmet)}")
        else:
            ready.append(task_id)
    if ready:
        readiness = "ready: " + ", ".join(ready) + " has satisfied prerequisites"
        if blocked:
            readiness += "; blocked: " + "; ".join(blocked)
        return readiness
    if blocked:
        return "blocked: " + "; ".join(blocked)
    return "blocked: no unticked task remains eligible"


def _derived_lane_views(
    plan: dict[str, Any], checked_tasks: set[str]
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    plan_lanes = plan["lanes"]
    plan_tasks = plan["tasks"]
    lanes: dict[str, dict[str, Any]] = {}
    for lane_id, lane in plan_lanes.items():
        members = [task_id for task_id, task in plan_tasks.items() if task.get("lane") == lane_id]
        lane_view = dict(lane)
        lane_view["depends_on"] = list(lane.get("depends_on", []))
        lane_view["risk"] = lane.get("risk")
        lane_view["ui"] = bool(lane.get("ui"))
        lane_view["sensitive"] = bool(lane.get("sensitive"))
        lane_view["tasks"] = members
        lane_view["checked_tasks"] = [task_id for task_id in members if task_id in checked_tasks]
        lane_view["complete"] = set(members) <= checked_tasks
        lane_view["ready"] = False
        lanes[lane_id] = lane_view
    _apply_readiness(lanes, plan, checked_tasks)
    return lanes, [lane_id for lane_id, lane in lanes.items() if lane.get("ready")]


def _apply_readiness(
    lanes: dict[str, dict[str, Any]], plan: dict[str, Any], checked_tasks: set[str]
) -> None:
    for lane_id, lane in lanes.items():
        lane["readiness"] = _lane_readiness(lane_id, lanes, plan, checked_tasks)
        lane["ready"] = lane["readiness"].startswith("ready:")


def _derived_task_views(plan: dict[str, Any], checked_tasks: set[str]) -> dict[str, dict[str, Any]]:
    tasks: dict[str, dict[str, Any]] = {}
    for task_id, task in plan["tasks"].items():
        task_view = dict(task)
        task_view["deps"] = list(task.get("deps", []))
        task_view["checked"] = task_id in checked_tasks
        task_view["status"] = "complete" if task_view["checked"] else "pending"
        tasks[task_id] = task_view
    return tasks


def state_derive(
    change: str, source_ref: str | None = None, checkout: Path | None = None,
    allow_draft: bool = False,
) -> dict[str, Any]:
    change = _safe_change(change)
    repository = (checkout or Path.cwd()).resolve()
    resolved_ref, source_sha = _resolve_state_ref(repository, source_ref)
    plan, plan_sha256, plan_relative = _change_plan_at(repository, source_sha, change)
    tasks_text, tasks_relative = _committed_tasks(repository, source_sha, change, allow_draft)
    draft = allow_draft and _state_git(
        repository, "cat-file", "-e", f"{source_sha}:{tasks_relative}"
    ).returncode != 0
    _validate_change_plan(plan, change, tasks_text, repository)
    checked_order = [] if draft else _checked_task_ids_from_text(tasks_text)
    checked_tasks = set(checked_order)
    lanes, ready_lanes = _derived_lane_views(plan, checked_tasks)
    owner = _workflow_checkout(repository)
    for lane_id, lane in lanes.items():
        lane["runtime_plan"] = plan_relative == _REPO_PLAN
        lane["lane_plan_sha256"] = _lane_plan_digest(plan, lane_id)
    state = {
        "schema_version": 3,
        "change": change,
        "source_ref": resolved_ref,
        "source_sha": source_sha,
        "branch": _state_git(owner, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip() or "HEAD",
        "feature_checkout": str(owner),
        "runtime_plan": plan_relative == _REPO_PLAN,
        "plan_sha256": plan_sha256,
        "plan_revision": plan_sha256,
        "tasks_path": tasks_relative,
        "checked_tasks": [task_id for task_id in plan["tasks"] if task_id in checked_tasks],
        "tasks": _derived_task_views(plan, checked_tasks),
        "lanes": lanes,
        "ready_lanes": ready_lanes,
    }
    if draft:
        state["planning"] = "draft"
    return state


_COORDINATION_FIELDS = (
    "phase", "worktree", "ref", "base", "dispatches", "reviews", "merge", "cleanup",
    "status", "status_reason", "task_states", "review_status", "reconstruction",
)

# Task states the coordinator store tracks per lane. "done" here is a lane-local claim; only a
# committed and merged tick makes it authoritative on the feature branch.
_TASK_STATES = ("pending", "in_progress", "done", "blocked")

# Canonical subagent roles for dispatch and review; the coordinator runs in the current session.
_CANONICAL_ROLES = ("implementer", "code-reviewer", "test-reviewer", "ui-reviewer", "oracle", "runner")


def _require_role(role: object) -> str:
    """Return the canonical role ID or refuse an alias/non-canonical name."""
    if not isinstance(role, str) or not role.strip():
        raise StateError(
            f"state: role must be one of {', '.join(_CANONICAL_ROLES)}; change it"
        )
    value = role.strip()
    if value not in _CANONICAL_ROLES:
        raise StateError(
            f"state: role {value!r} is not canonical; use one of {', '.join(_CANONICAL_ROLES)}"
        )
    return value


def _coordination_lane(lane: dict[str, Any] | None = None) -> dict[str, Any]:
    value = dict(lane) if isinstance(lane, dict) else {}
    defaults: dict[str, Any] = {
        "phase": "created",
        "worktree": None,
        "ref": None,
        "base": None,
        "dispatches": [],
        "reviews": [],
        "merge": None,
        "cleanup": None,
        "status": None,
        "status_reason": None,
        "task_states": {},
        "review_status": None,
        "reconstruction": None,
    }
    for key, default in defaults.items():
        if key not in value:
            value[key] = json.loads(json.dumps(default))
    return {key: value[key] for key in _COORDINATION_FIELDS}


def _lane_ref_progress(
    state: dict[str, Any], lane: dict[str, Any], plan: dict[str, Any]
) -> tuple[set[str], str | None]:
    """Checked tasks on the lane ref. The lane is the only place unmerged ticks exist."""
    ref = lane.get("ref")
    checkout_value = state.get("feature_checkout")
    change = state.get("change")
    if not isinstance(ref, str) or not ref or not isinstance(checkout_value, str) or not isinstance(change, str):
        return set(), None
    repository = Path(checkout_value).expanduser().resolve()
    try:
        head = _ref_sha(repository, ref)
        if not head:
            return set(), None
        text, relative = _committed_tasks(repository, head, change)
        _check_task_ids_text(plan, text, f"{relative} at lane ref")
        return set(_checked_task_ids_from_text(text)), head
    except (OSError, PlanError, StateError, ValueError) as exc:
        raise StateError(f"state: lane ref {ref} tasks cannot be read: {_one_line(exc)}; reconcile the lane ref") from exc


def _require_coordinator_cwd(state: dict[str, Any], command: str) -> None:
    """Refuse writes from a lane worktree: the coordinator store has one writer."""
    branch = subprocess.run(
        ["git", "-C", str(Path.cwd()), "symbolic-ref", "--short", "-q", "HEAD"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    if branch.startswith("featdev/"):
        raise StateError(
            f"{command}: refused on lane branch {branch}; "
            "run from the feature checkout. Lane worktrees and workers never write the coordinator store"
        )


def _register_subagent(
    coordination: dict[str, Any], lane_id: str | None, role: str,
    task: str | None, key: str | None, access: str | None, worktree: str | None,
) -> None:
    rows = coordination.get("subagents")
    if not isinstance(rows, list):
        rows = []
    if any(row.get("lane") == lane_id and row.get("key") == key for row in rows):
        return
    rows.append({
        "run_id": None, "key": key, "lane": lane_id, "role": role, "task": task,
        "access": access, "worktree": worktree,
        "status": "running", "started_at": _now_iso(), "updated_at": _now_iso(),
    })
    coordination["subagents"] = rows


def _close_subagent(
    coordination: dict[str, Any], lane_id: str | None, role: str, run_id: str | None, result: str | None,
    key: str | None = None,
) -> None:
    rows = coordination.get("subagents")
    if not isinstance(rows, list):
        return
    candidates = [
        row for row in rows
        if isinstance(row, dict) and row.get("lane") == lane_id and row.get("role") == role
        and row.get("status") == "running"
        and (key is None or row.get("key") == key)
    ]
    if not candidates:
        return
    row = candidates[-1]
    row["status"] = "done" if result in (None, "ok") else "failed"
    if run_id:
        row["run_id"] = run_id
    row["result"] = result
    row["updated_at"] = _now_iso()


def _lane_review_status(lane: dict[str, Any]) -> dict[str, Any] | None:
    reviews = [row for row in lane.get("reviews", []) if isinstance(row, dict)]
    if not reviews:
        return None
    last = reviews[-1]
    return {
        "state": "passed" if last.get("verdict") == "PASS" else "changes_requested",
        "round": len(reviews),
        "reviewer": last.get("role"),
        "commit": _review_commit(last),
        "report": last.get("report"),
    }


def _new_coordination(state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "change": state["change"],
        "feature_checkout": state["feature_checkout"],
        "branch": state.get("branch") or "HEAD",
        "plan_sha256": state["plan_sha256"],
        "subagents": [],
        "lanes": {
            lane_id: _coordination_lane({"task_states": {
                task_id: {"status": "pending"} for task_id, task in plan["tasks"].items()
                if task["lane"] == lane_id
            }}) for lane_id in plan.get("lanes", {})
        },
        "dispatches": [],
        "reviews": [],
        "worktrees": [],
        "authorizations": {},
        "human_gates": {},
    }


def _repository_coordination(checkout: Path) -> dict[str, Any]:
    path = workflow_dir(checkout) / "coordination.json"
    if not path.exists():
        return {"schema_version": 2, "feature_checkout": str(checkout), "changes": {}}
    value = _read_state(path)
    if value.get("schema_version") != 2 or not isinstance(value.get("changes"), dict):
        raise StateError(f"state: reconstruct {path} using coordination schema_version 2")
    if value.get("feature_checkout") != str(checkout):
        raise StateError("state: repository coordination belongs to another checkout; reconcile it")
    return value


def _stored_change_coordination(coordination: dict[str, Any]) -> dict[str, Any]:
    value = json.loads(json.dumps({key: item for key, item in coordination.items() if not key.startswith("_")}))
    for key in ("schema_version", "change", "feature_checkout", "review_mode", "finish", "finish_dispatches"):
        value.pop(key, None)
    lanes = value.get("lanes")
    if not isinstance(lanes, dict):
        raise StateError("state: coordination lanes must be an object; reconstruct the ledger using the current schema")
    for lane in lanes.values():
        if not isinstance(lane, dict):
            raise StateError("state: coordination lane must be an object; reconstruct the ledger using the current schema")
        lane["tasks"] = lane.pop("task_states", lane.get("tasks", {}))
        if not isinstance(lane["tasks"], dict):
            raise StateError("state: coordination lane tasks must be an object; reconstruct the ledger using the current schema")
    return value


def _load_coordination(state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    change = state.get("change")
    checkout = state.get("feature_checkout")
    if not isinstance(change, str) or not isinstance(checkout, str):
        raise StateError("state: coordination context is missing; re-derive the state")
    path = _coordination_path(change, Path(checkout))
    repository = _repository_coordination(Path(checkout))
    value = repository["changes"].get(change)
    if value is None:
        result = _new_coordination(state, plan)
        result["_available"] = False
        result["_error"] = "coordination data is missing for this change"
        return result
    if not isinstance(value, dict):
        raise StateError("state: coordination data must be an object; repair or remove it")
    value = {**value, "change": change, "feature_checkout": checkout}
    if value.get("run_id"):
        raise StateError("state: coordination data contains a run identifier; remove the legacy record")
    if value.get("change") != change or value.get("feature_checkout") != checkout:
        raise StateError("state: coordination data belongs to another change or checkout; reconcile it")
    if value.get("branch") not in {None, state.get("branch"), "HEAD"}:
        raise StateError("state: coordination branch differs from derived state; reconcile it")
    lanes = value.get("lanes")
    if not isinstance(lanes, dict):
        raise StateError("state: coordination lanes are missing; repair or remove the coordination data")
    result = _new_coordination(state, plan)
    for key in ("dispatches", "reviews", "worktrees", "authorizations", "human_gates", "subagents"):
        if key in value:
            result[key] = value[key]
    for lane_id in plan.get("lanes", {}):
        if lane_id in lanes:
            if not isinstance(lanes[lane_id], dict):
                raise StateError(f"state: coordination lane {lane_id!r} is malformed; repair it")
            lane = dict(lanes[lane_id])
            lane["task_states"] = lane.pop("tasks", lane.get("task_states", {}))
            result["lanes"][lane_id] = _coordination_lane(lane)
    for key in ("reconstruction", "plan_amendments"):
        if key in value:
            result[key] = value[key]
    result["_available"] = True
    return result


def _overlay_coordination(
    state: dict[str, Any], coordination: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    view = json.loads(json.dumps(state))
    lanes = view.get("lanes")
    coordination_lanes = coordination.get("lanes")
    if isinstance(lanes, dict) and isinstance(coordination_lanes, dict):
        for lane_id, lane in lanes.items():
            if not isinstance(lane, dict):
                continue
            runtime = coordination_lanes.get(lane_id)
            if isinstance(runtime, dict):
                lane.update({key: json.loads(json.dumps(runtime.get(key))) for key in _COORDINATION_FIELDS})
    gates = coordination.get("human_gates")
    view["human_gates"] = gates if isinstance(gates, dict) else {}
    # The summary validates a gate through the merge validator, which needs the live grants too.
    authorizations = coordination.get("authorizations")
    view["authorizations"] = authorizations if isinstance(authorizations, dict) else {}
    finish: dict[str, Any] = {}
    try:
        finish = _load_finish_record(Path(str(state["feature_checkout"])))
    except StateError as exc:
        finish = {"_error": _one_line(exc)}
    view["finish"] = finish
    view["finish_dispatches"] = finish.get("dispatches", []) if isinstance(finish, dict) else []
    view["coordination_path"] = str(
        _coordination_path(str(state["change"]), Path(str(state["feature_checkout"])))
    )
    view["subagents"] = coordination.get("subagents") if isinstance(coordination.get("subagents"), list) else []
    view["coordination_available"] = coordination.get("_available", True) is not False
    if not view["coordination_available"]:
        view["coordination_error"] = str(coordination.get("_error") or "coordination data is unavailable")
    return view


def _write_coordination(
    state: dict[str, Any], coordination: dict[str, Any], plan: dict[str, Any] | None = None
) -> Path:
    change = state.get("change")
    checkout = state.get("feature_checkout")
    if not isinstance(change, str) or not isinstance(checkout, str):
        raise StateError("state: coordination context is missing; re-derive the state")
    path = _coordination_path(change, Path(checkout))
    repository = _repository_coordination(Path(checkout))
    repository["changes"][change] = _stored_change_coordination(coordination)
    _atomic_write_json(path, repository)
    persisted = coordination
    _render_dashboard_best_effort(change, Path(checkout), persisted, plan)
    return path


def _render_dashboard_best_effort(
    change: str, checkout: Path, coordination: dict[str, Any], plan: dict[str, Any] | None = None
) -> None:
    """Refresh the rendered dashboard from the current derived state.

    Best effort by design: a browser artifact must never invalidate derived state, so a
    render failure is swallowed and status/dashboard still report the current view. Every
    command that changes derived or coordination state refreshes it through here, so a
    changed state is visible without running a separate render command.
    """
    try:
        derived = state_derive(change, None, checkout)
        current_plan = plan or _change_plan_at(checkout, str(derived["source_sha"]), change)[0]
        render_dashboard(
            workflow_dir(checkout) / change / "dashboard.html",
            _overlay_coordination(derived, coordination, current_plan), current_plan,
        )
    except (OSError, StateError, PlanError, json.JSONDecodeError):
        pass


def _validate_change_plan(
    plan: dict[str, Any], change: str, tasks_text: str, checkout: Path
) -> None:
    plan_path = checkout / _REPO_PLAN
    if plan.get("change") != change:
        raise PlanError("shape: selected plan belongs to another change")
    _check_shape(plan, plan_path)
    _check_task_ids_text(plan, tasks_text, f"openspec/changes/{change}/tasks.md")
    _check_references(plan)
    _check_acyclic(plan)


def _active_changes(checkout: Path) -> list[str]:
    repository_plan = _workflow_checkout(checkout) / _REPO_PLAN
    if repository_plan.is_file():
        return sorted(_change_plans(_load_json(repository_plan)))
    return []


def _infer_change(checkout: Path) -> str:
    changes = _active_changes(checkout)
    if len(changes) == 1:
        return changes[0]
    if not changes:
        raise StateError("state: no active OpenSpec change found; author the repo-level local graph and select --change")
    raise StateError(
        f"state: multiple active changes ({', '.join(changes)}); provide --change to select one"
    )


def _state_context(
    change: str | None = None,
    checkout: Path | None = None,
    coordination_optional: bool = False,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    root = (checkout or Path.cwd()).resolve()
    requested = _safe_change(change) if change else None
    selected = requested or _infer_change(root)
    state = (state_derive(selected, None, root, allow_draft=True)
             if coordination_optional else state_derive(selected, None, root))
    if state.get("run_id"):
        raise StateError("state: legacy run state is not a source of truth; derive the change state")
    checkout_value = state.get("feature_checkout")
    if not isinstance(checkout_value, str) or not checkout_value:
        raise StateError("state: feature_checkout is missing; re-derive the state")
    repository = Path(checkout_value).expanduser().resolve()
    state_change = state.get("change")
    if not isinstance(state_change, str) or not state_change:
        raise StateError("state: change is missing; re-derive the state")
    source_sha = state.get("source_sha")
    if not isinstance(source_sha, str) or not source_sha:
        raise StateError("state: source SHA is missing; re-derive the state")
    try:
        plan, _, _ = _change_plan_at(repository, source_sha, state_change)
        tasks_text, _ = _committed_tasks(repository, source_sha, state_change, coordination_optional)
    except json.JSONDecodeError as exc:
        raise PlanError(f"shape: cannot load committed plan at {source_sha}: {_one_line(exc)}; fix the plan") from exc
    if not isinstance(plan, dict):
        raise PlanError("shape: committed plan must be a JSON object; change it to an object")
    _validate_change_plan(plan, state_change, tasks_text, repository)
    try:
        coordination = _load_coordination(state, plan)
    except StateError as exc:
        if not coordination_optional:
            raise
        coordination = _new_coordination(state, plan)
        coordination["_available"] = False
        coordination["_error"] = _one_line(exc)
    view = _overlay_coordination(state, coordination, plan)
    return _coordination_path(state_change, repository), view, plan


def _git_commit(checkout: Path, revision: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "--verify", f"{revision}^{{commit}}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise StateError(f"state: cannot inspect commit {revision!r}: {_one_line(exc)}; use a git checkout") from exc
    commit = result.stdout.strip()
    if result.returncode or not commit:
        raise StateError(f"state: commit {revision!r} does not exist; provide an existing commit")
    return commit


def _state_lane(state: dict[str, Any], lane_id: str) -> dict[str, Any]:
    lanes = state.get("lanes")
    if not isinstance(lanes, dict) or lane_id not in lanes:
        raise StateError(f"state: unknown lane {lane_id!r}; use a lane from the committed plan")
    lane = lanes[lane_id]
    if not isinstance(lane, dict):
        raise StateError(f"state: lane {lane_id!r} is malformed; repair the state file")
    return lane


def _reject_finished_lane(lane_id: str, lane: dict[str, Any]) -> None:
    phase = lane.get("phase")
    if phase == "closed":
        raise StateError(
            f"lane: lane {lane_id!r} is closed (already merged); use a new lane for further work"
        )
    if phase == "merged":
        raise StateError(f"lane: lane {lane_id!r} is merged; use a new lane for further work")


def _dispatch_access(role: str, access: str | None = None) -> str:
    """Resolve the narrow authoring/read-only provenance recorded for a dispatch."""
    role = _require_role(role)
    if access is not None and access not in {"read-only", "write"}:
        raise StateError("state: dispatch access must be read-only or write; change --access")
    if access == "write" and role not in {"implementer", "oracle"}:
        raise StateError(
            f"state: dispatch role {role!r} cannot receive write access; reviewers and advice are read-only"
        )
    if access is not None:
        return access
    return "write" if role == "implementer" else "read-only"


def _dispatch_intent(
    state: dict[str, Any],
    lane_id: str | None,
    role: str,
    task: str,
    key: str | None = None,
    lane_head: str | None = None,
    access: str | None = None,
    acceptance: object = None,
    evidence: object = None,
) -> str:
    role = _require_role(role)
    lane = _state_lane(state, lane_id) if lane_id is not None else state
    if lane_id is not None:
        _reject_finished_lane(lane_id, lane)
    if lane.get("complete"):
        raise StateError(f"lane: lane {lane_id!r} is already complete from committed ticks")
    change = state.get("change") or "change"
    dispatch_key = key or f"{change}/{lane_id or 'finish'}/{role}/{task}/{uuid.uuid4().hex}"
    if not dispatch_key:
        raise StateError("state: dispatch key must not be empty; change --key")
    if not isinstance(lane_head, str) or not lane_head:
        raise StateError("state: dispatch lane HEAD is unavailable; open the lane before dispatching")
    resolved_access = _dispatch_access(role, access)
    acceptance_path = (
        _required_document(acceptance, "dispatch acceptance document")
        if acceptance is not None and str(acceptance) != ""
        else None
    )
    evidence_path = _review_binding_path(evidence)
    intent = {
        "role": role,
        "task": task,
        "key": dispatch_key,
        "access": resolved_access,
        "started_at": _utc_timestamp(),
        "lane_head": lane_head,
    }
    if acceptance_path is not None:
        intent["acceptance_path"] = acceptance_path
        intent["acceptance_sha256"] = _path_sha256(Path(acceptance_path))
    if evidence_path is not None:
        intent["evidence_path"] = evidence_path
        intent["evidence_sha256"] = _path_sha256(Path(evidence_path))
    dispatches = lane.setdefault("dispatches", [])
    if not isinstance(dispatches, list):
        raise StateError(f"state: dispatches for lane {lane_id!r} are malformed; repair coordination data")
    for record in dispatches:
        if not isinstance(record, dict) or record.get("key") != dispatch_key:
            continue
        if any(
            record.get(field) != intent[field]
            for field in ("role", "task", "key")
        ) or (
            record.get("access") is not None and record.get("access") != resolved_access
        ) or any(
            record.get(field) != intent.get(field)
            for field in ("acceptance_path", "acceptance_sha256", "evidence_path", "evidence_sha256")
        ) or (
            (lane_id is None or acceptance_path is not None or evidence_path is not None)
            and record.get("lane_head") != lane_head
        ):
            raise StateError(
                f"state: dispatch key {dispatch_key!r} has a different intent or receipt; change --key"
            )
        return dispatch_key
    if any(isinstance(row, dict) and row.get("result") is None
           and (row.get("access") == "write" or resolved_access == "write") for row in dispatches):
        raise StateError("dispatch: worktree has an active worker; complete it before starting a writer or reader")
    open_for_role = [
        record for record in dispatches
        if isinstance(record, dict) and record.get("role") == role
        and record.get("result") is None
    ]
    if open_for_role and key is None:
        raise StateError(
            f"state: role {role!r} already has an open dispatch; complete it or change --key"
        )
    dispatches.append(intent)
    return dispatch_key


def _refuse_missing_coordination(state: dict[str, Any]) -> None:
    """Refuse to rebuild a missing coordination record once lane branches exist."""
    repository = Path(state["feature_checkout"]).resolve()
    if any(_ref_sha(repository, _lane_ref(state, lane_id)) for lane_id in state.get("lanes", {})):
        raise StateError(
            "state: coordination record is missing but lane branches exist; restore coordination.json before continuing"
        )


def _coordination_lane_for(
    state: dict[str, Any], plan: dict[str, Any], lane_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    coordination = _load_coordination(state, plan)
    if coordination.get("_available") is False:
        _refuse_missing_coordination(state)
    lanes = coordination.setdefault("lanes", {})
    if not isinstance(lanes, dict):
        raise StateError("state: coordination lanes are malformed; repair or remove the coordination data")
    lane = lanes.get(lane_id)
    if not isinstance(lane, dict):
        if lane_id not in plan.get("lanes", {}):
            raise StateError(f"state: unknown lane {lane_id!r}; use a lane from the committed plan")
        lane = _coordination_lane()
        lanes[lane_id] = lane
    return coordination, lane


def dispatch_run(
    lane_id: str | None,
    role: str,
    task: str,
    key: str | None = None,
    change: str | None = None,
    access: str | None = None,
    acceptance: object = None,
    evidence: object = None,
    worktree: Path | None = None,
) -> Path:
    role = _require_role(role)
    if lane_id is None:
        repository = _workflow_checkout(Path.cwd())
        _require_coordinator_cwd({}, "finish dispatch")
        record = _load_finish_record(repository)
        head = _git_output(repository, "rev-parse", "HEAD")
        _finish_integrated(repository, head)
        target = (worktree or repository).resolve()
        common = (repository / _git_output(repository, "rev-parse", "--git-common-dir")).resolve()
        target_common = (target / _git_output(target, "rev-parse", "--git-common-dir")).resolve()
        if common != target_common:
            raise StateError("finish: worker worktree belongs to another repository")
        access = _dispatch_access(role, access)
        if access == "write" and target == repository.resolve():
            raise StateError("finish: authoring requires a separate --worktree; the Coordinator owns the feature checkout")
        dispatches = record.setdefault("dispatches", [])
        if not isinstance(dispatches, list):
            raise StateError("finish: dispatch records are malformed; repair the ledger")
        previous = next((row for row in dispatches if row.get("key") == key), None) if key else None
        if previous is not None and previous.get("worktree") != str(target):
            raise StateError("finish: dispatch key has another worktree; use a new key")
        if previous is None:
            target_head = _git_output(target, "rev-parse", "HEAD")
            if (access == "write" and target_head != head) or not _is_ancestor(repository, head, target_head):
                raise StateError("finish: authors start at integrated HEAD; review candidates must descend from it")
            if _run_git(target, "status", "--porcelain", "--untracked-files=no").stdout.strip():
                raise StateError("finish: worker worktree is dirty; reconcile it before dispatch")
            if any(row.get("result") is None and row.get("worktree") == str(target)
                   and (access == "write" or row.get("access") == "write") for row in dispatches):
                raise StateError("finish: worktree already has an active worker; establish ownership before dispatch")
        if previous is not None and previous.get("feature_head") != head:
            raise StateError("finish: dispatch key has a different intent at this feature HEAD; use a new key")
        snapshot = (previous["lane_head"] if previous is not None and access == "write"
                    else _git_output(target, "rev-parse", "HEAD"))
        holder = {"dispatches": dispatches}
        dispatch_key = _dispatch_intent(holder, None, role, task, key, snapshot, access, None, None)
        row = next(row for row in dispatches if row["key"] == dispatch_key)
        row.update({"scope": "finish", "worktree": str(target), "feature_head": head})
        _register_subagent(record, None, role, task, dispatch_key, access, str(target))
        _write_finish_record(repository, record)
        return finish_record_path(repository)
    path, state, plan = _state_context(change)
    if worktree is not None:
        raise StateError("dispatch: --worktree is only supported with --scope finish")
    _require_coordinator_cwd(state, "dispatch")
    _require_plan_authorization(state, lane_id)
    coordination, runtime = _coordination_lane_for(state, plan, lane_id)
    merged_view = dict(state["lanes"][lane_id])
    merged_view.update(runtime)
    worktree, recorded_ref = _lane_runtime_for_operation(state, lane_id, merged_view)
    runtime["worktree"] = str(worktree)
    runtime["ref"] = recorded_ref
    lane_head = _git_output(worktree, "rev-parse", "HEAD")
    dispatch_key = _dispatch_intent(
        {**state, "lanes": {lane_id: {**merged_view, "worktree": str(worktree), "ref": recorded_ref}}},
        lane_id, role,  task, key, lane_head, access, acceptance, evidence,
    )
    # _dispatch_intent updates the merged copy, so copy only the transient dispatch list back.
    runtime["dispatches"] = merged_view["dispatches"]
    _register_subagent(coordination, lane_id, role,  task, dispatch_key,
                       _dispatch_access(role, access), str(worktree))
    _write_coordination(state, coordination, plan)
    return path


def _complete_dispatch(
    state: dict[str, Any],
    lane_id: str | None,
    role: str,
    worker_run_id: str | None = None,
    tokens: int | None = None,
    cost: str | None = None,
    result: str | None = None,
    key: str | None = None,
) -> None:
    lane = _state_lane(state, lane_id) if lane_id is not None else state
    if lane_id is not None:
        _reject_finished_lane(lane_id, lane)
    dispatches = lane.setdefault("dispatches", [])
    if not isinstance(dispatches, list):
        raise StateError(f"state: dispatches for lane {lane_id!r} are malformed; repair coordination data")
    open_records = [
        record for record in dispatches
        if isinstance(record, dict) and record.get("role") == role and record.get("result") is None
        and (key is None or record.get("key") == key)
    ]
    if len(open_records) > 1:
        open_records = open_records[-1:]
    if not open_records:
        completed = [
            record for record in reversed(dispatches)
            if isinstance(record, dict) and record.get("role") == role and record.get("result") is not None
            and (key is None or record.get("key") == key)
        ]
        if not completed:
            raise StateError(f"state: role {role!r} has no open dispatch; dispatch it first")
        record = completed[0]
        requested = {
            "worker_run_id": worker_run_id,
            "tokens": tokens,
            "cost": cost,
            "result": result,
        }
        if all(value is None for value in requested.values()):
            return
        if any(value is not None and record.get(field) != value for field, value in requested.items()):
            raise StateError(f"state: role {role!r} has no matching open dispatch; dispatch it first")
        return
    record = open_records[0]
    record.update({
        "worker_run_id": worker_run_id,
        "result": result or "ok",
        # Missing telemetry is unknown. Zero is a measured value and must not be
        # fabricated when a runner did not report usage.
        "tokens": tokens,
        "cost": cost,
    })


def dispatch_done(
    lane_id: str | None,
    role: str,
    worker_run_id: str | None = None,
    tokens: int | None = None,
    cost: str | None = None,
    result: str | None = None,
    change: str | None = None,
    key: str | None = None,
) -> Path:
    role = _require_role(role)
    if lane_id is None:
        repository = _workflow_checkout(Path.cwd())
        _require_coordinator_cwd({}, "finish dispatch-done")
        record = _load_finish_record(repository)
        holder = {"dispatches": record.setdefault("dispatches", [])}
        _complete_dispatch(holder, None, role, worker_run_id, tokens, cost, result, key)
        _close_subagent(record, None, role, worker_run_id, result, key)
        _write_finish_record(repository, record)
        return finish_record_path(repository)
    path, state, plan = _state_context(change)
    _require_coordinator_cwd(state, "dispatch-done")
    coordination, runtime = _coordination_lane_for(state, plan, lane_id)
    merged_view = dict(state["lanes"][lane_id])
    merged_view.update(runtime)
    _complete_dispatch({**state, "lanes": {lane_id: merged_view}}, lane_id, role,
                       worker_run_id, tokens, cost, result, key)
    runtime["dispatches"] = merged_view["dispatches"]
    _close_subagent(coordination, lane_id, role, worker_run_id, result, key)
    _write_coordination(state, coordination, plan)
    return path


def _checked_tasks(state: dict[str, Any], plan: dict[str, Any]) -> set[str]:
    value = state.get("checked_tasks")
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return set(value)
    return {
        task_id for task_id, task in plan.get("tasks", {}).items()
        if isinstance(task, dict)
        and any(
            isinstance(lane, dict) and task_id in set(lane.get("checked_tasks") or [])
            for lane in (state.get("lanes") or {}).values()
        )
    }


def _readiness_lanes(state: dict[str, Any], plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    checked = _checked_tasks(state, plan)
    raw = state.get("lanes") if isinstance(state.get("lanes"), dict) else {}
    lanes: dict[str, dict[str, Any]] = {}
    for lane_id, plan_lane in plan.get("lanes", {}).items():
        source = raw.get(lane_id) if isinstance(raw.get(lane_id), dict) else {}
        lane = dict(source)
        members = [
            task_id for task_id, task in plan.get("tasks", {}).items()
            if isinstance(task, dict) and task.get("lane") == lane_id
        ]
        lane["tasks"] = members
        lane["checked_tasks"] = [task_id for task_id in members if task_id in checked]
        lane["depends_on"] = list(plan_lane.get("depends_on", []))
        lane["complete"] = set(members) <= checked
        lanes[lane_id] = lane
    _apply_readiness(lanes, plan, checked)
    return lanes


def _ready_lanes(state: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    lanes = _readiness_lanes(state, plan)
    return [
        lane_id
        for lane_id, lane in lanes.items()
        if lane.get("ready")
        and lane.get("phase", "created") in {"created", "writing", "reviewing", "revising"}
    ]


def _readiness_reasons(state: dict[str, Any], plan: dict[str, Any]) -> dict[str, str]:
    lanes = _readiness_lanes(state, plan)
    return {lane_id: str(lane.get("readiness", "blocked: state unavailable")) for lane_id, lane in lanes.items()}


def _open_dispatches(state: dict[str, Any]) -> list[dict[str, Any]]:
    intents: list[dict[str, Any]] = []
    lanes = state.get("lanes")
    if not isinstance(lanes, dict):
        raise StateError("state: dispatch view is unavailable; coordination lanes are malformed")
    groups = list(lanes.items())
    if state.get("finish_dispatches"):
        groups.append((None, {"dispatches": state["finish_dispatches"]}))
    for lane_id, lane in groups:
        if not isinstance(lane, dict):
            raise StateError(f"state: dispatch view is unavailable; lane {lane_id!r} is malformed")
        dispatches = lane.get("dispatches")
        if not isinstance(dispatches, list):
            raise StateError(f"state: dispatch view is unavailable; lane {lane_id!r} dispatches are malformed")
        for record in dispatches:
            if not isinstance(record, dict):
                raise StateError(f"state: dispatch view is unavailable; lane {lane_id!r} has a malformed intent")
            if record.get("result") is not None:
                continue
            if record.get("worker_run_id"):
                raise StateError(f"state: dispatch view is unavailable; lane {lane_id!r} has an incomplete intent")
            required = ("role", "task", "key", "started_at", "lane_head")
            if any(not isinstance(record.get(field), str) or not record[field].strip() for field in required):
                raise StateError(f"state: dispatch view is unavailable; lane {lane_id!r} has an incomplete intent")
            if lane.get("phase") in {"merged", "closed"}:
                current_head = None
                status = "stale"
            else:
                if lane_id is None:
                    try:
                        current_head = _git_output(Path(record["worktree"]), "rev-parse", "HEAD")
                    except (StateError, KeyError):
                        current_head = None
                else:
                    current_head = _lane_current_head(lane)
                if current_head is None:
                    status = "unavailable"
                else:
                    current = current_head == record["lane_head"]
                    if lane_id is None:
                        current = current and state.get("source_sha") == record.get("feature_head")
                    status = "current" if current else "stale"
            intents.append({
                "lane": lane_id,
                "scope": "finish" if lane_id is None else "lane",
                "role": record["role"],
                "access": record.get("access"),
                "task": record["task"],
                "key": record["key"],
                "started_at": record["started_at"],
                "lane_head": record["lane_head"],
                "current_head": current_head,
                "status": status,
            })
    return intents


def _dispatch_summary(item: dict[str, Any]) -> str:
    return (
        f"{item.get('lane') or 'finish'}/{item.get('role', '?')} "
        f"task={item.get('task', '?')} "
        f"access={item.get('access') or 'unknown'} "
        f"key={item.get('key', '?')} since={item.get('started_at', '?')} "
        f"status={item.get('status', 'unknown')} "
        f"dispatch-head={item.get('lane_head', '?')} "
        f"current-head={item.get('current_head') or 'unavailable'}"
    )


def _lane_has_committed_tasks(
    lane: dict[str, Any], change: str, plan: dict[str, Any]
) -> bool:
    worktree_value = lane.get("worktree")
    if not isinstance(worktree_value, str) or not worktree_value:
        return False
    worktree = Path(worktree_value).expanduser().resolve()
    try:
        dirty = _run_git(worktree, "status", "--porcelain", "--untracked-files=all")
        if dirty.returncode or dirty.stdout.strip():
            return False
        return _lane_tasks_complete(lane, worktree, change, plan)
    except (OSError, PlanError, StateError):
        return False


def _coordination_phase_for_display(
    lane: dict[str, Any], change: str, plan: dict[str, Any]
) -> str:
    phase = lane.get("phase", "created")
    if phase not in {"created", "writing", "reviewing", "revising", "merged", "closed"}:
        phase = "created"
    if lane.get("complete"):
        if phase in {"merged", "closed"}:
            return phase
        return f"complete from committed ticks (coordination: {phase})"
    if phase in {"created", "writing"} and _lane_has_committed_tasks(lane, change, plan):
        return "reviewing"
    return phase


def _inflight_task_status(
    state: dict[str, Any], lane: dict[str, Any], plan: dict[str, Any]
) -> str | None:
    if lane.get("complete") or lane.get("merge") is not None:
        return None
    ref = lane.get("ref")
    checkout_value = state.get("feature_checkout")
    change = state.get("change")
    if not isinstance(ref, str) or not ref or not isinstance(checkout_value, str) or not isinstance(change, str):
        return None
    repository = Path(checkout_value).expanduser().resolve()
    try:
        head = _ref_sha(repository, ref)
        if not head:
            return "unavailable (lane ref is missing)"
        tasks_text, relative = _committed_tasks(repository, head, change)
        _check_task_ids_text(plan, tasks_text, f"{relative} at lane ref")
        checked = set(_checked_task_ids_from_text(tasks_text))
        lane_id = lane.get("id")
        task_ids = [
            task_id for task_id, task in plan.get("tasks", {}).items()
            if isinstance(task, dict) and task.get("lane") == lane_id
        ]
        if not task_ids:
            task_ids = list(lane.get("tasks") or [])
        statuses = ", ".join(
            f"{task_id}: {'complete' if task_id in checked else 'pending'}" for task_id in task_ids
        ) or "none"
        return f"in-flight only (lane ref; not on feature branch): {len(set(task_ids) & checked)}/{len(task_ids)} [{statuses}]"
    except (OSError, PlanError, StateError, ValueError):
        return "unavailable (lane ref cannot be read)"


def _status_view(state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    view = json.loads(json.dumps(state))
    _annotate_review_view(view)
    view["plan_approval"] = _plan_approval_status(view)
    view["ready_lanes"] = _ready_lanes(view, plan)
    view["readiness"] = _readiness_reasons(view, plan)
    change = str(view.get("change") or "")
    coordination_available = view.get("coordination_available", True)
    for lane_id, lane in view.get("lanes", {}).items():
        if isinstance(lane, dict):
            lane["review_status"] = _lane_review_status(lane)
            lane["phase"] = _coordination_phase_for_display(lane, change, plan)
            lane["inflight"] = _inflight_task_status(view, lane, plan)
            lane["coordinator_status"] = lane.get("status")
            lane["coordinator_status_reason"] = lane.get("status_reason")
            checked, _ = _lane_ref_progress(view, lane, plan)
            checked |= set(view["checked_tasks"])
            open_tasks = {row.get("task") for row in lane.get("dispatches", [])
                          if row.get("result") is None}
            prior = lane.get("task_states") or {}
            lane["task_states"] = {
                task_id: ("done" if task_id in checked else
                          "in_progress" if task_id in open_tasks else
                          "blocked" if prior.get(task_id, {}).get("status") == "blocked" else "pending")
                for task_id in _lane_assigned_tasks(plan, lane_id)}
            lane["human_gate"] = _human_gate_summary(
                lane_id, lane, view.get("human_gates"), coordination_available, view.get("authorizations"),
                True,
            )
    ledger, _ = _load_plan_ledger(None, Path(view["feature_checkout"]))
    view["planning_dispatches"] = [row for row in ledger["dispatches"] if row.get("result") is None]
    if view.get("coordination_available", True):
        try:
            view["open_dispatches"] = _open_dispatches(view)
            view["dispatch_view_available"] = True
        except StateError as exc:
            view["open_dispatches"] = []
            view["dispatch_view_available"] = False
            view["dispatch_view_error"] = _one_line(exc)
    else:
        view["open_dispatches"] = []
        view["dispatch_view_available"] = False
        view["dispatch_view_error"] = str(
            view.get("coordination_error") or "coordination data is unavailable"
        )
    if view.get("planning") == "draft":
        reason = "blocked: planning artifacts are uncommitted; obtain approval before implementation"
        view["ready_lanes"] = []
        view["readiness"] = {lane_id: reason for lane_id in view.get("lanes", {})}
        for lane in view.get("lanes", {}).values():
            lane.update({"ready": False, "readiness": reason})
    return view


def _status_text(view: dict[str, Any]) -> str:
    lines = [
        f"change: {view.get('change', '')}",
        f"source: {view.get('source_sha', '')}",
        f"ready lanes: {', '.join(view.get('ready_lanes', [])) or 'none'}",
    ]
    if view.get("planning") == "draft":
        lines.append("planning: draft (reviews and human approval required before committing)")
    approval = view.get("plan_approval")
    if isinstance(approval, dict):
        if approval.get("state") == "missing":
            lines.append("plan approval: missing (record it with plan-approve)")
        elif approval.get("state") == "unreadable":
            lines.append(f"plan approval: unreadable ({approval.get('detail')})")
        else:
            lines.append(
                f"plan approval: {approval.get('approver') or approval.get('waived_by')} at {str(approval.get('head'))[:12]} "
                f"({approval.get('state')})"
            )
    if not view.get("dispatch_view_available", True):
        reason = view.get("dispatch_view_error") or "coordination data is unavailable"
        lines.append(f"open dispatch intents: unavailable ({_one_line(reason)})")
    else:
        open_dispatches = view.get("open_dispatches", [])
        if open_dispatches:
            lines.append(
                "open dispatch intents: "
                + "; ".join(_dispatch_summary(item) for item in open_dispatches)
            )
        else:
            lines.append("open dispatch intents: none")
    reasons = view.get("readiness", {})
    for lane_id, lane in view.get("lanes", {}).items():
        if not isinstance(lane, dict):
            continue
        task_ids = lane.get("tasks") if isinstance(lane.get("tasks"), list) else []
        checked = set(lane.get("checked_tasks") or [])
        task_status = ", ".join(
            f"{task_id}={'complete' if task_id in checked else 'pending'}" for task_id in task_ids
        ) or "none"
        dependencies = ", ".join(str(item) for item in lane.get("depends_on", [])) or "none"
        reviews = lane.get("reviews", [])
        _, verdicts = _review_summary(reviews)
        phase = lane.get("phase", "created")
        gate = lane.get("human_gate")
        lines.append(
            f"lane {lane_id}: {phase}; tasks {len(checked)}/{len(task_ids)} [{task_status}]; "
            f"lane dependencies {dependencies}; readiness {reasons.get(lane_id, 'blocked: unavailable')}; "
            f"reviews {verdicts}"
            + (f"; human gate {gate}" if gate else "")
        )
        raw_states = lane.get("task_states") if isinstance(lane.get("task_states"), dict) else {}
        states = {
            task_id: (row.get("status") if isinstance(row, dict) else row)
            for task_id, row in raw_states.items()
        }
        if states:
            done_count = sum(1 for status_value in states.values() if status_value == "done")
            detail = ", ".join(f"{task_id}={status_value}" for task_id, status_value in states.items())
            reason = lane.get("coordinator_status_reason")
            review = lane.get("review_status") if isinstance(lane.get("review_status"), dict) else None
            review_note = ""
            if review:
                review_note = (
                    f"; review {review.get('state')} r{review.get('round')} "
                    f"by {review.get('reviewer')} at {str(review.get('commit'))[:12]}"
                )
            escalation = lane.get("escalation") if isinstance(lane.get("escalation"), dict) else None
            escalation_note = ""
            if escalation:
                escalation_note = (
                    f"; escalation {escalation.get('state')}"
                    + (f" ({', '.join(escalation.get('options') or [])})" if escalation.get("options") else "")
                )
            lines.append(
                f"coordinator: {lane.get('coordinator_status')}; {done_count}/{len(states)} done"
                f" [{detail}]" + (f"; reason: {reason}" if reason else "")
                + review_note + escalation_note
            )
    subagents = view.get("subagents")
    if isinstance(subagents, list) and subagents:
        running = [row for row in subagents if isinstance(row, dict) and row.get("status") == "running"]
        summary = ", ".join(
            f"{row.get('role')}@{row.get('lane')}:{row.get('status')}"
            for row in subagents[-6:] if isinstance(row, dict)
        )
        lines.append(f"subagents: {len(running)} running of {len(subagents)} [{summary}]")
    return "\n".join(lines) + "\n"


def status(change: str | None = None, as_json: bool = False) -> str:
    _, state, plan = _state_context(change, coordination_optional=True)
    view = _status_view(state, plan)
    return json.dumps(view, indent=2, sort_keys=True) + "\n" if as_json else _status_text(view)


def _html_text(value: object, fallback: str = "—") -> str:
    if value is None or value == "":
        return fallback
    return html.escape(str(value), quote=True)


def _dispatch_totals(dispatches: object) -> tuple[int | None, float | None]:
    tokens = 0
    spend = 0.0
    tokens_known = True
    spend_known = True
    if not isinstance(dispatches, list):
        return None, None
    for item in dispatches:
        if not isinstance(item, dict):
            continue
        token_value = item.get("tokens")
        if token_value is None:
            tokens_known = False
        else:
            try:
                tokens += int(token_value)
            except (TypeError, ValueError):
                tokens_known = False
        cost_value = item.get("cost")
        if cost_value is None:
            spend_known = False
        else:
            try:
                value = float(cost_value)
                if value >= 0:
                    spend += value
                else:
                    spend_known = False
            except (TypeError, ValueError):
                spend_known = False
    return (tokens if tokens_known else None), (spend if spend_known else None)


def _review_summary(reviews: object) -> tuple[str, str]:
    if not isinstance(reviews, list):
        return "none", "none"
    rounds: list[int] = []
    labels: list[str] = []
    for review in reviews:
        if not isinstance(review, dict):
            continue
        role = html.escape(str(review.get("role") or "?"))
        # Historical carried-forward verdicts never satisfy current review gates.
        if review.get("reused") is True:
            verdict = "INVALID"
        else:
            verdict = html.escape(str(review.get("verdict") or "unknown"))
        number = _review_round(review)
        if number is not None:
            rounds.append(number)
            round_text = f"r{number}"
        else:
            round_text = "r?"
        commit = _review_commit(review)
        commit_text = html.escape(commit or "unknown")
        status = html.escape(str(review.get("status") or "current"))
        labels.append(f"{role}={verdict} {round_text} @{commit_text} [{status}]")
    return (f"r{max(rounds)}" if rounds else "none"), ", ".join(labels) or "none"


def _dashboard_html(
    state: dict[str, Any], plan: dict[str, Any], run_dir: Path, refresh_seconds: int = 0
) -> str:
    refresh_script = (
        f"<script>window.setTimeout(function () {{ window.location.reload(); }}, {refresh_seconds * 1000});</script>"
        if refresh_seconds > 0 else ""
    )
    view = _status_view(state, plan)
    state_lanes = view.get("lanes") if isinstance(view.get("lanes"), dict) else {}
    plan_lanes = plan.get("lanes") if isinstance(plan.get("lanes"), dict) else {}
    plan_tasks = plan.get("tasks") if isinstance(plan.get("tasks"), dict) else {}
    readiness = view.get("readiness") if isinstance(view.get("readiness"), dict) else {}
    lane_rows: list[str] = []
    task_rows: list[str] = []
    lane_dependency_rows: list[str] = []
    task_dependency_rows: list[str] = []
    cost_rows: list[str] = []
    for lane_id, lane_plan in plan_lanes.items():
        lane_state = state_lanes.get(lane_id) if isinstance(state_lanes.get(lane_id), dict) else {}
        task_ids = [
            task_id for task_id, task in plan_tasks.items()
            if isinstance(task, dict) and task.get("lane") == lane_id
        ]
        checked = set(lane_state.get("checked_tasks") or []) & set(task_ids)
        task_status = ", ".join(
            f"{_html_text(task_id)}: {_html_text('complete' if task_id in checked else 'pending')}"
            for task_id in task_ids
        ) or "none"
        dependencies = list(lane_plan.get("depends_on", []))
        dependency_text = ", ".join(_html_text(item) for item in dependencies) or "none"
        lane_dependency_rows.append(
            f"<tr><th scope=\"row\">{_html_text(lane_id)}</th><td>{dependency_text}</td>"
            f"<td>{_html_text(readiness.get(lane_id, 'blocked: unavailable'))}</td></tr>"
        )
        inflight = _inflight_task_status(view, lane_state, plan)
        inflight_text = inflight or "—"
        review_round, verdicts = _review_summary(lane_state.get("reviews"))
        merge = lane_state.get("merge")
        if lane_state.get("complete"):
            merge_status = "complete from committed ticks"
        elif isinstance(merge, dict):
            merge_status = "merged (temporary record)"
        else:
            merge_status = "pending"
        lane_rows.append(
            "<tr>"
            f"<th scope=\"row\">{_html_text(lane_id)}</th>"
            f"<td>{_html_text(lane_state.get('phase'), 'created')}</td>"
            f"<td>{len(checked)}/{len(task_ids)}<br><small>{task_status}</small></td>"
            f"<td>{_html_text(inflight_text)}</td>"
            f"<td>{dependency_text}</td>"
            f"<td>{_html_text(readiness.get(lane_id, 'blocked: unavailable'))}</td>"
            f"<td>{_html_text(lane_state.get('human_gate'), 'not required')}</td>"
            f"<td>{review_round}<br><small>{verdicts}</small></td>"
            f"<td>{_html_text(merge_status)}</td>"
            "</tr>"
        )
        tokens, spend = _dispatch_totals(lane_state.get("dispatches"))
        token_text = str(tokens) if tokens is not None else "unknown"
        spend_text = f"{spend:.6f}" if spend is not None else "unknown"
        cost_rows.append(
            f"<tr><th scope=\"row\">{_html_text(lane_id)}</th><td>{token_text}</td><td>{spend_text}</td></tr>"
        )
    for task_id, task in plan_tasks.items():
        if not isinstance(task, dict):
            continue
        lane_id = task.get("lane")
        lane_state = state_lanes.get(lane_id) if isinstance(state_lanes.get(lane_id), dict) else {}
        checked = task_id in set(lane_state.get("checked_tasks") or [])
        dependencies = list(task.get("deps", []))
        dependency_text = ", ".join(_html_text(item) for item in dependencies) or "none"
        task_dependency_rows.append(
            f"<tr><th scope=\"row\">{_html_text(task_id)}</th>"
            f"<td>{_html_text(lane_id)}</td><td>{dependency_text}</td></tr>"
        )
        task_rows.append(
            f"<tr><th scope=\"row\">{_html_text(task_id)}</th><td>{_html_text(lane_id)}</td>"
            f"<td>{_html_text('complete' if checked else 'pending')}</td><td>{dependency_text}</td></tr>"
        )
    if not lane_rows:
        lane_rows.append('<tr><td colspan="9">No lanes in the local plan</td></tr>')
    if not task_rows:
        task_rows.append('<tr><td colspan="4">No tasks in the local plan</td></tr>')
    if not lane_dependency_rows:
        lane_dependency_rows.append('<tr><td colspan="3">No lane dependencies</td></tr>')
    if not task_dependency_rows:
        task_dependency_rows.append('<tr><td colspan="3">No task dependencies</td></tr>')
    if not cost_rows:
        cost_rows.append('<tr><td colspan="3">No dispatch cost recorded</td></tr>')
    if not view.get("dispatch_view_available", True):
        dispatch_html = (
            "<p><strong>Dispatch view unavailable:</strong> "
            f"{_html_text(view.get('dispatch_view_error') or 'coordination data is unavailable')}</p>"
        )
    else:
        dispatches = view.get("open_dispatches") if isinstance(view.get("open_dispatches"), list) else []
        dispatch_text = "<br>".join(
            _html_text(_dispatch_summary(item)) for item in dispatches
        ) or "none"
        dispatch_html = f"<p>Open dispatch intents: <code>{dispatch_text}</code></p>"
    ready = ", ".join(str(item) for item in view.get("ready_lanes", [])) or "none"
    title = f"featdev — {_html_text(view.get('change'))}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
body {{ margin: 0 auto; max-width: 1400px; padding: 1.5rem; line-height: 1.45; }}
h1, h2 {{ margin-top: 0; }}
section {{ margin: 2rem 0; }}
table {{ border-collapse: collapse; width: 100%; margin-top: .75rem; }}
th, td {{ border: 1px solid #8886; padding: .55rem; text-align: left; vertical-align: top; }}
th {{ white-space: nowrap; }}
code {{ overflow-wrap: anywhere; }}
small {{ opacity: .8; }}
.meta {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr)); gap: .75rem; }}
.card {{ border: 1px solid #8886; border-radius: .4rem; padding: .75rem; }}
</style>
</head>
<body>
<h1>Feature development dashboard</h1>
<div class="meta">
<div class="card"><strong>Change</strong><br>{_html_text(view.get('change'))}</div>
<div class="card"><strong>Source commit</strong><br><code>{_html_text(view.get('source_sha'))}</code></div>
<div class="card"><strong>Branch</strong><br><code>{_html_text(view.get('branch'))}</code></div>
<div class="card"><strong>Ready lanes</strong><br>{_html_text(ready)}</div>
</div>
<section><h2>Lanes and assigned task status</h2>
<table><thead><tr><th>Lane</th><th>Coordination phase</th><th>Tasks from committed ticks</th><th>In-flight task ticks (not on feature branch)</th><th>Lane dependencies</th><th>Readiness</th><th>Human gate</th><th>Reviews</th><th>Merge</th></tr></thead>
<tbody>{''.join(lane_rows)}</tbody></table></section>
<section><h2>Tasks and task dependencies</h2>
<table><thead><tr><th>Task</th><th>Lane</th><th>Status from committed tick</th><th>Task dependencies</th></tr></thead>
<tbody>{''.join(task_rows)}</tbody></table></section>
<section><h2>Lane dependency graph</h2>
<table><thead><tr><th>Lane</th><th>Depends on</th><th>Readiness reasoning</th></tr></thead>
<tbody>{''.join(lane_dependency_rows)}</tbody></table></section>
<section><h2>Task dependency graph</h2>
<table><thead><tr><th>Task</th><th>Lane</th><th>Depends on</th></tr></thead>
<tbody>{''.join(task_dependency_rows)}</tbody></table></section>
<section><h2>Temporary coordination</h2>
{dispatch_html}
<table><thead><tr><th>Lane</th><th>Tokens</th><th>Spend</th></tr></thead>
<tbody>{''.join(cost_rows)}</tbody></table></section>
<footer><small>Live view of {html.escape(str(state.get('coordination_path') or run_dir / 'coordination.json'), quote=True)}; git commits remain authoritative for completion.</small></footer>
{refresh_script}
</body>
</html>
"""


def render_dashboard(state_path: Path, state: dict[str, Any], plan: dict[str, Any], output: Path | None = None) -> Path:
    target = (output or state_path.parent / "dashboard.html").expanduser().resolve()
    _atomic_write_text(target, _dashboard_html(state, plan, state_path.parent))
    return target


def dashboard(
    change: str | None = None, output: Path | None = None,
    serve: bool = False, port: int = 8765, refresh_seconds: int = 2,
) -> Path | None:
    """Render the dashboard. With serve=True the page re-reads coordination.json live."""
    if not serve:
        state_path, state, plan = _state_context(change, coordination_optional=True)
        return render_dashboard(state_path, state, plan, output)
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if self.path.split("?")[0] not in {"/", "/index.html", "/dashboard.html"}:
                self.send_error(404)
                return
            try:
                _, live, live_plan = _state_context(change, coordination_optional=True)
                body = _dashboard_html(live, live_plan, workflow_dir(Path.cwd()), refresh_seconds)
            except (StateError, PlanError, OSError) as exc:
                body = (
                    "<!doctype html><meta charset='utf-8'><title>workflow dashboard</title>"
                    f"<pre>dashboard unavailable: {_one_line(exc)}</pre>"
                    f"<script>setTimeout(function () {{ location.reload(); }}, {refresh_seconds * 1000});</script>"
                )
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    host, bound_port = server.server_address[:2]
    print(f"dashboard: http://{host}:{bound_port}/ (reads coordination.json per request; {refresh_seconds}s refresh)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return None


def _safe_component(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."} or Path(value).name != value:
        raise StateError(f"lane: {label} must be a non-empty path-safe name; change it")
    return value


def _run_git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repository), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise StateError(f"lane: git unavailable: {_one_line(exc)}; use a git checkout") from exc


def _git_output(repository: Path, *arguments: str) -> str:
    result = _run_git(repository, *arguments)
    if result.returncode:
        detail = _one_line(result.stderr.strip() or result.stdout.strip())
        raise StateError(
            f"lane: git {' '.join(arguments)} failed{(': ' + detail) if detail else ''}; repair the checkout"
        )
    return result.stdout.strip()


def _is_ancestor(repository: Path, ancestor: str, descendant: str) -> bool:
    return _run_git(repository, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0


def _feature_branch(state: dict[str, Any]) -> str:
    branch = state.get("branch")
    if not isinstance(branch, str) or not branch:
        raise StateError("lane: branch is missing; repair the state file")
    return branch


def _lane_worktree_root(state: dict[str, Any]) -> Path:
    """Resolve the directory that contains per-lane worktrees.

    Default is ``~/.agents/worktrees``. ``FEATDEV_LANE_WORKTREE_ROOT`` (absolute
    path) is an explicit Coordinator override. A recorded lane worktree is
    authoritative for that lane's lifetime; a tracker may attach metadata to it
    but never relocates it.
    """
    override = os.environ.get("FEATDEV_LANE_WORKTREE_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".agents" / "worktrees"


def _lane_worktree_path(state: dict[str, Any], lane_id: str) -> Path:
    checkout_value = state.get("feature_checkout")
    branch = _feature_branch(state)
    change = state.get("change")
    if not isinstance(checkout_value, str) or not checkout_value:
        raise StateError("lane: feature_checkout is missing; re-derive the state")
    if not isinstance(change, str) or not change:
        raise StateError("lane: change is missing; re-derive the state")
    if any(not part or part in {".", ".."} for part in branch.split("/")) or "\n" in branch or "\r" in branch:
        raise StateError("lane: branch must be a safe git branch name; repair the derived state")
    checkout = Path(checkout_value).resolve()
    repo = "repo-" + hashlib.sha256(os.fsencode(checkout)).hexdigest()
    lane_id = _safe_component(lane_id, "lane id")
    change = _safe_component(change, "change")
    result = _lane_worktree_root(state) / repo
    for part in branch.split("/"):
        result /= part
    return result / change / lane_id


def _lane_ref(state: dict[str, Any], lane_id: str) -> str:
    change = _safe_component(state.get("change"), "change")
    lane_id = _safe_component(lane_id, "lane id")
    return f"featdev/{change}-{lane_id}"


def _worktree_branch(repository: Path, path: Path) -> str | None:
    listing = _run_git(repository, "worktree", "list", "--porcelain")
    if listing.returncode:
        return None
    target = str(path.resolve())
    current_path: str | None = None
    current_branch: str | None = None
    for line in listing.stdout.splitlines() + [""]:
        if line.startswith("worktree "):
            current_path = str(Path(line[9:]).resolve())
            current_branch = None
        elif line.startswith("branch refs/heads/"):
            current_branch = line[len("branch refs/heads/"):]
        elif not line.strip():
            if current_path == target:
                return current_branch
            current_path = None
            current_branch = None
    return None


def _ref_sha(repository: Path, ref: str) -> str | None:
    result = _run_git(repository, "rev-parse", "--verify", f"refs/heads/{ref}")
    return result.stdout.strip() if result.returncode == 0 else None


def _lane_runtime_for_operation(
    state: dict[str, Any], lane_id: str, lane: dict[str, Any]
) -> tuple[Path, str]:
    repository = Path(str(state["feature_checkout"])).expanduser().resolve()
    expected_path = _lane_worktree_path(state, lane_id).resolve()
    expected_ref = _lane_ref(state, lane_id)
    recorded_path = lane.get("worktree")
    recorded_ref = lane.get("ref")
    if isinstance(recorded_path, str) and recorded_path and isinstance(recorded_ref, str) and recorded_ref:
        worktree = Path(recorded_path).expanduser().resolve()
        # A recorded worktree is authoritative for the lane's lifetime: root resolution may
        # change between open and later operations (for example a checkout adopting child
        # worktrees), and in-flight lanes must keep their existing location.
        if recorded_ref != expected_ref:
            raise StateError(f"lane: lane {lane_id!r} ref is not deterministic; reconcile it first")
        if _worktree_branch(repository, worktree) != recorded_ref:
            raise StateError(f"lane: worktree is not checked out on ref {recorded_ref}; reconcile it first")
        return worktree, recorded_ref
    if _worktree_branch(repository, expected_path) == expected_ref:
        return expected_path, expected_ref
    current_branch = _run_git(repository, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
    if current_branch.startswith("featdev/") and current_branch.endswith(f"-{lane_id}"):
        return repository, current_branch
    raise StateError(f"lane: lane {lane_id!r} has no open worktree; open it before reviewing or ticking")


def _lane_tasks_complete(lane: dict[str, Any], worktree: Path, change: str, plan: dict[str, Any]) -> bool:
    assigned = {
        task_id for task_id, task in plan.get("tasks", {}).items()
        if isinstance(task, dict) and task.get("lane") == lane.get("id", "")
    }
    if not assigned:
        assigned = set(lane.get("tasks") or [])
    return assigned <= _lane_checked_tasks(worktree, change, plan)


def _lane_assigned_tasks(plan: dict[str, Any], lane_id: str) -> set[str]:
    return {
        task_id for task_id, task in plan.get("tasks", {}).items()
        if isinstance(task, dict) and task.get("lane") == lane_id
    }


def _lane_ready_for_open(state: dict[str, Any], plan: dict[str, Any], lane_id: str) -> bool:
    return lane_id in _ready_lanes(state, plan)


def _lane_checked_tasks(worktree: Path, change: str, plan: dict[str, Any] | None = None) -> set[str]:
    tasks_path = _openspec_change_directory(worktree, change) / "tasks.md"
    try:
        text = tasks_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StateError(f"lane: cannot load {tasks_path}: {_one_line(exc)}; provide the committed tasks.md") from exc
    if plan is not None:
        _check_task_ids_text(plan, text, f"openspec/changes/{change}/tasks.md in lane worktree")
    return set(_checked_task_ids_from_text(text))


def _required_review_roles(lane: dict[str, Any]) -> list[str]:
    roles = ["code-reviewer", "test-reviewer"] if lane.get("risk") == "high" else ["code-reviewer"]
    if lane.get("ui"):
        roles.append("ui-reviewer")
    return roles


def _review_report(path_value: object) -> str:
    try:
        path = Path(path_value).expanduser().resolve()
    except (TypeError, ValueError) as exc:
        raise StateError(f"lane: review report path is invalid: {_one_line(exc)}; provide a report") from exc
    if not path.is_file() or path.stat().st_size == 0:
        raise StateError(f"lane: review report is missing or empty at {path}; provide a report")
    return str(path)


def _review_binding_path(path_value: object) -> str | None:
    if path_value is None or str(path_value) == "":
        return None
    try:
        path = Path(path_value).expanduser().resolve()
    except (TypeError, ValueError) as exc:
        raise StateError(f"lane: evidence path is invalid: {_one_line(exc)}; change --evidence") from exc
    if not path.is_file() or path.stat().st_size == 0:
        raise StateError(f"lane: evidence is missing or empty at {path}; provide current evidence")
    return str(path)


def _path_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _required_document(path_value: object, label: str) -> str:
    try:
        path = Path(str(path_value)).expanduser().resolve()
    except (TypeError, ValueError) as exc:
        raise StateError(f"lane: {label} path is invalid: {_one_line(exc)}; provide the file") from exc
    if not path.is_file() or path.stat().st_size == 0:
        raise StateError(f"lane: {label} is missing or empty at {path}; provide the file")
    return str(path)


def _load_grant(path_value: object) -> dict[str, Any]:
    try:
        path = Path(str(path_value)).expanduser()
    except (TypeError, ValueError) as exc:
        raise StateError(f"lane: authorization path is invalid: {_one_line(exc)}; fix it") from exc
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(
            f"lane: cannot load authorization {path}: {_one_line(exc)}; fix the grant record"
        ) from exc
    if not isinstance(value, dict):
        raise StateError(f"lane: authorization {path} must be a JSON object; fix the grant record")
    return value


def _record_authorization_approval(
    coordination: dict[str, Any],
    state: dict[str, Any],
    lane_id: str,
    ref: str,
    head: str,
    evidence_revision: str,
    authorization: object,
    stand_in: object,
    checklist: object,
) -> dict[str, Any]:
    """Validate a human grant and its Oracle stand-in for one per-workstream checkpoint.

    An authorization does not itself approve anything: it lets an Oracle stand-in supply the
    human acceptance judgment. Everything here fails closed, and the grant is stored so the
    one-use rule survives the approval that consumes it.
    """
    repository = Path(state["feature_checkout"]).resolve()
    feature_head = _git_output(repository, "rev-parse", _feature_branch(state))
    base = _lane_merge_base(repository, ref, feature_head)
    base_plan_sha256 = _change_plan_at(repository, base, state["change"])[1]
    plan_sha256 = _change_plan_at(repository, feature_head, state["change"])[1]
    grant = _load_grant(authorization) if isinstance(authorization, (str, Path)) else authorization
    if not isinstance(grant, dict):
        raise StateError(
            f"lane: --approved-by oracle needs --authorization; record the human grant for lane {lane_id!r}"
        )
    grant_id = grant.get("id")
    if not isinstance(grant_id, str) or not grant_id.strip():
        raise StateError("lane: authorization id must be non-empty; fix the grant record")
    grant_id = grant_id.strip()
    scope = grant.get("scope")
    if (
        not isinstance(scope, list)
        or not scope
        or any(not isinstance(item, str) or not item.strip() for item in scope)
    ):
        raise StateError(
            f"lane: authorization {grant_id!r} scope must be a non-empty list of checkpoint names; fix the grant"
        )
    scope = [item.strip() for item in scope]
    if "all" in scope:
        raise StateError(f"lane: authorization {grant_id!r} scope 'all' is not allowed; list lane IDs")
    if grant.get("granted_by") != "human":
        raise StateError(
            f"lane: authorization {grant_id!r} is not human-origin; only the human grants an authorization"
        )
    granted_at = grant.get("granted_at")
    if not isinstance(granted_at, str) or not granted_at.strip():
        raise StateError(f"lane: authorization {grant_id!r} has no grant time; fix the grant record")
    if base_plan_sha256 != plan_sha256:
        raise StateError(
            f"lane: the approved plan was revised after lane {lane_id!r} opened; reconcile the lane against "
            "the current approved plan, then obtain a grant that names it"
        )
    if grant.get("plan_sha256") != plan_sha256:
        raise StateError(
            f"lane: authorization {grant_id!r} was granted for another plan revision; renew it for the "
            "approved plan before it can satisfy this checkpoint"
        )
    if lane_id not in scope:
        raise StateError(
            f"lane: authorization {grant_id!r} does not cover lane {lane_id!r}; obtain a grant that names "
            "this checkpoint or all checkpoints of the approved plan"
        )
    stand_in_value = stand_in if isinstance(stand_in, dict) else None
    if stand_in_value is None:
        raise StateError(
            f"lane: --approved-by oracle needs --stand-in-report; the Oracle stand-in supplies the human "
            "acceptance judgment for lane {lane_id!r}"
        )
    if stand_in_value.get("verdict") != "PASS":
        raise StateError(
            f"lane: Oracle stand-in for {lane_id!r} is {stand_in_value.get('verdict') or 'missing'!r}, not PASS; "
            "the checkpoint stays closed and the grant unconsumed"
        )
    report_path = _required_document(stand_in_value.get("report"), "Oracle stand-in report")
    checklist_path = _required_document(checklist, "human acceptance checklist")
    if report_path == checklist_path:
        raise StateError(
            f"lane: the Oracle stand-in report and the human acceptance checklist must be different files; "
            "the stand-in has to cover the delegated acceptance duties, not just technical correctness"
        )
    grants = coordination.setdefault("authorizations", {})
    if not isinstance(grants, dict):
        raise StateError("state: authorizations are malformed; repair or remove the coordination data")
    stored = grants.get(grant_id)
    if isinstance(stored, dict) and stored.get("status") != "active":
        raise StateError(
            f"lane: authorization {grant_id!r} is {stored.get('status')}, not active; obtain fresh live "
            "approval or a renewed grant"
        )
    grants[grant_id] = {
        "id": grant_id,
        "scope": scope,
        "granted_by": "human",
        "granted_at": granted_at.strip(),
        "plan_sha256": plan_sha256,
        "status": "active",
    }

    return {
        "approved_by": "oracle",
        "auto_approval": True,
        "authorization": {
            "id": grant_id,
            "scope": scope,
            "granted_at": granted_at.strip(),
            "plan_sha256": plan_sha256,
        },
        "stand_in": {
            "verdict": "PASS",
            "report": report_path,
            "report_sha256": _path_sha256(Path(report_path)),
            "checklist": checklist_path,
            "checklist_sha256": _path_sha256(Path(checklist_path)),
            "binding": {"commit": head, "evidence_revision": evidence_revision},
        },
    }


def _validate_authorization_approval(
    lane_id: str,
    gate: dict[str, Any],
    head: str,
    evidence_revision: str,
    coordination: dict[str, Any],
    approved_plan_sha256: str | None = None,
    allow_consumed_by: str | None = None,
) -> None:
    """Re-check an Oracle stand-in approval against live coordination data at merge time."""
    if gate.get("auto_approval") is not True:
        raise StateError(
            f"lane: human gate for {lane_id!r} claims an Oracle stand-in without the auto_approval marker; "
            "record it through human-gate --approved-by oracle"
        )
    authorization = gate.get("authorization")
    grant_id = authorization.get("id") if isinstance(authorization, dict) else None
    if not isinstance(grant_id, str) or not grant_id.strip():
        raise StateError(f"lane: human gate for {lane_id!r} has no authorization id; record the human grant")
    grant_id = grant_id.strip()
    grants = coordination.get("authorizations")
    grant = grants.get(grant_id) if isinstance(grants, dict) else None
    if not isinstance(grant, dict):
        raise StateError(
            f"lane: authorization {grant_id!r} is missing from coordination data; obtain fresh live approval "
            "or a renewed grant"
        )
    status = grant.get("status")
    consumed_for_lane = (
        allow_consumed_by == lane_id
        and status == "consumed"
        and grant.get("consumed_by") == lane_id
    )
    if status != "active" and not consumed_for_lane:
        raise StateError(
            f"lane: authorization {grant_id!r} is {status or 'unrecorded'}, not active; obtain fresh live "
            "approval or a renewed grant"
        )
    scope = grant.get("scope")
    if not isinstance(scope, list) or any(not isinstance(item, str) for item in scope):
        raise StateError(f"lane: authorization {grant_id!r} scope is malformed; fix the grant")
    if "all" in scope:
        raise StateError(f"lane: authorization {grant_id!r} scope 'all' is not allowed; list lane IDs")
    if lane_id not in scope:
        raise StateError(
            f"lane: authorization {grant_id!r} does not cover lane {lane_id!r}; obtain fresh consent for "
            "this checkpoint"
        )
    recorded_plan = authorization.get("plan_sha256") if isinstance(authorization, dict) else None
    if recorded_plan != grant.get("plan_sha256"):
        raise StateError(
            f"lane: authorization {grant_id!r} does not match the recorded checkpoint binding; obtain fresh "
            "approval"
        )
    if approved_plan_sha256 and grant.get("plan_sha256") != approved_plan_sha256:
        raise StateError(
            f"lane: authorization {grant_id!r} was granted for another plan revision; obtain a renewed grant "
            "for the approved plan"
        )
    stand_in = gate.get("stand_in")
    if not isinstance(stand_in, dict):
        raise StateError(
            f"lane: human gate for {lane_id!r} has no Oracle stand-in; run the stand-in review"
        )
    if stand_in.get("verdict") != "PASS":
        raise StateError(
            f"lane: Oracle stand-in for {lane_id!r} is {stand_in.get('verdict') or 'missing'!r}, not PASS; "
            "the checkpoint stays closed and the grant unconsumed"
        )
    report_path = _required_document(stand_in.get("report"), "Oracle stand-in report")
    if stand_in.get("report_sha256") != _path_sha256(Path(report_path)):
        raise StateError(
            f"lane: Oracle stand-in report for {lane_id!r} changed after the approval; re-run the stand-in"
        )
    checklist_path = _required_document(stand_in.get("checklist"), "human acceptance checklist")
    if stand_in.get("checklist_sha256") != _path_sha256(Path(checklist_path)):
        raise StateError(
            f"lane: human acceptance checklist for {lane_id!r} changed after the approval; obtain fresh approval"
        )
    stand_in_binding = stand_in.get("binding")
    if (
        not isinstance(stand_in_binding, dict)
        or stand_in_binding.get("commit") != head
        or stand_in_binding.get("evidence_revision") != evidence_revision
    ):
        raise StateError(
            f"lane: Oracle stand-in for {lane_id!r} is bound to another commit or evidence revision; "
            "re-run the stand-in for the current lane state"
        )


def human_defer(lane_id: str, commit: str, evidence_revision: str, rationale: str,
                change: str | None = None, risk_assessment: str = "",
                evidence: object = None) -> Path:
    """Defer only a bounded, reversible, low-risk UI checkpoint (never a grant)."""
    path, state, plan = _state_context(change)
    lane = _state_lane(state, lane_id)
    _reject_finished_lane(lane_id, lane)
    if not lane.get("ui") or lane.get("sensitive") or lane.get("risk") == "high":
        raise StateError("lane: sensitive, high-risk, or non-UI lanes cannot defer the human checkpoint")
    if risk_assessment != "low" or not isinstance(rationale, str) or not rationale.strip():
        raise StateError("lane: deferral requires low-risk assessment and rationale for bounded, reversible impact and each excluded sensitive effect")
    coordination, runtime = _coordination_lane_for(state, plan, lane_id)
    gates = coordination.setdefault("human_gates", {})
    prior = gates.get(lane_id)
    if prior is not None and (not isinstance(prior, dict) or prior.get("state") != "waiting"):
        raise StateError("lane: existing human decision cannot be replaced by deferral; resolve checkpoint with human")
    evidence_path = _review_binding_path(evidence)
    if lane.get("ui") and not evidence_path:
        raise StateError("lane: ui deferral requires current evidence; provide --evidence")
    operation_lane = dict(lane)
    operation_lane.update(runtime)
    worktree, _ = _lane_runtime_for_operation(state, lane_id, operation_lane)
    if _run_git(worktree, "status", "--porcelain").stdout.strip():
        raise StateError("lane: commit changes before deferring human checkpoint")
    head = _git_output(worktree, "rev-parse", "HEAD")
    if commit != head or not evidence_revision:
        raise StateError("lane: deferral must bind current lane HEAD and evidence revision")
    # A decision must explicitly assert each exclusion. No mechanical risk classifier can
    # replace review of the actual diff; risk/plan flags remain binding at merge.
    gates[lane_id] = {
        "lane": lane_id, "state": "deferred-to-integrated", "approved_by": None,
        "binding": {"commit": head, "evidence_revision": evidence_revision,
                    "lane_plan_sha256": lane.get("lane_plan_sha256"),
                    "evidence_path": evidence_path,
                    "evidence_sha256": _path_sha256(Path(evidence_path)) if evidence_path else None},
        "rationale": rationale.strip(), "risk_assessment": risk_assessment,
        "bounded": True, "reversible": True,
        "excluded_effects": ["safety", "privacy", "sharing", "data-integrity", "content-correctness"],
    }
    _write_coordination(state, coordination, plan)
    return path


def _lane_requires_human_gate(lane: dict[str, Any]) -> bool:
    # A plan declaration, not an inferred state: `sensitive` marks a lane that touches
    # user-facing wording or content, safety, privacy, or other sensitive material.
    return bool(lane.get("ui") or lane.get("sensitive"))


def human_gate(
    lane_id: str,
    state_value: str,
    commit: str,
    evidence_revision: str,
    evidence: object = None,
    change: str | None = None,
    approved_by: str = "human",
    authorization: object = None,
    stand_in: object = None,
    checklist: object = None,
) -> Path:
    if approved_by not in {"human", "oracle"}:
        raise StateError("lane: --approved-by must be human or oracle; change it")
    if state_value not in {"waiting", "approved", "rejected", "revoked"}:
        raise StateError("lane: human gate state must be waiting, approved, rejected, or revoked; change --state")
    if not isinstance(commit, str) or not commit:
        raise StateError("lane: human gate commit must be a non-empty SHA; change --commit")
    if not isinstance(evidence_revision, str) or not evidence_revision.strip():
        raise StateError("lane: human gate evidence revision must be non-empty; change --evidence-revision")
    path, state, plan = _state_context(change)
    lane = _state_lane(state, lane_id)
    _reject_finished_lane(lane_id, lane)
    if not _lane_requires_human_gate(lane):
        raise StateError(f"lane: lane {lane_id!r} has no required human gate; use the plan flags")
    coordination, runtime = _coordination_lane_for(state, plan, lane_id)
    operation_lane = dict(lane)
    operation_lane.update(runtime)
    worktree, recorded_ref = _lane_runtime_for_operation(state, lane_id, operation_lane)
    runtime["worktree"] = str(worktree)
    runtime["ref"] = recorded_ref
    if _run_git(worktree, "status", "--porcelain").stdout.strip():
        raise StateError(f"lane: lane {lane_id!r} worktree is dirty; commit before recording human approval")
    actual_head = _git_output(worktree, "rev-parse", "HEAD")
    reviewed_head = _git_commit(worktree, commit)
    if reviewed_head != actual_head:
        raise StateError(
            f"lane: human gate commit {reviewed_head} is not the lane HEAD {actual_head}; approve current HEAD"
        )
    evidence_path = _review_binding_path(evidence)
    if lane.get("ui") and not evidence_path:
        raise StateError(f"lane: lane {lane_id!r} requires evidence for a human gate; provide --evidence")
    gates = coordination.setdefault("human_gates", {})
    if not isinstance(gates, dict):
        raise StateError("state: human gates are malformed; repair or remove the coordination data")
    record: dict[str, Any] = {
        "lane": lane_id,
        "state": state_value,
        "approved_by": "human" if state_value == "approved" else None,
        "binding": {
            "commit": reviewed_head,
            "lane_plan_sha256": lane.get("lane_plan_sha256"),
            "evidence_revision": evidence_revision.strip(),
            "evidence_path": evidence_path,
            "evidence_sha256": _path_sha256(Path(evidence_path)) if evidence_path else None,
        },
    }
    if state_value == "approved" and approved_by == "oracle":
        # A human grant lets an Oracle stand-in supply the acceptance judgment. The merge gate
        # that opens the pull request into the target branch is never authorizable and is not
        # performed by this tool.
        record.update(
            _record_authorization_approval(
                coordination, state, lane_id, recorded_ref, reviewed_head,
                evidence_revision.strip(), authorization, stand_in, checklist,
            )
        )
    elif state_value == "approved" and any(
        value is not None for value in (authorization, stand_in, checklist)
    ):
        raise StateError(
            f"lane: --authorization, --stand-in-report and --checklist need --approved-by oracle; "
            "an interactive approval carries no grant"
        )
    previous = gates.get(lane_id)
    if state_value == "waiting" and isinstance(previous, dict) and previous.get("state") in {"rejected", "revoked", "approved"}:
        raise StateError("lane: existing human decision cannot be replaced by waiting; resolve checkpoint with human")
    if state_value in {"rejected", "revoked"} and isinstance(previous, dict):
        # A human rejection or revocation suspends the grant for this checkpoint across subsequent
        # corrections: an old grant must not revive itself, so a renewal is a new grant with its own
        # identity rather than this record becoming active again.
        previous_authorization = previous.get("authorization")
        grant_id = previous_authorization.get("id") if isinstance(previous_authorization, dict) else None
        grants = coordination.get("authorizations")
        grant = grants.get(grant_id) if isinstance(grants, dict) and isinstance(grant_id, str) else None
        if isinstance(grant, dict) and grant.get("status") == "active":
            grant["status"] = "revoked" if state_value == "revoked" else "suspended"
            grant["suspended_by"] = state_value
    gates[lane_id] = record
    _write_coordination(state, coordination, plan)
    return path


def _review_commit(review: object) -> str | None:
    if not isinstance(review, dict):
        return None
    binding = review.get("binding")
    if isinstance(binding, dict):
        value = binding.get("commit")
        if isinstance(value, str) and value:
            return value
    value = review.get("reviewed_commit")
    return value if isinstance(value, str) and value else None


def _validate_human_gate(
    lane_id: str,
    lane: dict[str, Any],
    head: str,
    coordination: dict[str, Any],
    approved_plan_sha256: str | None = None,
    allow_consumed_by: str | None = None,
    allow_deferred: bool = False,
) -> None:
    if not _lane_requires_human_gate(lane):
        return
    gates = coordination.get("human_gates")
    if not isinstance(gates, dict):
        raise StateError(f"lane: human gate for {lane_id!r} is missing; obtain interactive approval")
    gate = gates.get(lane_id)
    if not isinstance(gate, dict):
        raise StateError(f"lane: human gate for {lane_id!r} is missing; obtain interactive approval")
    if gate.get("lane") != lane_id:
        raise StateError(f"lane: human gate for {lane_id!r} is bound to another lane; obtain fresh approval")
    binding = gate.get("binding")
    if lane.get("runtime_plan") and (not isinstance(binding, dict)
                                    or binding.get("lane_plan_sha256") != lane.get("lane_plan_sha256")):
        raise StateError(f"lane: human gate for {lane_id!r} has a stale plan binding; obtain fresh approval")
    if gate.get("state") == "deferred-to-integrated" and allow_deferred:
        binding = gate.get("binding")
        if (lane.get("sensitive") or lane.get("risk") == "high" or not lane.get("ui")
                or not isinstance(binding, dict) or binding.get("commit") != head
                or not binding.get("evidence_revision") or not gate.get("rationale")
                or gate.get("risk_assessment") != "low"
                or gate.get("approved_by") is not None or gate.get("authorization") is not None
                or gate.get("bounded") is not True or gate.get("reversible") is not True
                or not {"safety", "privacy", "sharing", "data-integrity", "content-correctness"}.issubset(gate.get("excluded_effects", []))):
            raise StateError("lane: deferred checkpoint is ineligible or stale; obtain early human approval")
        evidence_path = binding.get("evidence_path")
        if lane.get("ui") and not evidence_path:
            raise StateError("lane: ui deferral has no current evidence; obtain fresh evidence")
        if evidence_path:
            if (not isinstance(evidence_path, str) or not Path(evidence_path).is_file()
                    or _path_sha256(Path(evidence_path)) != binding.get("evidence_sha256")):
                raise StateError("lane: deferred ui evidence changed or missing; recapture before merge")
        return
    if gate.get("state") != "approved":
        state_value = gate.get("state") or "missing"
        raise StateError(
            f"lane: human gate for {lane_id!r} is {state_value!r}; obtain current interactive approval"
        )
    if gate.get("stale") or gate.get("revoked"):
        raise StateError(f"lane: human gate for {lane_id!r} is stale or revoked; obtain fresh approval")
    binding = gate.get("binding")
    if not isinstance(binding, dict):
        raise StateError(f"lane: human gate for {lane_id!r} has no binding; obtain fresh approval")
    if binding.get("commit") != head:
        raise StateError(
            f"lane: human gate for {lane_id!r} is bound to {binding.get('commit') or 'no commit'}, lane HEAD is {head}; "
            "obtain fresh approval"
        )
    evidence_revision = binding.get("evidence_revision")
    if not isinstance(evidence_revision, str) or not evidence_revision.strip():
        raise StateError(f"lane: human gate for {lane_id!r} has no evidence revision; obtain fresh approval")
    evidence_path = binding.get("evidence_path")
    if evidence_path is not None:
        _review_binding_path(evidence_path)
        if _path_sha256(Path(evidence_path)) != binding.get("evidence_sha256"):
            raise StateError(f"lane: human gate for {lane_id!r} has changed evidence; obtain fresh approval")
    if lane.get("ui") and not evidence_path:
        raise StateError(f"lane: human gate for {lane_id!r} has no current evidence; obtain fresh approval")
    approved_by = gate.get("approved_by")
    if approved_by == "human":
        # An interactive approval carries no grant. Letting one satisfy it is exactly the silent
        # substitution this checkpoint exists to prevent.
        if any(
            key in gate
            for key in ("authorization", "authorization_id", "auto_approved", "auto_approval", "stand_in")
        ):
            raise StateError(
                f"lane: interactive approval for {lane_id!r} cannot be satisfied by authorization; remove it "
                "and approve interactively, or record the Oracle stand-in with --approved-by oracle"
            )
        return
    if approved_by == "oracle":
        _validate_authorization_approval(
            lane_id, gate, head, evidence_revision, coordination, approved_plan_sha256, allow_consumed_by
        )
        return
    raise StateError(
        f"lane: human gate for {lane_id!r} has approved_by {approved_by or 'missing'!r}; record an interactive "
        "human approval or an Oracle stand-in under a current authorization"
    )


def _human_gate_flag(lane: dict[str, Any]) -> str:
    if lane.get("sensitive"):
        return "sensitive"
    return "ui"


def _human_gate_summary(
    lane_id: str,
    lane: dict[str, Any],
    gates: object,
    coordination_available: bool = True,
    authorizations: object = None,
    allow_deferred: bool = False,
) -> str | None:
    """Say whether a lane needs the human gate and what its record currently allows.

    Display only; `_validate_human_gate` on the merge path is the enforcement. When the
    lane HEAD is resolvable this calls that same validator, so the reader sees the exact
    refusal a merge would hit rather than a second opinion.
    """
    if not _lane_requires_human_gate(lane):
        return None
    flag = _human_gate_flag(lane)
    if not coordination_available:
        return f"required ({flag}); coordination unavailable"
    head = _lane_current_head(lane)
    gate = gates.get(lane_id) if isinstance(gates, dict) else None
    if (isinstance(gate, dict) and gate.get("state") == "waived" and head is not None
            and isinstance(gate.get("binding"), dict) and gate["binding"].get("commit") == head):
        return f"required ({flag}): waived"
    if head is not None:
        try:
            _validate_human_gate(
                lane_id, lane, head,
                {"human_gates": gates, "authorizations": authorizations},
                allow_deferred=allow_deferred,
            )
        except StateError as exc:
            return f"required ({flag}); blocked: {_one_line(exc)}"
        gate = gates.get(lane_id) if isinstance(gates, dict) else None
        return (f"required ({flag}): deferred-to-integrated; PR inspection pending"
                if isinstance(gate, dict) and gate.get("state") == "deferred-to-integrated"
                else f"required ({flag}): approved")
    if not isinstance(gates, dict):
        return f"required ({flag}): missing; obtain interactive approval"
    gate = gates.get(lane_id)
    if not isinstance(gate, dict):
        return f"required ({flag}): missing; obtain interactive approval"
    if gate.get("stale") or gate.get("revoked"):
        return f"required ({flag}): stale or revoked; obtain fresh approval"
    if gate.get("state") != "approved":
        return f"required ({flag}): {gate.get('state') or 'missing'}; obtain interactive approval"
    return f"required ({flag}): approved"


def _review_round(review: object) -> int | None:
    if not isinstance(review, dict):
        return None
    value = review.get("round")
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _lane_current_head(lane: dict[str, Any]) -> str | None:
    worktree_value = lane.get("worktree")
    if not isinstance(worktree_value, str) or not worktree_value:
        return None
    try:
        return _git_output(Path(worktree_value), "rev-parse", "HEAD")
    except StateError:
        return None


def _review_status(lane: dict[str, Any], review: object, head: str | None = None) -> str:
    bound = _review_commit(review)
    current_head = head or _lane_current_head(lane)
    if bound and current_head:
        if bound == current_head:
            return "current"
        worktree_value = lane.get("worktree")
        if isinstance(worktree_value, str) and worktree_value:
            try:
                if _is_ancestor(Path(worktree_value), bound, current_head):
                    return "superseded"
            except StateError:
                pass
        return "unrelated"
    if isinstance(review, dict) and review.get("status") in {"current", "superseded", "unrelated"}:
        return str(review["status"])
    return "current"


def _annotate_review_view(state: dict[str, Any]) -> None:
    lanes = state.get("lanes")
    if not isinstance(lanes, dict):
        return
    for lane in lanes.values():
        if not isinstance(lane, dict):
            continue
        reviews = lane.get("reviews")
        if not isinstance(reviews, list):
            continue
        head = _lane_current_head(lane)
        for review in reviews:
            if not isinstance(review, dict):
                continue
            commit = _review_commit(review)
            if commit:
                review["reviewed_commit"] = commit
            review["status"] = _review_status(lane, review, head)
            review["gate_eligible"] = review["status"] == "current"


def lane_open(lane_id: str, change: str | None = None) -> Path:
    _ensure_workflow_ignored(_workflow_checkout(Path.cwd()))
    path, state, plan = _state_context(change)
    lane = _state_lane(state, lane_id)
    if lane.get("complete"):
        raise StateError(
            f"lane: lane {lane_id!r} is already complete from committed ticks; do not reopen it"
        )
    _require_plan_authorization(state, lane_id)
    coordination, runtime = _coordination_lane_for(state, plan, lane_id)
    phase = runtime.get("phase", "created")
    expected_ref = _lane_ref(state, lane_id)
    recorded_path = runtime.get("worktree")
    recorded_ref = runtime.get("ref")
    expected_path = (Path(recorded_path).expanduser() if recorded_path
                     else _lane_worktree_path(state, lane_id)).resolve()
    if recorded_ref and recorded_ref != expected_ref:
        raise StateError(f"lane: lane {lane_id!r} has a different recorded ref; reconcile it first")
    repository = Path(state["feature_checkout"]).resolve()
    registered_branch = _worktree_branch(repository, expected_path)
    existing_ref = _ref_sha(repository, expected_ref)
    if phase in {"writing", "reviewing", "revising"} and registered_branch == expected_ref and existing_ref:
        return path
    if not _lane_ready_for_open(state, plan, lane_id):
        raise StateError(
            f"lane: lane {lane_id!r} is not ready; {_readiness_reasons(state, plan).get(lane_id, 'merge prerequisites first')}"
        )
    current_branch = _git_output(repository, "rev-parse", "--abbrev-ref", "HEAD")
    if current_branch != _feature_branch(state):
        raise StateError(
            f"lane: feature checkout is on {current_branch!r}, not {_feature_branch(state)!r}; switch branches"
        )
    feature_head = _git_output(repository, "rev-parse", "HEAD")
    if existing_ref:
        base = _lane_merge_base(repository, expected_ref, feature_head)
    else:
        base = feature_head
    runtime.update({"worktree": str(expected_path), "ref": expected_ref, "base": base, "phase": "created"})
    _write_coordination(state, coordination, plan)

    registered_branch = _worktree_branch(repository, expected_path)
    existing_ref = _ref_sha(repository, expected_ref)
    if registered_branch:
        if registered_branch != expected_ref:
            raise StateError(f"lane: worktree path is registered to {registered_branch}; reconcile it first")
        if not existing_ref:
            raise StateError(f"lane: worktree exists but ref {expected_ref} is missing; reconcile it first")
    elif expected_path.exists():
        raise StateError(f"lane: worktree path already exists but is not a git worktree; move it first")
    elif existing_ref:
        result = _run_git(repository, "worktree", "add", str(expected_path), expected_ref)
        if result.returncode:
            raise StateError(f"lane: cannot attach existing worktree ref: {_one_line(result.stderr)}; reconcile it")
    else:
        result = _run_git(repository, "worktree", "add", "-b", expected_ref, str(expected_path), base)
        if result.returncode:
            raise StateError(f"lane: cannot create worktree: {_one_line(result.stderr)}; retry after repair")
    if _worktree_branch(repository, expected_path) != expected_ref or not _ref_sha(repository, expected_ref):
        raise StateError(f"lane: worktree creation did not produce the expected ref {expected_ref}; reconcile it")
    runtime["phase"] = "writing"
    _write_coordination(state, coordination, plan)
    return path


def lane_close(lane_id: str, change: str | None = None) -> Path:
    path, state, plan = _state_context(change)
    lane = _state_lane(state, lane_id)
    coordination, runtime = _coordination_lane_for(state, plan, lane_id)
    phase = runtime.get("phase", "created")
    repository = Path(state["feature_checkout"]).resolve()
    expected_path = _lane_worktree_path(state, lane_id).resolve()
    expected_ref = _lane_ref(state, lane_id)
    if phase not in {"merged", "closed"}:
        lane_sha = _ref_sha(repository, expected_ref)
        if not lane_sha or not _is_ancestor(repository, lane_sha, _git_output(repository, "rev-parse", "HEAD")):
            raise StateError(f"lane: lane {lane_id!r} is not in the feature branch; merge it before closing")
    recorded_path = runtime.get("worktree")
    recorded_ref = runtime.get("ref")
    if recorded_ref and recorded_ref != expected_ref:
        raise StateError(f"lane: lane {lane_id!r} cleanup ref is not deterministic; reconcile it first")
    # A recorded worktree is authoritative for the lane's lifetime (matching
    # _lane_runtime_for_operation): root resolution may change between open and
    # close (FEATDEV_LANE_WORKTREE_ROOT override, child-worktree adoption), so
    # cleanup targets the recorded location and only falls back to the computed
    # path when nothing was recorded.
    recorded_path = Path(recorded_path).expanduser().resolve() if recorded_path else expected_path
    recorded_ref = recorded_ref or expected_ref
    registered_branch = _worktree_branch(repository, recorded_path)
    if registered_branch and registered_branch != recorded_ref:
        raise StateError(f"lane: worktree path is registered to {registered_branch}; reconcile it first")
    if registered_branch:
        if not recorded_path.is_dir():
            raise StateError(f"lane: registered worktree {recorded_path} is missing; reconcile it first")
        if _run_git(recorded_path, "status", "--porcelain").stdout.strip():
            raise StateError(f"lane: worktree {recorded_path} is dirty; commit or discard changes before closing")
        result = _run_git(repository, "worktree", "remove", str(recorded_path))
        if result.returncode:
            raise StateError(f"lane: cannot remove worktree: {_one_line(result.stderr)}; retry after repair")
    elif recorded_path.exists():
        raise StateError(f"lane: worktree path exists but is not registered; move it before closing")
    if _ref_sha(repository, recorded_ref):
        result = _run_git(repository, "branch", "-d", recorded_ref)
        if result.returncode:
            raise StateError(f"lane: cannot remove ref {recorded_ref}: {_one_line(result.stderr)}; retry after repair")
    runtime["phase"] = "closed"
    runtime["cleanup"] = {"worktree_removed": True, "ref_removed": True}
    _write_coordination(state, coordination, plan)
    return path


def lane_review(
    lane_id: str,
    role: str,
    verdict: str,
    commit: str,
    report: object,
    evidence_revision: str | None = None,
    evidence: object = None,
    change: str | None = None,
) -> Path:
    role = _require_role(role)
    path, state, plan = _state_context(change)
    lane = _state_lane(state, lane_id)
    _reject_finished_lane(lane_id, lane)
    if lane.get("complete"):
        raise StateError(f"lane: lane {lane_id!r} is already complete from committed ticks; review is unnecessary")
    if verdict not in {"PASS", "MORE WORK"}:
        raise StateError("lane: verdict must be PASS or MORE WORK; change it")
    coordination, runtime = _coordination_lane_for(state, plan, lane_id)
    operation_lane = dict(lane)
    operation_lane.update(runtime)
    if role not in _required_review_roles(operation_lane):
        raise StateError(f"lane: reviewer role {role!r} is not required for lane {lane_id!r}; use the plan flags")
    operation_lane = dict(lane)
    operation_lane.update(runtime)
    worktree, recorded_ref = _lane_runtime_for_operation(state, lane_id, operation_lane)
    runtime["worktree"] = str(worktree)
    runtime["ref"] = recorded_ref
    if _run_git(worktree, "status", "--porcelain").stdout.strip():
        raise StateError(f"lane: lane {lane_id!r} worktree is dirty; commit before review")
    actual_head = _git_output(worktree, "rev-parse", "HEAD")
    change_name = state.get("change")
    if not isinstance(change_name, str) or not change_name:
        raise StateError("lane: change is missing; re-derive the state")
    assigned = _lane_assigned_tasks(plan, lane_id)
    checked = _lane_checked_tasks(worktree, change_name, plan)
    missing = assigned - checked
    if missing:
        raise StateError(f"lane: lane {lane_id!r} has unfinished tasks ({', '.join(sorted(missing))}); tick every assigned task before review")
    reviewed_head = _git_commit(worktree, commit)
    current_review = reviewed_head == actual_head
    if not current_review and not _is_ancestor(worktree, reviewed_head, actual_head):
        raise StateError(
            f"lane: review commit {reviewed_head} is not in lane HEAD {actual_head} and is not an ancestor; "
            "review a commit in this lane"
        )
    report_path = _review_report(report)
    evidence_path = _review_binding_path(evidence)
    if lane.get("ui") and role == "ui-reviewer" and verdict == "PASS" and not evidence_path:
        raise StateError(f"lane: lane {lane_id!r} requires evidence for a UI PASS; provide --evidence")
    reviews = runtime.setdefault("reviews", [])
    if not isinstance(reviews, list):
        raise StateError("state: reviews are malformed; repair coordination data")
    role_reviews = [item for item in reviews if isinstance(item, dict) and item.get("role") == role]
    role_rounds = [number for item in role_reviews if (number := _review_round(item)) is not None]
    same_head = [item for item in role_reviews if _review_commit(item) == reviewed_head]
    round_number = max(
        (_review_round(item) or 0 for item in same_head),
        default=max(role_rounds, default=0) + 1,
    )
    prior_revision = next(
        (
            item.get("binding", {}).get("evidence_revision")
            for item in same_head
            if isinstance(item.get("binding"), dict) and item["binding"].get("evidence_revision")
        ),
        None,
    )
    revision = str(evidence_revision or prior_revision or f"r{round_number}")
    record = {
        "round": round_number,
        "role": role,
        "verdict": verdict,
        "reviewed_commit": reviewed_head,
        "status": "current" if current_review else "superseded",
        "gate_eligible": current_review,
        "binding": {
            "commit": reviewed_head,
            "lane_plan_sha256": lane.get("lane_plan_sha256"),
            "evidence_revision": revision,
            "evidence_path": evidence_path,
            "evidence_sha256": _path_sha256(Path(evidence_path)) if evidence_path else None,
        },
        "report": report_path,
    }
    for previous in reviews:
        if not isinstance(previous, dict):
            continue
        previous_binding = previous.get("binding")
        if not isinstance(previous_binding, dict):
            continue
        if (
            previous.get("role") == role
            and previous.get("reused") is not True
            and previous.get("verdict") == verdict
            and _review_commit(previous) == reviewed_head
            and previous_binding.get("evidence_revision") == revision
            and previous_binding.get("lane_plan_sha256") == lane.get("lane_plan_sha256")
            and previous_binding.get("evidence_path") == evidence_path
            and previous_binding.get("evidence_sha256") == record["binding"]["evidence_sha256"]
            and previous.get("report") == report_path
        ):
            previous.update({"status": record["status"], "gate_eligible": current_review, "reviewed_commit": reviewed_head})
            _write_coordination(state, coordination, plan)
            return path
    reviews.append(record)
    view_lane = dict(lane)
    view_lane.update(runtime)
    for previous in reviews:
        if not isinstance(previous, dict):
            continue
        previous_commit = _review_commit(previous)
        if previous_commit == actual_head:
            previous["status"] = "current"
            previous["gate_eligible"] = True
        elif previous_commit and _is_ancestor(worktree, previous_commit, actual_head):
            previous["status"] = "superseded"
            previous["gate_eligible"] = False
        if previous_commit:
            previous["reviewed_commit"] = previous_commit
    latest = _latest_reviews(view_lane, actual_head)
    runtime["phase"] = "revising" if any(
        item.get("verdict") == "MORE WORK" for item in latest.values()
    ) else "reviewing"
    _write_coordination(state, coordination, plan)
    return path


def _latest_reviews(lane: dict[str, Any], head: str | None = None) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for review in lane.get("reviews") or []:
        if not isinstance(review, dict) or not isinstance(review.get("role"), str):
            continue
        # Combined records have their own final-review validator. They must never silently
        # satisfy a later standard-mode code gate after the plan changes mode.
        if head is not None and _review_status(lane, review, head) != "current":
            continue
        if head is None and review.get("status") == "superseded":
            continue
        latest[review["role"]] = review
    return latest


def _validate_lane_reviews(
    lane_id: str,
    lane: dict[str, Any],
    head: str,
    required_roles: list[str] | None = None,
) -> None:
    roles = (
        list(required_roles) if required_roles is not None
        else _required_review_roles(lane)
    )
    latest = _latest_reviews(lane, head)
    for role in roles:
        review = latest.get(role)
        if not review:
            history = [
                item for item in lane.get("reviews") or []
                if isinstance(item, dict) and item.get("role") == role
            ]
            previous = history[-1] if history else None
            bound = _review_commit(previous)
            if bound and bound != head:
                state = _review_status(lane, previous, head)
                if state == "superseded":
                    raise StateError(
                        f"lane: review {role!r} is superseded (bound to {bound}), lane HEAD is {head}; re-review"
                    )
                raise StateError(
                    f"lane: review {role!r} is bound to {bound}, lane HEAD is {head}; re-review"
                )
            raise StateError(
                f"lane: required review {role!r} is missing for current HEAD; superseded history cannot satisfy "
                "the gate; review the lane before merging"
            )
        if review.get("verdict") != "PASS":
            raise StateError(f"lane: review {role!r} is {review.get('verdict')!r}, not PASS; fix the lane")
        binding = review.get("binding")
        if not isinstance(binding, dict) or binding.get("commit") != head:
            bound = binding.get("commit") if isinstance(binding, dict) else None
            raise StateError(f"lane: review {role!r} is bound to {bound or 'no commit'}, lane HEAD is {head}; re-review")
        if lane.get("runtime_plan") and binding.get("lane_plan_sha256") != lane.get("lane_plan_sha256"):
            raise StateError(f"lane: review {role!r} has a stale plan binding; re-review the current lane plan")
        if not binding.get("evidence_revision"):
            raise StateError(f"lane: review {role!r} has no evidence revision; re-record the review")
        if review.get("reused") is True:
            raise StateError(f"lane: review {role!r} is not a direct current-HEAD review; obtain a fresh verdict")
        report = review.get("report")
        if not isinstance(report, str) or not Path(report).is_file() or Path(report).stat().st_size == 0:
            raise StateError(f"lane: review {role!r} report is missing or empty; re-review the lane")
        if lane.get("ui") and role == "ui-reviewer":
            evidence = binding.get("evidence_path")
            if not isinstance(evidence, str) or not Path(evidence).is_file() or Path(evidence).stat().st_size == 0:
                raise StateError(f"lane: review {role!r} UI evidence is missing; re-review with --evidence")
            if _path_sha256(Path(evidence)) != binding.get("evidence_sha256"):
                raise StateError(f"lane: review {role!r} evidence changed; obtain a fresh current-HEAD review")


def _validate_resolved_lane_base(repository: Path, base: str, head: str, feature_head: str) -> None:
    if not _is_ancestor(repository, base, head) or not _is_ancestor(repository, base, feature_head):
        raise StateError(
            f"lane: resolved base {base} is not an ancestor of both lane HEAD {head} and feature HEAD "
            f"{feature_head}; reconcile ancestry"
        )


def _lane_merge_base(repository: Path, ref: str, feature_head: str) -> str:
    if _ref_sha(repository, ref) is None:
        raise StateError(f"lane: lane ref {ref} is missing; reconcile the lane ref before merging")
    result = _run_git(repository, "merge-base", "--all", f"refs/heads/{ref}", feature_head)
    if result.returncode:
        detail = _one_line(result.stderr.strip() or result.stdout.strip())
        raise StateError(
            f"lane: cannot resolve merge base for {ref} and {feature_head}"
            f"{(': ' + detail) if detail else ''}; reconcile ancestry"
        )
    bases = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(bases) != 1:
        found = ", ".join(bases) if bases else "none"
        raise StateError(
            f"lane: merge base for {ref} and {feature_head} is not unique ({found}); reconcile ancestry"
        )
    base = _git_commit(repository, bases[0])
    return base


def _candidate_tick_state(
    repository: Path,
    change: str,
    plan: dict[str, Any],
    lane_id: str,
    base: str,
    head: str,
    approved_head: str,
) -> tuple[set[str], set[str], bool]:
    base_tasks, base_relative = _committed_tasks(repository, base, change)
    candidate_tasks, tasks_relative = _committed_tasks(repository, head, change)
    approved_tasks, approved_relative = _committed_tasks(repository, approved_head, change)
    if tasks_relative != approved_relative:
        raise StateError("lane: candidate moved the approved task file; restore its approved location")
    changed_tasks = _git_output(repository, "diff", "--name-only", base, head,
                                "--", ":(glob)openspec/changes/**/tasks.md").splitlines()
    foreign_tasks = [path for path in changed_tasks if path != tasks_relative]
    if foreign_tasks:
        raise StateError("lane: candidate modified task files for other changes: " + ", ".join(foreign_tasks))
    _, _, relative = _change_plan_at(repository, approved_head, change)
    if relative != _REPO_PLAN:
        base_plan, _, base_relative = _change_plan_at(repository, base, change)
        candidate_plan, _, candidate_relative = _change_plan_at(repository, head, change)
        approved_plan, _, _ = _change_plan_at(repository, approved_head, change)
        base_document = json.loads(_committed_file(repository, base, base_relative))
        candidate_document = json.loads(_committed_file(repository, head, candidate_relative))
        if base_document != candidate_document or base_plan != approved_plan or candidate_plan != approved_plan:
            raise StateError(
                "lane: candidate plan differs from the approved plan at lane base or approved HEAD; "
                "revert the plan change and have the Coordinator revise it on the feature branch"
            )
    if _normalized_task_definitions(candidate_tasks) != _normalized_task_definitions(approved_tasks):
        raise StateError(
            f"lane: candidate task definitions in {tasks_relative} differ from the approved definitions at "
            f"approved HEAD {approved_head}; restore the definitions and tick only this lane's assigned tasks"
        )
    _check_task_ids_text(plan, base_tasks, f"{tasks_relative} at lane base")
    base_checked = set(_checked_task_ids_from_text(base_tasks))
    candidate_path = state_derive(change, head, repository)
    candidate_state = _read_state(candidate_path)
    if candidate_state.get("source_sha") != head:
        raise StateError(
            f"lane: candidate state source SHA {candidate_state.get('source_sha') or 'missing'} does not equal "
            f"lane HEAD {head}; re-derive candidate state"
        )
    checked_value = candidate_state.get("checked_tasks")
    if not isinstance(checked_value, list) or any(not isinstance(item, str) for item in checked_value):
        raise StateError("lane: candidate-derived checked tasks are malformed; re-derive candidate state")
    candidate_checked = set(checked_value)
    plan_tasks = plan.get("tasks")
    assignment = {
        task_id for task_id, task in plan_tasks.items()
        if isinstance(task, dict) and task.get("lane") == lane_id
    }
    candidate_lanes = candidate_state.get("lanes")
    candidate_lane = candidate_lanes.get(lane_id) if isinstance(candidate_lanes, dict) else None
    if not isinstance(candidate_lane, dict):
        raise StateError(f"lane: candidate-derived state has no lane {lane_id!r}; re-derive candidate state")
    candidate_lane_value = candidate_lane.get("checked_tasks")
    if not isinstance(candidate_lane_value, list) or any(not isinstance(item, str) for item in candidate_lane_value):
        raise StateError(f"lane: candidate-derived checked tasks for {lane_id!r} are malformed; re-derive candidate state")
    candidate_lane_checked = set(candidate_lane_value)
    added = candidate_checked - base_checked
    removed = base_checked - candidate_checked
    # Ticks of assigned tasks already checked at the lane base are inherited earlier
    # completion (e.g. a same-lane prerequisite ticked on the feature branch before the
    # lane opened). The documented rule excludes inherited earlier-lane ticks from the
    # added set instead of demanding a duplicate in-lane completion claim.
    inherited = candidate_checked & base_checked & assignment
    expected_added = assignment - inherited
    problems: list[str] = []
    if removed:
        problems.append(f"removed checked tasks {', '.join(sorted(removed))}")
    if added != expected_added:
        problems.append(
            f"added ticks {', '.join(sorted(added)) or 'none'} do not match plan assignment "
            f"{', '.join(sorted(expected_added)) or 'none'}"
        )
    if candidate_lane_checked != assignment:
        problems.append(
            f"candidate lane ticks {', '.join(sorted(candidate_lane_checked)) or 'none'} do not match plan "
            f"{', '.join(sorted(assignment)) or 'none'}"
        )
    if problems:
        raise StateError(
            f"lane: tick/state/plan mismatch for {lane_id!r}: {'; '.join(problems)}; "
            "tick exactly the assigned tasks and preserve existing ticks"
        )
    tick_protocol = bool(added or candidate_lane_checked)
    return candidate_lane_checked, base_checked, tick_protocol


def _completed_lane_reviewed_head(lane: dict[str, Any], lane_id: str) -> str | None:
    """Recover the reviewed commit of a completed lane from its current PASS review bindings."""
    heads: set[str] = set()
    for role in _required_review_roles(lane):
        passes = [
            item for item in lane.get("reviews") or []
            if isinstance(item, dict) and item.get("role") == role and item.get("verdict") == "PASS"
        ]
        bound = _review_commit(passes[-1]) if passes else None
        if not bound:
            return None
        heads.add(bound)
    if len(heads) != 1:
        return None
    return next(iter(heads))


def _validate_completed_lane_merge(
    state: dict[str, Any],
    lane_id: str,
    lane: dict[str, Any],
    runtime: dict[str, Any],
    coordination: dict[str, Any],
    approved_plan_sha256: str | None = None,
) -> None:
    """Establish that an already-complete lane was merged from reviewed, contained work.

    This runs before the completed-lane fast return in lane_merge. A hand-resolved merge reached
    through the documented recovery path lands the lane's ticks on the feature branch without this
    command having validated them, so the reviewed commit, the lane's required reviews, and the lane's
    human gate must be re-established here. Coordination that cannot supply that evidence fails closed.

    Two cases are distinguished. When coordination records the merge this tool performed, the gate was
    already validated and its one-use grant consumed at that merge, so re-litigating them would break
    idempotency. When no merge entry is recorded - the hand-resolved recovery - the human gate is
    validated now, its grant is consumed, and the validated completion is recorded so the recovery
    cannot be replayed.
    """
    repository = Path(state["feature_checkout"]).resolve()
    feature_head = _git_output(repository, "rev-parse", _feature_branch(state))
    operation_lane = dict(lane)
    operation_lane.update(runtime)
    merge = runtime.get("merge")
    recorded = isinstance(merge, dict) and bool(merge.get("reviewed_head"))
    if recorded:
        reviewed_head = merge.get("reviewed_head")
        merge_commit = merge.get("merge_commit")
    else:
        # A hand-resolved merge records no merge entry: recover the reviewed commit from the lane's
        # own review bindings, or refuse when even that evidence is gone.
        reviewed_head = _completed_lane_reviewed_head(operation_lane, lane_id)
        merge_commit = reviewed_head
    if not isinstance(reviewed_head, str) or not reviewed_head:
        raise StateError(
            f"lane: completed lane {lane_id!r} has no recorded merge and no review evidence; reconcile the "
            "manual merge and re-derive the state, then re-run lane-merge"
        )
    reviewed_head = _git_commit(repository, reviewed_head)
    if not _is_ancestor(repository, reviewed_head, feature_head):
        raise StateError(
            f"lane: reviewed commit {reviewed_head} of completed lane {lane_id!r} is not in the feature branch; "
            "reconcile the manual merge so the reviewed work is present, then re-run lane-merge"
        )
    if isinstance(merge_commit, str) and merge_commit:
        merged_head = _git_commit(repository, merge_commit)
        if not _is_ancestor(repository, merged_head, feature_head):
            raise StateError(
                f"lane: recorded merge commit {merged_head} of completed lane {lane_id!r} is not in the feature "
                "branch; reconcile the feature branch, then re-run lane-merge"
            )
    if not (recorded and "reviews" in (merge.get("human_override") or {}).get("skipped_gates", [])):
        _validate_lane_reviews(lane_id, operation_lane, reviewed_head)
    if recorded:
        # The tool validated this gate when it performed the merge, and its one-use grant is already
        # consumed: re-litigating a completed checkpoint would break idempotency.
        return
    # A hand-resolved recovery has no recorded validation, so the human gate must still hold here,
    # and its one-use grant is consumed by this validation so the recovery cannot be replayed.
    _validate_human_gate(lane_id, operation_lane, reviewed_head, coordination, approved_plan_sha256,
                         allow_deferred=True)
    _consume_human_grant(coordination, lane_id)
    # Record the validated completion so replaying this recovery is idempotent rather than a second,
    # unreviewed acceptance of the lane.
    runtime["merge"] = {
        "reviewed_head": reviewed_head,
        "merge_commit": merge_commit if isinstance(merge_commit, str) and merge_commit else reviewed_head,
        "base": reviewed_head,
    }


def _consume_human_grant(coordination: dict[str, Any], lane_id: str) -> None:
    gates = coordination.get("human_gates")
    consumed_gate = gates.get(lane_id) if isinstance(gates, dict) else None
    if isinstance(consumed_gate, dict) and consumed_gate.get("approved_by") == "oracle":
        authorization = consumed_gate.get("authorization")
        grant_id = authorization.get("id") if isinstance(authorization, dict) else None
        grants = coordination.get("authorizations")
        grant = grants.get(grant_id) if isinstance(grants, dict) else None
        if isinstance(grant, dict):
            grant["status"] = "consumed"
            grant["consumed_by"] = lane_id


def lane_merge(
    lane_id: str,
    change: str | None = None,
    human_override: bool = False,
    override_reason: str | None = None,
    override_head: str | None = None,
    waive: list[str] | None = None,
) -> Path:
    if not human_override and (override_reason or override_head or waive):
        raise StateError("lane: override options require --human-override")
    path, state, plan = _state_context(change)
    lane = _state_lane(state, lane_id)
    if not (human_override and "plan-approval" in (waive or [])):
        _require_plan_authorization(state, lane_id)
    coordination, runtime = _coordination_lane_for(state, plan, lane_id)
    if human_override:
        reason = (override_reason or "").strip()
        if not reason:
            raise StateError(
                "lane: --human-override requires --override-reason naming the human decision; "
                "an unexplained override cannot be recorded"
            )
        waived = set(waive or ["reviews"])
        if not override_head:
            raise StateError("lane: --human-override requires --override-head identifying the approved candidate")
        operation_lane = dict(lane)
        operation_lane.update(runtime)
        worktree, ref = _lane_runtime_for_operation(state, lane_id, operation_lane)
        runtime["worktree"] = str(worktree)
        runtime["ref"] = ref
        repository = Path(state["feature_checkout"]).resolve()
        branch = _feature_branch(state)
        current_branch = _git_output(repository, "rev-parse", "--abbrev-ref", "HEAD")
        if current_branch != branch:
            raise StateError(f"lane: feature checkout is on {current_branch!r}, not {branch!r}; switch branches")
        if "clean-trees" not in waived and _run_git(worktree, "status", "--porcelain").stdout.strip():
            raise StateError("lane: lane worktree is dirty; commit before merging")
        head = _git_output(worktree, "rev-parse", "HEAD")
        if _git_commit(worktree, override_head) != head:
            raise StateError("lane: override candidate differs from lane HEAD; obtain a renewed human decision")
        ref_head = _ref_sha(repository, ref)
        if ref_head != head:
            raise StateError(f"lane: ref {ref} is at {ref_head or 'no commit'}, worktree HEAD is {head}; reconcile it first")
        if "clean-trees" not in waived and _run_git(repository, "status", "--porcelain", "--untracked-files=no").stdout.strip():
            raise StateError("lane: feature checkout is dirty; commit or discard changes before merging")
        feature_head = _git_output(repository, "rev-parse", branch)
        base = None
        if "ancestry" not in waived or "tasks" not in waived:
            base = _lane_merge_base(repository, ref, feature_head)
        if "ancestry" not in waived:
            _validate_resolved_lane_base(repository, base, head, feature_head)
        if "tasks" not in waived:
            override_change = state.get("change")
            if not isinstance(override_change, str) or not override_change:
                raise StateError("lane: change is missing; re-derive the state")
            assigned = _lane_assigned_tasks(plan, lane_id)
            checked = _lane_checked_tasks(worktree, override_change, plan)
            if missing := (assigned - checked):
                raise StateError(f"lane: lane {lane_id!r} has unfinished tasks ({', '.join(sorted(missing))})")
            _candidate_tick_state(repository, override_change, plan, lane_id, base, head, feature_head)
        override_lane = dict(lane)
        override_lane.update(runtime)
        if "reviews" not in waived:
            _validate_lane_reviews(lane_id, override_lane, head)
        if "human-gate" not in waived:
            _validate_human_gate(lane_id, override_lane, head, coordination, state.get("plan_sha256"),
                                 allow_deferred=True)
        elif _lane_requires_human_gate(override_lane) and not isinstance(coordination.get("human_gates", {}), dict):
            raise StateError("state: human gates are malformed; repair the coordination data")
        if _is_ancestor(repository, head, feature_head):
            merge_commit = feature_head
        else:
            result = _run_git(
                repository,
                "merge",
                "--no-ff",
                "--no-edit",
                "-m",
                f"Merge lane {lane_id} (human override; candidate {head})",
                head,
            )
            if result.returncode:
                _run_git(repository, "merge", "--abort")
                raise StateError(
                    f"lane: merge failed: {_one_line(result.stderr or result.stdout)}; "
                    "resolve conflicts in the lane worktree, commit, and obtain fresh review or a renewed explicit override"
                )
            merge_commit = _git_output(repository, "rev-parse", "HEAD")
        runtime["merge"] = {
            "reviewed_head": head,
            "merge_commit": merge_commit,
            "base": base,
            "branch": branch,
            "human_override": {
                "reason": reason,
                "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                "skipped_gates": sorted(waived),
            },
        }
        runtime["phase"] = "merged"
        if "human-gate" in waived and _lane_requires_human_gate(override_lane):
            gates = coordination.setdefault("human_gates", {})
            if not isinstance(gates, dict):
                raise StateError("state: human gates are malformed; repair the coordination data")
            prior_gate = gates.get(lane_id)
            gates[lane_id] = {"lane": lane_id, "state": "waived", "reason": reason,
                              "binding": {"commit": head}, "recorded_at": _utc_timestamp()}
            if prior_gate is not None:
                gates[lane_id]["previous_decision"] = prior_gate
        if "human-gate" not in waived:
            _consume_human_grant(coordination, lane_id)
        _write_coordination(state, coordination, plan)
        return path
    if lane.get("complete"):
        # A merged tick is the durable completion record, but it is not proof on its own: the
        # documented manual-merge recovery path lands the lane's ticks on the feature branch
        # without this command validating them. Establish the reviewed commit, its containment in
        # the feature branch, the lane's required reviews, and the lane's human gate before
        # treating the lane as merged.
        recorded_before = bool((runtime.get("merge") or {}).get("reviewed_head"))
        _validate_completed_lane_merge(state, lane_id, lane, runtime, coordination, state.get("plan_sha256"))
        if not recorded_before:
            # A recovered hand-merge must record its validation - and consume its one-use grant - for
            # the same reason the normal path writes before deriving: never leave one-use state idle.
            _write_coordination(state, coordination, plan)
        return path
    operation_lane = dict(lane)
    operation_lane.update(runtime)
    worktree, ref = _lane_runtime_for_operation(state, lane_id, operation_lane)
    runtime["worktree"] = str(worktree)
    runtime["ref"] = ref
    change_name = state.get("change")
    if not isinstance(change_name, str) or not change_name:
        raise StateError("lane: change is missing; re-derive the state")
    assigned = _lane_assigned_tasks(plan, lane_id)
    checked = _lane_checked_tasks(worktree, change_name, plan)
    missing = assigned - checked
    if missing:
        raise StateError(
            f"lane: lane {lane_id!r} has unfinished tasks ({', '.join(sorted(missing))}); "
            "tick every assigned task before merging"
        )
    repository = Path(state["feature_checkout"]).resolve()
    branch = _feature_branch(state)
    current_branch = _git_output(repository, "rev-parse", "--abbrev-ref", "HEAD")
    if current_branch != branch:
        raise StateError(f"lane: feature checkout is on {current_branch!r}, not {branch!r}; switch branches")
    if _worktree_branch(repository, worktree) != ref:
        raise StateError(f"lane: worktree is not checked out on ref {ref}; reconcile it first")
    if _run_git(worktree, "status", "--porcelain").stdout.strip():
        raise StateError("lane: lane worktree is dirty; commit before merging")
    head = _git_output(worktree, "rev-parse", "HEAD")
    ref_head = _ref_sha(repository, ref)
    if ref_head != head:
        raise StateError(f"lane: ref {ref} is at {ref_head or 'no commit'}, worktree HEAD is {head}; reconcile it first")
    if _run_git(repository, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise StateError("lane: feature checkout is dirty; commit or discard changes before merging")
    feature_head = _git_output(repository, "rev-parse", branch)
    base = _lane_merge_base(repository, ref, feature_head)
    _validate_resolved_lane_base(repository, base, head, feature_head)
    _validate_lane_reviews(
        lane_id, operation_lane, head,

    )
    _validate_human_gate(lane_id, operation_lane, head, coordination, state.get("plan_sha256"),
                         allow_deferred=True)
    candidate_lane_checked, _, _ = _candidate_tick_state(
        repository, change_name, plan, lane_id, base, head, feature_head
    )
    if _is_ancestor(repository, head, feature_head):
        merge_commit = feature_head
    else:
        result = _run_git(
            repository,
            "merge",
            "--no-ff",
            "--no-edit",
            "-m",
            f"Merge lane {lane_id} (reviewed {head})",
            head,
        )
        if result.returncode:
            _run_git(repository, "merge", "--abort")
            raise StateError(
                f"lane: merge failed: {_one_line(result.stderr or result.stdout)}; merge the feature branch "
                "into the lane worktree, resolve conflicts there, commit, obtain fresh current-HEAD reviews "
                "and human gates, then rerun lane-merge"
            )
        merge_commit = _git_output(repository, "rev-parse", "HEAD")
        if not _is_ancestor(repository, head, merge_commit):
            raise StateError("lane: merge did not preserve the reviewed commit; reconcile the feature branch")
    runtime["merge"] = {
        "reviewed_head": head,
        "merge_commit": merge_commit,
        "base": base,
        "branch": branch,
    }
    runtime["phase"] = "merged"
    # An authorization is consumed at most once per checkpoint. Record the consumption before the
    # projection refresh so a second lane cannot reuse the same grant.
    _consume_human_grant(coordination, lane_id)
    # Persist the merge record, the merged phase, and the consumed grant before any step that can
    # fail: an interrupted refresh must never leave a one-use grant active on disk. The projection
    # refresh does not mutate coordination, so no second write is needed after it.
    _write_coordination(state, coordination, plan)
    return path


def _finish_plan_matches(record: dict[str, Any], repository: Path) -> bool:
    return record.get("plan_sha256") == _finish_plan_digest(repository)


def _finish_inputs_current(repository: Path, item: dict[str, Any], head: str) -> bool:
    source, inputs = item.get("head"), item.get("inputs")
    if not source or not isinstance(inputs, list) or not inputs:
        return False
    return source == head or (_is_ancestor(repository, source, head) and not
        _git_output(repository, "diff", "--name-only", source, head, "--", *inputs))


def _finish_item_current(repository: Path, item: dict[str, Any], head: str,
                         preparation_hash: str, inspection: bool = False) -> bool:
    if item.get("binding_kind") == "inputs":
        return _finish_inputs_current(repository, item, head)
    # Inspections without explicit inputs retain exact-HEAD bindings.
    return item.get("preparation_sha256") == preparation_hash and (
        item.get("head") == head if inspection else _finish_inputs_current(repository, item, head))


def _finish_preparation(record: dict[str, Any], repository: Path, head: str) -> str:
    """Check the supplied release-preparation artifact and its committed inputs."""
    preparation = record.get("preparation")
    if not isinstance(preparation, dict):
        raise StateError("finish: release preparation artifact is missing; restage with --artifact and --input")
    artifact = _required_document(preparation.get("artifact"), "release preparation artifact")
    digest = _path_sha256(Path(artifact))
    if digest != preparation.get("sha256"):
        raise StateError("finish: release preparation artifact changed; restage and recapture")
    source = preparation["head"]
    if source != head and (not _is_ancestor(repository, source, head) or
        _git_output(repository, "diff", "--name-only", source, head, "--", *preparation["inputs"])):
        raise StateError("finish: release preparation inputs changed; restage and recapture")
    return digest


def _human_inspection_verdict(path: Path, checkpoint: str) -> str | None:
    """Read unique authoritative fields, never quoted/history/observation substrings."""
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"(inspector|checkpoint|verdict):\s*(.*?)\s*", line)
        if match:
            if match[1] in fields:
                return None
            fields[match[1]] = match[2]
    if (fields.get("inspector") == "human" and fields.get("checkpoint") == checkpoint
            and fields.get("verdict") in {"accept", "reject"}):
        return fields["verdict"]
    return None


def _human_inspection_accepts(path: Path, checkpoint: str) -> bool:
    return _human_inspection_verdict(path, checkpoint) == "accept"


def _one_check(check: object) -> str:
    values = check if isinstance(check, list) else [check] if check is not None else []
    if len(values) != 1 or not isinstance(values[0], str) or not values[0].strip():
        raise StateError("finish: this action requires exactly one --check id")
    return values[0].strip()


def finish_record(action: str, classification: str | None = None, version: str | None = None,
                  check: object = None, artifact: str | None = None,
                  inputs: list[str] | None = None, change: str | None = None,
                  inspections: list[str] | None = None) -> Path:
    """Bind repository-level release artifacts to the committed integrated feature branch."""
    repository = _workflow_checkout(Path.cwd())
    _require_coordinator_cwd({}, "finish-record")
    if _run_git(repository, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise StateError("finish: tracked changes are uncommitted; commit before final capture")
    head = _git_output(repository, "rev-parse", "HEAD")
    record = _load_finish_record(repository)
    if action in {"classify", "restage"}:
        _finish_integrated(repository, head)
        if action == "classify":
            if not classification or not classification.strip():
                raise StateError("finish: record the human release classification with --classification")
        elif not (isinstance(classification, str) and classification.strip()):
            classification = record.get("classification")
        if not isinstance(classification, str) or not classification.strip() or not artifact or not inputs:
            raise StateError("finish: record human classification, release preparation --artifact, and committed --input paths")
        preparation_path = _required_document(artifact, "release preparation artifact")
        if action == "classify" and (record.get("evidence") or record.get("required_checks")):
            raise StateError("finish: classification after final evidence; use restage and recapture")
        if action == "restage":
            if not record.get("classification"):
                raise StateError("finish: classify before restage")
            required = record.setdefault("required_checks", {})
            for selected, item in record.get("evidence", {}).items():
                required.setdefault(selected, item["inputs"])
            same_contract = (_finish_plan_matches(record, repository)
                             and classification.strip() == record.get("classification")
                             and version == record.get("expected_version"))
            preparation_hash = _path_sha256(Path(preparation_path))
            for collection in ("evidence", "inspection"):
                record[collection] = {
                    key: item for key, item in record.get(collection, {}).items()
                    if same_contract and _finish_item_current(
                        repository, item, head, preparation_hash, collection == "inspection")
                    and Path(item["artifact"]).is_file()
                    and _path_sha256(Path(item["artifact"])) == item.get("sha256")
                }
        declared_checks = [
            value.strip() for value in (
                check if isinstance(check, list) else [check] if check is not None else []
            )
            if isinstance(value, str) and value.strip()
        ]
        if not declared_checks and not record.get("declared_checks"):
            raise StateError("finish: declare every selected release check with --check at classify or restage")
        if declared_checks:
            record["declared_checks"] = sorted(set(declared_checks))
            existing = record.get("required_checks")
            existing = existing if isinstance(existing, dict) else {}
            record["required_checks"] = {
                selected: list(existing.get(selected, []))
                for selected in sorted(set(declared_checks))
            }
            record["evidence"] = {key: item for key, item in record.get("evidence", {}).items()
                                  if key in declared_checks}
        record.update({"plan_sha256": _finish_plan_digest(repository),
                       "plan_digest_kind": "structure",
                       "included_changes": _finish_changes(repository),
                       "classification": classification.strip(), "expected_version": version,
                       "classified_at": head,
                       "preparation": {"head": head, "artifact": preparation_path,
                                       "sha256": _path_sha256(Path(preparation_path)),
                                       "inputs": sorted(set(inputs))}})
        if inspections is not None:
            if any(not checkpoint.strip() for checkpoint in inspections):
                raise StateError("finish: inspection checkpoint names must be non-empty")
            record["required_inspections"] = sorted(set(inspections))
            record["inspection"] = {key: item for key, item in record.get("inspection", {}).items()
                                    if key in inspections}
    else:
        if not record.get("classification"):
            raise StateError("finish: human release classification must precede final capture")
        preparation_hash = _finish_preparation(record, repository, head)
        evidence = record.setdefault("evidence", {})
        if action in {"capture", "inspect", "pr-check"}:
            if not _finish_plan_matches(record, repository):
                raise StateError("finish: repository plan changed; restage and renew release validation")
            _finish_integrated(repository, head)
        if action == "capture":
            check = _one_check(check)
            if not artifact or not inputs:
                raise StateError("finish: capture requires check, artifact, and affected input paths")
            declared = record.get("declared_checks")
            if isinstance(declared, list) and check not in declared:
                raise StateError(
                    f"finish: check {check!r} was not declared at classify/restage; declare it first"
                )
            artifact_path = Path(_required_document(artifact, "release evidence artifact"))
            required = record.setdefault("required_checks", {})
            declared = set(inputs)
            if check in required and not set(required[check]).issubset(declared):
                raise StateError("finish: recapture must retain all selected check input dependencies")
            required[check] = sorted(declared)
            evidence[check] = {"binding_kind": "inputs", "head": head, "preparation_sha256": preparation_hash,
                               "artifact": str(artifact_path), "sha256": _path_sha256(artifact_path),
                               "inputs": sorted(declared)}
        elif action == "inspect":
            check = _one_check(check)
            if not artifact:
                raise StateError("finish: direct human inspection requires checkpoint and human record path")
            report_path = Path(_required_document(artifact, "human inspection artifact"))
            digest = _path_sha256(report_path)
            prior = record.get("inspection_inputs", {}).get(check, [])
            if prior and (not inputs or not set(prior).issubset(inputs)):
                raise StateError("finish: inspection must retain all selected input dependencies")
            verdict = _human_inspection_verdict(report_path, check)
            if verdict in {"accept", "reject"}:
                record.setdefault("inspection", {})[check] = {
                    "head": head, "artifact": str(report_path), "sha256": digest,
                    "preparation_sha256": preparation_hash, "verdict": verdict}
                if inputs:
                    record.setdefault("inspection_inputs", {})[check] = sorted(set(inputs))
                    record["inspection"][check].update({
                        "binding_kind": "inputs", "inputs": sorted(set(inputs))})
            if verdict == "reject":
                _write_finish_record(repository, record)
            if verdict != "accept":
                raise StateError("finish: human must personally record accept at this checkpoint; Oracle stand-in cannot inspect")
        elif action == "pr-check":
            declared = record.get("declared_checks")
            required = record.get("required_checks", {})
            if isinstance(declared, list) and set(declared) != set(evidence):
                raise StateError(
                    "finish: declared selected checks and captured evidence differ; capture every declared check"
                )
            if not required or set(required) != set(evidence):
                raise StateError("finish: selected checks missing final evidence; recapture before PR approval")
            if any(item.get("verdict") == "reject" for item in record.get("inspection", {}).values()):
                raise StateError("finish: rejected human inspection blocks PR approval")
            deferred = any(
                isinstance(gate, dict) and gate.get("state") == "deferred-to-integrated"
                for node in _repository_coordination(repository).get("changes", {}).values()
                if isinstance(node, dict)
                for gate in (node.get("human_gates") or {}).values()
            )
            checkpoints = record.get("required_inspections", [])
            if deferred and not checkpoints:
                raise StateError("finish: deferred UI requires declared human inspection checkpoints; restage with --inspection")
            for checkpoint in checkpoints:
                item = record.get("inspection", {}).get(checkpoint)
                if (not isinstance(item, dict) or not _finish_item_current(
                        repository, item, head, preparation_hash, inspection=True)):
                    raise StateError(f"finish: human inspection missing/stale: {checkpoint}")
                report_path = Path(item["artifact"])
                if _path_sha256(report_path) != item["sha256"] or not _human_inspection_accepts(report_path, checkpoint):
                    raise StateError(f"finish: changed/rejected human inspection: {checkpoint}")
            for selected, item in evidence.items():
                if _path_sha256(Path(item["artifact"])) != item["sha256"]:
                    raise StateError(f"finish: final evidence changed: {selected}")
                if not _finish_item_current(repository, item, head, preparation_hash):
                    raise StateError(f"finish: affected final evidence: {selected}")
        else:
            raise StateError("finish: unknown action")
    _write_finish_record(repository, record)
    return finish_record_path(repository)


def _check_local_links(paths: list[Path]) -> None:
    """Check local inline Markdown links without network access."""
    # shortcut: inline links only; extend this if the docs adopt reference-style links.
    for path in paths:
        text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
        for href in re.findall(r"\[[^\]\n]+\]\(([^\s)]+)\)", text):
            target = urlparse(href)
            if target.scheme or not target.path:
                continue
            if not (path.parent / target.path).exists():
                raise AssertionError(f"doc lint: broken local link {href!r} in {path}")


def _check_docs(root: Path) -> None:
    paths = [root / "SKILL.md", root / "README.md", *sorted((root / "references").glob("*.md")),
             *sorted((root / "assets").glob("*.md"))]
    _check_local_links(paths)
    command_text = (root / "references/helper-commands.md").read_text(encoding="utf-8")
    table_commands = set(re.findall(r"^\|\s*`([a-z][a-z-]+)", command_text, flags=re.MULTILINE))
    parser_commands = set(next(action for action in _parser()._actions
                         if isinstance(action, argparse._SubParsersAction)).choices)
    if table_commands != parser_commands:
        raise AssertionError(f"command reference differs from CLI: {table_commands ^ parser_commands}")
    for role in _CANONICAL_ROLES:
        path = root / "assets" / f"{role}.md"
        if f"role_id: {role}" not in path.read_text(encoding="utf-8").splitlines():
            raise AssertionError(f"role identity missing: {path}")


def _self_check() -> int:
    import unittest
    _check_docs(Path(__file__).resolve().parent.parent)
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parent / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def _add_change_option(command: argparse.ArgumentParser) -> None:
    command.add_argument("--change", dest="state_change", metavar="<change>")


def _selected_change(args: argparse.Namespace) -> str | None:
    return getattr(args, "state_change", None) or getattr(args, "global_change", None)


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        description="Validate local repo plan/coordination objects and committed OpenSpec task ticks. Record scoped Oracle reviews and lane/finish decisions. Use repository guides for test selection; review roles follow the lane risk and UI flags.",
        epilog=FIELD_TABLE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--self-check", action="store_true", help="run built-in checks and exit")
    parser.add_argument("--change", dest="global_change", metavar="<change>", help="select the change state")
    commands = parser.add_subparsers(dest="command")

    validate = commands.add_parser("plan-validate", help="run the four plan checks")
    validate.add_argument("plan", nargs="?", type=Path, default=Path(_REPO_PLAN))
    validate.add_argument("--workflow", action="store_true", help="also validate current-schema workflow records")
    validate.add_argument("--tasks", type=Path, metavar="<tasks.md>")
    _add_change_option(validate)

    reviewed = commands.add_parser("review", help="record an independent plan or lane verdict")
    reviewed.add_argument("lane", nargs="?")
    reviewed.add_argument("role", help="oracle for initial planning; assigned reviewer for a lane")
    reviewed.add_argument("verdict", choices=("PASS", "MORE WORK"))
    reviewed.add_argument("--scope", choices=("plan", "lane"), default="lane")
    reviewed.add_argument("--commit", required=True)
    reviewed.add_argument("--report", required=True)
    reviewed.add_argument("--evidence-revision")
    reviewed.add_argument("--evidence")
    _add_change_option(reviewed)

    approve = commands.add_parser(
        "plan-approve", help="record human approval of committed OpenSpec artifacts and the reviewed repo graph"
    )
    approve.add_argument("--approver", default="human", metavar="<approver>", help="approver label; defaults to human")
    approve.add_argument("--waiver", help="explicit human waiver and reason")
    approve.add_argument("--lane", action="append", help="waiver scope as change/lane")
    approve.add_argument("--all-lanes", action="store_true")

    send = commands.add_parser("dispatch", help="record a temporary worker dispatch intent")
    send.add_argument("lane", nargs="?", metavar="<lane>")
    send.add_argument("--scope", choices=("plan", "lane", "finish"), default="lane")
    send.add_argument("--worktree", type=Path, help="finish worker checkout; required for authors")
    send.add_argument("role", metavar="<role>")
    send.add_argument("--task", required=True, metavar="<task>")
    send.add_argument("--report", help="optional initial planning review report path")
    send.add_argument("--key", metavar="<key>", help="retry identity; new dispatches generate a unique key")
    send.add_argument(
        "--access", choices=("read-only", "write"),
        help="dispatch access; implementers default to write, reviewers and Oracle advice to read-only",
    )
    _add_change_option(send)

    sent = commands.add_parser("dispatch-done", help="record a temporary worker outcome")
    sent.add_argument("lane", nargs="?", metavar="<lane>")
    sent.add_argument("--scope", choices=("plan", "lane", "finish"), default="lane")
    sent.add_argument("role", metavar="<role>")
    sent.add_argument("--key", help="complete the dispatch with this unique key")
    sent.add_argument("--worker-run-id", metavar="<worker-run-id>")
    sent.add_argument("--tokens", type=int, metavar="<n>")
    sent.add_argument("--cost", metavar="<x>")
    sent.add_argument("--result", choices=("ok", "failed"), metavar="<ok|failed>")
    _add_change_option(sent)

    opened = commands.add_parser("lane-open", help="create or recover a lane worktree")
    opened.add_argument("lane", metavar="<lane>")
    _add_change_option(opened)

    closed = commands.add_parser("lane-close", help="remove a merged lane worktree")
    closed.add_argument("lane", metavar="<lane>")
    _add_change_option(closed)

    gate = commands.add_parser(
        "human-gate",
        help="record a temporary human gate (interactive, or an Oracle stand-in under a grant)",
        epilog=(
            "A grant is JSON with: id, granted_by ('human'), granted_at, scope (lane ids)\n"
            "and plan_sha256 (the approved plan digest). With --approved-by oracle the per-workstream\n"
            "checkpoint is satisfied by that grant plus a PASS Oracle stand-in whose report covers\n"
            "--checklist; the grant is then consumed. Merging into the target branch is never\n"
            "authorizable and is not performed by this tool."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    gate.add_argument("lane", metavar="<lane>")
    gate.add_argument(
        "--state", required=True, choices=("waiting", "approved", "rejected", "revoked", "deferred-to-integrated"), metavar="<state>"
    )
    gate.add_argument("--commit", required=True, metavar="<sha>")
    gate.add_argument("--evidence-revision", required=True, metavar="<rev>")
    gate.add_argument("--evidence", metavar="<path>")
    gate.add_argument(
        "--approved-by", choices=("human", "oracle"), default="human", metavar="<who>",
        help="human: interactive approval; oracle: human grant satisfied by an Oracle stand-in",
    )
    gate.add_argument("--authorization", metavar="<path>", help="human grant record (with --approved-by oracle)")
    gate.add_argument(
        "--stand-in-report", metavar="<path>", help="Oracle stand-in report (with --approved-by oracle)"
    )
    gate.add_argument(
        "--stand-in-verdict", choices=("PASS", "MORE WORK"), metavar="<verdict>",
        help="Oracle stand-in verdict; only PASS can satisfy the checkpoint",
    )
    gate.add_argument(
        "--checklist", metavar="<path>", help="human acceptance checklist (with --approved-by oracle)"
    )
    _add_change_option(gate)

    gate.add_argument("--rationale", help="bounded reversible impact for an explicit deferral")
    gate.add_argument("--risk-assessment", choices=("low", "high", "uncertain"))

    merged = commands.add_parser("lane-merge", help="merge a reviewed lane")
    merged.add_argument("lane", metavar="<lane>")
    merged.add_argument(
        "--human-override",
        action="store_true",
        help="merge on an explicit human decision, waiving the specified gates",
    )
    merged.add_argument(
        "--override-reason",
        default=None,
        metavar="<reason>",
        help="required with --human-override; the recorded human decision",
    )
    merged.add_argument("--override-head", help="candidate commit approved by the human")
    merged.add_argument("--waive", action="append", choices=("reviews", "tasks", "human-gate", "ancestry", "clean-trees", "plan-approval"),
                        help="gate waived by the human; repeat for multiple gates (default: reviews)")
    _add_change_option(merged)

    finish = commands.add_parser("finish-record", help="bind classification/version before final capture; restage after changes and recapture before PR")
    finish.add_argument("action", choices=("classify", "restage", "capture", "inspect", "pr-check"))
    finish.add_argument("--classification", help="human release classification from repository policy")
    finish.add_argument("--version", help="optional version label; repository tooling validates the version")
    finish.add_argument(
        "--check", action="append", dest="checks", metavar="<check>",
        help="selected check id; repeat on classify/restage to declare the selected set",
    )
    finish.add_argument("--artifact", help="gitignored receipt or manifest")
    finish.add_argument("--input", action="append", dest="inputs", help="preparation, check, or inspection input path (repeat)")
    finish.add_argument("--inspection", action="append", dest="inspections",
                        help="required human inspection checkpoint for classify/restage (repeat)")
    _add_change_option(finish)

    dashboard_command = commands.add_parser("dashboard", help="render or serve the live coordination dashboard")
    dashboard_command.add_argument("--output", type=Path, metavar="<path>")
    dashboard_command.add_argument("--serve", action="store_true", help="serve a live page that re-reads coordination.json")
    dashboard_command.add_argument("--port", type=int, default=8765)
    dashboard_command.add_argument("--refresh-seconds", type=int, default=2)
    _add_change_option(dashboard_command)

    current = commands.add_parser("status", help="show the current derived task and lane state")
    current.add_argument("--format", choices=("text", "md", "mermaid"), default="text")
    current.add_argument("--json", action="store_true", dest="as_json", help="emit machine-readable JSON")
    _add_change_option(current)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.self_check:
        return _self_check()
    change = _selected_change(args)
    try:
        if args.command == "plan-validate":
            validate_plan(args.plan, args.tasks, change)
            if args.workflow:
                validate_workflow(_workflow_checkout(Path.cwd()))
            print("plan-validate: PASS")
        elif args.command == "plan-approve":
            print(plan_approve(args.approver, args.waiver, args.lane, args.all_lanes))
        elif args.command == "review":
            if args.scope == "plan":
                if args.lane is not None or change is not None or args.role != "oracle":
                    raise StateError("plan: combined review takes role oracle, no lane or --change")
                if args.evidence or args.evidence_revision:
                    raise StateError("plan: evidence flags apply to lane reviews only")
                path = plan_review(args.verdict, args.commit, args.report)
            else:
                if args.lane is None:
                    raise StateError("review: lane scope requires a lane")
                path = lane_review(args.lane, args.role, args.verdict, args.commit, args.report,
                                   args.evidence_revision, args.evidence, change)
            print(path)
        elif args.command in {"dispatch", "dispatch-done"}:
            if (args.scope == "lane") != (args.lane is not None):
                raise StateError("dispatch: lane scope requires a lane; plan/finish scopes take only a role")
            if args.scope != "lane" and change is not None:
                raise StateError("dispatch: plan/finish scopes are repository-level; omit --change")
            if args.scope == "plan":
                if args.command == "dispatch":
                    if args.worktree or args.access not in {None, "read-only"}:
                        raise StateError("plan: initial review is read-only in the feature checkout")
                    path = plan_dispatch(args.role, args.task, args.key, args.report)
                else:
                    if args.role != "oracle":
                        raise StateError("plan: initial review uses oracle")
                    root = _workflow_checkout(Path.cwd())
                    _require_coordinator_cwd({}, "dispatch-done")
                    ledger, path = _load_plan_ledger(None, root)
                    _complete_dispatch(ledger, None, args.role, args.worker_run_id,
                                       args.tokens, args.cost, args.result, args.key)
                    _write_plan_ledger(None, root, ledger)
            elif args.command == "dispatch":
                if args.report:
                    raise StateError("dispatch: --report applies only to planning review")
                path = dispatch_run(args.lane, args.role, args.task, args.key, change,
                                    args.access, worktree=args.worktree)
            else:
                path = dispatch_done(args.lane, args.role, args.worker_run_id, args.tokens,
                                     args.cost, args.result, change, args.key)
            print(path)
        elif args.command == "lane-open":
            print(lane_open(args.lane, change))
        elif args.command == "lane-close":
            print(lane_close(args.lane, change))
        elif args.command == "human-gate":
            if args.state == "deferred-to-integrated":
                if args.approved_by != "human" or any((args.authorization, args.stand_in_report,
                                                       args.stand_in_verdict, args.checklist)):
                    raise StateError("human-gate: deferral requires an explicit human decision")
                path = human_defer(args.lane, args.commit, args.evidence_revision, args.rationale or "",
                                   change, args.risk_assessment or "uncertain", args.evidence)
            else:
                if args.rationale or args.risk_assessment:
                    raise StateError("human-gate: deferral flags require deferred-to-integrated state")
                stand_in = ({"verdict": args.stand_in_verdict, "report": args.stand_in_report}
                            if args.stand_in_report or args.stand_in_verdict else None)
                path = human_gate(args.lane, args.state, args.commit, args.evidence_revision,
                                  args.evidence, change, args.approved_by, args.authorization,
                                  stand_in, args.checklist)
            print(path)
        elif args.command == "lane-merge":
            print(lane_merge(args.lane, change, args.human_override, args.override_reason,
                             args.override_head, args.waive))
        elif args.command == "finish-record":
            if change is not None:
                raise StateError("finish: repository-level operation; omit --change")
            print(finish_record(args.action, args.classification, args.version, args.checks,
                                args.artifact, args.inputs, inspections=args.inspections))
        elif args.command == "dashboard":
            path = dashboard(change, args.output, args.serve, args.port, args.refresh_seconds)
            if path:
                print(path)
        elif args.command == "status":
            if args.as_json and args.format != "text":
                raise StateError("status: choose --json or --format")
            if args.format == "text":
                print(status(change, args.as_json), end="")
            else:
                _, _, plan = _state_context(change, coordination_optional=True)
                print((render_markdown if args.format == "md" else render_mermaid)(plan), end="")
        else:
            parser.print_help()
        return 0
    except (OSError, PlanError, StateError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Small plan validator and graph viewer for feature-development runs."""
from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import html
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from typing import Any


FIELD_TABLE = """Plan fields:
  schema_version  integer version (currently 1)
  change          change directory name
  review_mode     optional 'standard' (default) or 'combined-single-lane'
  lanes           lane id -> risk, ui, screen, sensitive (all four required), depends_on
  tasks           task id -> lane, deps

Validation checks: shape, task-id bijection, reference resolution, acyclicity."""

# WHATWG URL parsing discards leading C0 controls and spaces before resolving an authority.
_C0_OR_SPACE = "".join(chr(code) for code in range(0x21))


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


def _change_directory(plan_path: Path) -> str:
    directory = plan_path.resolve().parent
    return directory.parent.name if directory.name == "plan" else directory.name


def _check_shape(plan: dict[str, Any], plan_path: Path) -> None:
    expected_change = _change_directory(plan_path)
    if plan.get("change") != expected_change:
        raise PlanError(
            f"shape: change must be {expected_change!r} for this plan directory; change the plan's change field"
        )

    lanes = plan.get("lanes")
    tasks = plan.get("tasks")
    review_mode = plan.get("review_mode", "standard")
    if not isinstance(review_mode, str) or review_mode not in {"standard", "combined-single-lane"}:
        raise PlanError(
            "shape: review_mode must be 'standard' or 'combined-single-lane'; change the review mode"
        )
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
        # The three gate-driving flags are required, not optional. A missing flag would be
        # coerced to false in the derived lane views, silently dropping the UI review and the
        # human checkpoint the plan flags exist to require.
        for flag in ("ui", "screen", "sensitive"):
            if not isinstance(lane.get(flag), bool):
                raise PlanError(
                    f"shape: lane {lane_id!r} {flag} must be true or false; change the lane flag"
                )

    if review_mode == "combined-single-lane":
        eligible = [
            lane_id for lane_id, lane in lanes.items()
            if isinstance(lane, dict) and lane.get("risk") == "ordinary"
        ]
        if len(lanes) != 1 or len(eligible) != 1:
            raise PlanError(
                "shape: review_mode 'combined-single-lane' requires exactly one ordinary-risk lane; "
                "change the plan to one ordinary lane or use standard mode"
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
    check_graph(
        {lane_id: lane.get("depends_on", []) for lane_id, lane in plan["lanes"].items()},
        "lane",
    )


def validate_plan(plan_path: Path, tasks_path: Path | None = None) -> None:
    """Run the four plan checks, raising PlanError on the first failure."""
    plan = _load_json(plan_path)
    _check_shape(plan, plan_path)
    _check_task_ids(plan, tasks_path or plan_path.resolve().parent.parent / "tasks.md")
    _check_references(plan)
    _check_acyclic(plan)


def _list(value: object) -> str:
    if not value:
        return "none"
    return ", ".join(f"`{item}`" for item in value)  # type: ignore[union-attr]


def _bool(value: object) -> str:
    return str(bool(value)).lower()


def render_markdown(plan: dict[str, Any]) -> str:
    lines = [f"# Plan: `{plan.get('change', '')}`", "", "## Lanes", ""]
    tasks = plan.get("tasks", {})
    lanes = plan.get("lanes", {})
    for lane_id, lane in lanes.items():
        members = [task_id for task_id, task in tasks.items() if task.get("lane") == lane_id]
        lines.append(
            f"- `{lane_id}` — risk: `{lane.get('risk', '')}`; ui: `{_bool(lane.get('ui'))}`; "
            f"screen: `{_bool(lane.get('screen'))}`; depends_on: {_list(lane.get('depends_on', []))}"
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
    lines = ["flowchart TD"]
    lanes = plan.get("lanes", {})
    tasks = plan.get("tasks", {})
    for lane_id, lane in lanes.items():
        lane_node = _mermaid_id("lane", lane_id)
        dependencies = ", ".join(str(item) for item in lane.get("depends_on", [])) or "none"
        label = (
            f"{lane_id} — risk: {lane.get('risk', '')}; ui: {_bool(lane.get('ui'))}; "
            f"screen: {_bool(lane.get('screen'))}; depends_on: {dependencies}"
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
    root = (checkout or Path.cwd()).resolve()
    return workflow_dir(root) / _safe_change(change) / "coordination.json"


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


def _committed_plan_revision(checkout: Path, commit: str, relative: str) -> str:
    result = _state_git(checkout, "log", "-1", "--format=%H", commit, "--", relative)
    revision = result.stdout.strip()
    if result.returncode or not revision:
        detail = _one_line(result.stderr.strip() or result.stdout.strip())
        raise StateError(
            f"state: cannot resolve plan revision at {commit}"
            f"{(': ' + detail) if detail else ''}; commit the plan first"
        )
    return revision.splitlines()[0]


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
        lane_view["screen"] = bool(lane.get("screen"))
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


def state_derive(change: str, source_ref: str | None = None, checkout: Path | None = None) -> dict[str, Any]:
    change = _safe_change(change)
    repository = (checkout or Path.cwd()).resolve()
    _ensure_workflow_ignored(repository)
    resolved_ref, source_sha = _resolve_state_ref(repository, source_ref)
    plan_relative = f"openspec/changes/{change}/plan/graph-plan.json"
    tasks_relative = f"openspec/changes/{change}/tasks.md"
    try:
        plan_text = _committed_file(repository, source_sha, plan_relative)
        tasks_text = _committed_file(repository, source_sha, tasks_relative)
        plan_bytes = plan_text.encode("utf-8")
        plan = json.loads(plan_text)
    except UnicodeDecodeError as exc:
        raise PlanError(
            f"shape: committed artifacts at {source_sha} are not UTF-8: {_one_line(exc)}; fix the files"
        ) from exc
    except json.JSONDecodeError as exc:
        raise PlanError(
            f"shape: cannot load committed plan at {source_sha}: {_one_line(exc)}; fix the plan file"
        ) from exc
    if not isinstance(plan, dict):
        raise PlanError("shape: committed plan must be a JSON object; change it to an object")
    _validate_committed_plan(plan, change, tasks_text, repository)
    checked_order = _checked_task_ids_from_text(tasks_text)
    checked_tasks = set(checked_order)
    lanes, ready_lanes = _derived_lane_views(plan, checked_tasks)
    state = {
        "schema_version": 3,
        "change": change,
        "source_ref": resolved_ref,
        "source_sha": source_sha,
        "branch": _state_git(repository, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip() or "HEAD",
        "feature_checkout": str(repository),
        "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "plan_revision": _committed_plan_revision(repository, source_sha, plan_relative),
        "checked_tasks": [task_id for task_id in plan["tasks"] if task_id in checked_tasks],
        "tasks": _derived_task_views(plan, checked_tasks),
        "lanes": lanes,
        "ready_lanes": ready_lanes,
    }
    return state


_COORDINATION_FIELDS = (
    "phase", "worktree", "ref", "base", "dispatches", "reviews", "merge", "cleanup", "mediations",
    "decisions", "status", "status_reason", "task_states", "review_status", "escalation",
)

# Task states the coordinator store tracks per lane. "done" here is a lane-local claim; only a
# committed and merged tick makes it authoritative on the feature branch.
_TASK_STATES = ("pending", "in_progress", "done", "blocked")

# Review-duty decision vocabulary (design D5).
_DECISION_DUTIES = ("code", "test", "ui")
_DECISION_MODES = ("full", "focused", "skip")
# Design D6: the change that introduces the risk-based gates cannot use them to merge its
# own lanes. The archive/main activation has completed; this guard remains as a permanent
# self-hosting bar for the recorded change name.
_SELF_REVIEW_CHANGE = "optimize-testing-and-review-velocity"


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
        "mediations": [],
        "decisions": None,
        "status": None,
        "status_reason": None,
        "task_states": {},
        "review_status": None,
        "escalation": None,
    }
    for key, default in defaults.items():
        if key not in value:
            value[key] = json.loads(json.dumps(default))
    return {key: value[key] for key in _COORDINATION_FIELDS}


def _coordinator_lane_tasks(lane_id: str, lane: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    assigned = [
        task_id for task_id, task in plan.get("tasks", {}).items()
        if isinstance(task, dict) and task.get("lane") == lane_id
    ]
    return assigned or [str(task) for task in (lane.get("tasks") or [])]


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
        text = _committed_file(repository, head, f"openspec/changes/{change}/tasks.md")
        _check_task_ids_text(plan, text, f"openspec/changes/{change}/tasks.md at lane ref")
        return set(_checked_task_ids_from_text(text)), head
    except (OSError, PlanError, StateError, ValueError):
        return set(), None


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


def coordination_sync(change: str | None = None, checkout: Path | None = None) -> dict[str, Any]:
    """Refresh lane and task status in the coordinator store from lane refs and records.

    The coordinator runs this after every subagent update. Workers never write this file; they
    commit ticks. Reconciliation is monotonic for "done" (a task never regresses) and recomputed
    for the other states, so a stale in_progress or blocked entry cannot survive a real change.
    """
    _, state, plan = _state_context(change, checkout)
    _require_coordinator_cwd(state, "sync")
    coordination = _load_coordination(state, plan)
    _backfill_subagents(coordination)
    for lane_id, lane in coordination.get("lanes", {}).items():
        if not isinstance(lane, dict):
            continue
        done, head = _lane_ref_progress(state, lane, plan)
        assigned = _coordinator_lane_tasks(lane_id, lane, plan)
        previous = lane.get("task_states") if isinstance(lane.get("task_states"), dict) else {}
        open_dispatch = next(
            (row for row in lane.get("dispatches", [])
             if isinstance(row, dict) and row.get("result") in (None, "open")),
            None,
        )
        last_review = lane.get("reviews", [])[-1] if lane.get("reviews") else None
        review_blocked = (
            isinstance(last_review, dict) and last_review.get("verdict") == "MORE WORK"
        )
        tasks: dict[str, Any] = {}
        for task_id in assigned:
            prior = previous.get(task_id) if isinstance(previous.get(task_id), dict) else {}
            if task_id in done:
                tasks[task_id] = {"status": "done", "commit": head, "updated_at": prior.get("updated_at")}
                continue
            if prior.get("status") == "blocked":
                tasks[task_id] = prior
                continue
            if isinstance(open_dispatch, dict) and open_dispatch.get("task") == task_id:
                tasks[task_id] = {"status": "in_progress", "updated_at": prior.get("updated_at")}
                continue
            tasks[task_id] = {"status": "pending", "updated_at": prior.get("updated_at")}
        lane["task_states"] = tasks
        lane["review_status"] = _lane_review_status(lane)
        lane["escalation"] = _lane_escalation_status(lane)
        worktree_path = lane.get("worktree")
        lane["worktree_present"] = bool(
            isinstance(worktree_path, str) and Path(worktree_path).is_dir()
        )
        states = {row.get("status") for row in tasks.values()}
        if assigned and all(row.get("status") == "done" for row in tasks.values()):
            lane["status"], lane["status_reason"] = "done", None
        elif "blocked" in states:
            blocked = sorted(task_id for task_id, row in tasks.items() if row.get("status") == "blocked")
            lane["status"] = "blocked"
            lane["status_reason"] = "blocked tasks: " + ", ".join(blocked)
        elif review_blocked and isinstance(last_review, dict) and last_review.get("commit") == head:
            lane["status"], lane["status_reason"] = "blocked", f"review returned MORE WORK at {head[:12]}"
        elif "in_progress" in states or isinstance(open_dispatch, dict) or "done" in states:
            lane["status"], lane["status_reason"] = "in_progress", None
        else:
            lane["status"], lane["status_reason"] = "pending", None
    _write_coordination(state, coordination, plan)
    return {"change": state.get("change"), "lanes": {
        lane_id: {"status": lane.get("status"), "reason": lane.get("status_reason"),
                  "tasks": {task_id: row.get("status") for task_id, row in (lane.get("task_states") or {}).items()}}
        for lane_id, lane in coordination.get("lanes", {}).items() if isinstance(lane, dict)
    }}


def coordination_task_status(
    lane_id: str, task_id: str, status: str, reason: str | None = None,
    change: str | None = None, checkout: Path | None = None,
) -> Path:
    """Set one task's coordinator-side state, for cases a sync cannot infer (blocked with a reason)."""
    if status not in _TASK_STATES:
        raise StateError(f"task-status: status must be one of {', '.join(_TASK_STATES)}")
    _, state, plan = _state_context(change, checkout)
    _require_coordinator_cwd(state, "task-status")
    coordination = _load_coordination(state, plan)
    lane = coordination.get("lanes", {}).get(lane_id)
    if not isinstance(lane, dict):
        raise StateError(f"lane: lane {lane_id!r} is not in the committed plan; change it")
    if task_id not in _coordinator_lane_tasks(lane_id, lane, plan):
        raise StateError(f"lane: task {task_id!r} is not assigned to lane {lane_id!r}; change it")
    tasks = lane.get("task_states") if isinstance(lane.get("task_states"), dict) else {}
    row: dict[str, Any] = {"status": status, "updated_at": _now_iso()}
    if reason:
        row["reason"] = reason
    tasks[task_id] = row
    lane["task_states"] = tasks
    if status == "blocked":
        lane["status"] = "blocked"
        lane["status_reason"] = reason or f"task {task_id} blocked"
    _write_coordination(state, coordination, plan)
    path = _coordination_path(str(state.get("change")), Path(str(state.get("feature_checkout"))))
    return path


def _backfill_subagents(coordination: dict[str, Any]) -> None:
    """Seed the subagent list from recorded lane dispatches (older runs, recovered stores)."""
    rows = coordination.get("subagents")
    if not isinstance(rows, list):
        rows = []
    known = {
        (row.get("lane"), row.get("role"), row.get("task"), row.get("started_at"))
        for row in rows if isinstance(row, dict)
    }
    for lane_id, lane in (coordination.get("lanes") or {}).items():
        if not isinstance(lane, dict):
            continue
        for row in lane.get("dispatches", []):
            if not isinstance(row, dict):
                continue
            identity = (lane_id, row.get("role"), row.get("task"), row.get("started_at"))
            if identity in known:
                continue
            result = row.get("result")
            rows.append({
                "run_id": row.get("worker_run_id"),
                "key": row.get("key"),
                "lane": lane_id,
                "role": row.get("role"),
                "task": row.get("task"),
                "model": row.get("model"),
                "level": row.get("level"),
                "access": row.get("access"),
                "worktree": lane.get("worktree"),
                "status": "running" if result in (None, "open") else ("done" if result == "ok" else "failed"),
                "result": result,
                "started_at": row.get("started_at"),
                "updated_at": _now_iso(),
            })
            known.add(identity)
    coordination["subagents"] = rows


def _register_subagent(
    coordination: dict[str, Any], lane_id: str, role: str, model: str, level: str,
    task: str | None, key: str | None, access: str | None, worktree: str | None,
) -> None:
    rows = coordination.get("subagents")
    if not isinstance(rows, list):
        rows = []
    rows.append({
        "run_id": None, "key": key, "lane": lane_id, "role": role, "task": task,
        "model": model, "level": level, "access": access, "worktree": worktree,
        "status": "running", "started_at": _now_iso(), "updated_at": _now_iso(),
    })
    coordination["subagents"] = rows


def _close_subagent(
    coordination: dict[str, Any], lane_id: str, role: str, run_id: str | None, result: str | None,
) -> None:
    rows = coordination.get("subagents")
    if not isinstance(rows, list):
        return
    candidates = [
        row for row in rows
        if isinstance(row, dict) and row.get("lane") == lane_id and row.get("role") == role
        and row.get("status") == "running"
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
        "commit": last.get("commit"),
        "report": last.get("report"),
    }


def _lane_escalation_status(lane: dict[str, Any]) -> dict[str, Any] | None:
    reviews = [row for row in lane.get("reviews", []) if isinstance(row, dict)]
    more_work = [row for row in reviews if row.get("verdict") == "MORE WORK"]
    mediated = [row for row in lane.get("mediations", []) if isinstance(row, dict)]
    if not more_work:
        return None
    required = reviews[-1].get("verdict") == "MORE WORK" and len(more_work) >= 2 and not mediated
    decision = mediated[-1].get("decision") if mediated else None
    return {
        "required": required,
        "rounds": len(reviews),
        "more_work_rounds": len(more_work),
        "state": "awaiting-human" if required else ("mediated" if decision else "none"),
        "decision": decision,
        "options": ["revise", "approve"] if required else [],
    }


def _review_mode(plan: dict[str, Any]) -> str:
    """Return the committed plan's review mode, preserving old plans as standard."""
    value = plan.get("review_mode", "standard")
    return value if isinstance(value, str) else "standard"


def _validate_decision_record(
    lane: dict[str, Any], decision: object, oracle_authored: bool = False
) -> None:
    """Validate decision-record shape and the mechanical risk invariants (design D5)."""
    if not isinstance(decision, dict):
        raise StateError("lane: review decision record is malformed; record it with review-decision")
    duties = decision.get("duties")
    if not isinstance(duties, dict) or set(duties) != set(_DECISION_DUTIES):
        raise StateError(
            "lane: review decision must record all three duties (code, test, ui); record it with review-decision"
        )
    for duty, entry in duties.items():
        if not isinstance(entry, dict) or entry.get("mode") not in _DECISION_MODES:
            raise StateError(f"lane: {duty} duty mode must be full, focused, or skip")
        if not isinstance(entry.get("risk_factors"), list) or any(
            not isinstance(item, str) for item in entry.get("risk_factors", [])
        ):
            raise StateError(f"lane: {duty} duty risk_factors must be a list of strings")
        if not isinstance(entry.get("acceptance_coverage"), str) or not entry["acceptance_coverage"].strip():
            raise StateError(f"lane: {duty} duty must name its acceptance coverage")
        if not isinstance(entry.get("checks"), list) or any(not isinstance(item, str) for item in entry.get("checks", [])):
            raise StateError(f"lane: {duty} duty checks must be a list of strings")
        rationale_value = entry.get("rationale") if isinstance(entry, dict) else None
        if entry["mode"] == "skip" and (not isinstance(rationale_value, str) or not rationale_value.strip()):
            raise StateError(f"lane: skipping the {duty} duty requires a recorded rationale")
    if not (lane.get("ui") or lane.get("screen")) and duties["ui"]["mode"] != "skip":
        raise StateError(
            "lane: a lane without the ui/screen plan flags has no UI review duty; record --ui skip"
        )
    if lane.get("risk") == "high":
        skipped = [duty for duty in ("code", "test") if duties[duty]["mode"] == "skip"]
        if skipped:
            raise StateError(
                "lane: high-risk changes require independent review; "
                f"refusing to skip the {', '.join(skipped)} review dut{'y' if len(skipped) == 1 else 'ies'}"
            )
    if oracle_authored:
        # Task 5.4 / workflow correction rule: Oracle-authored write work receives an
        # independent review that did not author it, regardless of the selected duties.
        # The applicable independent duties are the ones the lane requires anyway.
        applicable = ["code", "test"] if lane.get("risk") == "high" else ["code"]
        skipped = [duty for duty in applicable if duties[duty]["mode"] == "skip"]
        if skipped:
            raise StateError(
                "lane: Oracle-authored write work requires an independent review; refusing to skip "
                f"the {', '.join(skipped)} review dut{'y' if len(skipped) == 1 else 'ies'} for authored changes; "
                "record a decision that keeps them and obtain a current independent reviewer PASS"
            )
    digest = decision.get("recorded_sha256")
    expected = hashlib.sha256(
        json.dumps(decision.get("duties"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    if digest != expected:
        raise StateError(
            "lane: review decision record changed after it was recorded; have the Coordinator record a fresh decision"
        )


def _decision_skipped_roles(roles: list[str], decisions: object) -> list[str]:
    """Return roles whose recorded decision selects 'skip' (never enforced under legacy policy)."""
    if not isinstance(decisions, dict):
        return []
    duties = decisions.get("duties")
    if not isinstance(duties, dict):
        return []
    return [
        role for role in roles
        if isinstance(duties.get(role), dict) and duties[role].get("mode") == "skip"
    ]


def _decisions_from_merge(merge: object) -> dict[str, Any] | None:
    """Rebuild the decisions view honored at merge time from the persisted merge record."""
    if not isinstance(merge, dict):
        return None
    duties = merge.get("review_duties")
    if not isinstance(duties, dict):
        return None
    return {
        "duties": {
            duty: {"mode": mode}
            for duty, mode in duties.items()
            if isinstance(mode, str)
        }
    }


def review_decision(
    lane_id: str,
    duties: dict[str, str],
    risk_factors: list[str] | None,
    acceptance_coverage: object,
    checks: list[str] | None,
    rationale: object,
    opt_in: bool,
    change: str | None = None,
) -> Path:
    """Record the Coordinator's per-lane review-duty decision for code/test/UI.

    Without --opt-in the record is shadow-only: it is persisted and displayed, but the
    standard review gates stay authoritative (design D6). With --opt-in the coordination
    review policy becomes 'risk-based' and lane-merge honors the selected duties.
    """
    path, state, plan = _state_context(change)
    lane = _state_lane(state, lane_id)
    _reject_finished_lane(lane_id, lane)
    if _review_mode(plan) == "combined-single-lane":
        raise StateError(
            "lane: combined review keeps the exact Oracle binding; decision records are not honored in combined mode"
        )
    if opt_in and state.get("change") == _SELF_REVIEW_CHANGE:
        raise StateError(
            "lane: design D6 forbids the archived self-review change from relaxing its own review gates; "
            "--opt-in is refused for its own lanes"
        )
    coordination, runtime = _coordination_lane_for(state, plan, lane_id)
    if not isinstance(rationale, str) or not rationale.strip():
        raise StateError("lane: a review decision requires --rationale naming the recorded reasoning")
    if not isinstance(acceptance_coverage, str) or not acceptance_coverage.strip():
        raise StateError("lane: a review decision requires --acceptance-coverage naming the covered criteria")
    duty_modes: dict[str, str] = {}
    for duty in _DECISION_DUTIES:
        mode = duties.get(duty)
        if not isinstance(mode, str) or mode not in _DECISION_MODES:
            raise StateError(f"lane: {duty} duty mode must be full, focused, or skip; record it with review-decision")
        duty_modes[duty] = mode
    decision: dict[str, Any] = {
        "duties": {
            duty: {
                "mode": mode,
                "risk_factors": [str(item) for item in (risk_factors or [])],
                "acceptance_coverage": acceptance_coverage.strip(),
                "checks": [str(item) for item in (checks or [])],
                "rationale": rationale.strip(),
            }
            for duty, mode in duty_modes.items()
        },
        "recorded_at": _utc_timestamp(),
    }
    decision["recorded_sha256"] = hashlib.sha256(
        json.dumps(decision["duties"], sort_keys=True).encode("utf-8")
    ).hexdigest()
    _validate_decision_record(lane, decision, oracle_authored=_lane_oracle_write_authored(runtime))
    if opt_in:
        coordination["review_policy"] = "risk-based"
    runtime["decisions"] = decision
    _write_coordination(state, coordination, plan)
    return path


def review_reuse(
    lane_id: str,
    role: str,
    ancestor: object,
    intervening_diff: object,
    assessment: object,
    evidence_inputs_changed: bool,
    change: str | None = None,
) -> Path:
    """Record assessed reuse of an ordinary agent PASS recorded at an ancestor commit.

    Design D5: an ancestor verdict can satisfy a later HEAD only when the Coordinator
    records the intervening diff, whether evidence inputs changed, and an assessment.
    Affected/uncertain inputs require fresh re-review instead. Human authorization and
    combined-Oracle approvals stay exact-bound: reuse never applies to them.
    """
    path, state, plan = _state_context(change)
    lane = _state_lane(state, lane_id)
    _reject_finished_lane(lane_id, lane)
    coordination, runtime = _coordination_lane_for(state, plan, lane_id)
    if _review_mode(plan) == "combined-single-lane":
        raise StateError(
            "lane: combined review approvals stay exact-bound; ancestor reuse is not honored in combined mode"
        )
    if state.get("change") == _SELF_REVIEW_CHANGE or coordination.get("review_policy") != "risk-based":
        # Design D5/D6: assessed reuse is a risk-policy relaxation. It requires the active
        # risk-based policy, and this change can never grant it to its own lanes.
        raise StateError(
            "lane: ancestor reuse requires the risk-based review policy; record the Coordinator "
            "decision with review-decision --opt-in first"
        )
    operation_lane = dict(lane)
    operation_lane.update(runtime)
    if role not in _effective_review_roles(operation_lane, runtime.get("decisions")):
        raise StateError(f"lane: reviewer role {role!r} is not required for lane {lane_id!r}; use the plan flags")
    if _lane_oracle_write_authored(runtime):
        raise StateError(
            "lane: Oracle-authored write work requires a direct independent current-HEAD review; "
            "ancestor reuse cannot cover authored corrections"
        )
    if not isinstance(assessment, str) or not assessment.strip():
        raise StateError("lane: ancestor reuse requires --assessment naming what the intervening change does not touch")
    if evidence_inputs_changed:
        raise StateError(
            "lane: evidence inputs changed since the ancestor review; affected or uncertain changes "
            "require a fresh re-review, not ancestor reuse"
        )
    worktree, recorded_ref = _lane_runtime_for_operation(state, lane_id, operation_lane)
    runtime["worktree"] = str(worktree)
    runtime["ref"] = recorded_ref
    if _run_git(worktree, "status", "--porcelain").stdout.strip():
        raise StateError(f"lane: lane {lane_id!r} worktree is dirty; commit before recording ancestor reuse")
    actual_head = _git_output(worktree, "rev-parse", "HEAD")
    ancestor_commit = _git_commit(worktree, ancestor)
    if ancestor_commit == actual_head:
        raise StateError(
            "lane: ancestor reuse must cite a strict ancestor; record a fresh lane-review for the current HEAD"
        )
    if not _is_ancestor(worktree, ancestor_commit, actual_head):
        raise StateError(
            f"lane: reuse source {ancestor_commit} is not an ancestor of lane HEAD {actual_head}; "
            "a non-ancestor verdict is invalid and cannot be reused"
        )
    # The binding is the real repository diff, not caller-supplied text: the helper computes
    # the canonical ancestor..HEAD diff itself and refuses anything else (stale, unrelated,
    # fabricated, or empty) so the recorded PASS can never rest on unverified input.
    real_diff = _run_git(worktree, "diff", f"{ancestor_commit}..{actual_head}", "--").stdout
    if not real_diff.strip():
        raise StateError(
            "lane: the actual git diff between the ancestor and lane HEAD is empty; ancestor reuse "
            "cannot bind an empty intervening diff; record a fresh lane-review instead"
        )
    real_diff_sha256 = hashlib.sha256(real_diff.encode("utf-8")).hexdigest()
    diff_path = _required_document(intervening_diff, "intervening diff record")
    if _path_sha256(Path(diff_path)) != real_diff_sha256:
        raise StateError(
            f"lane: the supplied intervening diff does not match the actual git diff "
            f"{ancestor_commit}..{actual_head}; an unrelated, stale, or fabricated diff cannot "
            "satisfy ancestor reuse"
        )
    source = next(
        (
            item for item in runtime.get("reviews") or []
            if isinstance(item, dict)
            and item.get("role") == role
            and item.get("verdict") == "PASS"
            and item.get("reused") is not True
            and item.get("combined") is not True
            and _review_commit(item) == ancestor_commit
        ),
        None,
    )
    if not isinstance(source, dict):
        raise StateError(
            f"lane: no completed ordinary {role!r} PASS is recorded at {ancestor_commit}; "
            "ancestor reuse can only carry a directly recorded PASS"
        )
    evidence_path = source.get("binding", {}).get("evidence_path")
    source_digest = source.get("binding", {}).get("evidence_sha256")
    if evidence_path is not None:
        evidence_path = _required_document(evidence_path, "ancestor review evidence")
        if not source_digest or _path_sha256(Path(evidence_path)) != source_digest:
            raise StateError("lane: ancestor review evidence changed since direct review; re-review the lane")
    diff_path = _required_document(intervening_diff, "intervening diff record")
    binding = {
        "commit": actual_head,
        "evidence_revision": source.get("binding", {}).get("evidence_revision") or "reused",
        "evidence_path": evidence_path,
        "evidence_sha256": source_digest if evidence_path is not None else None,
        "ancestor_commit": ancestor_commit,
        "intervening_diff": diff_path,
        "intervening_diff_sha256": real_diff_sha256,
        "evidence_inputs_changed": False,
    }
    record = {
        "role": role,
        "verdict": "PASS",
        "reused": True,
        "reused_from": ancestor_commit,
        "assessment": assessment.strip(),
        "binding": binding,
        "report": source.get("report"),
    }
    reviews = runtime.setdefault("reviews", [])
    if not isinstance(reviews, list):
        raise StateError("state: reviews are malformed; repair coordination data")
    _validate_reuse_record(role, record, actual_head, worktree, True, operation_lane)
    reviews.append(record)
    _write_coordination(state, coordination, plan)
    return path


def _new_coordination(state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "change": state["change"],
        "feature_checkout": state["feature_checkout"],
        "branch": state.get("branch") or "HEAD",
        "review_mode": _review_mode(plan),
        "subagents": [],
        "lanes": {
            lane_id: _coordination_lane()
            for lane_id in plan.get("lanes", {})
        },
        "dispatches": [],
        "reviews": [],
        "worktrees": [],
        "model_selection": None,
        "authorizations": {},
        "human_gates": {},
        # The standard gates stay authoritative by default (design D6).
        # 'risk-based' is an explicit Coordinator opt-in recorded here.
        "review_policy": "legacy",
        "finish": {},
    }


def _load_coordination(state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    change = state.get("change")
    checkout = state.get("feature_checkout")
    if not isinstance(change, str) or not isinstance(checkout, str):
        raise StateError("state: coordination context is missing; re-derive the state")
    path = _coordination_path(change, Path(checkout))
    if not path.exists():
        result = _new_coordination(state, plan)
        result["_available"] = False
        result["_error"] = "coordination data is missing"
        return result
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"state: cannot load coordination data {path}: {_one_line(exc)}; repair or remove it") from exc
    if not isinstance(value, dict):
        raise StateError("state: coordination data must be an object; repair or remove it")
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
    for key in ("dispatches", "reviews", "worktrees", "model_selection", "authorizations", "human_gates", "review_policy", "finish", "subagents"):
        if key in value:
            result[key] = value[key]
    for lane_id in plan.get("lanes", {}):
        if lane_id in lanes:
            if not isinstance(lanes[lane_id], dict):
                raise StateError(f"state: coordination lane {lane_id!r} is malformed; repair it")
            result["lanes"][lane_id] = _coordination_lane(lanes[lane_id])
    # A committed plan revision may switch modes. Old combined records never satisfy the
    # standard path, and old standard records never satisfy combined mode; discard only the
    # incompatible combined lane records while preserving other coordination for recovery.
    # Apply this after persisted lanes have been copied into the result; filtering the fresh
    # lanes created above would leave stale combined rounds in place.
    if value.get("review_mode") not in {None, _review_mode(plan)}:
        for lane in result.get("lanes", {}).values():
            if isinstance(lane, dict):
                reviews = lane.get("reviews")
                if isinstance(reviews, list):
                    lane["reviews"] = [
                        item for item in reviews
                        if isinstance(item, dict) and item.get("combined") is not True
                    ]
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
    view["review_policy"] = coordination.get("review_policy", "legacy")
    gates = coordination.get("human_gates")
    view["human_gates"] = gates if isinstance(gates, dict) else {}
    # The summary validates a gate through the merge validator, which needs the live grants too.
    authorizations = coordination.get("authorizations")
    view["authorizations"] = authorizations if isinstance(authorizations, dict) else {}
    view["review_mode"] = coordination.get("review_mode", _review_mode(plan))
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
    persisted = {key: value for key, value in coordination.items() if not key.startswith("_")}
    _atomic_write_json(path, persisted)
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
        current_plan = plan or json.loads(
            _committed_file(checkout, str(derived["source_sha"]),
                            f"openspec/changes/{change}/plan/graph-plan.json")
        )
        render_dashboard(
            workflow_dir(checkout) / change / "dashboard.html",
            _overlay_coordination(derived, coordination, current_plan), current_plan,
        )
    except (OSError, StateError, PlanError, json.JSONDecodeError):
        pass


def _validate_committed_plan(
    plan: dict[str, Any], change: str, tasks_text: str, checkout: Path
) -> None:
    plan_path = checkout / "openspec" / "changes" / change / "plan" / "graph-plan.json"
    _check_shape(plan, plan_path)
    _check_task_ids_text(plan, tasks_text, f"openspec/changes/{change}/tasks.md")
    _check_references(plan)
    _check_acyclic(plan)


def _guard_derived_state_target(path: Path) -> None:
    if not path.exists():
        return
    current = _read_state(path)
    if current.get("run_id"):
        raise StateError(
            f"state: {path} holds legacy run state; move it aside before deriving change state"
        )


def _active_changes(checkout: Path) -> list[str]:
    changes_root = checkout / "openspec" / "changes"
    if not changes_root.is_dir():
        return []
    return sorted(
        path.parent.name
        for path in changes_root.glob("*/plan/graph-plan.json")
        if path.is_file()
    )


def _infer_change(checkout: Path) -> str:
    changes = _active_changes(checkout)
    if len(changes) == 1:
        return changes[0]
    if not changes:
        raise StateError("state: no active OpenSpec change found; provide <change> to state-derive")
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
    state = state_derive(selected, None, root)
    if state.get("run_id"):
        raise StateError("state: legacy run state is not a source of truth; derive the change state")
    checkout_value = state.get("feature_checkout")
    if not isinstance(checkout_value, str) or not checkout_value:
        raise StateError("state: feature_checkout is missing; re-derive the state")
    repository = Path(checkout_value).expanduser().resolve()
    state_change = state.get("change")
    if not isinstance(state_change, str) or not state_change:
        raise StateError("state: change is missing; re-derive the state")
    plan_relative = f"openspec/changes/{state_change}/plan/graph-plan.json"
    source_sha = state.get("source_sha")
    if not isinstance(source_sha, str) or not source_sha:
        raise StateError("state: source SHA is missing; re-derive the state")
    try:
        plan = json.loads(_committed_file(repository, source_sha, plan_relative))
        tasks_text = _committed_file(repository, source_sha, f"openspec/changes/{state_change}/tasks.md")
    except json.JSONDecodeError as exc:
        raise PlanError(f"shape: cannot load committed plan at {source_sha}: {_one_line(exc)}; fix the plan") from exc
    if not isinstance(plan, dict):
        raise PlanError("shape: committed plan must be a JSON object; change it to an object")
    _validate_committed_plan(plan, state_change, tasks_text, repository)
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


def _write_lane_tasks(path: Path, text: str) -> None:
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise StateError(f"lane: cannot tick {path}: {_one_line(exc)}; check the lane worktree") from exc
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def task_tick(lane_id: str, task_id: str, change: str | None = None) -> Path:
    """Check one plan-assigned top-level task in the lane worktree."""
    _, state, plan = _state_context(change)
    lane = _state_lane(state, lane_id)
    _reject_finished_lane(lane_id, lane)
    if lane.get("complete"):
        raise StateError(f"lane: lane {lane_id!r} is already complete from committed ticks")
    if not isinstance(task_id, str) or not task_id or "\n" in task_id or "\r" in task_id:
        raise StateError("lane: task id must be a non-empty top-level task identifier; change <task>")
    task = plan.get("tasks", {}).get(task_id)
    if not isinstance(task, dict):
        raise StateError(
            f"lane: task {task_id!r} is not a top-level task in the committed plan; "
            "use a plan task identifier (nested checkboxes are annotations)"
        )
    if task.get("lane") != lane_id:
        raise StateError(
            f"lane: task {task_id!r} belongs to lane {task.get('lane')!r}, not {lane_id!r}; "
            "tick it from its planned lane"
        )
    repository = Path(state["feature_checkout"]).expanduser().resolve()
    worktree_value = lane.get("worktree")
    recorded_ref = lane.get("ref")
    worktree: Path | None = None
    expected_path = _lane_worktree_path(state, lane_id).resolve()
    expected_ref = _lane_ref(state, lane_id)
    if isinstance(worktree_value, str) and worktree_value and isinstance(recorded_ref, str) and recorded_ref:
        worktree = Path(worktree_value).expanduser().resolve()
        if worktree != expected_path or recorded_ref != expected_ref:
            raise StateError(f"lane: lane {lane_id!r} worktree or ref is not deterministic; reconcile it first")
        if _worktree_branch(repository, worktree) != recorded_ref:
            raise StateError(f"lane: worktree is not checked out on ref {recorded_ref}; reconcile it first")
    else:
        # A lost coordination file is recoverable from the deterministic worktree/ref. A
        # worker invoked from its own lane checkout may also tick directly without a run record.
        if _worktree_branch(repository, expected_path) == expected_ref:
            worktree, recorded_ref = expected_path, expected_ref
        else:
            current_branch = _run_git(repository, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
            if current_branch.endswith(f"-{lane_id}") and current_branch.startswith("featdev/"):
                worktree, recorded_ref = repository, current_branch
            else:
                raise StateError(f"lane: lane {lane_id!r} has no open worktree; open it before ticking")
    assert worktree is not None and recorded_ref is not None
    change_name = state.get("change")
    if not isinstance(change_name, str) or not change_name:
        raise StateError("lane: change is missing; re-derive the state")
    tasks_path = worktree / "openspec" / "changes" / change_name / "tasks.md"
    dirty = _run_git(worktree, "status", "--porcelain", "--untracked-files=all")
    if dirty.returncode:
        raise StateError(f"lane: cannot inspect lane worktree: {_one_line(dirty.stderr or dirty.stdout)}; repair it")
    relative_tasks = str(tasks_path.relative_to(worktree))
    dirty_paths = [line[3:] for line in dirty.stdout.splitlines() if len(line) >= 4]
    if any(path != relative_tasks for path in dirty_paths):
        raise StateError("lane: worktree is dirty; commit code and discard unrelated changes before ticking")
    try:
        text = tasks_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StateError(f"lane: cannot load {tasks_path}: {_one_line(exc)}; provide the committed tasks.md") from exc
    _check_task_ids_text(plan, text, f"openspec/changes/{change_name}/tasks.md in lane worktree")
    lines = text.splitlines(keepends=True)
    matches: list[tuple[int, re.Match[str]]] = []
    entry = re.compile(r"^-\s*\[([ xX])\]\s+(\S+)(?=\s|$)")
    for index, line in enumerate(lines):
        match = entry.match(line)
        if match and match.group(2) == task_id:
            matches.append((index, match))
    if not matches:
        raise StateError(
            f"lane: task {task_id!r} is not a top-level entry in {tasks_path}; "
            "nested checkboxes are annotations, not tasks"
        )
    if len(matches) != 1:
        raise StateError(f"lane: task {task_id!r} appears more than once at the top level; repair tasks.md")
    index, match = matches[0]
    if match.group(1).lower() == "x":
        raise StateError(f"lane: task {task_id!r} is already ticked; do not rewrite its completion record")
    line = lines[index]
    lines[index] = line[:match.start(1)] + "x" + line[match.end(1):]
    _write_lane_tasks(tasks_path, "".join(lines))
    return tasks_path


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
    if not isinstance(role, str) or not role.strip():
        raise StateError("state: dispatch role must be non-empty; change it")
    if access is not None and access not in {"read-only", "write"}:
        raise StateError("state: dispatch access must be read-only or write; change --access")
    if access == "write" and role not in {"implementer", "worker", "oracle"}:
        raise StateError(
            f"state: dispatch role {role!r} cannot receive write access; reviewers and advice are read-only"
        )
    if access is not None:
        return access
    return "write" if role in {"implementer", "worker"} else "read-only"


def _dispatch_intent(
    state: dict[str, Any],
    lane_id: str,
    role: str,
    model: str,
    level: str,
    task: str,
    key: str | None = None,
    lane_head: str | None = None,
    access: str | None = None,
    acceptance: object = None,
    evidence: object = None,
) -> str:
    lane = _state_lane(state, lane_id)
    _reject_finished_lane(lane_id, lane)
    if lane.get("complete"):
        raise StateError(f"lane: lane {lane_id!r} is already complete from committed ticks")
    change = state.get("change") or "change"
    dispatch_key = key or f"{change}/{lane_id}/{role}"
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
        "model": model,
        "level": level,
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
            for field in ("role", "model", "level", "task", "key")
        ) or (
            record.get("access") is not None and record.get("access") != resolved_access
        ) or any(
            record.get(field) != intent.get(field)
            for field in ("acceptance_path", "acceptance_sha256", "evidence_path", "evidence_sha256")
        ) or (
            (acceptance_path is not None or evidence_path is not None)
            and record.get("lane_head") != lane_head
        ):
            raise StateError(
                f"state: dispatch key {dispatch_key!r} has a different intent or receipt; change --key"
            )
        return dispatch_key
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


def _coordination_lane_for(
    state: dict[str, Any], plan: dict[str, Any], lane_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    coordination = _load_coordination(state, plan)
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
    lane_id: str,
    role: str,
    model: str,
    level: str,
    task: str,
    key: str | None = None,
    change: str | None = None,
    access: str | None = None,
    acceptance: object = None,
    evidence: object = None,
) -> Path:
    path, state, plan = _state_context(change)
    coordination, runtime = _coordination_lane_for(state, plan, lane_id)
    merged_view = dict(state["lanes"][lane_id])
    merged_view.update(runtime)
    worktree, recorded_ref = _lane_runtime_for_operation(state, lane_id, merged_view)
    runtime["worktree"] = str(worktree)
    runtime["ref"] = recorded_ref
    lane_head = _git_output(worktree, "rev-parse", "HEAD")
    _dispatch_intent(
        {**state, "lanes": {lane_id: {**merged_view, "worktree": str(worktree), "ref": recorded_ref}}},
        lane_id, role, model, level, task, key, lane_head, access, acceptance, evidence,
    )
    # _dispatch_intent updates the merged copy, so copy only the transient dispatch list back.
    runtime["dispatches"] = merged_view["dispatches"]
    _register_subagent(coordination, lane_id, role, model, level, task, key, access, str(worktree))
    _write_coordination(state, coordination, plan)
    return path


def _complete_dispatch(
    state: dict[str, Any],
    lane_id: str,
    role: str,
    worker_run_id: str | None = None,
    tokens: int | None = None,
    cost: str | None = None,
    result: str | None = None,
) -> None:
    lane = _state_lane(state, lane_id)
    _reject_finished_lane(lane_id, lane)
    dispatches = lane.setdefault("dispatches", [])
    if not isinstance(dispatches, list):
        raise StateError(f"state: dispatches for lane {lane_id!r} are malformed; repair coordination data")
    open_records = [
        record for record in dispatches
        if isinstance(record, dict) and record.get("role") == role and record.get("result") is None
    ]
    if len(open_records) > 1:
        open_records = open_records[-1:]
    if not open_records:
        completed = [
            record for record in reversed(dispatches)
            if isinstance(record, dict) and record.get("role") == role and record.get("result") is not None
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
    lane_id: str,
    role: str,
    worker_run_id: str | None = None,
    tokens: int | None = None,
    cost: str | None = None,
    result: str | None = None,
    change: str | None = None,
) -> Path:
    path, state, plan = _state_context(change)
    coordination, runtime = _coordination_lane_for(state, plan, lane_id)
    merged_view = dict(state["lanes"][lane_id])
    merged_view.update(runtime)
    _complete_dispatch({**state, "lanes": {lane_id: merged_view}}, lane_id, role,
                       worker_run_id, tokens, cost, result)
    runtime["dispatches"] = merged_view["dispatches"]
    _close_subagent(coordination, lane_id, role, worker_run_id, result)
    _write_coordination(state, coordination, plan)
    return path


def _merged_lanes(state: dict[str, Any]) -> set[str]:
    result = set()
    for lane_id, lane in state.get("lanes", {}).items():
        if isinstance(lane, dict) and lane.get("complete"):
            result.add(lane_id)
    return result


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
    for lane_id, lane in lanes.items():
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
            required = ("role", "model", "task", "key", "started_at", "lane_head")
            if any(not isinstance(record.get(field), str) or not record[field].strip() for field in required):
                raise StateError(f"state: dispatch view is unavailable; lane {lane_id!r} has an incomplete intent")
            if lane.get("phase") in {"merged", "closed"}:
                current_head = None
                status = "stale"
            else:
                current_head = _lane_current_head(lane)
                if current_head is None:
                    status = "unavailable"
                else:
                    status = "current" if current_head == record["lane_head"] else "stale"
            intents.append({
                "lane": lane_id,
                "role": record["role"],
                "model": record["model"],
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
        f"{item.get('lane', '?')}/{item.get('role', '?')} "
        f"worker={item.get('model', '?')} task={item.get('task', '?')} "
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
        tasks_text = _committed_file(repository, head, f"openspec/changes/{change}/tasks.md")
        _check_task_ids_text(plan, tasks_text, f"openspec/changes/{change}/tasks.md at lane ref")
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
    view["ready_lanes"] = _ready_lanes(view, plan)
    view["readiness"] = _readiness_reasons(view, plan)
    change = str(view.get("change") or "")
    coordination_available = view.get("coordination_available", True)
    for lane_id, lane in view.get("lanes", {}).items():
        if isinstance(lane, dict):
            lane["phase"] = _coordination_phase_for_display(lane, change, plan)
            lane["inflight"] = _inflight_task_status(view, lane, plan)
            lane["coordinator_status"] = lane.get("status")
            lane["coordinator_status_reason"] = lane.get("status_reason")
            lane["task_states"] = {
                task_id: row.get("status")
                for task_id, row in (lane.get("task_states") or {}).items()
                if isinstance(row, dict)
            }
            lane["human_gate"] = _human_gate_summary(
                lane_id, lane, view.get("human_gates"), coordination_available, view.get("authorizations"),
                view.get("review_policy") == "risk-based" and change != _SELF_REVIEW_CHANGE,
            )
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
    return view


def _status_text(view: dict[str, Any]) -> str:
    lines = [
        f"change: {view.get('change', '')}",
        f"source: {view.get('source_sha', '')}",
        f"ready lanes: {', '.join(view.get('ready_lanes', [])) or 'none'}",
    ]
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
        decision = lane.get("decisions")
        phase = lane.get("phase", "created")
        gate = lane.get("human_gate")
        duties = _decision_duty_text(decision)
        duty_note = f"; review decisions {duties}" if decision else ""
        lines.append(
            f"lane {lane_id}: {phase}; tasks {len(checked)}/{len(task_ids)} [{task_status}]; "
            f"lane dependencies {dependencies}; readiness {reasons.get(lane_id, 'blocked: unavailable')}; "
            f"reviews {verdicts}"
            + duty_note
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


def _safe_href(value: object) -> str:
    text = str(value or "#")
    normalized = text.replace("\\", "/").lstrip(_C0_OR_SPACE)
    parsed = urlparse(normalized)
    if normalized.startswith("//") or parsed.scheme or parsed.netloc:
        return "#"
    return html.escape(text, quote=True)


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


def _decision_duty_text(decision: object) -> str:
    """One-line duty summary; a skip duty always displays as 'skipped', never 'PASS'."""
    if not isinstance(decision, dict) or not isinstance(decision.get("duties"), dict):
        return "none"
    duties = decision["duties"]
    return ", ".join(
        f"{duty}={'skipped' if isinstance(duties.get(duty), dict) and duties[duty].get('mode') == 'skip' else duties.get(duty, {}).get('mode', 'unrecorded')}"
        for duty in _DECISION_DUTIES
    )


def _review_summary(reviews: object) -> tuple[str, str]:
    if not isinstance(reviews, list):
        return "none", "none"
    rounds: list[int] = []
    labels: list[str] = []
    for review in reviews:
        if not isinstance(review, dict):
            continue
        role = html.escape(str(review.get("role") or "?"))
        # A reused ancestor verdict and a selected skip are never labeled PASS.
        if review.get("reused") is True:
            verdict = "reused"
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


def _dashboard_html(state: dict[str, Any], plan: dict[str, Any], run_dir: Path) -> str:
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
        decision_text = _decision_duty_text(lane_state.get("decisions"))
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
            f"<td>{_html_text(decision_text)}</td>"
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
        lane_rows.append('<tr><td colspan="10">No lanes in the committed plan</td></tr>')
    if not task_rows:
        task_rows.append('<tr><td colspan="4">No tasks in the committed plan</td></tr>')
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
<table><thead><tr><th>Lane</th><th>Coordination phase</th><th>Tasks from committed ticks</th><th>In-flight task ticks (not on feature branch)</th><th>Lane dependencies</th><th>Readiness</th><th>Human gate</th><th>Reviews</th><th>Selected review duties</th><th>Merge</th></tr></thead>
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
<footer><small>Rendered from derived state {html.escape(str(run_dir / 'state.json'), quote=True)}; coordination is temporary and never task truth.</small></footer>
</body>
</html>
"""


def render_dashboard(state_path: Path, state: dict[str, Any], plan: dict[str, Any], output: Path | None = None) -> Path:
    target = (output or state_path.parent / "dashboard.html").expanduser().resolve()
    _atomic_write_text(target, _dashboard_html(state, plan, state_path.parent))
    return target


def dashboard(change: str | None = None, output: Path | None = None) -> Path:
    state_path, state, plan = _state_context(change, coordination_optional=True)
    return render_dashboard(state_path, state, plan, output)


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
    path) is an explicit Coordinator override. Orca-managed checkouts keep the
    same default root; tracking in Orca is achieved by registering the lane
    worktree as a child of the feature worktree (see ``lane_open``), not by
    relocating it.
    """
    override = os.environ.get("FEATDEV_LANE_WORKTREE_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".agents" / "worktrees"


def _register_orca_child_worktree(repository: Path, worktree_path: Path) -> None:
    """Best-effort Orca registration of a lane worktree as a child of the feature worktree.

    Keeps the lane worktree at its default ``~/.agents/worktrees`` location while making
    it visible in Orca under the feature worktree's lineage. Registration is optional:
    outside Orca, or when the ``orca`` CLI is unavailable, this silently does nothing and
    the workflow is unaffected.
    """
    if not os.environ.get("ORCA_WORKTREE_ID", "").strip():
        return
    # Only register when the feature checkout itself is the Orca-managed worktree named
    # by the environment; sandbox fixtures and unrelated checkouts must not leak into
    # the caller's Orca workspace.
    if repository.as_posix() not in os.environ["ORCA_WORKTREE_ID"]:
        return
    try:
        result = subprocess.run(
            [
                "orca", "worktree", "set",
                "--worktree", f"path:{worktree_path}",
                "--parent-worktree", "current",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if result.returncode != 0:
        print(
            f"lane: note: Orca registration of {worktree_path} failed: "
            f"{_one_line(result.stderr or result.stdout)}",
            file=sys.stderr,
        )


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
    repo = _safe_component(checkout.name, "repository")
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
        # change between open and later operations (for example an Orca checkout adopting
        # child-worktree lanes), and in-flight lanes must keep their existing location.
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


def _coordination_view_for_lane(
    state: dict[str, Any], plan: dict[str, Any], lane_id: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    coordination, runtime = _coordination_lane_for(state, plan, lane_id)
    lane = _state_lane(state, lane_id)
    return coordination, runtime, lane


def _lane_ready_for_open(state: dict[str, Any], plan: dict[str, Any], lane_id: str) -> bool:
    return lane_id in _ready_lanes(state, plan)


def _lane_checked_tasks(worktree: Path, change: str, plan: dict[str, Any] | None = None) -> set[str]:
    tasks_path = worktree / "openspec" / "changes" / change / "tasks.md"
    try:
        text = tasks_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StateError(f"lane: cannot load {tasks_path}: {_one_line(exc)}; provide the committed tasks.md") from exc
    if plan is not None:
        _check_task_ids_text(plan, text, f"openspec/changes/{change}/tasks.md in lane worktree")
    return set(_checked_task_ids_from_text(text))


def _required_review_roles(lane: dict[str, Any]) -> list[str]:
    roles = ["code", "test"] if lane.get("risk") == "high" else ["code"]
    if lane.get("ui"):
        roles.append("ui")
    return roles


def _effective_review_roles(lane: dict[str, Any], decisions: object) -> list[str]:
    """Required roles under a decision record: a selected full/focused test duty binds
    ordinary lanes too, not only high-risk ones. Without a valid decision the standard
    role set is returned unchanged."""
    roles = _required_review_roles(lane)
    duties = decisions.get("duties") if isinstance(decisions, dict) else None
    test = duties.get("test") if isinstance(duties, dict) else None
    if (
        lane.get("risk") != "high"
        and isinstance(test, dict)
        and test.get("mode") in ("full", "focused")
        and "test" not in roles
    ):
        roles.insert(roles.index("code") + 1, "test")
    ui = duties.get("ui") if isinstance(duties, dict) else None
    if isinstance(ui, dict) and ui.get("mode") in ("full", "focused") and "ui" not in roles:
        roles.append("ui")
    return roles


def _lane_oracle_write_authored(runtime: dict[str, Any]) -> bool:
    """True when a writing Oracle dispatch authored this lane's work (task 5.4).

    Oracle-authored commits always need a direct independent review that did not author
    them. Decisions cannot skip required duties, and ancestor reuse cannot satisfy them.
    """
    dispatches = runtime.get("dispatches")
    if not isinstance(dispatches, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("role") == "oracle"
        and item.get("access") == "write"
        for item in dispatches
    )


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
    plan_relative = f"openspec/changes/{state['change']}/plan/graph-plan.json"
    # A grant is bound to the approved plan revision, and the approved plan is the one committed at the
    # feature-branch HEAD - not merely the revision the lane happened to open from. Both must match: a
    # revised plan invalidates outstanding grants, and a renewal is a new grant with its own identity.
    base_plan_sha256 = hashlib.sha256(
        _committed_file(repository, base, plan_relative).encode("utf-8")
    ).hexdigest()
    plan_sha256 = hashlib.sha256(
        _committed_file(repository, feature_head, plan_relative).encode("utf-8")
    ).hexdigest()
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
    if "all" not in scope and lane_id not in scope:
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
    if "all" not in scope and lane_id not in scope:
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
    if state.get("change") == _SELF_REVIEW_CHANGE or _review_mode(plan) == "combined-single-lane":
        raise StateError("lane: design D6 forbids self-use or combined review deferral")
    if (not (lane.get("ui") or lane.get("screen")) or lane.get("sensitive")
            or lane.get("risk") == "high"):
        raise StateError("lane: sensitive, high-risk, or non-UI lanes cannot defer the human checkpoint")
    if risk_assessment != "low" or not isinstance(rationale, str) or not rationale.strip() or any(
        keyword not in rationale.lower() for keyword in
        ("bounded", "reversible", "medical", "safety", "privacy", "sharing", "data-integrity", "content")
    ):
        raise StateError("lane: deferral requires low-risk assessment and rationale for bounded, reversible impact and each excluded sensitive effect")
    coordination, runtime = _coordination_lane_for(state, plan, lane_id)
    if coordination.get("review_policy") != "risk-based":
        raise StateError("lane: deferral requires the opt-in risk-based policy")
    gates = coordination.setdefault("human_gates", {})
    prior = gates.get(lane_id)
    if prior is not None and (not isinstance(prior, dict) or prior.get("state") != "waiting"):
        raise StateError("lane: existing human decision cannot be replaced by deferral; resolve checkpoint with human")
    evidence_path = _review_binding_path(evidence)
    if lane.get("screen") and not evidence_path:
        raise StateError("lane: screen deferral requires current evidence; provide --evidence")
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
                    "evidence_path": evidence_path,
                    "evidence_sha256": _path_sha256(Path(evidence_path)) if evidence_path else None},
        "rationale": rationale.strip(), "risk_assessment": risk_assessment,
        "bounded": True, "reversible": True,
        "excluded_effects": ["medical", "safety", "privacy", "sharing", "data-integrity", "content-correctness"],
    }
    _write_coordination(state, coordination, plan)
    return path


def _lane_requires_human_gate(lane: dict[str, Any]) -> bool:
    # A plan declaration, not an inferred state: `sensitive` marks a lane that touches
    # user-facing wording or content, safety, privacy, or medically sensitive material.
    return bool(lane.get("ui") or lane.get("screen") or lane.get("sensitive"))


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
    if lane.get("screen") and not evidence_path:
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
            "evidence_revision": evidence_revision.strip(),
            "evidence_path": evidence_path,
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
    if gate.get("state") == "deferred-to-integrated" and allow_deferred:
        binding = gate.get("binding")
        if (lane.get("sensitive") or lane.get("risk") == "high" or not (lane.get("ui") or lane.get("screen"))
                or not isinstance(binding, dict) or binding.get("commit") != head
                or not binding.get("evidence_revision") or not gate.get("rationale")
                or gate.get("risk_assessment") != "low"
                or gate.get("approved_by") is not None or gate.get("authorization") is not None
                or gate.get("bounded") is not True or gate.get("reversible") is not True
                or set(gate.get("excluded_effects", [])) != {"medical", "safety", "privacy", "sharing", "data-integrity", "content-correctness"}):
            raise StateError("lane: deferred checkpoint is ineligible or stale; obtain early human approval")
        evidence_path = binding.get("evidence_path")
        if lane.get("screen") and not evidence_path:
            raise StateError("lane: screen deferral has no current evidence; obtain fresh evidence")
        if evidence_path:
            if (not isinstance(evidence_path, str) or not Path(evidence_path).is_file()
                    or _path_sha256(Path(evidence_path)) != binding.get("evidence_sha256")):
                raise StateError("lane: deferred screen evidence changed or missing; recapture before merge")
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
    if lane.get("screen") and not evidence_path:
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
    if lane.get("ui"):
        return "ui"
    return "screen"


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


def _mediation_covers(lane: dict[str, Any], role: str) -> bool:
    """Say whether a recorded Oracle mediation covers this role's work on the lane.

    A mediation only counts when it was recorded after the role had already exhausted its two
    ordinary rounds, and its report must still be retained with the recorded digest: a record
    written before the failures, or pointing at a report that has since changed, proves nothing
    about them.
    """
    mediations = lane.get("mediations")
    if not isinstance(mediations, list):
        return False
    for item in mediations:
        if not isinstance(item, dict) or item.get("role") not in {None, role}:
            continue
        try:
            round_at_mediation = int(item.get("round_at_mediation"))
        except (TypeError, ValueError):
            continue
        report = item.get("report")
        digest = item.get("report_sha256")
        if round_at_mediation < 2 or not isinstance(report, str) or not report:
            continue
        if not isinstance(digest, str) or not digest:
            continue
        try:
            if _path_sha256(Path(report)) != digest:
                continue
        except OSError:
            continue
        return True
    return False


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
    path, state, plan = _state_context(change)
    lane = _state_lane(state, lane_id)
    if lane.get("complete"):
        raise StateError(
            f"lane: lane {lane_id!r} is already complete from committed ticks; do not reopen it"
        )
    coordination, runtime = _coordination_lane_for(state, plan, lane_id)
    phase = runtime.get("phase", "created")
    expected_path = _lane_worktree_path(state, lane_id).resolve()
    expected_ref = _lane_ref(state, lane_id)
    recorded_path = runtime.get("worktree")
    recorded_ref = runtime.get("ref")
    if recorded_path and Path(recorded_path).resolve() != expected_path:
        raise StateError(f"lane: lane {lane_id!r} has a different recorded worktree; reconcile it first")
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
    _register_orca_child_worktree(repository, expected_path)
    runtime["phase"] = "writing"
    _write_coordination(state, coordination, plan)
    return path


def lane_close(lane_id: str, change: str | None = None) -> Path:
    path, state, plan = _state_context(change)
    lane = _state_lane(state, lane_id)
    coordination, runtime = _coordination_lane_for(state, plan, lane_id)
    phase = runtime.get("phase", "created")
    if not lane.get("complete") and phase not in {"merged", "closed"}:
        raise StateError(f"lane: lane {lane_id!r} is not complete; merge it before closing")
    repository = Path(state["feature_checkout"]).resolve()
    expected_path = _lane_worktree_path(state, lane_id).resolve()
    expected_ref = _lane_ref(state, lane_id)
    recorded_path = runtime.get("worktree")
    recorded_ref = runtime.get("ref")
    if recorded_ref and recorded_ref != expected_ref:
        raise StateError(f"lane: lane {lane_id!r} cleanup ref is not deterministic; reconcile it first")
    # A recorded worktree is authoritative for the lane's lifetime (matching
    # _lane_runtime_for_operation): root resolution may change between open and
    # close (FEATDEV_LANE_WORKTREE_ROOT override, Orca child-worktree adoption),
    # so cleanup targets the recorded location and only falls back to the
    # computed path when nothing was recorded.
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
        result = _run_git(repository, "branch", "-D", recorded_ref)
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
    combined: bool = False,
    oracle_provenance: str | None = None,
    feature_base: str | None = None,
    review_base: str | None = None,
    acceptance: object = None,
) -> Path:
    path, state, plan = _state_context(change)
    if combined:
        return _combined_lane_review(
            lane_id, role, verdict, commit, report, evidence_revision, evidence,
            oracle_provenance, feature_base, review_base, acceptance,
            path, state, plan,
        )
    if any(value is not None for value in (oracle_provenance, feature_base, review_base, acceptance)):
        raise StateError(
            "lane: combined review inputs require --combined; remove them or select combined mode"
        )
    lane = _state_lane(state, lane_id)
    _reject_finished_lane(lane_id, lane)
    if lane.get("complete"):
        raise StateError(f"lane: lane {lane_id!r} is already complete from committed ticks; review is unnecessary")
    if verdict not in {"PASS", "MORE WORK"}:
        raise StateError("lane: verdict must be PASS or MORE WORK; change it")
    coordination, runtime = _coordination_lane_for(state, plan, lane_id)
    operation_lane = dict(lane)
    operation_lane.update(runtime)
    if role not in _effective_review_roles(operation_lane, runtime.get("decisions")):
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
    if lane.get("screen") and verdict == "PASS" and not evidence_path:
        raise StateError(f"lane: lane {lane_id!r} requires evidence for a PASS; provide --evidence")
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
    # Two ordinary rounds are the bound: an initial review plus one correction. Past that the
    # workflow requires an Oracle diagnosis of the root cause, so a further round is refused until
    # the mediation is recorded.
    if round_number > 2 and not _mediation_covers(runtime, role):
        raise StateError(
            f"lane: review round {round_number} for {role!r} is past the two-round limit; consult the Oracle "
            "for the root cause and record the mediation with lane-mediate before reviewing again"
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
            and previous.get("verdict") == verdict
            and _review_commit(previous) == reviewed_head
            and previous_binding.get("evidence_revision") == revision
            and previous_binding.get("evidence_path") == evidence_path
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


def _combined_lane_from_plan(plan: dict[str, Any], lane_id: str) -> dict[str, Any]:
    """Validate the narrow eligibility contract for the combined review path."""
    if _review_mode(plan) != "combined-single-lane":
        raise StateError(
            "lane: combined review requires plan review_mode 'combined-single-lane'; use standard review"
        )
    lanes = plan.get("lanes")
    if not isinstance(lanes, dict) or len(lanes) != 1:
        raise StateError(
            "lane: combined review requires exactly one ordinary-risk lane; use standard review"
        )
    lane_plan = lanes.get(lane_id)
    if not isinstance(lane_plan, dict) or lane_plan.get("risk") != "ordinary":
        raise StateError(
            f"lane: combined review lane {lane_id!r} is not the single ordinary-risk lane; use standard review"
        )
    return lane_plan


def _combined_reviews(lane: dict[str, Any]) -> list[dict[str, Any]]:
    records = lane.setdefault("reviews", [])
    if not isinstance(records, list):
        raise StateError("state: reviews are malformed; repair coordination data")
    if any(not isinstance(item, dict) for item in records):
        raise StateError("state: review record is malformed; repair coordination data")
    return records  # type: ignore[return-value]


def _combined_oracle_dispatch(
    runtime: dict[str, Any],
    provenance: object,
    reviewed_head: str,
    acceptance_path: str,
    evidence_path: str,
) -> dict[str, Any]:
    """Resolve combined provenance to one completed, read-only Oracle dispatch."""
    if not isinstance(provenance, str) or not provenance.strip():
        raise StateError("lane: combined review needs non-empty --oracle-provenance")
    dispatches = runtime.get("dispatches")
    if not isinstance(dispatches, list):
        raise StateError(
            "lane: combined Oracle provenance has no dispatch ledger; record a completed read-only Oracle dispatch"
        )
    token = provenance.strip()
    matches = [
        item for item in dispatches
        if isinstance(item, dict)
        and (item.get("worker_run_id") == token or item.get("key") == token)
    ]
    if len(matches) == 1:
        dispatch = matches[0]
    else:
        reviewed_matches = [item for item in matches if item.get("lane_head") == reviewed_head]
        if len(reviewed_matches) != 1:
            raise StateError(
                "lane: combined Oracle provenance must identify exactly one completed Oracle dispatch for the "
                "reviewed lane HEAD (lane_head); record an independent read-only Oracle dispatch for that exact candidate"
            )
        dispatch = reviewed_matches[0]
    if not isinstance(dispatch, dict):
        raise StateError(
            "lane: combined Oracle provenance must identify exactly one completed Oracle dispatch; "
            "record an independent read-only Oracle dispatch"
        )
    if dispatch.get("role") != "oracle":
        raise StateError(
            "lane: combined Oracle provenance identifies authored or non-Oracle work; use an independent Oracle"
        )
    if dispatch.get("result") != "ok":
        raise StateError(
            "lane: combined Oracle provenance identifies a dispatch whose result is not 'ok'; "
            "complete the Oracle successfully before recording or merging the combined review"
        )
    access = dispatch.get("access")
    if access == "write":
        raise StateError(
            "lane: combined Oracle provenance identifies an Oracle authoring dispatch; an independent Oracle "
            "must review the authored candidate"
        )
    if access != "read-only":
        raise StateError(
            "lane: combined Oracle provenance identifies an ambiguous legacy Oracle dispatch; "
            "record it with --access read-only"
        )
    if dispatch.get("lane_head") != reviewed_head:
        raise StateError(
            "lane: combined Oracle provenance dispatch lane_head does not match the reviewed lane HEAD; "
            "record a dispatch for the exact candidate commit"
        )
    for label, expected_path, path_field, digest_field in (
        ("acceptance", acceptance_path, "acceptance_path", "acceptance_sha256"),
        ("evidence", evidence_path, "evidence_path", "evidence_sha256"),
    ):
        if dispatch.get(path_field) != expected_path or not isinstance(dispatch.get(digest_field), str):
            raise StateError(
                f"lane: combined Oracle provenance {label} snapshot is missing or bound to another path; "
                "re-dispatch the Oracle with the current review documents"
            )
        if dispatch[digest_field] != _path_sha256(Path(expected_path)):
            raise StateError(
                f"lane: combined Oracle provenance {label} document changed since dispatch; "
                "re-dispatch the Oracle before recording or merging the review"
            )
    reviewer_session = dispatch.get("worker_run_id")
    if not isinstance(reviewer_session, str) or not reviewer_session.strip():
        raise StateError(
            "lane: combined Oracle provenance requires the reviewer dispatch to have a non-empty stable "
            "worker_run_id"
        )
    reviewer_session = reviewer_session.strip()
    for author in dispatches:
        if not isinstance(author, dict) or author is dispatch:
            continue
        author_access = author.get("access")
        authoring = author_access == "write" or (
            author_access is None and author.get("role") in {"implementer", "worker", "oracle"}
        )
        if not authoring:
            continue
        author_session = author.get("worker_run_id")
        if not isinstance(author_session, str) or not author_session.strip():
            raise StateError(
                "lane: combined Oracle provenance cannot verify an author-capable dispatch without a "
                "non-empty stable worker_run_id"
            )
        if author_session.strip() == reviewer_session:
            raise StateError(
                "lane: combined Oracle provenance shares a worker session with authored work; "
                "an independent Oracle must review the authored candidate"
            )
    return dispatch


def _combined_lane_review(
    lane_id: str,
    role: str,
    verdict: str,
    commit: str,
    report: object,
    evidence_revision: str | None,
    evidence: object,
    oracle_provenance: str | None,
    feature_base: str | None,
    review_base: str | None,
    acceptance: object,
    path: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
) -> Path:
    """Record one exact-HEAD Oracle pass covering lane code/test and final review."""
    if role != "code":
        raise StateError("lane: combined review must use role 'code'; the Oracle supplies the combined duty")
    if verdict not in {"PASS", "MORE WORK"}:
        raise StateError("lane: verdict must be PASS or MORE WORK; change it")
    _combined_lane_from_plan(plan, lane_id)
    lane = _state_lane(state, lane_id)
    _reject_finished_lane(lane_id, lane)
    coordination, runtime = _coordination_lane_for(state, plan, lane_id)
    operation_lane = dict(lane)
    operation_lane.update(runtime)
    worktree, recorded_ref = _lane_runtime_for_operation(state, lane_id, operation_lane)
    runtime["worktree"] = str(worktree)
    runtime["ref"] = recorded_ref
    if _run_git(worktree, "status", "--porcelain").stdout.strip():
        raise StateError(f"lane: lane {lane_id!r} worktree is dirty; commit before review")
    actual_head = _git_output(worktree, "rev-parse", "HEAD")
    reviewed_head = _git_commit(worktree, commit)
    if reviewed_head != actual_head:
        raise StateError(
            f"lane: combined review commit {reviewed_head} is not the lane HEAD {actual_head}; "
            "review the exact candidate HEAD"
        )
    change_name = state.get("change")
    if not isinstance(change_name, str) or not change_name:
        raise StateError("lane: change is missing; re-derive the state")
    assigned = _lane_assigned_tasks(plan, lane_id)
    checked = _lane_checked_tasks(worktree, change_name, plan)
    missing = assigned - checked
    if missing:
        raise StateError(
            f"lane: lane {lane_id!r} has unfinished tasks ({', '.join(sorted(missing))}); "
            "tick every assigned task before combined review"
        )
    oracle_provenance = oracle_provenance.strip() if isinstance(oracle_provenance, str) else ""
    if not isinstance(feature_base, str) or not feature_base:
        raise StateError("lane: combined review needs --feature-base")
    if not isinstance(review_base, str) or not review_base:
        raise StateError("lane: combined review needs --review-base")
    if acceptance is None:
        raise StateError("lane: combined review needs --acceptance")
    repository = Path(state["feature_checkout"]).resolve()
    branch = _feature_branch(state)
    feature_head = _git_output(repository, "rev-parse", branch)
    feature_base_sha = _git_commit(repository, feature_base)
    review_base_sha = _git_commit(repository, review_base)
    lane_base_value = runtime.get("base")
    if not isinstance(lane_base_value, str) or not lane_base_value:
        raise StateError("lane: combined review has no recorded lane base; reopen the lane")
    lane_base_sha = _git_commit(repository, lane_base_value)
    if feature_base_sha != feature_head:
        raise StateError(
            f"lane: combined review feature base {feature_base_sha} is not current feature HEAD {feature_head}; "
            "review the unchanged feature base"
        )
    if lane_base_sha != feature_base_sha:
        raise StateError(
            f"lane: combined review lane base {lane_base_sha} differs from feature base {feature_base_sha}; "
            "reopen the lane from the current feature base"
        )
    if not _is_ancestor(repository, review_base_sha, feature_base_sha):
        raise StateError(
            f"lane: whole-change review base {review_base_sha} is not an ancestor of feature base "
            f"{feature_base_sha}; change --review-base"
        )
    if not _is_ancestor(repository, feature_base_sha, reviewed_head):
        raise StateError(
            f"lane: feature base {feature_base_sha} is not an ancestor of reviewed candidate {reviewed_head}; "
            "reconcile the lane history"
        )
    report_path = _review_report(report)
    evidence_path = _review_binding_path(evidence)
    if not evidence_path:
        raise StateError("lane: combined review needs final evidence; provide --evidence")
    acceptance_path = _required_document(acceptance, "combined acceptance document")
    if acceptance_path == report_path:
        raise StateError(
            "lane: combined acceptance document and review report must be different files; "
            "record the final acceptance separately"
        )
    _combined_oracle_dispatch(runtime, oracle_provenance, reviewed_head, acceptance_path, evidence_path)
    revision = str(evidence_revision or "").strip()
    if not revision:
        raise StateError("lane: combined review needs --evidence-revision")
    plan_sha256 = state.get("plan_sha256")
    if not isinstance(plan_sha256, str) or not plan_sha256:
        raise StateError("lane: combined review has no approved plan hash; re-derive the state")
    records = _combined_reviews(runtime)
    role_records = [
        item for item in records
        if item.get("lane") == lane_id and item.get("combined") is True
    ]
    same_head = [item for item in role_records if _review_commit(item) == reviewed_head]
    prior_rounds = [number for item in role_records if (number := _review_round(item)) is not None]
    round_number = max(
        (_review_round(item) or 0 for item in same_head),
        default=max(prior_rounds, default=0) + 1,
    )
    if round_number > 2 and not _mediation_covers(runtime, "code"):
        raise StateError(
            f"lane: combined review round {round_number} is past the two-round limit; consult the Oracle "
            "for mediation before reviewing again"
        )
    record = {
        "round": round_number,
        "lane": lane_id,
        "role": "code",
        "combined": True,
        "duties": ["code", "test", "final"],
        "oracle_provenance": oracle_provenance,
        "verdict": verdict,
        "reviewed_commit": reviewed_head,
        "status": "current",
        "gate_eligible": True,
        "report": report_path,
        "report_sha256": _path_sha256(Path(report_path)),
        "binding": {
            "commit": reviewed_head,
            "plan_sha256": plan_sha256,
            "feature_base": feature_base_sha,
            "lane_base": lane_base_sha,
            "review_base": review_base_sha,
            "report_sha256": _path_sha256(Path(report_path)),
            "evidence_revision": revision,
            "evidence_path": evidence_path,
            "evidence_sha256": _path_sha256(Path(evidence_path)),
            "acceptance_path": acceptance_path,
            "acceptance_sha256": _path_sha256(Path(acceptance_path)),
        },
    }
    for previous in records:
        if not isinstance(previous, dict) or previous.get("lane") != lane_id or previous.get("combined") is not True:
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
    for previous in same_head:
        previous_binding = previous.get("binding")
        if (
            previous.get("combined") is True
            and previous.get("verdict") == verdict
            and previous.get("oracle_provenance") == record["oracle_provenance"]
            and previous.get("report") == report_path
            and previous.get("report_sha256") == record["report_sha256"]
            and isinstance(previous_binding, dict)
            and previous_binding == record["binding"]
        ):
            previous.update({"status": "current", "gate_eligible": True, "reviewed_commit": reviewed_head})
            runtime["phase"] = "revising" if verdict == "MORE WORK" else "reviewing"
            _write_coordination(state, coordination, plan)
            return path
    records.append(record)
    runtime["phase"] = "revising" if verdict == "MORE WORK" else "reviewing"
    _write_coordination(state, coordination, plan)
    return path


def _latest_reviews(lane: dict[str, Any], head: str | None = None) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for review in lane.get("reviews") or []:
        if not isinstance(review, dict) or not isinstance(review.get("role"), str):
            continue
        # Combined records have their own final-review validator. They must never silently
        # satisfy a later standard-mode code gate after the plan changes mode.
        if review.get("combined") is True:
            continue
        if head is not None and _review_status(lane, review, head) != "current":
            continue
        if head is None and review.get("status") == "superseded":
            continue
        latest[review["role"]] = review
    return latest


def _latest_combined_review(
    lane: dict[str, Any], lane_id: str, head: str | None = None
) -> dict[str, Any] | None:
    records = _combined_reviews(lane)
    candidates = [
        (index, item) for index, item in enumerate(records)
        if item.get("lane", lane_id) == lane_id
        and item.get("combined") is True
        and (head is None or _review_commit(item) == head)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda pair: (_review_round(pair[1]) or 0, pair[0]))[1]


def _validate_combined_review(
    state: dict[str, Any],
    plan: dict[str, Any],
    lane_id: str,
    lane: dict[str, Any],
    runtime: dict[str, Any],
    coordination: dict[str, Any],
    head: str,
    feature_head: str,
    lane_base: str,
    post_merge: bool = False,
) -> dict[str, Any]:
    """Validate combined review and all supplied files at the exact pre-merge state."""
    _combined_lane_from_plan(plan, lane_id)
    review = _latest_combined_review(runtime, lane_id, head)
    if not review:
        raise StateError(
            f"lane: combined review is missing for current HEAD {head}; record an independent combined review"
        )
    if review.get("verdict") != "PASS":
        raise StateError(
            f"lane: combined review is {review.get('verdict')!r}, not PASS; fix the lane"
        )
    if review.get("role") != "code" or review.get("duties") != ["code", "test", "final"]:
        raise StateError(
            "lane: combined review does not record code/test/final duties; record a fresh combined review"
        )
    round_number = _review_round(review)
    if round_number and round_number > 2 and not _mediation_covers(runtime, "code"):
        raise StateError(
            f"lane: combined review reached round {round_number} without Oracle mediation; consult the Oracle"
        )
    binding = review.get("binding")
    if not isinstance(binding, dict):
        raise StateError("lane: combined review has no binding; record a fresh combined review")
    if binding.get("commit") != head:
        raise StateError(
            f"lane: combined review is bound to {binding.get('commit') or 'no commit'}, lane HEAD is {head}; "
            "re-review the lane"
        )
    if binding.get("plan_sha256") != state.get("plan_sha256"):
        raise StateError("lane: combined review is bound to another plan revision; record a fresh review")
    repository = Path(state["feature_checkout"]).resolve()
    for label in ("feature_base", "lane_base", "review_base"):
        value = binding.get(label)
        if not isinstance(value, str) or not value:
            raise StateError(f"lane: combined review has no {label}; record a fresh review")
        binding[label] = _git_commit(repository, value)
    expected_feature_base = lane_base if post_merge else feature_head
    if binding["feature_base"] != expected_feature_base:
        raise StateError(
            f"lane: combined review feature base {binding['feature_base']} differs from expected feature base "
            f"{expected_feature_base}; re-review the unchanged feature base"
        )
    if binding["lane_base"] != lane_base:
        raise StateError(
            f"lane: combined review lane base {binding['lane_base']} differs from current lane base {lane_base}; "
            "reconcile the lane before merging"
        )
    if not _is_ancestor(repository, binding["review_base"], binding["feature_base"]):
        raise StateError("lane: combined whole-change review base is not an ancestor of feature base")
    if not _is_ancestor(repository, binding["feature_base"], head):
        raise StateError("lane: combined feature base is not an ancestor of reviewed HEAD")
    evidence_revision = binding.get("evidence_revision")
    if not isinstance(evidence_revision, str) or not evidence_revision.strip():
        raise StateError("lane: combined review has no evidence revision; record a fresh review")
    report_path = _required_document(review.get("report"), "combined review report")
    report_digest = review.get("report_sha256")
    if (
        report_digest != _path_sha256(Path(report_path))
        or binding.get("report_sha256") != report_digest
    ):
        raise StateError("lane: combined review report changed after review; record a fresh review")
    evidence_path = _required_document(binding.get("evidence_path"), "combined review evidence")
    if binding.get("evidence_sha256") != _path_sha256(Path(evidence_path)):
        raise StateError("lane: combined review evidence changed after review; record fresh evidence and review")
    acceptance_path = _required_document(binding.get("acceptance_path"), "combined acceptance document")
    if binding.get("acceptance_sha256") != _path_sha256(Path(acceptance_path)):
        raise StateError(
            "lane: combined acceptance document changed after review; record a fresh acceptance and review"
        )
    provenance = review.get("oracle_provenance")
    _combined_oracle_dispatch(runtime, provenance, head, acceptance_path, evidence_path)
    return review


def lane_mediate(
    lane_id: str,
    report: object,
    role: str | None = None,
    change: str | None = None,
) -> Path:
    """Record an Oracle mediation for a lane that has used its two ordinary review rounds."""
    path, state, plan = _state_context(change)
    lane = _state_lane(state, lane_id)
    _reject_finished_lane(lane_id, lane)
    if role is not None and role not in _required_review_roles(lane):
        raise StateError(
            f"lane: mediation role {role!r} is not required for lane {lane_id!r}; use the plan flags or omit --role"
        )
    coordination, runtime = _coordination_lane_for(state, plan, lane_id)
    mediation_report = _required_document(report, "Oracle mediation report")
    mediations = runtime.setdefault("mediations", [])
    if not isinstance(mediations, list):
        raise StateError("state: mediations are malformed; repair the coordination data")
    mediations.append(
        {
            "role": role,
            "report": mediation_report,
            "report_sha256": _path_sha256(Path(mediation_report)),
            "round_at_mediation": max(
                (
                    number
                    for item in runtime.get("reviews") or []
                    if isinstance(item, dict)
                    and (number := _review_round(item)) is not None
                ),
                default=0,
            ),
        }
    )
    _write_coordination(state, coordination, plan)
    return path


def _validate_reuse_record(
    role: str, review: dict[str, Any], head: str, repository: Path | None, allow_reuse: bool,
    lane: dict[str, Any],
) -> None:
    """Verify an ancestor-reuse record's bindings before the reused PASS can satisfy a gate.

    The record must carry a real intervening diff: the stored file must still hash to the
    recorded digest, and the digest itself must equal the actual ``git diff <ancestor>..<head>``
    recomputed from the repository. Evidence presence/path must match the direct source
    PASS; screen lanes always require a path and digest. The standard (legacy) policy never
    honors reuse. """
    if not allow_reuse:
        raise StateError(
            f"lane: review {role!r} is an ancestor-reuse record, but the standard review policy is "
            "active; ancestor reuse requires the risk-based policy; review the lane at current HEAD"
        )
    if repository is None:
        raise StateError(
            f"lane: reuse record for {role!r} cannot be verified without a repository; re-review the lane"
        )
    binding = review.get("binding")
    if not isinstance(binding, dict):
        raise StateError(f"lane: reuse record for {role!r} has no binding; re-record it with review-reuse")
    evidence_path = binding.get("evidence_path")
    if lane.get("screen") and (not evidence_path or not binding.get("evidence_sha256")):
        raise StateError(
            f"lane: reused review evidence for {role!r} requires path and digest for a screen lane; "
            "re-review with evidence"
        )
    source = next(
        (
            item for item in lane.get("reviews") or []
            if isinstance(item, dict)
            and item.get("role") == role
            and item.get("verdict") == "PASS"
            and item.get("reused") is not True
            and item.get("combined") is not True
            and _review_commit(item) == binding.get("ancestor_commit")
        ),
        None,
    )
    source_binding = source.get("binding") if source else None
    if (not isinstance(source_binding, dict) or evidence_path != source_binding.get("evidence_path")
            or (evidence_path is not None and (not source_binding.get("evidence_sha256")
                or binding.get("evidence_sha256") != source_binding.get("evidence_sha256")))):
        raise StateError(
            f"lane: reused review evidence for {role!r} differs from its direct ancestor PASS; "
            "re-review the lane"
        )
    if evidence_path is not None:
        evidence_path = _required_document(evidence_path, "reused review evidence")
        if binding.get("evidence_sha256") != _path_sha256(Path(evidence_path)):
            raise StateError(
                f"lane: evidence for {role!r} changed after the reuse record; "
                "re-review the lane at current HEAD"
            )
    elif binding.get("evidence_sha256") is not None:
        raise StateError(f"lane: reused review evidence for {role!r} is missing; re-review the lane")
    ancestor = binding.get("ancestor_commit")
    if not isinstance(ancestor, str) or not ancestor:
        raise StateError(
            f"lane: reuse record for {role!r} does not name its ancestor commit; re-record the reuse"
        )
    diff_path = binding.get("intervening_diff")
    if not isinstance(diff_path, str) or not Path(diff_path).is_file() or Path(diff_path).stat().st_size == 0:
        raise StateError(
            f"lane: the intervening diff for {role!r} is missing or empty; re-review the lane at current HEAD"
        )
    stored = binding.get("intervening_diff_sha256")
    diff_text = Path(diff_path).read_text(encoding="utf-8")
    if not isinstance(stored, str) or hashlib.sha256(diff_text.encode("utf-8")).hexdigest() != stored:
        raise StateError(
            f"lane: the intervening diff for {role!r} changed after the reuse record was written; "
            "re-review the lane at current HEAD"
        )
    real_diff = _run_git(repository, "diff", f"{ancestor}..{head}", "--").stdout
    if not real_diff.strip() or hashlib.sha256(real_diff.encode("utf-8")).hexdigest() != stored:
        raise StateError(
            f"lane: the recorded intervening diff for {role!r} no longer matches the actual git diff "
            f"{ancestor}..{head}; re-review the lane at current HEAD"
        )


def _validate_lane_reviews(
    lane_id: str,
    lane: dict[str, Any],
    head: str,
    required_roles: list[str] | None = None,
    decisions: dict[str, Any] | None = None,
    repository: Path | None = None,
    allow_reuse: bool = False,
) -> None:
    roles = (
        list(required_roles) if required_roles is not None
        else _effective_review_roles(lane, decisions)
    )
    skipped_roles = _decision_skipped_roles(roles, decisions)
    latest = _latest_reviews(lane, head)
    for role in roles:
        if role in skipped_roles:
            # A recorded Coordinator decision selects 'skip' for this duty; it is never
            # labeled PASS and the merge record preserves the selected duty.
            continue
        rounds = [
            number for item in lane.get("reviews") or []
            if isinstance(item, dict) and item.get("role") == role
            and (number := _review_round(item)) is not None
        ]
        # The two-round bound is re-checked here so hand-edited coordination data cannot buy a
        # third round, or a merge after one, without a recorded Oracle mediation.
        if rounds and max(rounds) > 2 and not _mediation_covers(lane, role):
            raise StateError(
                f"lane: review {role!r} reached round {max(rounds)} without an Oracle mediation record; "
                "consult the Oracle and record it with lane-mediate before merging"
            )
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
        if not binding.get("evidence_revision"):
            raise StateError(f"lane: review {role!r} has no evidence revision; re-record the review")
        if review.get("reused") is True:
            if _lane_oracle_write_authored(lane):
                raise StateError(
                    "lane: Oracle-authored write work requires a direct independent current-HEAD review; "
                    "ancestor reuse cannot cover authored corrections"
                )
            _validate_reuse_record(role, review, head, repository, allow_reuse, lane)
        report = review.get("report")
        if not isinstance(report, str) or not Path(report).is_file() or Path(report).stat().st_size == 0:
            raise StateError(f"lane: review {role!r} report is missing or empty; re-review the lane")
        if lane.get("screen"):
            evidence = binding.get("evidence_path")
            if not isinstance(evidence, str) or not Path(evidence).is_file() or Path(evidence).stat().st_size == 0:
                raise StateError(f"lane: review {role!r} changed-screen evidence is missing; re-review with --evidence")


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
    tasks_relative = f"openspec/changes/{change}/tasks.md"
    plan_relative = f"openspec/changes/{change}/plan/graph-plan.json"
    # Approved definitions come from feature HEAD; historical base still owns tick accounting.
    base_plan = _committed_file(repository, base, plan_relative)
    if not (
        base_plan == _committed_file(repository, head, plan_relative)
        == _committed_file(repository, approved_head, plan_relative)
    ):
        raise StateError(
            f"lane: candidate plan {plan_relative} differs from the approved plan at lane base {base} "
            f"or approved HEAD {approved_head}; "
            "revert the plan change and have the Coordinator revise it on the feature branch"
        )
    base_tasks = _committed_file(repository, base, tasks_relative)
    candidate_tasks = _committed_file(repository, head, tasks_relative)
    approved_tasks = _committed_file(repository, approved_head, tasks_relative)
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
    # added set instead of demanding a duplicate in-lane tick that task-tick would refuse.
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


def _validate_completed_combined_lane_merge(
    state: dict[str, Any],
    plan: dict[str, Any],
    lane_id: str,
    lane: dict[str, Any],
    runtime: dict[str, Any],
    coordination: dict[str, Any],
) -> None:
    """Revalidate an already-complete combined lane without trusting ancestry alone."""
    _combined_lane_from_plan(plan, lane_id)
    merge = runtime.get("merge")
    if not isinstance(merge, dict):
        raise StateError(
            f"lane: completed combined lane {lane_id!r} has no merge record; matching ticks do not establish "
            "combined approval; recover with a fresh combined review"
        )
    if merge.get("review_mode") != "combined-single-lane":
        raise StateError(
            f"lane: completed combined lane {lane_id!r} has an inconsistent merge mode; "
            "recover with a fresh combined review"
        )
    operation_lane = dict(lane)
    operation_lane.update(runtime)
    reviewed_head = merge.get("reviewed_head")
    merge_commit = merge.get("merge_commit")
    recorded_base = merge.get("base")
    recorded_branch = merge.get("branch")
    if not all(
        isinstance(value, str) and value
        for value in (reviewed_head, merge_commit, recorded_base, recorded_branch)
    ):
        raise StateError(
            f"lane: completed combined lane {lane_id!r} has an incomplete merge record; recover with a fresh review"
        )
    repository = Path(state["feature_checkout"]).resolve()
    feature_branch = _feature_branch(state)
    if recorded_branch != feature_branch:
        raise StateError(
            f"lane: completed combined lane {lane_id!r} merge is bound to branch {recorded_branch!r}, "
            f"not {feature_branch!r}; reconcile the feature branch"
        )
    current_branch = _git_output(repository, "rev-parse", "--abbrev-ref", "HEAD")
    if current_branch != feature_branch:
        raise StateError(
            f"lane: feature checkout is on {current_branch!r}, not recorded branch {feature_branch!r}; "
            "switch branches before recovery"
        )
    if _run_git(repository, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise StateError(
            f"lane: feature checkout is dirty after combined merge; commit or discard tracked changes before recovery"
        )
    reviewed_head = _git_commit(repository, reviewed_head)
    merge_commit = _git_commit(repository, merge_commit)
    recorded_base = _git_commit(repository, recorded_base)
    feature_head = _git_output(repository, "rev-parse", feature_branch)
    if feature_head != reviewed_head or merge_commit != reviewed_head:
        raise StateError(
            f"lane: completed combined lane {lane_id!r} is not at exact reviewed HEAD {reviewed_head}; "
            "reconcile the feature branch and revalidate"
        )
    review = _validate_combined_review(
        state, plan, lane_id, lane, runtime, coordination,
        reviewed_head, feature_head, recorded_base, post_merge=True,
    )
    if review.get("binding", {}).get("commit") != merge_commit:
        raise StateError("lane: combined merge record and review binding disagree; recover with a fresh review")
    ui_roles = [role for role in _required_review_roles(operation_lane) if role != "code"]
    _validate_lane_reviews(
        lane_id, operation_lane, reviewed_head, ui_roles,
        decisions=_decisions_from_merge(runtime.get("merge")),
        repository=repository, allow_reuse=False,
    )
    _validate_human_gate(
        lane_id, operation_lane, reviewed_head, coordination, state.get("plan_sha256"),
        allow_consumed_by=lane_id,
    )


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
    _validate_lane_reviews(
        lane_id, operation_lane, reviewed_head,
        # Lanes of the archived self-review change merge under standard authority regardless
        # of any scratch opt-in (design D6), so decision records recovered from a merge
        # record are ignored.
        decisions=None if state.get("change") == _SELF_REVIEW_CHANGE
        else _decisions_from_merge(runtime.get("merge")),
        repository=repository,
        allow_reuse=coordination.get("review_policy") == "risk-based"
        and state.get("change") != _SELF_REVIEW_CHANGE,
    )
    if recorded:
        # The tool validated this gate when it performed the merge, and its one-use grant is already
        # consumed: re-litigating a completed checkpoint would break idempotency.
        return
    # A hand-resolved recovery has no recorded validation, so the human gate must still hold here,
    # and its one-use grant is consumed by this validation so the recovery cannot be replayed.
    _validate_human_gate(lane_id, operation_lane, reviewed_head, coordination, approved_plan_sha256,
                         allow_deferred=coordination.get("review_policy") == "risk-based"
                         and state.get("change") != _SELF_REVIEW_CHANGE)
    gates = coordination.get("human_gates") or {}
    gate = gates.get(lane_id) if isinstance(gates, dict) else None
    authorization = gate.get("authorization") if isinstance(gate, dict) else None
    grant_id = authorization.get("id") if isinstance(authorization, dict) else None
    grants = coordination.get("authorizations")
    grant = grants.get(grant_id) if isinstance(grants, dict) and isinstance(grant_id, str) else None
    if isinstance(grant, dict) and grant.get("status") == "active":
        grant["status"] = "consumed"
        grant["consumed_by"] = lane_id
    # Record the validated completion so replaying this recovery is idempotent rather than a second,
    # unreviewed acceptance of the lane.
    runtime["merge"] = {
        "reviewed_head": reviewed_head,
        "merge_commit": merge_commit if isinstance(merge_commit, str) and merge_commit else reviewed_head,
        "base": reviewed_head,
    }


def lane_merge(
    lane_id: str,
    change: str | None = None,
    human_override: bool = False,
    override_reason: str | None = None,
) -> Path:
    path, state, plan = _state_context(change)
    lane = _state_lane(state, lane_id)
    coordination, runtime = _coordination_lane_for(state, plan, lane_id)
    combined_mode = _review_mode(plan) == "combined-single-lane"
    if human_override:
        # A recorded human decision trumps the review gates. Design D5 restricts the
        # override to waivable review duties: it can never waive clean-tree, task-tick
        # ownership/completeness, lane-base/ancestry validation, current required
        # evidence, or human authorization integrity, so those checks still run below.
        # The reason is mandatory and is persisted in the coordination record beside
        # the merge so the audit trail shows which decision bypassed which gate.
        reason = (override_reason or "").strip()
        if not reason:
            raise StateError(
                "lane: --human-override requires --override-reason naming the human decision; "
                "an unexplained override cannot be recorded"
            )
        if lane.get("complete"):
            raise StateError(
                f"lane: lane {lane_id!r} is already complete from committed ticks; "
                "record a human override only on a lane that is not merged"
            )
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
        if _run_git(worktree, "status", "--porcelain").stdout.strip():
            raise StateError("lane: lane worktree is dirty; commit before merging")
        head = _git_output(worktree, "rev-parse", "HEAD")
        ref_head = _ref_sha(repository, ref)
        if ref_head != head:
            raise StateError(f"lane: ref {ref} is at {ref_head or 'no commit'}, worktree HEAD is {head}; reconcile it first")
        if _run_git(repository, "status", "--porcelain", "--untracked-files=no").stdout.strip():
            raise StateError("lane: feature checkout is dirty; commit or discard changes before merging")
        feature_head = _git_output(repository, "rev-parse", branch)
        # Waivable review duties only: ticks, tick-set ownership, base/ancestry, and the
        # human checkpoint still bind an override merge.
        base = _lane_merge_base(repository, ref, feature_head)
        _validate_resolved_lane_base(repository, base, head, feature_head)
        override_change = state.get("change")
        if not isinstance(override_change, str) or not override_change:
            raise StateError("lane: change is missing; re-derive the state")
        override_assigned = _lane_assigned_tasks(plan, lane_id)
        override_checked = _lane_checked_tasks(worktree, override_change, plan)
        if override_missing := (override_assigned - override_checked):
            raise StateError(
                f"lane: lane {lane_id!r} has unfinished tasks ({', '.join(sorted(override_missing))}); "
                "a human override cannot waive task-tick completeness; tick every assigned task first"
            )
        _candidate_tick_state(repository, override_change, plan, lane_id, base, head, feature_head)
        override_lane = dict(lane)
        override_lane.update(runtime)
        _validate_human_gate(
            lane_id, override_lane, head, coordination, state.get("plan_sha256"),
            allow_deferred=coordination.get("review_policy") == "risk-based"
            and state.get("change") != _SELF_REVIEW_CHANGE,
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
                f"Merge lane {lane_id} (human override; candidate {head})",
                head,
            )
            if result.returncode:
                _run_git(repository, "merge", "--abort")
                raise StateError(
                    f"lane: merge failed: {_one_line(result.stderr or result.stdout)}; "
                    "resolve conflicts manually, then rerun lane-merge --human-override"
                )
            merge_commit = _git_output(repository, "rev-parse", "HEAD")
        runtime["merge"] = {
            "reviewed_head": head,
            "merge_commit": merge_commit,
            "base": base,
            "branch": branch,
            "review_mode": "combined-single-lane" if combined_mode else "standard",
            "human_override": {
                "reason": reason,
                "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                "skipped_gates": ["reviews"],
            },
        }
        runtime["phase"] = "merged"
        _write_coordination(state, coordination, plan)
        state_derive(state.get("change"), None, repository)
        return path
    if lane.get("complete"):
        # A merged tick is the durable completion record, but it is not proof on its own: the
        # documented manual-merge recovery path lands the lane's ticks on the feature branch
        # without this command validating them. Establish the reviewed commit, its containment in
        # the feature branch, the lane's required reviews, and the lane's human gate before
        # treating the lane as merged.
        if combined_mode:
            _validate_completed_combined_lane_merge(state, plan, lane_id, lane, runtime, coordination)
            return path
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
    # Design D5/D6: decision-driven review selection only under the explicit risk-based
    # policy; a missing decision record (or standard policy) falls back to the standard gate.
    # Lanes of the archived self-review change keep the standard authority no matter what a
    # scratch opt-in recorded: the change cannot relax its own review gates (design D6).
    honored_decision = None
    policy_relaxed = (
        coordination.get("review_policy") == "risk-based"
        and state.get("change") != _SELF_REVIEW_CHANGE
    )
    if policy_relaxed and runtime.get("decisions") is not None:
        honored_decision = runtime.get("decisions")
        _validate_decision_record(
            operation_lane, honored_decision,
            oracle_authored=_lane_oracle_write_authored(runtime),
        )
    if combined_mode:
        _validate_combined_review(
            state, plan, lane_id, operation_lane, runtime, coordination,
            head, feature_head, base,
        )
        ui_roles = [role for role in _required_review_roles(operation_lane) if role != "code"]
        _validate_lane_reviews(
            lane_id, operation_lane, head, ui_roles, decisions=honored_decision,
            repository=repository, allow_reuse=False,
        )
    else:
        _validate_lane_reviews(
            lane_id, operation_lane, head, decisions=honored_decision,
            repository=repository, allow_reuse=policy_relaxed,
        )
    _validate_human_gate(lane_id, operation_lane, head, coordination, state.get("plan_sha256"),
                         allow_deferred=policy_relaxed)
    candidate_lane_checked, _, _ = _candidate_tick_state(
        repository, change_name, plan, lane_id, base, head, feature_head
    )
    if combined_mode:
        result = _run_git(repository, "merge", "--ff-only", head)
        if result.returncode:
            raise StateError(
                f"lane: combined fast-forward failed: {_one_line(result.stderr or result.stdout)}; "
                "the feature base moved or the reviewed candidate is no longer a direct descendant; "
                "obtain a fresh combined review"
            )
        merge_commit = _git_output(repository, "rev-parse", "HEAD")
        if merge_commit != head:
            raise StateError("lane: combined fast-forward did not preserve the exact reviewed candidate SHA")
    elif _is_ancestor(repository, head, feature_head):
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
                f"lane: merge failed: {_one_line(result.stderr or result.stdout)}; manually merge reviewed lane HEAD "
                f"{head} into the feature branch, resolve conflicts, commit, then rerun lane-merge to validate "
                "and record the preserved reviewed SHA"
            )
        merge_commit = _git_output(repository, "rev-parse", "HEAD")
        if not _is_ancestor(repository, head, merge_commit):
            raise StateError("lane: merge did not preserve the reviewed commit; reconcile the feature branch")
    runtime["merge"] = {
        "reviewed_head": head,
        "merge_commit": merge_commit,
        "base": base,
        "branch": branch,
        "review_mode": "combined-single-lane" if combined_mode else "standard",
    }
    if honored_decision is not None:
        # Persist the selected duties beside the merge so recovery and status stay visible.
        # An ordinary lane that selects a full/focused test duty records it too (the duty
        # was enforced, not ignored).
        runtime["merge"]["review_duties"] = {
            role: honored_decision["duties"][role]["mode"]
            for role in _effective_review_roles(operation_lane, honored_decision)
        }
    runtime["phase"] = "merged"
    # An authorization is consumed at most once per checkpoint. Record the consumption before the
    # projection refresh so a second lane cannot reuse the same grant.
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
    # Persist the merge record, the merged phase, and the consumed grant before any step that can
    # fail: an interrupted refresh must never leave a one-use grant active on disk. The projection
    # refresh does not mutate coordination, so no second write is needed after it.
    _write_coordination(state, coordination, plan)
    return path


def _finish_version(repository: Path) -> tuple[str, str]:
    source = repository / "App/project.yml"
    generated = repository / "App/PregnancyTracker.xcodeproj/project.pbxproj"
    if not source.is_file() or not generated.is_file():
        raise StateError("finish: version source or generated project is missing; regenerate before capture")
    match = re.findall(r"^\s*MARKETING_VERSION:\s*['\"]?([0-9]+(?:\.[0-9]+){1,2})", source.read_text(), re.M)
    if len(match) != 1 or f"MARKETING_VERSION = {match[0]};" not in generated.read_text():
        raise StateError("finish: version and generated project disagree; regenerate and commit before capture")
    return match[0], hashlib.sha256(source.read_bytes() + generated.read_bytes()).hexdigest()


def _human_inspection_verdict(path: Path, runtime: str) -> str | None:
    """Read unique authoritative fields, never quoted/history/observation substrings."""
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"(inspector|runtime|verdict):\s*(.*?)\s*", line)
        if match:
            if match[1] in fields:
                return None
            fields[match[1]] = match[2]
    if fields.get("inspector") == "human" and fields.get("runtime") == runtime and fields.get("verdict") in {"accept", "reject"}:
        return fields["verdict"]
    return None


def _human_inspection_accepts(path: Path, runtime: str) -> bool:
    return _human_inspection_verdict(path, runtime) == "accept"


def _finish_integrated(state: dict[str, Any], coordination: dict[str, Any], repository: Path, head: str) -> None:
    lanes = state.get("lanes", {})
    recorded = coordination.get("lanes", {})
    if not isinstance(lanes, dict) or not lanes or not isinstance(recorded, dict):
        raise StateError("finish: no completed integrated lanes; merge assigned lanes before final capture")
    for lane_id, lane in lanes.items():
        merge = recorded.get(lane_id, {}).get("merge") if isinstance(recorded.get(lane_id), dict) else None
        if not isinstance(lane, dict) or lane.get("complete") is not True or not isinstance(merge, dict):
            raise StateError(f"finish: lane {lane_id} not merged; integrate all assigned ticks before final capture")
        merged_head = merge.get("merge_commit")
        if not isinstance(merged_head, str) or not _is_ancestor(repository, merged_head, head):
            raise StateError(f"finish: lane {lane_id} merge commit is not in integrated HEAD")


def finish_record(action: str, classification: str | None = None, version: str | None = None,
                  check: str | None = None, artifact: str | None = None,
                  inputs: list[str] | None = None, change: str | None = None) -> Path:
    """Bind final receipts to committed integrated source, preserving unrelated checks on later HEADs."""
    path, state, plan = _state_context(change)
    repository = Path(state["feature_checkout"])
    coordination = _load_coordination(state, plan)
    if _run_git(repository, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise StateError("finish: tracked changes are uncommitted; commit before final capture")
    head = _git_output(repository, "rev-parse", "HEAD")
    record = coordination.setdefault("finish", {})
    if action in {"classify", "restage"}:
        if classification not in {"pre-release", "minor-update", "feature-update"} or not version:
            raise StateError("finish: record a human classification and expected marketing version")
        if action == "classify" and (record.get("evidence") or record.get("required_checks")):
            raise StateError("finish: classification/version after final evidence; use restage and recapture")
        if action == "restage":
            _finish_integrated(state, coordination, repository, head)
            actual_version, _ = _finish_version(repository)
            if version != actual_version:
                raise StateError("finish: restage requires committed regenerated expected version")
            if not record.get("classification"):
                raise StateError("finish: classify before restage")
            required = record.setdefault("required_checks", {})
            for selected, item in record.get("evidence", {}).items():
                required.setdefault(selected, item["inputs"])
            record["evidence"] = {}
            record["inspection"] = {}
        record.update({"classification": classification, "expected_version": version, "classified_at": head})
    else:
        if not record.get("classification"):
            raise StateError("finish: human release classification must precede final capture")
        actual_version, version_hash = _finish_version(repository)
        if actual_version != record.get("expected_version"):
            raise StateError("finish: marketing version differs from classification; commit required version first")
        evidence = record.setdefault("evidence", {})
        if action in {"capture", "inspect", "pr-check"}:
            _finish_integrated(state, coordination, repository, head)
        if action == "capture":
            if not check or not artifact or not inputs:
                raise StateError("finish: capture requires check, artifact, and affected input paths")
            artifact_path = Path(artifact).resolve()
            digest = _path_sha256(artifact_path)
            required = record.setdefault("required_checks", {})
            declared = set(inputs)
            if check in required and not set(required[check]).issubset(declared):
                raise StateError("finish: recapture must retain all selected check input dependencies")
            required[check] = sorted(declared)
            evidence[check] = {"head": head, "version_sha256": version_hash,
                               "artifact": str(artifact_path), "sha256": digest,
                               "inputs": sorted(declared)}
        elif action == "inspect":
            if check not in {"ios-27.0", "ios-26.5", "ios-18.6"} or not artifact:
                raise StateError("finish: direct human inspection requires runtime and human record path")
            report_path = Path(artifact).resolve()
            digest = _path_sha256(report_path)
            verdict = _human_inspection_verdict(report_path, check)
            if verdict == "reject":
                record.setdefault("inspection", {})[check] = {"head": head, "artifact": str(report_path),
                    "sha256": digest, "version_sha256": version_hash, "verdict": "reject"}
                _write_coordination(state, coordination, plan)
            if verdict != "accept":
                raise StateError("finish: human must personally record accept on this runtime; Oracle stand-in cannot inspect")
            record.setdefault("inspection", {})[check] = {"head": head, "artifact": str(report_path),
                                                        "sha256": digest, "version_sha256": version_hash, "verdict": "accept"}
        elif action == "pr-check":
            required = record.get("required_checks", {})
            if not required or set(required) != set(evidence):
                raise StateError("finish: selected checks missing final evidence; recapture before PR approval")
            if any(item.get("verdict") == "reject" for item in record.get("inspection", {}).values()):
                raise StateError("finish: rejected human inspection blocks PR approval")
            changed_ui = any(
                (lane.get("ui") or lane.get("screen")) and isinstance(
                    coordination.get("lanes", {}).get(lane_id, {}).get("merge"), dict)
                for lane_id, lane in state.get("lanes", {}).items()
            )
            deferred = any(isinstance(gate, dict) and gate.get("state") == "deferred-to-integrated"
                           for gate in coordination.get("human_gates", {}).values())
            if changed_ui or deferred:
                for runtime in ("ios-27.0", "ios-26.5", "ios-18.6"):
                    item = record.get("inspection", {}).get(runtime)
                    if not isinstance(item, dict) or item.get("head") != head or item.get("version_sha256") != version_hash:
                        raise StateError(f"finish: changed-UI checkpoint blocks PR approval; human three-runtime inspection missing/stale: {runtime}")
                    report_path = Path(item["artifact"])
                    if _path_sha256(report_path) != item["sha256"] or not _human_inspection_accepts(report_path, runtime):
                        raise StateError(f"finish: changed/rejected human inspection: {runtime}")
            for selected in evidence:
                item = evidence[selected]
                if item.get("version_sha256") != version_hash or _path_sha256(Path(item["artifact"])) != item["sha256"]:
                    raise StateError(f"finish: final evidence changed: {selected}")
                if item["head"] != head and (not _is_ancestor(repository, item["head"], head) or
                    _git_output(repository, "diff", "--name-only", item["head"], head, "--", *item["inputs"])):
                    raise StateError(f"finish: affected final evidence: {selected}")
        elif action == "check":
            if not check or check not in evidence:
                raise StateError("finish: selected check has no final evidence")
            item = evidence[check]
            if item.get("version_sha256") != version_hash:
                raise StateError("finish: version changed after evidence; recapture affected evidence")
            if _path_sha256(Path(item["artifact"])) != item["sha256"]:
                raise StateError("finish: final evidence artifact changed; recapture")
            if item["head"] != head:
                if not _is_ancestor(repository, item["head"], head):
                    raise StateError("finish: final evidence is not from an ancestor; recapture")
                changed = _git_output(repository, "diff", "--name-only", item["head"], head, "--", *item["inputs"])
                if changed:
                    raise StateError("finish: affected evidence/review inputs changed; recapture selected check")
                # Only unchanged declared inputs survive. Coordinator must still verify the
                # repository receipt/reuse proof and independently reassess any affected review.
        else:
            raise StateError("finish: unknown action")
    _write_coordination(state, coordination, plan)
    return path


def _self_check() -> int:
    """Exercise the plan, derived-state, tick, coordination, and merge gates."""
    original_cwd = Path.cwd()
    saved_environment = os.environ.copy()
    with tempfile.TemporaryDirectory(prefix="featdev-check-") as temporary:
        root = Path(temporary)
        isolated_home = root / "home"
        isolated_home.mkdir()
        # Git has several environment channels. Clear all of them so the check cannot
        # inspect or mutate a caller-selected repository, index, or configuration.
        for key in list(os.environ):
            if key.startswith("GIT_CONFIG") or key in {
                "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CEILING_DIRECTORIES",
                "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_NAMESPACE",
            }:
                os.environ.pop(key, None)
        os.environ.update({
            "HOME": str(isolated_home),
            "XDG_CONFIG_HOME": str(isolated_home / ".config"),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        })
        os.chdir(root)

        def git(repository: Path, *arguments: str) -> str:
            result = subprocess.run(
                ["git", "-C", str(repository), *arguments],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()

        def commit(repository: Path, message: str) -> str:
            subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repository), "commit", "-q", "-m", message], check=True)
            return git(repository, "rev-parse", "HEAD")

        def make_repository(name: str, change: str, plan: dict[str, Any], tasks: str) -> Path:
            repository = root / name
            plan_dir = repository / "openspec" / "changes" / change / "plan"
            plan_dir.mkdir(parents=True)
            repository.joinpath(".gitignore").write_text(".agents/tmp/\\n", encoding="utf-8")
            plan_dir.joinpath("graph-plan.json").write_text(json.dumps(plan), encoding="utf-8")
            plan_dir.parent.joinpath("tasks.md").write_text(tasks, encoding="utf-8")
            subprocess.run(["git", "init", "-q", "-b", "main", str(repository)], check=True)
            git(repository, "config", "user.email", "self-check@example.com")
            git(repository, "config", "user.name", "Self Check")
            commit(repository, "planning checkpoint approved by human")
            return repository

        def invoke(*arguments: str) -> tuple[int, str, str]:
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                try:
                    result = main(list(arguments))
                except SystemExit as exc:
                    result = exc.code if isinstance(exc.code, int) else 1
            return result, stdout.getvalue(), stderr.getvalue()

        def expect_refusal(*arguments: str, contains: str) -> None:
            result, _, stderr = invoke(*arguments)
            assert result == 1, (arguments, result, stderr)
            assert contains in stderr, (arguments, stderr)

        # Documentation cannot silently restore the retired capture-before-version order:
        # classification/version (step 2) must precede the release-gate/capture step (step 3).
        finish_guidance = (Path(__file__).resolve().parent.parent / "workflow/finish.md").read_text()
        assert "Release classification and version" in finish_guidance
        classification_at = finish_guidance.index("Release classification and version")
        gate_at = finish_guidance.index("Execute the repository's release gate")
        assert classification_at < gate_at
        assert "capture only after" not in finish_guidance.lower().replace("classification and version are complete", "")
        assert "no workflow CLI command" not in finish_guidance
        assert "The release gate (step 1)" not in finish_guidance

        # Finish ordering: classification -> generated/committed version -> exact-HEAD capture.
        finish_repo = make_repository("finishcase", "finishcase", {
            "schema_version": 1, "change": "finishcase",
            "lanes": {"one": {"risk": "ordinary", "ui": False, "screen": False,
                               "sensitive": False, "depends_on": []}},
            "tasks": {"1.1": {"lane": "one", "deps": []}},
        }, "- [ ] 1.1 first\n")
        (finish_repo / ".gitignore").write_text(".agents/tmp/\n")
        (finish_repo / "App/PregnancyTracker.xcodeproj").mkdir(parents=True)
        (finish_repo / "App/project.yml").write_text("MARKETING_VERSION: 0.2\n")
        (finish_repo / "App/PregnancyTracker.xcodeproj/project.pbxproj").write_text("MARKETING_VERSION = 0.2;\n")
        (finish_repo / "input-a").write_text("a")
        (finish_repo / "input-b").write_text("b")
        commit(finish_repo, "version and integrated source")
        os.chdir(finish_repo)
        state_derive("finishcase")
        artifact = finish_repo / ".agents/tmp/receipt.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("passed")
        expect_refusal("--change", "finishcase", "finish-record", "capture", "--check", "a",
                       "--artifact", str(artifact), "--input", "input-a", contains="classification")
        assert invoke("--change", "finishcase", "finish-record", "classify", "--classification",
                      "pre-release", "--version", "0.2")[0] == 0
        (finish_repo / "input-a").write_text("dirty")
        expect_refusal("--change", "finishcase", "finish-record", "capture", "--check", "a",
                       "--artifact", str(artifact), "--input", "input-a", contains="uncommitted")
        (finish_repo / "input-a").write_text("a")
        expect_refusal("--change", "finishcase", "finish-record", "capture", "--check", "a",
                       "--artifact", str(artifact), "--input", "input-a", contains="not merged")
        expect_refusal("--change", "finishcase", "finish-record", "pr-check", contains="not merged")
        (finish_repo / "openspec/changes/finishcase/tasks.md").write_text("- [x] 1.1 first\n")
        integrated_head = commit(finish_repo, "merge lane and tick")
        finish_state = _read_state(state_derive("finishcase"))
        finish_plan = json.loads((finish_repo / "openspec/changes/finishcase/plan/graph-plan.json").read_text())
        finish_coord = _load_coordination(finish_state, finish_plan)
        finish_coord["lanes"]["one"]["merge"] = {"reviewed_head": integrated_head,
                                                       "merge_commit": integrated_head}
        _write_coordination(finish_state, finish_coord, finish_plan)
        assert invoke("--change", "finishcase", "finish-record", "capture", "--check", "a",
                      "--artifact", str(artifact), "--input", "input-a")[0] == 0
        (finish_repo / "App/project.yml").write_text("MARKETING_VERSION: 0.3\n")
        (finish_repo / "App/PregnancyTracker.xcodeproj/project.pbxproj").write_text("MARKETING_VERSION = 0.3;\n")
        commit(finish_repo, "late version bump")
        expect_refusal("--change", "finishcase", "finish-record", "check", "--check", "a",
                       contains="marketing version differs")
        expect_refusal("--change", "finishcase", "finish-record", "capture", "--check", "a",
                       "--artifact", str(artifact), "--input", "input-a", contains="marketing version differs")
        expect_refusal("--change", "finishcase", "finish-record", "restage", "--classification",
                       "feature-update", "--version", "0.4", contains="committed regenerated")
        assert invoke("--change", "finishcase", "finish-record", "restage", "--classification",
                      "feature-update", "--version", "0.3")[0] == 0
        expect_refusal("--change", "finishcase", "finish-record", "pr-check", contains="missing final evidence")
        expect_refusal("--change", "finishcase", "finish-record", "capture", "--check", "a",
                       "--artifact", str(artifact), "--input", "input-b", contains="retain all")
        artifact.write_text("recaptured 0.3")
        assert invoke("--change", "finishcase", "finish-record", "capture", "--check", "a",
                      "--artifact", str(artifact), "--input", "input-a")[0] == 0
        assert invoke("--change", "finishcase", "finish-record", "check", "--check", "a")[0] == 0
        assert invoke("--change", "finishcase", "finish-record", "pr-check")[0] == 0
        (finish_repo / "input-b").write_text("changed")
        commit(finish_repo, "unrelated input")
        assert invoke("--change", "finishcase", "finish-record", "check", "--check", "a")[0] == 0
        (finish_repo / "input-a").write_text("changed")
        commit(finish_repo, "relevant input")
        expect_refusal("--change", "finishcase", "finish-record", "check", "--check", "a",
                       contains="affected evidence")
        os.chdir(root)

        # Deferred UI: merge after selected duties, never consume authorization; PR
        # requires three direct human acceptances bound to the integrated HEAD.
        ui_plan = {"schema_version": 1, "change": "defercase", "lanes": {
            "ui": {"risk": "ordinary", "ui": True, "screen": True, "sensitive": False, "depends_on": []}},
            "tasks": {"1.1": {"lane": "ui", "deps": []}}}
        ui_repo = make_repository("defercase", "defercase", ui_plan, "- [ ] 1.1 UI\n")
        (ui_repo / ".gitignore").write_text(".agents/tmp/\n")
        (ui_repo / "App/PregnancyTracker.xcodeproj").mkdir(parents=True)
        (ui_repo / "App/project.yml").write_text("MARKETING_VERSION: 0.2\n")
        (ui_repo / "App/PregnancyTracker.xcodeproj/project.pbxproj").write_text("MARKETING_VERSION = 0.2;\n")
        commit(ui_repo, "version source")
        os.chdir(ui_repo)
        state_derive("defercase")
        lane_open("ui", "defercase")
        ui_worktree = _lane_worktree_path(_read_state(state_derive("defercase", checkout=ui_repo)), "ui")
        task_tick("ui", "1.1", "defercase")
        ui_head = commit(ui_worktree, "UI lane and tick")
        review_decision("ui", {"code": "skip", "test": "skip", "ui": "skip"}, [],
                        "1.1 checked", ["static:check"], "bounded reversible cosmetic UI", True, "defercase")
        expect_refusal("--change", "defercase", "lane-merge", "ui", contains="human gate")
        # A grant sitting unused cannot be consumed by deferral.
        ui_state = _read_state(state_derive("defercase", checkout=ui_repo))
        ui_coord = _load_coordination(ui_state, ui_plan)
        ui_coord["authorizations"]["unused"] = {"status": "active"}
        _write_coordination(ui_state, ui_coord, ui_plan)
        defer_rationale = "bounded reversible cosmetic UI; no medical/safety/privacy/sharing/data-integrity/content effect"
        screen_evidence = ui_repo / ".agents" / "workflow" / "defercase" / "screen-receipt.json"
        screen_evidence.parent.mkdir(parents=True, exist_ok=True)
        screen_evidence.write_text("current screen evidence")
        expect_refusal("--change", "defercase", "human-defer", "ui", "--commit", ui_head,
                       "--evidence-revision", "r1", "--risk-assessment", "uncertain", "--rationale",
                       defer_rationale, "--evidence", str(screen_evidence), contains="low-risk assessment")
        for prior_state in ("rejected", "revoked", "approved"):
            ui_coord = _load_coordination(ui_state, ui_plan)
            ui_coord["human_gates"]["ui"] = {
                "lane": "ui", "state": prior_state,
                "binding": {"commit": ui_head, "evidence_revision": "r1",
                            "evidence_path": str(screen_evidence)},
            }
            _write_coordination(ui_state, ui_coord, ui_plan)
            expect_refusal("--change", "defercase", "human-defer", "ui", "--commit", ui_head,
                           "--evidence-revision", "r1", "--risk-assessment", "low", "--rationale",
                           defer_rationale, "--evidence", str(screen_evidence), contains="existing human decision")
            assert _load_coordination(ui_state, ui_plan)["human_gates"]["ui"]["state"] == prior_state
            expect_refusal("--change", "defercase", "human-gate", "ui", "--state", "waiting",
                           "--commit", ui_head, "--evidence-revision", "r1", "--evidence",
                           str(screen_evidence), contains="existing human decision")
            assert _load_coordination(ui_state, ui_plan)["human_gates"]["ui"]["state"] == prior_state
            expect_refusal("--change", "defercase", "lane-merge", "ui", contains=prior_state)
        ui_coord = _load_coordination(ui_state, ui_plan)
        ui_coord["human_gates"].pop("ui")
        _write_coordination(ui_state, ui_coord, ui_plan)
        expect_refusal("--change", "defercase", "human-defer", "ui", "--commit", ui_head,
                       "--evidence-revision", "r1", "--risk-assessment", "low", "--rationale",
                       defer_rationale, contains="screen deferral requires")
        assert invoke("--change", "defercase", "human-gate", "ui", "--state", "waiting",
                      "--commit", ui_head, "--evidence-revision", "r1", "--evidence", str(screen_evidence))[0] == 0
        human_defer("ui", ui_head, "r1", defer_rationale, "defercase", "low", str(screen_evidence))
        ui_coord = _load_coordination(ui_state, ui_plan)
        assert ui_coord["authorizations"]["unused"]["status"] == "active"
        screen_lane = {"ui": True, "screen": True, "risk": "ordinary"}
        _validate_human_gate("ui", screen_lane, ui_head, ui_coord, allow_deferred=True)
        no_screen_evidence = json.loads(json.dumps(ui_coord))
        no_screen_evidence["human_gates"]["ui"]["binding"].pop("evidence_path")
        try:
            _validate_human_gate("ui", screen_lane, ui_head, no_screen_evidence, allow_deferred=True)
        except StateError as exc:
            assert "screen deferral has no current evidence" in str(exc)
        else:
            raise AssertionError("screen deferral without evidence accepted")
        screen_evidence.write_text("changed screen evidence")
        try:
            _validate_human_gate("ui", screen_lane, ui_head, ui_coord, allow_deferred=True)
        except StateError as exc:
            assert "screen evidence changed" in str(exc)
        else:
            raise AssertionError("changed screen evidence accepted")
        screen_evidence.write_text("current screen evidence")
        for altered in ({"sensitive": True}, {"risk": "high"}):
            try:
                _validate_human_gate("ui", {"ui": True, **altered}, ui_head, ui_coord, allow_deferred=True)
            except StateError as exc:
                assert "ineligible" in str(exc)
            else:
                raise AssertionError("sensitive/high-risk deferral accepted")
        # High-risk internal tooling needs code/test agents, not a new human lane gate.
        internal = {"risk": "high", "ui": False, "screen": False, "sensitive": False}
        _validate_human_gate("internal", internal, ui_head, ui_coord)
        try:
            _validate_decision_record(internal, ui_coord["lanes"]["ui"]["decisions"])
        except StateError as exc:
            assert "independent review" in str(exc)
        else:
            raise AssertionError("high-risk internal tooling skipped code/test review")
        stale_gate = json.loads(json.dumps(ui_coord))
        stale_gate["human_gates"]["ui"]["binding"]["commit"] = "0" * 40
        try:
            _validate_human_gate("ui", {"ui": True, "risk": "ordinary"}, ui_head,
                                 stale_gate, allow_deferred=True)
        except StateError as exc:
            assert "stale" in str(exc)
        else:
            raise AssertionError("stale deferral accepted")
        lane_merge("ui", "defercase")
        assert _load_coordination(_read_state(state_derive("defercase", checkout=ui_repo)), ui_plan)["authorizations"]["unused"]["status"] == "active"
        assert invoke("--change", "defercase", "finish-record", "classify", "--classification", "pre-release", "--version", "0.2")[0] == 0
        assert invoke("--change", "defercase", "finish-record", "capture", "--check", "static:check",
                      "--artifact", str(screen_evidence), "--input", "App/project.yml")[0] == 0
        expect_refusal("--change", "defercase", "finish-record", "pr-check", contains="inspection missing")
        for runtime in ("ios-27.0", "ios-26.5", "ios-18.6"):
            report = ui_repo / ".agents" / "workflow" / "defercase" / f"{runtime}.md"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(f"inspector: human\nruntime: {runtime}\nverdict: accept\n")
            assert invoke("--change", "defercase", "finish-record", "inspect", "--check", runtime,
                          "--artifact", str(report))[0] == 0
        assert invoke("--change", "defercase", "finish-record", "pr-check")[0] == 0
        # A normally checkpointed merged changed-UI lane still needs integrated inspection.
        normal_gate = _read_state(_coordination_path("defercase", ui_repo))
        saved_gate = json.loads(json.dumps(normal_gate))
        normal_gate["human_gates"]["ui"]["state"] = "accepted"
        normal_gate["finish"]["inspection"] = {}
        _atomic_write_json(_coordination_path("defercase", ui_repo), normal_gate)
        expect_refusal("--change", "defercase", "finish-record", "pr-check", contains="inspection missing")
        _atomic_write_json(_coordination_path("defercase", ui_repo), saved_gate)
        separate_reject = ui_repo / ".agents" / "workflow" / "defercase" / "later-ios-18.6.md"
        separate_reject.write_text('inspector: human\nruntime: ios-18.6\nverdict: reject\nprevious verdict: accept\n')
        expect_refusal("--change", "defercase", "finish-record", "inspect", "--check", "ios-18.6",
                       "--artifact", str(separate_reject), contains="personally record accept")
        expect_refusal("--change", "defercase", "finish-record", "pr-check", contains="rejected human inspection")
        separate_reject.write_text('inspector: human\nruntime: ios-18.6\nverdict: accept\n')
        assert invoke("--change", "defercase", "finish-record", "inspect", "--check", "ios-18.6",
                      "--artifact", str(separate_reject))[0] == 0
        assert invoke("--change", "defercase", "finish-record", "pr-check")[0] == 0
        report.write_text('inspector: human\nruntime: ios-18.6\nverdict: reject\nprevious verdict: accept\n')
        assert invoke("--change", "defercase", "finish-record", "inspect", "--check", "ios-18.6",
                      "--artifact", str(report))[0] == 1
        expect_refusal("--change", "defercase", "finish-record", "inspect", "--check", "ios-18.6",
                       "--artifact", str(report), contains="personally record accept")
        # Even if a stored hash is refreshed externally, re-parse authoritative verdict.
        inspection_state = _read_state(state_derive("defercase", checkout=ui_repo))
        inspection_coord = _load_coordination(inspection_state, ui_plan)
        inspection_coord["finish"]["inspection"]["ios-18.6"]["sha256"] = _path_sha256(report)
        _write_coordination(inspection_state, inspection_coord, ui_plan)
        expect_refusal("--change", "defercase", "finish-record", "pr-check", contains="rejected human inspection")
        report.write_text('inspector: human\nruntime: ios-18.6\nverdict: accept\nverdict: reject\n')
        expect_refusal("--change", "defercase", "finish-record", "inspect", "--check", "ios-18.6",
                       "--artifact", str(report), contains="personally record accept")
        expect_refusal("--change", "defercase", "finish-record", "pr-check", contains="rejected human inspection")
        report.write_text("inspector: human\nruntime: ios-18.6\nverdict: accept\n")
        assert invoke("--change", "defercase", "finish-record", "inspect", "--check", "ios-18.6",
                      "--artifact", str(report))[0] == 0
        assert invoke("--change", "defercase", "finish-record", "pr-check")[0] == 0
        report.write_text("inspector: oracle\nruntime: ios-18.6\nverdict: accept\n")
        expect_refusal("--change", "defercase", "finish-record", "pr-check", contains="changed/rejected")
        report.write_text("inspector: human\nruntime: ios-18.6\nverdict: accept\n")
        (ui_repo / "new-input").write_text("changed")
        commit(ui_repo, "changed after human inspection")
        expect_refusal("--change", "defercase", "finish-record", "pr-check", contains="inspection missing/stale")
        os.chdir(root)

        # Plan validation remains read-only and only top-level checkboxes identify tasks.
        plan_dir = root / "plan-check" / "openspec" / "changes" / "check" / "plan"
        plan_dir.mkdir(parents=True)
        plan_path = plan_dir / "graph-plan.json"
        tasks_path = plan_dir.parent / "tasks.md"
        check_plan = {
            "schema_version": 1,
            "change": "check",
            "lanes": {"one": {"risk": "ordinary", "ui": False, "screen": False,
                              "sensitive": False, "depends_on": []}},
            "tasks": {"1.1": {"lane": "one", "deps": []}},
        }
        tasks_path.write_text(
            "- [ ] 1.1 first\n  - [ ] **Phase:** annotation\n", encoding="utf-8"
        )
        plan_path.write_text(json.dumps(check_plan), encoding="utf-8")
        validate_plan(plan_path, tasks_path)
        malformed_mode = json.loads(json.dumps(check_plan))
        malformed_mode["review_mode"] = "oracle-fast"
        plan_path.write_text(json.dumps(malformed_mode), encoding="utf-8")
        try:
            validate_plan(plan_path, tasks_path)
        except PlanError as exc:
            assert str(exc).startswith("shape: review_mode"), str(exc)
        else:
            raise AssertionError("an unknown review mode must fail plan validation")
        malformed_mode_type = json.loads(json.dumps(check_plan))
        malformed_mode_type["review_mode"] = []
        plan_path.write_text(json.dumps(malformed_mode_type), encoding="utf-8")
        try:
            validate_plan(plan_path, tasks_path)
        except PlanError as exc:
            assert str(exc).startswith("shape: review_mode"), str(exc)
        else:
            raise AssertionError("a non-string review mode must fail plan validation")
        ineligible_combined = json.loads(json.dumps(check_plan))
        ineligible_combined["review_mode"] = "combined-single-lane"
        ineligible_combined["lanes"]["two"] = dict(ineligible_combined["lanes"]["one"])
        ineligible_combined["lanes"]["two"]["depends_on"] = []
        ineligible_combined["tasks"]["2.1"] = {"lane": "two", "deps": []}
        tasks_path.write_text("- [ ] 1.1 first\n- [ ] 2.1 second\n", encoding="utf-8")
        plan_path.write_text(json.dumps(ineligible_combined), encoding="utf-8")
        try:
            validate_plan(plan_path, tasks_path)
        except PlanError as exc:
            assert "exactly one ordinary-risk lane" in str(exc), str(exc)
        else:
            raise AssertionError("combined mode with multiple lanes must fail plan validation")
        ineligible_risk = json.loads(json.dumps(check_plan))
        ineligible_risk["review_mode"] = "combined-single-lane"
        ineligible_risk["lanes"]["one"]["risk"] = "high"
        tasks_path.write_text("- [ ] 1.1 first\n", encoding="utf-8")
        plan_path.write_text(json.dumps(ineligible_risk), encoding="utf-8")
        try:
            validate_plan(plan_path, tasks_path)
        except PlanError as exc:
            assert "exactly one ordinary-risk lane" in str(exc), str(exc)
        else:
            raise AssertionError("combined mode with a high-risk lane must fail plan validation")
        plan_path.write_text(json.dumps(check_plan), encoding="utf-8")
        tasks_path.write_text("- [ ] 1.1 first\n  - [ ] **Phase:** annotation\n", encoding="utf-8")
        validate_plan(plan_path, tasks_path)
        assert _dispatch_totals([{"tokens": None, "cost": None}]) == (None, None)
        assert _dispatch_totals([{"tokens": 12, "cost": "1.5"}]) == (12, 1.5)
        malformed = json.loads(json.dumps(check_plan))
        malformed["tasks"]["**Phase:"] = {"lane": "one", "deps": []}
        plan_path.write_text(json.dumps(malformed), encoding="utf-8")
        try:
            validate_plan(plan_path, tasks_path)
        except PlanError as exc:
            assert str(exc).startswith("task-id bijection:"), str(exc)
        else:
            raise AssertionError("invented nested task id must fail plan validation")
        malformed_sensitive = json.loads(json.dumps(check_plan))
        malformed_sensitive["lanes"]["one"]["sensitive"] = "yes"
        plan_path.write_text(json.dumps(malformed_sensitive), encoding="utf-8")
        try:
            validate_plan(plan_path, tasks_path)
        except PlanError as exc:
            assert str(exc).startswith("shape: lane 'one' sensitive"), str(exc)
        else:
            raise AssertionError("a non-boolean sensitive flag must fail plan validation")
        # Every gate-driving flag is required and must be a real boolean. A lane that omitted one
        # previously passed validation and then silently lost the review and human checkpoint it
        # drives, because the derived lane views coerce a missing flag to false.
        for required_flag in ("ui", "screen", "sensitive"):
            incomplete = json.loads(json.dumps(check_plan))
            del incomplete["lanes"]["one"][required_flag]
            plan_path.write_text(json.dumps(incomplete), encoding="utf-8")
            try:
                validate_plan(plan_path, tasks_path)
            except PlanError as exc:
                assert str(exc).startswith(f"shape: lane 'one' {required_flag}"), str(exc)
            else:
                raise AssertionError(f"a lane missing {required_flag} must fail plan validation")
        plan_path.write_text(json.dumps(check_plan), encoding="utf-8")
        validate_plan(plan_path, tasks_path)
        # A lane that declares none of the gate-driving flags requires no human gate. The
        # flags themselves are mandatory in the plan, which the shape cases above pin down.
        assert not _lane_requires_human_gate(
            {"risk": "ordinary", "ui": False, "screen": False, "sensitive": False}
        )

        # Derived state is rebuilt from committed artifacts, not a working-tree tick or a
        # legacy run file. Plan changes refresh the stamp instead of refusing the state.
        derived_plan = {
            "schema_version": 1,
            "change": "derived",
            "lanes": {
                "root": {"risk": "ordinary", "ui": False, "screen": False, "sensitive": False, "depends_on": []},
                "child": {"risk": "high", "ui": False, "screen": False, "sensitive": False, "depends_on": ["root"]},
            },
            "tasks": {
                "1.1": {"lane": "root", "deps": []},
                "2.1": {"lane": "child", "deps": ["1.1"]},
            },
        }
        derived_repository = make_repository(
            "derived-repository", "derived", derived_plan, "- [ ] 1.1 root\n- [ ] 2.1 child\n"
        )
        derived_state = state_derive("derived", checkout=derived_repository)
        derived_head = git(derived_repository, "rev-parse", "HEAD")
        assert derived_state["source_sha"] == derived_head
        assert "run_id" not in derived_state
        assert derived_state["checked_tasks"] == []
        assert derived_state["lanes"]["root"]["ready"]
        assert derived_state["lanes"]["child"]["readiness"].startswith("blocked:")
        assert derived_state["tasks"]["1.1"]["status"] == "pending"
        legacy = derived_repository / ".agents" / "tmp" / "featdev-old-run" / "state.json"
        _atomic_write_json(legacy, {"run_id": "old-run", "change": "derived", "checked_tasks": ["1.1"]})
        legacy_bytes = legacy.read_bytes()
        state_derive("derived", checkout=derived_repository)
        assert not (derived_repository / ".agents" / "workflow" / "derived" / "state.json").exists()
        assert legacy.exists(), "legacy scratch must not be selected or overwritten"
        assert legacy.read_bytes() == legacy_bytes, "legacy scratch must remain byte-identical"
        old_hash = derived_state["plan_sha256"]
        derived_tasks = derived_repository / "openspec" / "changes" / "derived" / "tasks.md"
        derived_tasks.write_text("- [x] 1.1 root\n- [ ] 2.1 child\n", encoding="utf-8")
        # Uncommitted ticks are not source truth.
        assert state_derive("derived", checkout=derived_repository)["checked_tasks"] == []
        derived_head = commit(derived_repository, "tick root")
        derived_state = state_derive("derived", checkout=derived_repository)
        assert derived_state["source_sha"] == derived_head
        assert derived_state["checked_tasks"] == ["1.1"]
        assert derived_state["lanes"]["root"]["complete"]
        assert derived_state["lanes"]["child"]["ready"]
        os.chdir(derived_repository)
        complete_result, complete_status, complete_error = invoke("status", "--change", "derived")
        assert complete_result == 0, complete_error
        assert "lane root: complete from committed ticks (coordination: created);" in complete_status
        os.chdir(root)
        derived_plan_path = derived_repository / "openspec" / "changes" / "derived" / "plan" / "graph-plan.json"
        derived_plan["lanes"]["root"]["screen"] = True
        derived_plan_path.write_text(json.dumps(derived_plan), encoding="utf-8")
        revised_head = commit(derived_repository, "revise plan")
        revised_state = state_derive("derived", checkout=derived_repository)
        assert revised_state["plan_sha256"] != old_hash
        assert revised_state["plan_revision"] == revised_head
        candidate_branch = git(derived_repository, "branch", "--show-current")
        assert candidate_branch == "main"
        git(derived_repository, "checkout", "-q", "-b", "candidate")
        derived_tasks.write_text("- [x] 1.1 root\n- [x] 2.1 child\n", encoding="utf-8")
        candidate_head = commit(derived_repository, "tick child")
        git(derived_repository, "checkout", "-q", "main")
        main_head = git(derived_repository, "rev-parse", "HEAD")
        candidate_state = state_derive("derived", "candidate", derived_repository)
        assert candidate_state["source_ref"] == "candidate"
        assert candidate_state["source_sha"] == candidate_head and candidate_head != main_head
        fresh_clone = root / "derived-fresh-clone"
        subprocess.run(["git", "clone", "-q", str(derived_repository), str(fresh_clone)], check=True)
        # A real clone has no scratch; make that precondition explicit even when a local
        # Git configuration copies ignored files during a test.
        import shutil
        shutil.rmtree(fresh_clone / ".agents", ignore_errors=True)
        assert state_derive("derived", checkout=fresh_clone)["source_sha"] == main_head
        assert not (fresh_clone / ".agents" / "tmp" / "featdev-old-run").exists()

        # Readiness is one computation for status, dashboard, and lane-open. A lane may
        # have one task ready while a later task waits on another lane.
        mixed_plan = {
            "schema_version": 1,
            "change": "mixed",
            "lanes": {
                "prep": {"risk": "ordinary", "ui": False, "screen": False, "sensitive": False, "depends_on": []},
                "work": {"risk": "ordinary", "ui": False, "screen": False, "sensitive": False, "depends_on": []},
            },
            "tasks": {
                "1.1": {"lane": "prep", "deps": []},
                "2.1": {"lane": "work", "deps": []},
                "2.2": {"lane": "work", "deps": ["1.1"]},
            },
        }
        mixed_repository = make_repository(
            "mixed-repository", "mixed", mixed_plan,
            "- [ ] 1.1 prep\n- [ ] 2.1 ready work\n- [ ] 2.2 blocked work\n",
        )
        os.chdir(mixed_repository)
        mixed_result, mixed_status, mixed_error = invoke("status", "--change", "mixed")
        assert mixed_result == 0, mixed_error
        assert "ready lanes: prep, work" in mixed_status
        assert "lane work:" in mixed_status
        assert "readiness ready: 2.1 has satisfied prerequisites; blocked: 2.2 needs 1.1" in mixed_status
        mixed_dashboard_path = root / "mixed-dashboard.html"
        mixed_result, _, mixed_error = invoke(
            "dashboard", "--change", "mixed", "--output", str(mixed_dashboard_path)
        )
        assert mixed_result == 0, mixed_error
        mixed_page = mixed_dashboard_path.read_text(encoding="utf-8")
        assert "ready: 2.1 has satisfied prerequisites; blocked: 2.2 needs 1.1" in mixed_page
        mixed_result, _, mixed_error = invoke("lane-open", "work", "--change", "mixed")
        assert mixed_result == 0, mixed_error
        mixed_coordination = _read_state(_coordination_path("mixed", mixed_repository))
        mixed_worktree = Path(mixed_coordination["lanes"]["work"]["worktree"])
        mixed_ref = mixed_coordination["lanes"]["work"]["ref"]
        subprocess.run(
            ["git", "-C", str(mixed_repository), "worktree", "remove", "--force", str(mixed_worktree)],
            check=True,
        )
        subprocess.run(["git", "-C", str(mixed_repository), "branch", "-D", mixed_ref], check=True)
        os.chdir(root)

        # A user-facing lane needs an interactive human gate bound to its current HEAD
        # and evidence revision. Internal-only lanes below still merge on automated reviews.
        human_plan = {
            "schema_version": 1,
            "change": "visible",
            "lanes": {
                "visible": {"risk": "ordinary", "ui": True, "screen": False, "sensitive": False, "depends_on": []},
            },
            "tasks": {"1.1": {"lane": "visible", "deps": []}},
        }
        human_repository = make_repository(
            "human-repository", "visible", human_plan, "- [ ] 1.1 visible work\n"
        )
        os.chdir(human_repository)
        human_result, _, human_error = invoke("lane-open", "visible", "--change", "visible")
        assert human_result == 0, human_error
        human_coordination_path = _coordination_path("visible", human_repository)
        human_coordination = _read_state(human_coordination_path)
        human_worktree = Path(human_coordination["lanes"]["visible"]["worktree"])
        subprocess.run(
            ["git", "-C", str(human_worktree), "commit", "--allow-empty", "-q", "-m", "visible code"],
            check=True,
        )
        task_tick("visible", "1.1", "visible")
        subprocess.run(
            ["git", "-C", str(human_worktree), "add", "openspec/changes/visible/tasks.md"], check=True
        )
        subprocess.run(
            ["git", "-C", str(human_worktree), "commit", "-q", "-m", "visible tick"], check=True
        )
        human_head = git(human_worktree, "rev-parse", "HEAD")
        human_code_report = root / "human-code-review.md"
        human_code_report.write_text("code review\n", encoding="utf-8")
        lane_review("visible", "code", "PASS", human_head, human_code_report, "human-r1", change="visible")
        human_ui_report = root / "human-ui-review.md"
        human_ui_report.write_text("ui review\n", encoding="utf-8")
        lane_review("visible", "ui", "PASS", human_head, human_ui_report, "human-r1", change="visible")
        expect_refusal("lane-merge", "visible", "--change", "visible", contains="human gate")
        human_result, _, human_error = invoke(
            "human-gate", "visible", "--state", "approved", "--commit", human_head,
            "--evidence-revision", "human-r1", "--change", "visible",
        )
        assert human_result == 0, human_error
        human_coordination = _read_state(human_coordination_path)
        human_coordination["human_gates"]["visible"]["state"] = "revoked"
        _atomic_write_json(human_coordination_path, human_coordination)
        expect_refusal("lane-merge", "visible", "--change", "visible", contains="human gate")
        human_gate("visible", "approved", human_head, "human-r1", change="visible")
        human_coordination = _read_state(human_coordination_path)
        human_coordination["human_gates"]["visible"]["revoked"] = True
        _atomic_write_json(human_coordination_path, human_coordination)
        expect_refusal("lane-merge", "visible", "--change", "visible", contains="human gate")
        human_gate("visible", "approved", human_head, "human-r1", change="visible")
        human_coordination = _read_state(human_coordination_path)
        human_coordination["human_gates"]["visible"]["stale"] = True
        _atomic_write_json(human_coordination_path, human_coordination)
        expect_refusal("lane-merge", "visible", "--change", "visible", contains="human gate")
        human_gate("visible", "approved", human_head, "human-r1", change="visible")
        human_coordination = _read_state(human_coordination_path)
        human_coordination["human_gates"]["visible"]["binding"]["commit"] = "0" * 40
        _atomic_write_json(human_coordination_path, human_coordination)
        expect_refusal("lane-merge", "visible", "--change", "visible", contains="human gate")
        human_gate("visible", "approved", human_head, "human-r1", change="visible")
        human_coordination = _read_state(human_coordination_path)
        del human_coordination["human_gates"]["visible"]["binding"]
        _atomic_write_json(human_coordination_path, human_coordination)
        expect_refusal("lane-merge", "visible", "--change", "visible", contains="human gate")
        human_gate("visible", "approved", human_head, "human-r1", change="visible")
        human_coordination = _read_state(human_coordination_path)
        human_coordination["human_gates"]["visible"]["authorization_id"] = "grant"
        _atomic_write_json(human_coordination_path, human_coordination)
        expect_refusal("lane-merge", "visible", "--change", "visible", contains="cannot be satisfied by authorization")
        human_coordination["human_gates"] = []
        _atomic_write_json(human_coordination_path, human_coordination)
        expect_refusal("lane-merge", "visible", "--change", "visible", contains="human gate")
        human_coordination["human_gates"] = {}
        _atomic_write_json(human_coordination_path, human_coordination)
        human_gate("visible", "approved", human_head, "human-r1", change="visible")
        lane_merge("visible", "visible")
        lane_close("visible", "visible")
        human_coordination_path.write_text("{bad json", encoding="utf-8")
        malformed_result, malformed_status, malformed_error = invoke("status", "--change", "visible")
        assert malformed_result == 0, malformed_error
        assert "open dispatch intents: unavailable" in malformed_status, malformed_status
        malformed_dashboard_path = root / "visible-unavailable-dashboard.html"
        malformed_result, _, malformed_error = invoke(
            "dashboard", "--change", "visible", "--output", str(malformed_dashboard_path)
        )
        assert malformed_result == 0, malformed_error
        assert "Dispatch view unavailable" in malformed_dashboard_path.read_text(encoding="utf-8")
        os.chdir(root)

        # A lane that touches user-facing wording, safety, privacy or medical material needs
        # the interactive human gate even when it changes no user-facing appearance. The plan
        # declares that, so the gate cannot be inferred away, and the flag alone turns it on.
        sensitive_plan = {
            "schema_version": 1,
            "change": "sensitive",
            "lanes": {
                "safety": {"risk": "ordinary", "ui": False, "screen": False,
                           "sensitive": True, "depends_on": []},
                "screening": {"risk": "ordinary", "ui": False, "screen": True, "sensitive": False, "depends_on": []},
            },
            "tasks": {"1.1": {"lane": "safety", "deps": []},
                      "1.2": {"lane": "screening", "deps": []}},
        }
        sensitive_repository = make_repository(
            "sensitive-repository", "sensitive", sensitive_plan,
            "- [ ] 1.1 safety wording\n- [ ] 1.2 screening evidence\n",
        )
        os.chdir(sensitive_repository)
        sensitive_open, _, sensitive_open_error = invoke("lane-open", "safety", "--change", "sensitive")
        assert sensitive_open == 0, sensitive_open_error
        sensitive_coordination = _read_state(_coordination_path("sensitive", sensitive_repository))
        sensitive_worktree = Path(sensitive_coordination["lanes"]["safety"]["worktree"])
        subprocess.run(
            ["git", "-C", str(sensitive_worktree), "commit", "--allow-empty", "-q", "-m", "safety wording"],
            check=True,
        )
        (sensitive_worktree / "openspec" / "changes" / "sensitive" / "tasks.md").write_text(
            "- [x] 1.1 safety wording\n- [ ] 1.2 screening evidence\n", encoding="utf-8"
        )
        subprocess.run(["git", "-C", str(sensitive_worktree), "add", "."], check=True)
        subprocess.run(["git", "-C", str(sensitive_worktree), "commit", "-q", "-m", "tick 1.1"], check=True)
        sensitive_head = git(sensitive_worktree, "rev-parse", "HEAD")
        sensitive_report = root / "sensitive-code-review.md"
        sensitive_report.write_text("code review\n", encoding="utf-8")
        lane_review(
            "safety", "code", "PASS", sensitive_head, sensitive_report, "sensitive-r1", change="sensitive"
        )
        # A screen lane is the only lane that can reach the screen-lane refusal branch, because it
        # is the only one that needs an evidence file to hold an approval at all. It starts from a
        # valid evidence file; the matrix below removes the binding's evidence path to refuse it.
        screening_open, _, screening_open_error = invoke("lane-open", "screening", "--change", "sensitive")
        assert screening_open == 0, screening_open_error
        screening_coordination = _read_state(_coordination_path("sensitive", sensitive_repository))
        screening_worktree = Path(screening_coordination["lanes"]["screening"]["worktree"])
        subprocess.run(
            ["git", "-C", str(screening_worktree), "commit", "--allow-empty", "-q",
             "-m", "screening evidence"],
            check=True,
        )
        (screening_worktree / "openspec" / "changes" / "sensitive" / "tasks.md").write_text(
            "- [ ] 1.1 safety wording\n- [x] 1.2 screening evidence\n", encoding="utf-8"
        )
        subprocess.run(["git", "-C", str(screening_worktree), "add", "."], check=True)
        subprocess.run(["git", "-C", str(screening_worktree), "commit", "-q", "-m", "tick 1.2"], check=True)
        screening_head = git(screening_worktree, "rev-parse", "HEAD")
        screening_evidence = root / "screening-evidence.txt"
        screening_evidence.write_text("screening evidence\n", encoding="utf-8")
        screening_report = root / "screening-code-review.md"
        screening_report.write_text("code review\n", encoding="utf-8")
        lane_review(
            "screening", "code", "PASS", screening_head, screening_report, "sensitive-r1",
            screening_evidence, change="sensitive",
        )
        # Every refusal branch must render the same answer as the merge validator. The expected
        # text comes from enforcement, never the summary that renders it, so a false approval
        # cannot pass simply because this fixture happens not to sample its invalid gate state.
        sensitive_state = _read_state(state_derive("sensitive", checkout=sensitive_repository))
        sensitive_lanes = {
            "safety": {"flag": "sensitive", "head": sensitive_head, "evidence": None},
            "screening": {"flag": "screen", "head": screening_head, "evidence": screening_evidence},
        }

        def reset_sensitive_gate(lane_id: str) -> None:
            coordination = _read_state(_coordination_path("sensitive", sensitive_repository))
            if not isinstance(coordination.get("human_gates"), dict):
                coordination["human_gates"] = {}
                _write_coordination(sensitive_state, coordination, sensitive_plan)
            entry = sensitive_lanes[lane_id]
            human_gate(
                lane_id, "approved", entry["head"], "sensitive-r1", entry["evidence"],
                change="sensitive",
            )

        invalid_sensitive_gates = [
            ("safety", "missing", lambda coordination: coordination["human_gates"].pop("safety")),
            ("safety", "stale", lambda coordination: coordination["human_gates"]["safety"].__setitem__("stale", True)),
            ("safety", "revoked", lambda coordination: coordination["human_gates"]["safety"].__setitem__("revoked", True)),
            ("safety", "wrong-lane", lambda coordination: coordination["human_gates"]["safety"].__setitem__("lane", "other")),
            ("safety", "unapproved-state", lambda coordination: coordination["human_gates"]["safety"].__setitem__("state", "revoked")),
            ("safety", "non-human", lambda coordination: coordination["human_gates"]["safety"].__setitem__("approved_by", "service")),
            ("safety", "authorization", lambda coordination: coordination["human_gates"]["safety"].__setitem__("authorization", "grant")),
            ("safety", "missing-binding", lambda coordination: coordination["human_gates"]["safety"].pop("binding")),
            ("safety", "wrong-commit", lambda coordination: coordination["human_gates"]["safety"]["binding"].__setitem__("commit", "0" * 40)),
            ("safety", "missing-evidence-revision", lambda coordination: coordination["human_gates"]["safety"]["binding"].pop("evidence_revision")),
            ("safety", "missing-evidence", lambda coordination: coordination["human_gates"]["safety"]["binding"].__setitem__("evidence_path", str(root / "missing-evidence"))),
            ("safety", "malformed-evidence-path", lambda coordination: coordination["human_gates"]["safety"]["binding"].__setitem__("evidence_path", [])),
            ("safety", "malformed-gate", lambda coordination: coordination["human_gates"].__setitem__("safety", [])),
            ("safety", "malformed-gates", lambda coordination: coordination.__setitem__("human_gates", [])),
            ("screening", "screen-without-evidence", lambda coordination: coordination["human_gates"]["screening"]["binding"].__setitem__("evidence_path", None)),
        ]
        for lane_id, case_name, invalidate in invalid_sensitive_gates:
            flag = sensitive_lanes[lane_id]["flag"]
            false_approval = f"required ({flag}): approved"
            reset_sensitive_gate(lane_id)
            sensitive_coordination = _read_state(_coordination_path("sensitive", sensitive_repository))
            invalidate(sensitive_coordination)
            _write_coordination(sensitive_state, sensitive_coordination, sensitive_plan)
            try:
                _validate_human_gate(
                    lane_id, sensitive_state["lanes"][lane_id], sensitive_lanes[lane_id]["head"],
                    sensitive_coordination,
                )
            except StateError as exc:
                expected_refusal = _one_line(exc)
            else:
                raise AssertionError(f"a {case_name} {lane_id} approval must be refused")
            assert sensitive_lanes[lane_id]["flag"] == _human_gate_flag(
                sensitive_state["lanes"][lane_id]
            ), case_name
            expected_blocked = f"required ({flag}); blocked: {expected_refusal}"
            sensitive_status_result, sensitive_status, sensitive_status_error = invoke(
                "status", "--change", "sensitive"
            )
            assert sensitive_status_result == 0, sensitive_status_error
            assert expected_blocked in sensitive_status, (case_name, lane_id, sensitive_status)
            assert false_approval not in sensitive_status, (case_name, lane_id, sensitive_status)
            sensitive_dashboard = root / f"sensitive-{case_name}-dashboard.html"
            sensitive_dashboard_result, _, sensitive_dashboard_error = invoke(
                "dashboard", "--change", "sensitive", "--output", str(sensitive_dashboard)
            )
            assert sensitive_dashboard_result == 0, sensitive_dashboard_error
            sensitive_page = sensitive_dashboard.read_text(encoding="utf-8")
            assert "<th>Human gate</th>" in sensitive_page, sensitive_page
            assert html.escape(expected_blocked, quote=True) in sensitive_page, (case_name, lane_id)
            assert false_approval not in sensitive_page, (case_name, lane_id)
            expect_refusal("lane-merge", lane_id, "--change", "sensitive", contains=expected_refusal)
            reset_sensitive_gate(lane_id)
        # The fresh approval after every refused state permits the successful merge path.
        reset_sensitive_gate("safety")
        lane_merge("safety", "sensitive")
        sensitive_state = _read_state(state_derive("sensitive", checkout=sensitive_repository))
        assert sensitive_state["lanes"]["safety"]["complete"], sensitive_state["lanes"]["safety"]
        os.chdir(root)

        # Deriving refreshes the rendered dashboard through the same best-effort path the
        # coordination-writing commands use, so a changed state reaches the page a reader
        # opens without anyone running a separate render command.
        refresh_plan = {
            "schema_version": 1,
            "change": "refresh",
            "lanes": {"only": {"risk": "ordinary", "ui": False, "screen": False, "sensitive": False, "depends_on": []}},
            "tasks": {"1.1": {"lane": "only", "deps": []}},
        }
        refresh_repository = make_repository(
            "refresh-repository", "refresh", refresh_plan, "- [ ] 1.1 refresh work\n"
        )
        refresh_dashboard = workflow_dir(refresh_repository) / "refresh" / "dashboard.html"
        if refresh_dashboard.exists():
            refresh_dashboard.unlink()
        os.chdir(refresh_repository)
        try:
            dashboard("refresh", refresh_dashboard)
        finally:
            os.chdir(root)
        assert refresh_dashboard.is_file(), "the dashboard command must render the page"
        pending_page = refresh_dashboard.read_text(encoding="utf-8")
        assert "1.1" in pending_page and "pending" in pending_page, pending_page
        (refresh_repository / "openspec" / "changes" / "refresh" / "tasks.md").write_text(
            "- [x] 1.1 refresh work\n", encoding="utf-8"
        )
        commit(refresh_repository, "refresh tick")
        os.chdir(refresh_repository)
        try:
            dashboard("refresh", refresh_dashboard)
        finally:
            os.chdir(root)
        refreshed_page = refresh_dashboard.read_text(encoding="utf-8")
        assert refreshed_page != pending_page, "a changed state must reach the rendered dashboard"
        assert "complete" in refreshed_page, refreshed_page

        # New CLI surface: no initializer, per-task record, human-record command, or legacy
        # decision command. Unknown command names fail at parser level and are absent from help.
        help_result, help_out, _ = invoke("--help")
        assert help_result == 0
        def _lists_command(help_text: str, name: str) -> bool:
            return any(line.split()[0].lstrip(" ") == name for line in help_out.splitlines() if line.strip())
        for obsolete in ("run-init", "task-done", "approval", "decision"):
            assert not _lists_command(help_out, obsolete), obsolete
            obsolete_result, _, _ = invoke(obsolete)
            assert obsolete_result == 2, obsolete

        # End-to-end flow starts without any scratch state or run identifier. The first
        # status call derives state automatically, then all lane coordination lives beside
        # that projection and never becomes task truth.
        flow_plan = {
            "schema_version": 1,
            "change": "flow",
            "lanes": {
                "a": {"risk": "ordinary", "ui": False, "screen": False, "sensitive": False, "depends_on": []},
                "b": {"risk": "ordinary", "ui": False, "screen": False, "sensitive": False, "depends_on": ["a"]},
                "c": {"risk": "ordinary", "ui": False, "screen": False, "sensitive": False, "depends_on": ["b"]},
            },
            "tasks": {
                "1.1": {"lane": "a", "deps": []},
                "2.1": {"lane": "b", "deps": ["1.1"]},
                "3.1": {"lane": "c", "deps": ["2.1"]},
            },
        }
        flow_repository = make_repository(
            "flow-repository", "flow", flow_plan,
            "- [ ] 1.1 lane a\n- [ ] 2.1 lane b\n- [ ] 3.1 lane c\n",
        )
        os.chdir(flow_repository)
        assert not (flow_repository / ".agents" / "tmp").exists()
        result, status_output, status_error = invoke("status", "--change", "flow")
        assert result == 0, status_error
        assert "change: flow" in status_output and "ready lanes: a" in status_output
        flow_state_path = state_derive("flow", checkout=flow_repository)
        flow_state = _read_state(flow_state_path)
        assert "run_id" not in flow_state
        result, _, stderr = invoke("lane-open", "a", "--change", "flow")
        assert result == 0, stderr
        opening_result, opening_status, opening_error = invoke("status", "--change", "flow")
        assert opening_result == 0, opening_error
        assert "lane a: writing;" in opening_status, opening_status
        assert "lane a: reviewing;" not in opening_status, opening_status
        coordination_path = _coordination_path("flow", flow_repository)
        coordination = _read_state(coordination_path)
        assert "run_id" not in coordination
        a_worktree = Path(coordination["lanes"]["a"]["worktree"])
        a_ref = coordination["lanes"]["a"]["ref"]
        # A dependent lane cannot open before its prerequisite merges. This is a
        # separate readiness gate, not just a display assertion.
        expect_refusal("lane-open", "b", "--change", "flow", contains="not ready")
        subprocess.run(["git", "-C", str(a_worktree), "commit", "--allow-empty", "-q", "-m", "a code"], check=True)
        task_tick("a", "1.1", "flow")
        subprocess.run(["git", "-C", str(a_worktree), "add", "openspec/changes/flow/tasks.md"], check=True)
        subprocess.run(["git", "-C", str(a_worktree), "commit", "-q", "-m", "a tick"], check=True)
        a_head = git(a_worktree, "rev-parse", "HEAD")
        phase_result, phase_status, phase_error = invoke("status", "--change", "flow")
        assert phase_result == 0, phase_error
        assert "lane a: reviewing;" in phase_status, phase_status
        report = root / "review.md"
        report.write_text("PASS\n", encoding="utf-8")
        lane_review("a", "code", "PASS", a_head, report, "flow-r1", change="flow")
        coordination = _read_state(coordination_path)
        assert coordination["lanes"]["a"]["reviews"][0]["reviewed_commit"] == a_head
        lane_merge("a", "flow")
        lane_close("a", "flow")
        result, status_output, status_error = invoke("status", "--change", "flow")
        assert result == 0, status_error
        assert "ready lanes: b" in status_output
        assert "lane dependencies a" in status_output
        assert "2.1=pending" in status_output
        assert git(flow_repository, "rev-parse", "HEAD")
        assert (flow_repository / "openspec/changes/flow/tasks.md").read_text(encoding="utf-8").startswith("- [x] 1.1")

        # A second lane is opened after the first merge. Its readiness and completion come
        # from committed ticks; dispatch and review records are only temporary coordination.
        result, _, stderr = invoke("lane-open", "b", "--change", "flow")
        assert result == 0, stderr
        coordination = _read_state(coordination_path)
        b_worktree = Path(coordination["lanes"]["b"]["worktree"])
        b_ref = coordination["lanes"]["b"]["ref"]
        dispatch_run("b", "code", "local/model", "normal", "2.1", change="flow")
        assert _read_state(coordination_path)["lanes"]["b"]["dispatches"][0]["access"] == "read-only"
        subprocess.run(["git", "-C", str(b_worktree), "commit", "--allow-empty", "-q", "-m", "b code"], check=True)
        dispatch_result, dispatch_status, dispatch_error = invoke("status", "--change", "flow")
        assert dispatch_result == 0, dispatch_error
        assert "open dispatch intents:" in dispatch_status
        assert "b/code" in dispatch_status and "worker=local/model" in dispatch_status
        assert "task=2.1" in dispatch_status and "key=flow/b/code" in dispatch_status
        assert "since=" in dispatch_status and "status=stale" in dispatch_status
        dispatch_dashboard_path = root / "flow-dispatch-dashboard.html"
        dispatch_result, _, dispatch_error = invoke(
            "dashboard", "--change", "flow", "--output", str(dispatch_dashboard_path)
        )
        assert dispatch_result == 0, dispatch_error
        dispatch_page = dispatch_dashboard_path.read_text(encoding="utf-8")
        assert "Dispatch view unavailable" not in dispatch_page
        assert "b/code" in dispatch_page and "worker=local/model" in dispatch_page
        assert "task=2.1" in dispatch_page and "status=stale" in dispatch_page
        dispatch_done("b", "code", "worker-1", 12, "0.5", "ok", "flow")
        b_tasks_path = b_worktree / "openspec" / "changes" / "flow" / "tasks.md"
        b_tasks_original = b_tasks_path.read_text(encoding="utf-8")
        b_tasks_path.write_text(
            b_tasks_original.replace("- [ ] 2.1 lane b", "- [ ] 2.1-forged lane b"), encoding="utf-8"
        )
        expect_refusal("task-tick", "b", "2.1", "--change", "flow", contains="task-id bijection")
        b_tasks_path.write_text(b_tasks_original + "- [ ] 2.1 duplicate\n", encoding="utf-8")
        expect_refusal("task-tick", "b", "2.1", "--change", "flow", contains="task-id duplicates")
        b_tasks_path.write_text(b_tasks_original, encoding="utf-8")
        expect_refusal("task-tick", "b", "1.1", "--change", "flow", contains="belongs to lane")
        assert b_tasks_path.read_text(encoding="utf-8") == b_tasks_original
        task_tick("b", "2.1", "flow")
        subprocess.run(["git", "-C", str(b_worktree), "add", "openspec/changes/flow/tasks.md"], check=True)
        subprocess.run(["git", "-C", str(b_worktree), "commit", "-q", "-m", "b tick"], check=True)
        b_head = git(b_worktree, "rev-parse", "HEAD")
        # Lane-ref ticks are display-only: they cannot complete b or unlock c before merge.
        inflight_state = _read_state(state_derive("flow", checkout=flow_repository))
        assert inflight_state["tasks"]["2.1"]["status"] == "pending", inflight_state
        assert not inflight_state["lanes"]["b"]["complete"], inflight_state
        expect_refusal("lane-open", "c", "--change", "flow", contains="not ready")
        phase_result, phase_status, phase_error = invoke("status", "--change", "flow")
        assert phase_result == 0, phase_error
        assert "lane b: reviewing;" in phase_status, phase_status
        inflight_dashboard_path = root / "flow-inflight-dashboard.html"
        inflight_result, _, inflight_error = invoke(
            "dashboard", "--change", "flow", "--output", str(inflight_dashboard_path)
        )
        assert inflight_result == 0, inflight_error
        inflight_page = inflight_dashboard_path.read_text(encoding="utf-8")
        assert "Tasks from committed ticks" in inflight_page
        assert "In-flight task ticks (not on feature branch)" in inflight_page
        inflight_lane_section = inflight_page.split(
            "<section><h2>Lanes and assigned task status</h2>", 1
        )[1].split("</section>", 1)[0]
        inflight_lane_match = re.search(
            r'<tr><th scope="row">b</th>.*?</tr>', inflight_lane_section, re.S
        )
        assert inflight_lane_match, inflight_lane_section
        inflight_lane_row = inflight_lane_match.group(0)
        assert "0/1" in inflight_lane_row and "2.1: pending" in inflight_lane_row
        assert "in-flight only (lane ref; not on feature branch): 1/1" in inflight_lane_row
        assert "2.1: complete" in inflight_lane_row
        lane_review("b", "code", "PASS", b_head, report, "flow-r1", change="flow")
        report.unlink()
        expect_refusal("lane-merge", "b", "--change", "flow", contains="report is missing")
        replacement_report = root / "replacement-review.md"
        replacement_report.write_text("replacement review\n", encoding="utf-8")
        lane_review("b", "code", "PASS", b_head, replacement_report, "flow-r2", change="flow")
        before_invalid_merge = git(flow_repository, "rev-parse", "HEAD")
        b_tasks_path.write_text(
            b_tasks_path.read_text(encoding="utf-8").replace("- [x] 1.1 lane a", "- [ ] 1.1 lane a"),
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(b_worktree), "add", "openspec/changes/flow/tasks.md"], check=True)
        subprocess.run(["git", "-C", str(b_worktree), "commit", "-q", "-m", "invalid removed tick"], check=True)
        invalid_head = git(b_worktree, "rev-parse", "HEAD")
        lane_review("b", "code", "PASS", invalid_head, replacement_report, "flow-invalid", change="flow")
        expect_refusal("lane-merge", "b", "--change", "flow", contains="removed checked tasks")
        assert git(flow_repository, "rev-parse", "HEAD") == before_invalid_merge
        subprocess.run(["git", "-C", str(b_worktree), "reset", "--hard", b_head], check=True)
        before_bad_state_merge = git(flow_repository, "rev-parse", "HEAD")
        real_state_derive = globals()["state_derive"]

        def corrupt_candidate_state(change_name, source_ref=None, checkout=None):
            candidate_state = real_state_derive(change_name, source_ref, checkout)
            if change_name == "flow" and source_ref == b_head:
                return {**candidate_state, "source_sha": "0" * 40}
            return candidate_state

        globals()["state_derive"] = corrupt_candidate_state
        try:
            expect_refusal("lane-merge", "b", "--change", "flow", contains="candidate state source SHA")
        finally:
            globals()["state_derive"] = real_state_derive
            real_state_derive("flow", checkout=flow_repository)
        assert git(flow_repository, "rev-parse", "HEAD") == before_bad_state_merge
        # Losing active coordination must not fabricate a review or completion. The
        # deterministic ref/worktree is recoverable, but the review gate is closed.
        coordination_path.unlink()
        unavailable_result, unavailable_status, unavailable_error = invoke("status", "--change", "flow")
        assert unavailable_result == 0, unavailable_error
        assert "open dispatch intents: unavailable" in unavailable_status, unavailable_status
        unavailable_dashboard_path = root / "flow-unavailable-dashboard.html"
        unavailable_result, _, unavailable_error = invoke(
            "dashboard", "--change", "flow", "--output", str(unavailable_dashboard_path)
        )
        assert unavailable_result == 0, unavailable_error
        unavailable_page = unavailable_dashboard_path.read_text(encoding="utf-8")
        assert "Dispatch view unavailable" in unavailable_page, unavailable_page
        state_derive("flow", checkout=flow_repository)
        current_flow_head = git(flow_repository, "rev-parse", "HEAD")
        result, stale_status, stale_error = invoke("status", "--change", "flow")
        assert result == 0, stale_error
        assert f"source: {current_flow_head}\n" in stale_status, stale_status
        # There is no persisted derived state to go stale: the store is coordination.json only.
        assert not (workflow_dir(flow_repository) / "flow" / "state.json").exists()
        expect_refusal("lane-merge", "b", "--change", "flow", contains="required review 'code'")
        lane_review("b", "code", "PASS", b_head, replacement_report, "flow-r1", change="flow")
        # An unfinished dispatch on a terminal lane is stale before any worktree-head
        # lookup. The lookup trap catches a refactor that gets the label right while
        # still consulting a stale or reused terminal worktree.
        dispatch_run("b", "test", "local/model", "normal", "2.1", change="flow")
        lane_merge("b", "flow")
        def terminal_dispatches_without_head_lookup():
            real_lane_current_head = globals()["_lane_current_head"]

            def forbid_terminal_head_lookup(lane):
                if lane.get("phase") in {"merged", "closed"}:
                    raise AssertionError("terminal dispatch must not resolve a worktree head")
                return real_lane_current_head(lane)

            globals()["_lane_current_head"] = forbid_terminal_head_lookup
            try:
                _, dispatch_state, _ = _state_context("flow")
                return _open_dispatches(dispatch_state)
            finally:
                globals()["_lane_current_head"] = real_lane_current_head

        merged_dispatches = terminal_dispatches_without_head_lookup()
        assert any(
            item["lane"] == "b" and item["role"] == "test"
            and item["status"] == "stale" and item["current_head"] is None
            for item in merged_dispatches
        ), merged_dispatches
        merged_result, merged_status, merged_error = invoke("status", "--change", "flow")
        assert merged_result == 0, merged_error
        assert "b/test" in merged_status and "status=stale" in merged_status, merged_status
        assert "current-head=unavailable" in merged_status, merged_status
        merged_dispatch_dashboard = root / "flow-merged-dispatch-dashboard.html"
        merged_result, _, merged_error = invoke(
            "dashboard", "--change", "flow", "--output", str(merged_dispatch_dashboard)
        )
        assert merged_result == 0, merged_error
        merged_dispatch_page = merged_dispatch_dashboard.read_text(encoding="utf-8")
        assert "b/test" in merged_dispatch_page and "status=stale" in merged_dispatch_page
        assert "current-head=unavailable" in merged_dispatch_page
        # A recorded lane worktree stays authoritative at close: shifting the
        # root environment between open and close must neither redirect cleanup
        # to the recomputed path nor fail determinism (matches
        # _lane_runtime_for_operation).
        _, pre_close_state, pre_close_plan = _state_context("flow")
        _, b_runtime = _coordination_lane_for(pre_close_state, pre_close_plan, "b")
        b_recorded_path = Path(b_runtime["worktree"]).expanduser().resolve()
        b_recorded_ref = b_runtime["ref"]
        assert b_recorded_path.is_dir(), b_recorded_path
        os.environ["FEATDEV_LANE_WORKTREE_ROOT"] = str(root / "shifted-lane-root")
        lane_close("b", "flow")
        os.environ.pop("FEATDEV_LANE_WORKTREE_ROOT", None)
        assert not b_recorded_path.exists(), b_recorded_path
        assert not _ref_sha(flow_repository, b_recorded_ref), b_recorded_ref
        closed_coordination = _read_state(_coordination_path("flow", flow_repository))
        assert closed_coordination["lanes"]["b"]["phase"] == "closed"
        closed_dispatches = terminal_dispatches_without_head_lookup()
        assert any(
            item["lane"] == "b" and item["role"] == "test"
            and item["status"] == "stale" and item["current_head"] is None
            for item in closed_dispatches
        ), closed_dispatches
        closed_result, closed_status, closed_error = invoke("status", "--change", "flow")
        assert closed_result == 0, closed_error
        assert "b/test" in closed_status and "status=stale" in closed_status, closed_status
        assert "current-head=unavailable" in closed_status, closed_status
        closed_dispatch_dashboard = root / "flow-closed-dispatch-dashboard.html"
        closed_result, _, closed_error = invoke(
            "dashboard", "--change", "flow", "--output", str(closed_dispatch_dashboard)
        )
        assert closed_result == 0, closed_error
        closed_dispatch_page = closed_dispatch_dashboard.read_text(encoding="utf-8")
        assert "b/test" in closed_dispatch_page and "status=stale" in closed_dispatch_page
        assert "current-head=unavailable" in closed_dispatch_page
        assert git(flow_repository, "rev-parse", "HEAD")
        final_state = _read_state(state_derive("flow", checkout=flow_repository))
        assert set(final_state["checked_tasks"]) == {"1.1", "2.1"}
        assert "coordination_path" not in final_state
        assert all("reviews" not in lane and "dispatches" not in lane for lane in final_state["lanes"].values())
        assert final_state["lanes"]["a"]["complete"] and final_state["lanes"]["b"]["complete"]
        assert not final_state["lanes"]["c"]["complete"]
        assert final_state["ready_lanes"] == ["c"]
        assert "run_id" not in final_state
        status_result, status_output, status_error = invoke("status", "--change", "flow")
        assert status_result == 0, status_error
        assert "tasks 1/1" in status_output and "complete: every assigned task is ticked" in status_output
        dashboard_path = root / "flow-dashboard.html"
        dashboard_result, _, dashboard_error = invoke(
            "dashboard", "--change", "flow", "--output", str(dashboard_path)
        )
        assert dashboard_result == 0, dashboard_error
        dashboard_page = dashboard_path.read_text(encoding="utf-8")
        for marker in (
            "Lanes and assigned task status", "Coordination phase", "Tasks and task dependencies", "Lane dependency graph",
            "Task dependency graph", "1.1", "2.1", "complete from committed ticks", "never task truth",
        ):
            assert marker in dashboard_page, marker
        lane_section = dashboard_page.split(
            "<section><h2>Lanes and assigned task status</h2>", 1
        )[1].split("</section>", 1)[0]
        for lane_id, task_ids, lane_dependency in (
            ("a", ["1.1"], "none"),
            ("b", ["2.1"], "a"),
        ):
            lane_match = re.search(
                rf'<tr><th scope="row">{re.escape(lane_id)}</th>.*?</tr>', lane_section, re.S
            )
            assert lane_match, lane_id
            lane_row = lane_match.group(0)
            for task_id in task_ids:
                assert f"{task_id}: complete" in lane_row, (lane_id, task_id, lane_row)
            assert lane_dependency in lane_row, (lane_id, lane_row)
        task_section = dashboard_page.split(
            "<section><h2>Tasks and task dependencies</h2>", 1
        )[1].split("</section>", 1)[0]
        for task_id, lane_id, dependency in (("1.1", "a", "none"), ("2.1", "b", "1.1")):
            task_match = re.search(
                rf'<tr><th scope="row">{re.escape(task_id)}</th>.*?</tr>', task_section, re.S
            )
            assert task_match, task_id
            task_row = task_match.group(0)
            assert lane_id in task_row and "complete" in task_row and dependency in task_row, task_row
        assert not any(
            key in dashboard_page for key in ("Approvals queue", "Decisions", "run-id")
        )
        assert a_ref.startswith("featdev/flow-") and b_ref.startswith("featdev/flow-")

        # Two independent lanes opened before either merge retain the common base. This
        # catches using the feature tip as the second lane's base.
        parallel_plan = {
            "schema_version": 1,
            "change": "parallel",
            "lanes": {
                "a": {"risk": "ordinary", "ui": False, "screen": False, "sensitive": False, "depends_on": []},
                "b": {"risk": "ordinary", "ui": False, "screen": False, "sensitive": False, "depends_on": []},
            },
            "tasks": {
                "1.1": {"lane": "a", "deps": []},
                "2.1": {"lane": "b", "deps": []},
            },
        }
        parallel_repository = make_repository(
            "parallel-repository", "parallel", parallel_plan,
            "- [ ] 1.1 lane a\n\n\n\n- [ ] 2.1 lane b\n",
        )
        os.chdir(parallel_repository)
        state_derive("parallel", checkout=parallel_repository)
        lane_open("a", "parallel")
        lane_open("b", "parallel")
        parallel_coord_path = _coordination_path("parallel", parallel_repository)
        parallel_coord = _read_state(parallel_coord_path)
        parallel_a = Path(parallel_coord["lanes"]["a"]["worktree"])
        parallel_b = Path(parallel_coord["lanes"]["b"]["worktree"])
        parallel_base = git(parallel_repository, "rev-parse", "HEAD")
        for lane_id, worktree, task_id in (("a", parallel_a, "1.1"), ("b", parallel_b, "2.1")):
            subprocess.run(["git", "-C", str(worktree), "commit", "--allow-empty", "-q", "-m", f"{lane_id} code"], check=True)
            task_tick(lane_id, task_id, "parallel")
            subprocess.run(["git", "-C", str(worktree), "add", "openspec/changes/parallel/tasks.md"], check=True)
            subprocess.run(["git", "-C", str(worktree), "commit", "-q", "-m", f"{lane_id} tick"], check=True)
        parallel_report = root / "parallel-review.md"
        parallel_report.write_text("review\n", encoding="utf-8")
        for lane_id, worktree in (("a", parallel_a), ("b", parallel_b)):
            head = git(worktree, "rev-parse", "HEAD")
            lane_review(lane_id, "code", "PASS", head, parallel_report, "r1", change="parallel")
        lane_merge("a", "parallel")
        parallel_coord = _read_state(parallel_coord_path)
        b_base = _lane_merge_base(
            parallel_repository, parallel_coord["lanes"]["b"]["ref"], git(parallel_repository, "rev-parse", "HEAD")
        )
        assert b_base == parallel_base, (b_base, parallel_base)
        lane_merge("b", "parallel")
        lane_close("a", "parallel")
        lane_close("b", "parallel")
        os.chdir(root)

        # The approved planning commit is the contract, and a lane owns only its own ticks. A
        # lane that rewrites the plan, or rewords a task while keeping its identifier and ticks,
        # must not be able to merge that unapproved contract into the feature branch.
        contract_plan = {
            "schema_version": 1,
            "change": "contract",
            "lanes": {
                "only": {"risk": "ordinary", "ui": False, "screen": False,
                         "sensitive": False, "depends_on": []},
            },
            "tasks": {"1.1": {"lane": "only", "deps": []}},
        }
        contract_repository = make_repository(
            "contract-repository", "contract", contract_plan, "- [ ] 1.1 contract work\n"
        )
        os.chdir(contract_repository)
        lane_open("only", "contract")
        contract_coordination = _read_state(_coordination_path("contract", contract_repository))
        contract_worktree = Path(contract_coordination["lanes"]["only"]["worktree"])
        contract_plan_path = contract_worktree / "openspec" / "changes" / "contract" / "plan" / "graph-plan.json"
        contract_tasks_path = contract_worktree / "openspec" / "changes" / "contract" / "tasks.md"
        subprocess.run(
            ["git", "-C", str(contract_worktree), "commit", "--allow-empty", "-q", "-m", "contract code"],
            check=True,
        )
        task_tick("only", "1.1", "contract")
        subprocess.run(
            ["git", "-C", str(contract_worktree), "add", "openspec/changes/contract/tasks.md"], check=True
        )
        subprocess.run(
            ["git", "-C", str(contract_worktree), "commit", "-q", "-m", "contract tick"], check=True
        )
        contract_head = git(contract_worktree, "rev-parse", "HEAD")
        contract_report = root / "contract-review.md"
        contract_report.write_text("code review\n", encoding="utf-8")
        lane_review("only", "code", "PASS", contract_head, contract_report, "contract-r1", change="contract")
        # A lane-authored plan change is refused even while its ticks stay valid, and the
        # review is re-bound to the tampered HEAD so the plan guard is what refuses the merge.
        forged_plan = json.loads(contract_plan_path.read_text(encoding="utf-8"))
        forged_plan["lanes"]["only"]["sensitive"] = True
        contract_plan_path.write_text(json.dumps(forged_plan), encoding="utf-8")
        subprocess.run(["git", "-C", str(contract_worktree), "add", "."], check=True)
        subprocess.run(["git", "-C", str(contract_worktree), "commit", "-q", "-m", "forge plan"], check=True)
        forged_head = git(contract_worktree, "rev-parse", "HEAD")
        lane_review("only", "code", "PASS", forged_head, contract_report, "contract-r2", change="contract")
        expect_refusal("lane-merge", "only", "--change", "contract", contains="approved plan")
        subprocess.run(["git", "-C", str(contract_worktree), "reset", "--hard", contract_head], check=True)
        # Rewording a task definition, keeping its identifier and its tick, is refused too. The
        # guard is exercised directly here so this case stays inside the two-round review bound the
        # lane-merge path enforces.
        contract_base = git(contract_repository, "rev-parse", "HEAD")
        contract_tasks_path.write_text("- [x] 1.1 renamed contract work\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(contract_worktree), "add", "."], check=True)
        subprocess.run(["git", "-C", str(contract_worktree), "commit", "-q", "-m", "reword task"], check=True)
        reworded_head = git(contract_worktree, "rev-parse", "HEAD")
        try:
            _candidate_tick_state(
                contract_repository, "contract", contract_plan, "only", contract_base, reworded_head,
                contract_base,
            )
        except StateError as exc:
            assert "task definitions" in str(exc), str(exc)
        else:
            raise AssertionError("a reworded task definition must be refused")
        subprocess.run(["git", "-C", str(contract_worktree), "reset", "--hard", contract_head], check=True)
        # The lane's own tick, and nothing else, still merges. A review recorded against a commit
        # that already carries one keeps the same round, so the bound is not consumed by re-review.
        lane_review("only", "code", "PASS", contract_head, contract_report, "contract-r1", change="contract")
        lane_merge("only", "contract")
        # The recorded merge entry makes a re-run idempotent: the completed-lane fast path now
        # validates against the reviewed commit it recorded instead of returning on faith.
        lane_merge("only", "contract")
        lane_close("only", "contract")
        os.chdir(root)

        # An approved text-only amendment changes definitions, not the historical tick baseline.
        amendment_plan = {
            "schema_version": 1,
            "change": "amendment",
            "lanes": {
                lane_id: {"risk": "ordinary", "ui": False, "screen": False,
                          "sensitive": False, "depends_on": []}
                for lane_id in ("prior", "only", "foreign")
            },
            "tasks": {"1.1": {"lane": "prior", "deps": []},
                      "2.1": {"lane": "only", "deps": []},
                      "3.1": {"lane": "foreign", "deps": []}},
        }
        historical_tasks = "- [x] 1.1 inherited work\n- [ ] 2.1 original work\n- [ ] 3.1 foreign work\n"
        approved_tasks = historical_tasks.replace("original work", "approved amended work")
        candidate_tasks = approved_tasks.replace("- [ ] 2.1", "- [x] 2.1")
        amendment_repository = make_repository(
            "amendment-repository", "amendment", amendment_plan, historical_tasks
        )
        amendment_repository.joinpath(".gitignore").write_text(".agents/tmp/\n", encoding="utf-8")
        commit(amendment_repository, "ignore fixture scratch state")
        os.chdir(amendment_repository)
        amendment_base = git(amendment_repository, "rev-parse", "HEAD")
        lane_open("only", "amendment")
        amendment_coord_path = _coordination_path("amendment", amendment_repository)
        amendment_runtime = _read_state(amendment_coord_path)["lanes"]["only"]
        amendment_worktree = Path(amendment_runtime["worktree"])
        amendment_ref = amendment_runtime["ref"]
        amendment_tasks_relative = "openspec/changes/amendment/tasks.md"
        amendment_plan_relative = "openspec/changes/amendment/plan/graph-plan.json"
        feature_tasks_path = amendment_repository / amendment_tasks_relative
        candidate_tasks_path = amendment_worktree / amendment_tasks_relative
        candidate_plan_path = amendment_worktree / amendment_plan_relative
        feature_tasks_path.write_text(approved_tasks, encoding="utf-8")
        amendment_repository.joinpath("approved-plan.md").write_text(
            "Fixture-only approved amendment; preserve this feature-only artifact.\n", encoding="utf-8"
        )
        commit(amendment_repository, "approved task-text amendment (fixture)")
        git(amendment_repository, "commit", "--allow-empty", "-q", "-m", "approved by human (fixture)")
        approved_head = git(amendment_repository, "rev-parse", "HEAD")
        candidate_tasks_path.write_text(candidate_tasks, encoding="utf-8")
        amendment_worktree.joinpath("lane-result.txt").write_text("reviewed implementation\n", encoding="utf-8")
        amendment_head = commit(amendment_worktree, "carry approved amendment and assigned tick")
        assert _lane_merge_base(amendment_repository, amendment_ref, approved_head) == amendment_base
        assert _normalized_task_definitions(candidate_tasks) == _normalized_task_definitions(approved_tasks)
        assert _normalized_task_definitions(candidate_tasks) != _normalized_task_definitions(historical_tasks)
        assert _candidate_tick_state(
            amendment_repository, "amendment", amendment_plan, "only", amendment_base,
            amendment_head, approved_head,
        ) == ({"2.1"}, {"1.1"}, True)
        changed_graph = json.loads(candidate_plan_path.read_text(encoding="utf-8"))
        changed_graph["lanes"]["only"]["sensitive"] = True
        for invalid_tasks, invalid_graph, refusal in (
            (candidate_tasks.replace("approved amended work", "candidate-only work"), False, "task definitions"),
            (historical_tasks.replace("- [ ] 2.1", "- [x] 2.1"), False, "task definitions"),
            (candidate_tasks.replace("- [ ] 3.1", "- [x] 3.1"), False, "added ticks"),
            (candidate_tasks.replace("- [x] 1.1", "- [ ] 1.1"), False, "removed checked tasks"),
            (candidate_tasks, True, "approved plan"),
        ):
            candidate_tasks_path.write_text(invalid_tasks, encoding="utf-8")
            if invalid_graph:
                candidate_plan_path.write_text(json.dumps(changed_graph), encoding="utf-8")
            invalid_head = commit(amendment_worktree, "invalid amendment candidate (fixture)")
            try:
                _candidate_tick_state(
                    amendment_repository, "amendment", amendment_plan, "only", amendment_base,
                    invalid_head, approved_head,
                )
            except StateError as exc:
                assert refusal in str(exc), str(exc)
                if refusal == "task definitions":
                    assert approved_head in str(exc), str(exc)
            else:
                raise AssertionError(f"amendment candidate must refuse {refusal}")
            git(amendment_worktree, "reset", "--hard", amendment_head)
        # The approved feature graph must also equal the historical graph, even if the candidate agrees.
        amendment_repository.joinpath(amendment_plan_relative).write_text(
            json.dumps(changed_graph), encoding="utf-8"
        )
        changed_feature_head = commit(amendment_repository, "invalid feature graph (fixture)")
        candidate_plan_path.write_text(json.dumps(changed_graph), encoding="utf-8")
        changed_candidate_head = commit(amendment_worktree, "matching invalid graph (fixture)")
        for graph_candidate_head in (amendment_head, changed_candidate_head):
            try:
                _candidate_tick_state(
                    amendment_repository, "amendment", amendment_plan, "only", amendment_base,
                    graph_candidate_head, changed_feature_head,
                )
            except StateError as exc:
                assert "approved plan" in str(exc), str(exc)
            else:
                raise AssertionError("a graph change at approved HEAD must be refused")
        git(amendment_repository, "reset", "--hard", approved_head)
        git(amendment_worktree, "reset", "--hard", amendment_head)
        state_derive("amendment", checkout=amendment_repository)
        amendment_report = root / "amendment-review.md"
        amendment_report.write_text("fixture code review\n", encoding="utf-8")
        lane_review("only", "code", "PASS", amendment_head, amendment_report, "amendment-r1", change="amendment")
        expect_refusal("lane-merge", "only", "--change", "amendment", contains="merge failed")
        expect_refusal(
            "lane-merge", "only", "--change", "amendment", "--human-override",
            "--override-reason", "fixture human decision", contains="merge failed",
        )
        assert git(amendment_repository, "rev-parse", "HEAD") == approved_head
        assert git(amendment_worktree, "rev-parse", "HEAD") == amendment_head
        assert not git(amendment_repository, "status", "--porcelain")
        assert not git(amendment_worktree, "status", "--porcelain")
        conflict = _run_git(amendment_repository, "merge", "--no-ff", "--no-commit", amendment_head)
        assert conflict.returncode == 1, (conflict.stdout, conflict.stderr)
        assert git(amendment_repository, "diff", "--name-only", "--diff-filter=U") == amendment_tasks_relative
        git(amendment_repository, "restore", f"--source={amendment_head}", "--staged", "--worktree",
            "--", amendment_tasks_relative)
        assert not git(amendment_repository, "diff", "--name-only", "--diff-filter=U")
        git(amendment_repository, "diff", "--cached", "--exit-code", amendment_head, "--", amendment_tasks_relative)
        git(amendment_repository, "diff", "--cached", "--check", "--", amendment_tasks_relative)
        merged_head = commit(amendment_repository, "preserve reviewed amendment candidate (fixture)")
        assert git(amendment_repository, "log", "-1", "--format=%P").split() == [approved_head, amendment_head]
        assert git(amendment_repository, "rev-parse", f"refs/heads/{amendment_ref}") == amendment_head
        assert git(amendment_repository, "diff", "--name-only", amendment_head, merged_head) == "approved-plan.md"
        git(amendment_repository, "diff", "--exit-code", approved_head, merged_head, "--", "approved-plan.md")
        assert _committed_file(amendment_repository, merged_head, amendment_tasks_relative) == candidate_tasks
        assert _committed_file(amendment_repository, merged_head, amendment_plan_relative) == _committed_file(
            amendment_repository, amendment_base, amendment_plan_relative
        )
        amendment_state = _read_state(state_derive("amendment", checkout=amendment_repository))
        assert amendment_state["source_sha"] == merged_head
        assert set(amendment_state["checked_tasks"]) == {"1.1", "2.1"}
        assert amendment_state["lanes"]["only"]["complete"]
        lane_merge("only", "amendment")
        recovered_runtime = _read_state(amendment_coord_path)["lanes"]["only"]
        assert recovered_runtime["merge"]["reviewed_head"] == amendment_head
        assert recovered_runtime["base"] == amendment_base
        assert recovered_runtime["reviews"][-1]["binding"]["commit"] == amendment_head
        assert git(amendment_repository, "rev-parse", "HEAD") == merged_head
        assert not git(amendment_repository, "status", "--porcelain")
        lane_close("only", "amendment")
        os.chdir(root)

        # A completed lane is no longer accepted on faith. The documented manual-merge recovery
        # path lands the lane's ticks on the feature branch without lane_merge validating them, so
        # the fast path must establish the reviewed commit, its containment in the feature branch,
        # and the lane's required reviews before treating the lane as merged.
        recovery_plan = {
            "schema_version": 1,
            "change": "recovery",
            "lanes": {"only": {"risk": "ordinary", "ui": False, "screen": False,
                               "sensitive": False, "depends_on": []},
                      "gated": {"risk": "ordinary", "ui": False, "screen": False,
                                "sensitive": True, "depends_on": []}},
            "tasks": {"1.1": {"lane": "only", "deps": []},
                      "1.2": {"lane": "gated", "deps": []}},
        }
        recovery_repository = make_repository(
            "recovery-repository", "recovery", recovery_plan,
            "- [ ] 1.1 recovery work\n- [ ] 1.2 gated work\n"
        )
        os.chdir(recovery_repository)
        recovery_open, _, recovery_open_error = invoke("lane-open", "only", "--change", "recovery")
        assert recovery_open == 0, recovery_open_error
        recovery_coordination_path = _coordination_path("recovery", recovery_repository)
        recovery_coordination = _read_state(recovery_coordination_path)
        recovery_worktree = Path(recovery_coordination["lanes"]["only"]["worktree"])
        task_tick("only", "1.1", "recovery")
        subprocess.run(["git", "-C", str(recovery_worktree), "add", "."], check=True)
        subprocess.run(["git", "-C", str(recovery_worktree), "commit", "-q", "-m", "recovery tick"], check=True)
        recovery_head = git(recovery_worktree, "rev-parse", "HEAD")
        recovery_report = root / "recovery-review.md"
        recovery_report.write_text("code review\n", encoding="utf-8")
        lane_review("only", "code", "PASS", recovery_head, recovery_report, "recovery-r1", change="recovery")
        # A hand-resolved merge: the lane branch lands on the feature branch directly, so the lane
        # becomes complete and lane_merge never records a merge entry for it.
        subprocess.run(
            ["git", "-C", str(recovery_repository), "merge", "--no-ff", "-q",
             "-m", "hand-resolved merge", "featdev/recovery-only"],
            check=True,
        )
        recovery_state = _read_state(state_derive("recovery", checkout=recovery_repository))
        assert recovery_state["lanes"]["only"]["complete"], recovery_state["lanes"]["only"]
        recovery_reviews = _read_state(recovery_coordination_path)["lanes"]["only"]["reviews"]
        assert recovery_reviews, "the recovery lane must hold its review before the merge checks"

        def write_recovery_coordination(lane_mutate):
            coordination = _read_state(recovery_coordination_path)
            lane_mutate(coordination["lanes"]["only"])
            _write_coordination(recovery_state, coordination, recovery_plan)

        # A recorded merge entry naming a commit outside the feature branch proves nothing: the
        # reviewed work is not present, so the merge is refused instead of accepted. The unrelated
        # commit is minted with commit-tree so it exists but is reachable from no branch.
        unrelated = git(recovery_repository, "commit-tree", "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
                        "-m", "unrelated root")
        write_recovery_coordination(lambda lane: lane.__setitem__(
            "merge", {"reviewed_head": unrelated, "merge_commit": unrelated, "base": recovery_head}))
        try:
            lane_merge("only", "recovery")
        except StateError as exc:
            assert "reviewed commit" in str(exc) and "is not in the feature branch" in str(exc), str(exc)
        else:
            raise AssertionError("a completed lane whose reviewed commit is absent must be refused")

        # The recorded merge commit itself must be contained too, separately from the reviewed one.
        write_recovery_coordination(lambda lane: lane.__setitem__(
            "merge", {"reviewed_head": recovery_head, "merge_commit": unrelated, "base": recovery_head}))
        try:
            lane_merge("only", "recovery")
        except StateError as exc:
            assert "recorded merge commit" in str(exc) and "is not in the feature branch" in str(exc), str(exc)
        else:
            raise AssertionError("a completed lane whose recorded merge commit is absent must be refused")

        # Losing the coordination evidence also fails closed instead of accepting on faith.
        def drop_recovery_evidence(lane):
            lane["reviews"] = []
            lane.pop("merge", None)

        write_recovery_coordination(drop_recovery_evidence)
        try:
            lane_merge("only", "recovery")
        except StateError as exc:
            assert "no recorded merge and no review evidence" in str(exc), str(exc)
        else:
            raise AssertionError("a completed lane without review evidence must be refused")

        # The documented recovery path itself is accepted: the reviews hold and the reviewed commit
        # is contained in the feature branch, so the lane merges without a second merge attempt.
        write_recovery_coordination(lambda lane: lane.__setitem__("reviews", recovery_reviews))
        lane_merge("only", "recovery")
        lane_close("only", "recovery")

        # A completed lane that requires a human gate cannot ride the hand-resolved recovery: the
        # gate must hold here, the grant it names is consumed, and the validated completion is
        # recorded so the recovery cannot be replayed.
        gated_open, _, gated_open_error = invoke("lane-open", "gated", "--change", "recovery")
        assert gated_open == 0, gated_open_error
        gated_coordination = _read_state(recovery_coordination_path)
        gated_worktree = Path(gated_coordination["lanes"]["gated"]["worktree"])
        task_tick("gated", "1.2", "recovery")
        subprocess.run(["git", "-C", str(gated_worktree), "add", "."], check=True)
        subprocess.run(["git", "-C", str(gated_worktree), "commit", "-q", "-m", "gated tick"], check=True)
        gated_head = git(gated_worktree, "rev-parse", "HEAD")
        gated_report = root / "gated-review.md"
        gated_report.write_text("code review\n", encoding="utf-8")
        lane_review("gated", "code", "PASS", gated_head, gated_report, "recovery-r1", change="recovery")
        subprocess.run(
            ["git", "-C", str(recovery_repository), "merge", "--no-ff", "-q",
             "-m", "hand-resolved gated merge", "featdev/recovery-gated"],
            check=True,
        )
        # No merge entry and no human gate: the recovery is refused instead of accepted.
        try:
            lane_merge("gated", "recovery")
        except StateError as exc:
            assert "human gate" in str(exc) and "missing" in str(exc), str(exc)
        else:
            raise AssertionError("a gate-requiring completed lane without a gate record must be refused")
        human_gate("gated", "approved", gated_head, "recovery-r1", None, "recovery")
        lane_merge("gated", "recovery")
        gated_after = _read_state(recovery_coordination_path)["lanes"]["gated"]
        assert gated_after.get("merge", {}).get("reviewed_head") == gated_head, gated_after
        assert recovery_state is not None
        consumed_gated = _read_state(recovery_coordination_path)["authorizations"]
        # This gate was interactive, so there is no grant to consume - but the recorded completion
        # makes a replay idempotent instead of a second acceptance.
        lane_merge("gated", "recovery")
        lane_close("gated", "recovery")
        os.chdir(root)

        # A human grant lets an Oracle stand-in satisfy the per-workstream checkpoint, which is what
        # lets an unattended run finish a user-facing lane. An authorization never approves by
        # itself, it binds to the approved plan revision and the lane, and it is consumed once.
        grant_plan = {
            "schema_version": 1,
            "change": "grant",
            "lanes": {
                "alpha": {"risk": "ordinary", "ui": False, "screen": False,
                          "sensitive": True, "depends_on": []},
                "beta": {"risk": "ordinary", "ui": False, "screen": False,
                         "sensitive": True, "depends_on": []},
            },
            "tasks": {"1.1": {"lane": "alpha", "deps": []}, "1.2": {"lane": "beta", "deps": []}},
        }
        grant_repository = make_repository(
            "grant-repository", "grant", grant_plan, "- [ ] 1.1 alpha work\n- [ ] 1.2 beta work\n"
        )
        os.chdir(grant_repository)
        grant_open, _, grant_open_error = invoke("lane-open", "alpha", "--change", "grant")
        assert grant_open == 0, grant_open_error
        grant_coordination = _read_state(_coordination_path("grant", grant_repository))
        grant_worktree = Path(grant_coordination["lanes"]["alpha"]["worktree"])
        subprocess.run(
            ["git", "-C", str(grant_worktree), "commit", "--allow-empty", "-q", "-m", "alpha code"],
            check=True,
        )
        task_tick("alpha", "1.1", "grant")
        subprocess.run(["git", "-C", str(grant_worktree), "add", "."], check=True)
        subprocess.run(["git", "-C", str(grant_worktree), "commit", "-q", "-m", "alpha tick"], check=True)
        grant_head = git(grant_worktree, "rev-parse", "HEAD")
        grant_report = root / "grant-code-review.md"
        grant_report.write_text("code review\n", encoding="utf-8")
        lane_review("alpha", "code", "PASS", grant_head, grant_report, "grant-r1", change="grant")
        # The lane opened from the current feature HEAD, and the plan cannot drift (guarded above),
        # so the approved plan revision the grant must name is the committed plan at that HEAD.
        grant_plan_sha = hashlib.sha256(
            _committed_file(
                grant_repository, git(grant_repository, "rev-parse", "HEAD"),
                "openspec/changes/grant/plan/graph-plan.json",
            ).encode("utf-8")
        ).hexdigest()
        grant_record = {
            "id": "grant-a1",
            "granted_by": "human",
            "granted_at": "2026-09-11T00:00:00Z",
            "scope": ["alpha"],
            "plan_sha256": grant_plan_sha,
        }
        stand_in_report = root / "grant-stand-in.md"
        stand_in_report.write_text("oracle stand-in: acceptance duties covered\n", encoding="utf-8")
        acceptance_checklist = root / "grant-checklist.md"
        acceptance_checklist.write_text("- wording reads correctly\n", encoding="utf-8")

        # Record-time refusals: the grant must be human-origin, name this checkpoint, and match the
        # approved plan revision. A refused grant must not be consumed or stored.
        for case_name, grant_mutation, expected in (
            ("wrong-plan", {"plan_sha256": "0" * 64}, "another plan revision"),
            ("out-of-scope", {"scope": ["beta"]}, "does not cover lane"),
            ("not-human-origin", {"granted_by": "oracle"}, "not human-origin"),
            ("no-id", {"id": ""}, "id must be non-empty"),
        ):
            bad_grant = dict(grant_record)
            bad_grant.update(grant_mutation)
            try:
                human_gate(
                    "alpha", "approved", grant_head, "grant-r1", None, "grant", "oracle", bad_grant,
                    {"verdict": "PASS", "report": str(stand_in_report)}, str(acceptance_checklist),
                )
            except StateError as exc:
                assert expected in str(exc), (case_name, str(exc))
            else:
                raise AssertionError(f"a {case_name} grant must be refused")
        for case_name, stand_in_value, checklist_value, expected in (
            ("non-pass-stand-in", {"verdict": "MORE WORK", "report": str(stand_in_report)},
             str(acceptance_checklist), "not PASS"),
            ("missing-checklist", {"verdict": "PASS", "report": str(stand_in_report)},
             str(root / "absent-checklist.md"), "checklist is missing or empty"),
            ("checklist-is-report", {"verdict": "PASS", "report": str(stand_in_report)},
             str(stand_in_report), "must be different files"),
        ):
            try:
                human_gate(
                    "alpha", "approved", grant_head, "grant-r1", None, "grant", "oracle", grant_record,
                    stand_in_value, checklist_value,
                )
            except StateError as exc:
                assert expected in str(exc), (case_name, str(exc))
            else:
                raise AssertionError(f"a {case_name} authorization must be refused")
        # An interactive approval that carries a grant reference is still refused.
        try:
            human_gate(
                "alpha", "approved", grant_head, "grant-r1", None, "grant", "human", grant_record,
                {"verdict": "PASS", "report": str(stand_in_report)}, str(acceptance_checklist),
            )
        except StateError as exc:
            assert "need --approved-by oracle" in str(exc), str(exc)
        else:
            raise AssertionError("a grant on an interactive approval must be refused")

        # The valid grant satisfies the checkpoint, merges, and is consumed. Both the refusal and the
        # approval go through the CLI so the argument wiring is covered, not just the helpers.
        grant_file = root / "grant-a1.json"
        grant_file.write_text(json.dumps(grant_record), encoding="utf-8")
        gate_arguments = (
            "human-gate", "alpha", "--state", "approved", "--commit", grant_head,
            "--evidence-revision", "grant-r1", "--change", "grant", "--approved-by", "oracle",
            "--authorization", str(grant_file), "--stand-in-report", str(stand_in_report),
            "--checklist", str(acceptance_checklist),
        )
        more_work, _, more_work_error = invoke(*gate_arguments, "--stand-in-verdict", "MORE WORK")
        assert more_work == 1 and "not PASS" in more_work_error, (more_work, more_work_error)
        approved, _, approved_error = invoke(*gate_arguments, "--stand-in-verdict", "PASS")
        assert approved == 0, approved_error
        # The consumed grant must be durable before the fallible projection refresh: a failure
        # there must not leave a one-use grant active on disk. Derive first so the only
        # state_derive call left inside lane_merge is the post-merge refresh being tested.
        state_derive("grant", None, grant_repository)
        lane_merge("alpha", "grant")
        persisted = _read_state(_coordination_path("grant", grant_repository))
        assert persisted["lanes"]["alpha"].get("phase") == "merged", persisted["lanes"]["alpha"]
        assert persisted["authorizations"]["grant-a1"]["status"] == "consumed", persisted["authorizations"]
        # Repair the projection, then confirm the retry is idempotent rather than a second merge.
        state_derive("grant", None, grant_repository)
        lane_merge("alpha", "grant")
        lane_close("alpha", "grant")
        consumed = _read_state(_coordination_path("grant", grant_repository))
        assert consumed["authorizations"]["grant-a1"]["status"] == "consumed", consumed["authorizations"]

        # Merge-time refusals. Each invalid gate state must be refused by the merge validator with
        # the same answer the summary renders, and the one-use rule must survive.
        grant_state = _read_state(state_derive("grant", checkout=grant_repository))
        grant_lane = grant_state["lanes"]["alpha"]
        original_stand_in_report = stand_in_report.read_text(encoding="utf-8")
        for case_name, mutate, expected in (
            ("consumed", lambda c: c["authorizations"]["grant-a1"].__setitem__("status", "consumed"), "not active"),
            ("revoked", lambda c: c["authorizations"]["grant-a1"].__setitem__("status", "revoked"), "not active"),
            ("out-of-scope", lambda c: c["authorizations"]["grant-a1"].__setitem__("scope", ["beta"]),
             "does not cover lane"),
            ("grant-missing", lambda c: c["authorizations"].pop("grant-a1"), "is missing from coordination data"),
            ("non-pass-stand-in", lambda c: c["human_gates"]["alpha"]["stand_in"].__setitem__("verdict", "MORE WORK"),
             "not PASS"),
            ("stand-in-other-commit",
             lambda c: c["human_gates"]["alpha"]["stand_in"]["binding"].__setitem__("commit", "0" * 40),
             "another commit or evidence revision"),
            ("stale-approved-plan",
             lambda c: c["authorizations"]["grant-a1"].__setitem__("plan_sha256", "f" * 64),
             "does not match the recorded checkpoint binding"),
            ("no-auto-marker", lambda c: c["human_gates"]["alpha"].__setitem__("auto_approval", False),
             "without the auto_approval marker"),
            ("tampered-report",
             lambda c: Path(c["human_gates"]["alpha"]["stand_in"]["report"]).write_text(
                 "rewritten after approval\n", encoding="utf-8"
             ),
             "changed after the approval"),
        ):
            case_coordination = _read_state(_coordination_path("grant", grant_repository))
            case_coordination["authorizations"]["grant-a1"]["status"] = "active"
            mutate(case_coordination)
            try:
                _validate_human_gate(
                    "alpha", grant_lane, grant_head, case_coordination,
                    grant_state.get("plan_sha256"),
                )
            except StateError as exc:
                assert expected in str(exc), (case_name, str(exc))
            else:
                raise AssertionError(f"a {case_name} authorization must be refused")
            stand_in_report.write_text(original_stand_in_report, encoding="utf-8")
        # The restored gate is still valid, so the refusals above came from the mutations alone.
        restored_coordination = _read_state(_coordination_path("grant", grant_repository))
        restored_coordination["authorizations"]["grant-a1"]["status"] = "active"
        _validate_human_gate("alpha", grant_lane, grant_head, restored_coordination)
        # A grant bound to a superseded plan revision is stale even when everything else is intact.
        stale_plan_coordination = _read_state(_coordination_path("grant", grant_repository))
        stale_plan_coordination["authorizations"]["grant-a1"]["status"] = "active"
        try:
            _validate_human_gate(
                "alpha", grant_lane, grant_head, stale_plan_coordination, "0" * 64,
            )
        except StateError as exc:
            assert "another plan revision" in str(exc), str(exc)
        else:
            raise AssertionError("a grant for a superseded plan revision must be refused")

        # A human rejection or revocation suspends the grant for that checkpoint, so an old grant
        # cannot revive itself after a correction: a renewal must be a new grant.
        beta_open, _, beta_open_error = invoke("lane-open", "beta", "--change", "grant")
        assert beta_open == 0, beta_open_error
        beta_coordination = _read_state(_coordination_path("grant", grant_repository))
        beta_worktree = Path(beta_coordination["lanes"]["beta"]["worktree"])
        task_tick("beta", "1.2", "grant")
        subprocess.run(["git", "-C", str(beta_worktree), "add", "."], check=True)
        subprocess.run(["git", "-C", str(beta_worktree), "commit", "-q", "-m", "beta tick"], check=True)
        beta_head = git(beta_worktree, "rev-parse", "HEAD")
        beta_report = root / "beta-code-review.md"
        beta_report.write_text("code review\n", encoding="utf-8")
        # A mediation recorded before any review has seen a failure proves nothing about those
        # failures, so it must not license a third round later.
        premature_report = root / "beta-premature-mediation.md"
        premature_report.write_text("oracle mediation recorded too early\n", encoding="utf-8")
        lane_mediate("beta", premature_report, "code", change="grant")
        lane_review("beta", "code", "PASS", beta_head, beta_report, "beta-r1", change="grant")
        beta_grant = dict(grant_record)
        beta_grant["id"] = "grant-b1"
        beta_grant["scope"] = ["beta"]
        beta_grant_file = root / "grant-b1.json"
        beta_grant_file.write_text(json.dumps(beta_grant), encoding="utf-8")
        beta_gate_arguments = (
            "human-gate", "beta", "--state", "approved", "--commit", beta_head,
            "--evidence-revision", "beta-r1", "--change", "grant", "--approved-by", "oracle",
            "--authorization", str(beta_grant_file), "--stand-in-report", str(stand_in_report),
            "--stand-in-verdict", "PASS", "--checklist", str(acceptance_checklist),
        )
        beta_approved, _, beta_approved_error = invoke(*beta_gate_arguments)
        assert beta_approved == 0, beta_approved_error
        # A valid stand-in must read as approved in status, matching what the merge gate accepts:
        # the summary validates through the merge validator, so it needs the live grants too.
        beta_status_result, beta_status, beta_status_error = invoke("status", "--change", "grant", "--json")
        assert beta_status_result == 0, beta_status_error
        beta_gate_display = json.loads(beta_status)["lanes"]["beta"]["human_gate"]
        assert beta_gate_display == "required (sensitive): approved", beta_gate_display
        beta_rejected, _, beta_rejected_error = invoke(
            "human-gate", "beta", "--state", "rejected", "--commit", beta_head,
            "--evidence-revision", "beta-r1", "--change", "grant",
        )
        assert beta_rejected == 0, beta_rejected_error
        suspended = _read_state(_coordination_path("grant", grant_repository))
        assert suspended["authorizations"]["grant-b1"]["status"] == "suspended", suspended["authorizations"]
        revived, _, revived_error = invoke(*beta_gate_arguments)
        assert revived == 1 and "not active" in revived_error, (revived, revived_error)

        # The two-round review bound: a third round needs a recorded Oracle mediation, and the merge
        # validator enforces the same bound so hand-edited coordination data cannot buy one.
        subprocess.run(
            ["git", "-C", str(beta_worktree), "commit", "--allow-empty", "-q", "-m", "beta fix 1"],
            check=True,
        )
        second_head = git(beta_worktree, "rev-parse", "HEAD")
        lane_review("beta", "code", "MORE WORK", second_head, beta_report, "beta-r2", change="grant")
        subprocess.run(
            ["git", "-C", str(beta_worktree), "commit", "--allow-empty", "-q", "-m", "beta fix 2"],
            check=True,
        )
        third_head = git(beta_worktree, "rev-parse", "HEAD")
        try:
            lane_review("beta", "code", "PASS", third_head, beta_report, "beta-r3", change="grant")
        except StateError as exc:
            assert "two-round limit" in str(exc), str(exc)
        else:
            raise AssertionError("a third review round without an Oracle mediation must be refused")
        mediation_report = root / "beta-mediation.md"
        mediation_report.write_text("oracle mediation: root cause and resolution\n", encoding="utf-8")
        mediate_result, _, mediate_error = invoke(
            "lane-mediate", "beta", "--report", str(mediation_report), "--role", "code", "--change", "grant"
        )
        assert mediate_result == 0, mediate_error
        lane_review("beta", "code", "PASS", third_head, beta_report, "beta-r3", change="grant")
        mediate_state = _read_state(state_derive("grant", checkout=grant_repository))
        mediate_coordination = _read_state(_coordination_path("grant", grant_repository))
        beta_operation = dict(mediate_state["lanes"]["beta"])
        beta_operation.update(mediate_coordination["lanes"]["beta"])
        _validate_lane_reviews("beta", beta_operation, third_head)
        without_mediation = dict(beta_operation)
        without_mediation["mediations"] = []
        try:
            _validate_lane_reviews("beta", without_mediation, third_head)
        except StateError as exc:
            assert "without an Oracle mediation" in str(exc), str(exc)
        else:
            raise AssertionError("a third round without a mediation record must not satisfy the merge gate")
        # The premature record and a tampered digest must not satisfy the gate either.
        for case_name, mediations in (
            ("premature-mediation", [{
                "role": "code", "report": str(premature_report),
                "report_sha256": _path_sha256(Path(premature_report)), "round_at_mediation": 0,
            }]),
            ("tampered-mediation-report", [dict(
                item, report_sha256="0" * 64
            ) for item in beta_operation["mediations"]]),
        ):
            mutated = dict(beta_operation)
            mutated["mediations"] = mediations
            try:
                _validate_lane_reviews("beta", mutated, third_head)
            except StateError as exc:
                assert "without an Oracle mediation" in str(exc), str(exc)
            else:
                raise AssertionError(f"a {case_name} must not satisfy the merge gate")

        # Combined single-lane mode records one independent Oracle pass for code/test and the
        # whole-change final duty. The fast-forward preserves that exact candidate SHA, while
        # moved bases, candidate HEADs, and changed untracked binding files fail closed.
        combined_plan = {
            "schema_version": 1,
            "change": "combined",
            "review_mode": "combined-single-lane",
            "lanes": {
                "only": {"risk": "ordinary", "ui": False, "screen": False,
                          "sensitive": False, "depends_on": []},
            },
            "tasks": {"1.1": {"lane": "only", "deps": []}},
        }
        combined_repository = make_repository(
            "combined-repository", "combined", combined_plan, "- [ ] 1.1 combined work\n"
        )
        os.chdir(combined_repository)
        combined_base = git(combined_repository, "rev-parse", "HEAD")
        lane_open("only", "combined")
        dispatch_run("only", "implementer", "provider/worker:low", "low", "1.1", change="combined")
        dispatch_done("only", "implementer", "impl/session-1", 20, "0.1", "ok", "combined")
        combined_coordination_path = _coordination_path("combined", combined_repository)
        combined_coordination = _read_state(combined_coordination_path)
        assert combined_coordination["lanes"]["only"]["dispatches"][0]["access"] == "write"
        combined_worktree = Path(combined_coordination["lanes"]["only"]["worktree"])
        combined_worktree.joinpath("implementation.txt").write_text("combined implementation\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(combined_worktree), "add", "implementation.txt"], check=True)
        subprocess.run(["git", "-C", str(combined_worktree), "commit", "-q", "-m", "combined code"], check=True)
        task_tick("only", "1.1", "combined")
        subprocess.run(
            ["git", "-C", str(combined_worktree), "add", "openspec/changes/combined/tasks.md"], check=True
        )
        subprocess.run(["git", "-C", str(combined_worktree), "commit", "-q", "-m", "combined tick"], check=True)
        combined_head = git(combined_worktree, "rev-parse", "HEAD")
        dispatch_run(
            "only", "oracle", "provider/oracle:review", "normal", "1.1",
            key="oracle/author-session", change="combined", access="write",
        )
        dispatch_done("only", "oracle", "oracle/author-session", 15, "0.2", "ok", "combined")
        combined_report = root / "combined-review.md"
        combined_evidence = root / "combined-evidence.json"
        combined_acceptance = root / "combined-acceptance.md"
        combined_report.write_text("code/test/final review\n", encoding="utf-8")
        combined_evidence.write_text('{"revision":"combined-r1"}\n', encoding="utf-8")
        combined_acceptance.write_text("classification: pre-release\nversion: 0.2+1\n", encoding="utf-8")
        self_authored_result, _, self_authored_error = invoke(
            "lane-review", "only", "code", "PASS", "--commit", combined_head,
            "--report", str(combined_report), "--evidence-revision", "combined-r1",
            "--evidence", str(combined_evidence), "--combined", "--oracle-provenance", "impl/session-1",
            "--feature-base", combined_base, "--review-base", combined_base,
            "--acceptance", str(combined_acceptance), "--change", "combined",
        )
        assert self_authored_result == 1 and "authored or non-Oracle work" in self_authored_error, (
            self_authored_result, self_authored_error
        )
        oracle_author_result, _, oracle_author_error = invoke(
            "lane-review", "only", "code", "PASS", "--commit", combined_head,
            "--report", str(combined_report), "--evidence-revision", "combined-r1",
            "--evidence", str(combined_evidence), "--combined", "--oracle-provenance", "oracle/author-session",
            "--feature-base", combined_base, "--review-base", combined_base,
            "--acceptance", str(combined_acceptance), "--change", "combined",
        )
        assert oracle_author_result == 1 and "authoring dispatch" in oracle_author_error, (
            oracle_author_result, oracle_author_error
        )
        missing_oracle_result, _, missing_oracle_error = invoke(
            "lane-review", "only", "code", "PASS", "--commit", combined_head,
            "--report", str(combined_report), "--evidence-revision", "combined-r1",
            "--evidence", str(combined_evidence), "--combined", "--oracle-provenance", "oracle/missing",
            "--feature-base", combined_base, "--review-base", combined_base,
            "--acceptance", str(combined_acceptance), "--change", "combined",
        )
        assert missing_oracle_result == 1 and "exactly one completed Oracle dispatch" in missing_oracle_error, (
            missing_oracle_result, missing_oracle_error
        )
        combined_coordination = _read_state(combined_coordination_path)
        combined_coordination["lanes"]["only"]["dispatches"].append({
            "role": "oracle", "model": "provider/oracle:legacy", "level": "normal", "task": "1.1",
            "key": "oracle/legacy", "worker_run_id": "oracle/legacy", "result": "ok",
            "started_at": "2026-09-12T00:00:00Z", "lane_head": combined_head,
        })
        combined_state = _read_state(state_derive("combined", checkout=combined_repository))
        _write_coordination(combined_state, combined_coordination, combined_plan)
        legacy_oracle_result, _, legacy_oracle_error = invoke(
            "lane-review", "only", "code", "PASS", "--commit", combined_head,
            "--report", str(combined_report), "--evidence-revision", "combined-r1",
            "--evidence", str(combined_evidence), "--combined", "--oracle-provenance", "oracle/legacy",
            "--feature-base", combined_base, "--review-base", combined_base,
            "--acceptance", str(combined_acceptance), "--change", "combined",
        )
        assert legacy_oracle_result == 1 and "ambiguous legacy Oracle" in legacy_oracle_error, (
            legacy_oracle_result, legacy_oracle_error
        )
        dispatch_run(
            "only", "oracle", "provider/oracle:review", "normal", "1.1",
            key="oracle/session-1", change="combined", access="read-only",
            acceptance=combined_acceptance, evidence=combined_evidence,
        )
        dispatch_done("only", "oracle", "oracle/author-session", 25, "0.3", "ok", "combined")
        shared_session_result, _, shared_session_error = invoke(
            "lane-review", "only", "code", "PASS", "--commit", combined_head,
            "--report", str(combined_report), "--evidence-revision", "combined-r1",
            "--evidence", str(combined_evidence), "--combined", "--oracle-provenance", "oracle/session-1",
            "--feature-base", combined_base, "--review-base", combined_base,
            "--acceptance", str(combined_acceptance), "--change", "combined",
        )
        assert shared_session_result == 1 and "shares a worker session" in shared_session_error, (
            shared_session_result, shared_session_error
        )
        dispatch_run(
            "only", "oracle", "provider/oracle:review", "normal", "1.1",
            key="oracle/session-2", change="combined", access="read-only",
            acceptance=combined_acceptance, evidence=combined_evidence,
        )
        dispatch_done("only", "oracle", "oracle/session-2", 25, "0.3", "ok", "combined")
        combined_review_arguments = (
            "lane-review", "only", "code", "PASS", "--commit", combined_head,
            "--report", str(combined_report), "--evidence-revision", "combined-r1",
            "--evidence", str(combined_evidence), "--combined", "--oracle-provenance", "oracle/session-2",
            "--feature-base", combined_base, "--review-base", combined_base,
            "--acceptance", str(combined_acceptance), "--change", "combined",
        )
        combined_coordination = _read_state(combined_coordination_path)
        combined_state = _read_state(state_derive("combined", checkout=combined_repository))
        combined_dispatches = combined_coordination["lanes"]["only"]["dispatches"]
        session_2_dispatch = next(item for item in combined_dispatches if item.get("key") == "oracle/session-2")
        implementer_dispatch = next(item for item in combined_dispatches if item.get("key") == "combined/only/implementer")
        legacy_oracle_dispatch = next(item for item in combined_dispatches if item.get("key") == "oracle/legacy")

        # Provenance is resolved at the reviewed candidate HEAD, not by whichever dispatch
        # happens to be found first in one read-only session. An old session-A dispatch may
        # coexist with the current session-B dispatch, while two session-B records remain
        # ambiguous and must refuse. Author identity is scanned across the full ledger, even
        # when an authored record is bound to an older candidate HEAD.
        old_session_dispatch = {
            "role": "oracle", "model": "provider/oracle:old", "level": "normal", "task": "1.1",
            "key": "oracle/session-2-old", "access": "read-only", "worker_run_id": "oracle/session-2",
            "result": "ok", "started_at": "2026-09-12T00:00:00Z", "lane_head": combined_base,
        }
        combined_dispatches.append(old_session_dispatch)
        _write_coordination(combined_state, combined_coordination, combined_plan)
        old_head_result, _, old_head_error = invoke(*combined_review_arguments)
        assert old_head_result == 0, old_head_error
        combined_coordination = _read_state(combined_coordination_path)
        combined_state = _read_state(state_derive("combined", checkout=combined_repository))
        combined_dispatches = combined_coordination["lanes"]["only"]["dispatches"]
        session_2_dispatch = next(item for item in combined_dispatches if item.get("key") == "oracle/session-2")
        implementer_dispatch = next(item for item in combined_dispatches if item.get("key") == "combined/only/implementer")
        legacy_oracle_dispatch = next(item for item in combined_dispatches if item.get("key") == "oracle/legacy")
        duplicate_session_dispatch = dict(session_2_dispatch)
        duplicate_session_dispatch["key"] = "oracle/session-2-duplicate"
        combined_dispatches.append(duplicate_session_dispatch)
        _write_coordination(combined_state, combined_coordination, combined_plan)
        duplicate_head_result, _, duplicate_head_error = invoke(*combined_review_arguments)
        assert duplicate_head_result == 1 and "exactly one" in duplicate_head_error, duplicate_head_error
        combined_dispatches.remove(duplicate_session_dispatch)
        authored_old_head = {
            "role": "oracle", "model": "provider/oracle:author-old", "level": "normal", "task": "1.1",
            "key": "oracle/session-2-author-old", "access": "write", "worker_run_id": "oracle/session-2",
            "result": "ok", "started_at": "2026-09-12T00:00:00Z", "lane_head": combined_base,
        }
        combined_dispatches.append(authored_old_head)
        _write_coordination(combined_state, combined_coordination, combined_plan)
        old_author_result, _, old_author_error = invoke(*combined_review_arguments)
        assert old_author_result == 1 and "shares a worker session" in old_author_error, old_author_error
        combined_dispatches.remove(authored_old_head)
        _write_coordination(combined_state, combined_coordination, combined_plan)

        # The Oracle receipt snapshots both review documents before launch. Editing either
        # document, omitting a stored snapshot, or retrying the same key with a new receipt
        # must refuse without replacing the original snapshot.
        original_acceptance = combined_acceptance.read_text(encoding="utf-8")
        original_evidence = combined_evidence.read_text(encoding="utf-8")
        original_acceptance_sha = session_2_dispatch["acceptance_sha256"]
        original_evidence_sha = session_2_dispatch["evidence_sha256"]
        for changed_path, changed_text in (
            (combined_acceptance, "classification: changed after dispatch\n"),
            (combined_evidence, '{"revision":"changed-after-dispatch"}\n'),
        ):
            before = changed_path.read_text(encoding="utf-8")
            changed_path.write_text(changed_text, encoding="utf-8")
            drift_result, _, drift_error = invoke(*combined_review_arguments)
            assert drift_result == 1 and "since dispatch" in drift_error, drift_error
            changed_path.write_text(before, encoding="utf-8")
        missing_snapshot = session_2_dispatch.pop("acceptance_sha256")
        _write_coordination(combined_state, combined_coordination, combined_plan)
        missing_snapshot_result, _, missing_snapshot_error = invoke(*combined_review_arguments)
        assert missing_snapshot_result == 1 and "snapshot" in missing_snapshot_error, missing_snapshot_error
        session_2_dispatch["acceptance_sha256"] = missing_snapshot
        _write_coordination(combined_state, combined_coordination, combined_plan)
        changed_acceptance = combined_acceptance.with_name("combined-acceptance-retry.md")
        changed_acceptance.write_text("classification: retry receipt\n", encoding="utf-8")
        try:
            dispatch_run(
                "only", "oracle", "provider/oracle:review", "normal", "1.1",
                key="oracle/session-2", change="combined", access="read-only",
                acceptance=changed_acceptance, evidence=combined_evidence,
            )
        except StateError as exc:
            assert "different intent or receipt" in str(exc), str(exc)
        else:
            raise AssertionError("a same-key retry with a changed receipt must be refused")
        persisted_retry = _read_state(combined_coordination_path)
        persisted_retry_dispatch = next(
            item for item in persisted_retry["lanes"]["only"]["dispatches"]
            if item.get("key") == "oracle/session-2"
        )
        assert persisted_retry_dispatch["acceptance_sha256"] == original_acceptance_sha, persisted_retry_dispatch
        changed_acceptance.unlink()
        combined_acceptance.write_text(original_acceptance, encoding="utf-8")
        combined_evidence.write_text(original_evidence, encoding="utf-8")
        assert session_2_dispatch["acceptance_sha256"] == original_acceptance_sha
        assert session_2_dispatch["evidence_sha256"] == original_evidence_sha
        _write_coordination(combined_state, combined_coordination, combined_plan)

        # Combined provenance must be tied to the exact candidate HEAD, not merely to an old
        # completed Oracle dispatch that happens to have the requested key or run ID.
        for dispatch_head in (combined_base, None):
            session_2_dispatch["lane_head"] = dispatch_head
            _write_coordination(combined_state, combined_coordination, combined_plan)
            stale_dispatch, _, stale_dispatch_error = invoke(*combined_review_arguments)
            assert stale_dispatch == 1 and "lane_head" in stale_dispatch_error, (
                dispatch_head, stale_dispatch_error
            )
        session_2_dispatch["lane_head"] = combined_head

        # Combined provenance accepts only a successfully completed Oracle. Failed, unknown,
        # and incomplete outcomes must fail at review recording rather than being treated as
        # merely present.
        for outcome in ("failed", "unknown", None):
            session_2_dispatch["result"] = outcome
            _write_coordination(combined_state, combined_coordination, combined_plan)
            failed_result, _, failed_error = invoke(*combined_review_arguments)
            assert failed_result == 1 and "result is not 'ok'" in failed_error, (outcome, failed_error)
        session_2_dispatch["result"] = "ok"

        # Combined provenance also requires stable identity for the selected reviewer and every
        # author-capable record. Explicit read-only advice remains compatible without an ID.
        session_2_dispatch["worker_run_id"] = None
        _write_coordination(combined_state, combined_coordination, combined_plan)
        missing_reviewer, _, missing_reviewer_error = invoke(*combined_review_arguments)
        assert missing_reviewer == 1 and "reviewer dispatch" in missing_reviewer_error, missing_reviewer_error
        session_2_dispatch["worker_run_id"] = "oracle/session-2"

        implementer_dispatch["worker_run_id"] = None
        _write_coordination(combined_state, combined_coordination, combined_plan)
        missing_writer, _, missing_writer_error = invoke(*combined_review_arguments)
        assert missing_writer == 1 and "author-capable dispatch" in missing_writer_error, missing_writer_error
        implementer_dispatch["worker_run_id"] = "impl/session-1"

        legacy_worker_dispatch = {
            "role": "worker", "model": "provider/worker:legacy", "level": "normal", "task": "1.1",
            "key": "worker/legacy-missing-id", "result": "ok",
            "started_at": "2026-09-12T00:00:00Z", "lane_head": combined_head,
        }
        combined_dispatches.append(legacy_worker_dispatch)
        _write_coordination(combined_state, combined_coordination, combined_plan)
        missing_legacy_worker, _, missing_legacy_worker_error = invoke(*combined_review_arguments)
        assert missing_legacy_worker == 1 and "author-capable dispatch" in missing_legacy_worker_error, (
            missing_legacy_worker, missing_legacy_worker_error
        )
        combined_dispatches.remove(legacy_worker_dispatch)

        legacy_oracle_dispatch["worker_run_id"] = None
        _write_coordination(combined_state, combined_coordination, combined_plan)
        missing_legacy_oracle, _, missing_legacy_oracle_error = invoke(*combined_review_arguments)
        assert missing_legacy_oracle == 1 and "author-capable dispatch" in missing_legacy_oracle_error, (
            missing_legacy_oracle, missing_legacy_oracle_error
        )
        legacy_oracle_dispatch["worker_run_id"] = "oracle/legacy"

        read_only_advice = {
            "role": "oracle", "model": "provider/oracle:advice", "level": "normal", "task": "1.1",
            "key": "oracle/advice-without-id", "access": "read-only", "worker_run_id": None,
            "result": "ok", "started_at": "2026-09-12T00:00:00Z", "lane_head": combined_head,
        }
        combined_dispatches.append(read_only_advice)
        _write_coordination(combined_state, combined_coordination, combined_plan)
        combined_review_result, _, combined_review_error = invoke(*combined_review_arguments)
        assert combined_review_result == 0, combined_review_error
        combined_coordination = _read_state(combined_coordination_path)
        combined_records = [
            item for item in combined_coordination["lanes"]["only"].get("reviews", [])
            if isinstance(item, dict) and item.get("combined") is True
        ]
        assert isinstance(combined_records, list) and len(combined_records) == 1, combined_coordination
        combined_record = combined_records[0]
        assert combined_record["duties"] == ["code", "test", "final"], combined_record
        assert combined_record["binding"]["feature_base"] == combined_base, combined_record
        assert combined_record["binding"]["lane_base"] == combined_base, combined_record
        assert combined_record["binding"]["review_base"] == combined_base, combined_record
        assert combined_record["oracle_provenance"] == "oracle/session-2", combined_record

        # Merge revalidation must apply the same session-independence check to an existing
        # combined record, not only to a newly recorded review.
        combined_record["oracle_provenance"] = "oracle/session-1"
        combined_state = _read_state(state_derive("combined", checkout=combined_repository))
        try:
            _write_coordination(combined_state, combined_coordination, combined_plan)
            lane_merge("only", "combined")
        except StateError as exc:
            assert "shares a worker session" in str(exc), str(exc)
        else:
            raise AssertionError("merge revalidation must reject a shared Oracle author session")
        combined_record["oracle_provenance"] = "oracle/session-2"
        _write_coordination(combined_state, combined_coordination, combined_plan)

        # The same exact dispatch-HEAD binding is enforced on the normal pre-merge path, not only
        # when a completed lane is recovered after cleanup.
        for dispatch_head in (combined_base, None):
            premerge_coordination = json.loads(json.dumps(combined_coordination))
            premerge_dispatch = next(
                item for item in premerge_coordination["lanes"]["only"]["dispatches"]
                if item.get("key") == "oracle/session-2"
            )
            premerge_dispatch["lane_head"] = dispatch_head
            _write_coordination(combined_state, premerge_coordination, combined_plan)
            try:
                lane_merge("only", "combined")
            except StateError as exc:
                assert "lane_head" in str(exc), (dispatch_head, str(exc))
            else:
                raise AssertionError("a stale or missing dispatch HEAD must block pre-merge validation")
        _write_coordination(combined_state, combined_coordination, combined_plan)

        # A changed candidate HEAD cannot reuse the combined verdict.
        subprocess.run(
            ["git", "-C", str(combined_worktree), "commit", "--allow-empty", "-q", "-m", "combined drift"],
            check=True,
        )
        drifted_head = git(combined_worktree, "rev-parse", "HEAD")
        try:
            lane_merge("only", "combined")
        except StateError as exc:
            assert "combined review is missing" in str(exc), str(exc)
        else:
            raise AssertionError("a combined verdict must not cover a moved candidate HEAD")
        subprocess.run(["git", "-C", str(combined_worktree), "reset", "--hard", combined_head], check=True)
        assert git(combined_worktree, "rev-parse", "HEAD") == combined_head

        # A moved feature base invalidates the review before merge, even when the lane candidate
        # and all supplied files remain unchanged.
        subprocess.run(
            ["git", "-C", str(combined_repository), "commit", "--allow-empty", "-q", "-m", "feature drift"],
            check=True,
        )
        try:
            lane_merge("only", "combined")
        except StateError as exc:
            assert "feature base" in str(exc), str(exc)
        else:
            raise AssertionError("a moved feature base must invalidate combined review")
        subprocess.run(["git", "-C", str(combined_repository), "reset", "--hard", combined_base], check=True)
        lane_merge("only", "combined")
        assert git(combined_repository, "rev-parse", "HEAD") == combined_head
        combined_coordination = _read_state(combined_coordination_path)
        combined_merge = combined_coordination["lanes"]["only"]["merge"]
        assert combined_merge["reviewed_head"] == combined_head
        assert combined_merge["merge_commit"] == combined_head
        assert combined_merge["branch"] == git(combined_repository, "branch", "--show-current")
        assert combined_merge["review_mode"] == "combined-single-lane"

        # Completed-lane recovery must re-check both provenance identity and the exact successful
        # Oracle outcome; a hand-edited coordination record cannot turn missing/failed/unknown
        # evidence into a valid combined merge.
        valid_combined_coordination = _read_state(combined_coordination_path)
        combined_evidence_copy = root / "combined-evidence-copy.json"
        combined_evidence_copy.write_text(combined_evidence.read_text(encoding="utf-8"), encoding="utf-8")
        for case_name, field, value, expected in (
            ("stale-dispatch-head", "lane_head", combined_base, "lane_head"),
            ("missing-dispatch-head", "lane_head", None, "lane_head"),
            ("missing-acceptance-snapshot", "acceptance_sha256", None, "snapshot"),
            ("changed-acceptance-snapshot", "acceptance_sha256", "0" * 64, "since dispatch"),
            ("changed-evidence-snapshot", "evidence_sha256", "0" * 64, "since dispatch"),
            ("swapped-evidence-snapshot-path", "evidence_path", str(combined_evidence_copy), "snapshot"),
            ("missing-reviewer-id", "worker_run_id", None, "reviewer dispatch"),
            ("missing-author-id", "worker_run_id", None, "author-capable dispatch"),
            ("failed-result", "result", "failed", "result is not 'ok'"),
            ("unknown-result", "result", "unknown", "result is not 'ok'"),
            ("incomplete-result", "result", None, "result is not 'ok'"),
        ):
            case_coordination = json.loads(json.dumps(valid_combined_coordination))
            case_dispatches = case_coordination["lanes"]["only"]["dispatches"]
            case_key = "oracle/session-2" if field == "worker_run_id" and case_name == "missing-reviewer-id" else (
                "combined/only/implementer" if case_name == "missing-author-id" else "oracle/session-2"
            )
            case_dispatch = next(item for item in case_dispatches if item.get("key") == case_key)
            case_dispatch[field] = value
            _write_coordination(
                _read_state(state_derive("combined", checkout=combined_repository)),
                case_coordination,
                combined_plan,
            )
            try:
                lane_merge("only", "combined")
            except StateError as exc:
                assert expected in str(exc), (case_name, str(exc))
            else:
                raise AssertionError(f"{case_name} must block combined merge recovery")
        _write_coordination(
            _read_state(state_derive("combined", checkout=combined_repository)),
            valid_combined_coordination,
            combined_plan,
        )
        lane_merge("only", "combined")

        # Post-merge recovery requires the exact recorded branch and a clean feature checkout;
        # equal trees do not make a dirty checkout acceptable.
        combined_implementation = combined_repository / "implementation.txt"
        combined_implementation.write_text("unreviewed feature edit\n", encoding="utf-8")
        try:
            lane_merge("only", "combined")
        except StateError as exc:
            assert "feature checkout is dirty" in str(exc), str(exc)
        else:
            raise AssertionError("a dirty completed combined feature checkout must block recovery")
        subprocess.run(["git", "-C", str(combined_repository), "checkout", "--", "implementation.txt"], check=True)

        # Post-merge recovery validates binding-file hashes rather than trusting matching trees.
        combined_evidence.write_text('{"revision":"tampered"}\n', encoding="utf-8")
        try:
            lane_merge("only", "combined")
        except StateError as exc:
            assert "evidence changed" in str(exc), str(exc)
        else:
            raise AssertionError("changed combined evidence must block recovery")
        combined_evidence.write_text('{"revision":"combined-r1"}\n', encoding="utf-8")
        lane_close("only", "combined")
        combined_acceptance.write_text("classification: release\nversion: 0.3\n", encoding="utf-8")
        try:
            lane_merge("only", "combined")
        except StateError as exc:
            assert "acceptance document changed" in str(exc), str(exc)
        else:
            raise AssertionError("changed combined acceptance must block post-close recovery")
        combined_acceptance.write_text("classification: pre-release\nversion: 0.2+1\n", encoding="utf-8")
        lane_merge("only", "combined")
        # Removing the merge record must not turn matching ticks and trees into approval.
        recovered_coordination = _read_state(combined_coordination_path)
        recovered_coordination["lanes"]["only"]["merge"] = None
        _write_coordination(_read_state(state_derive("combined", checkout=combined_repository)), recovered_coordination, combined_plan)
        try:
            lane_merge("only", "combined")
        except StateError as exc:
            assert "has no merge record" in str(exc), str(exc)
        else:
            raise AssertionError("missing combined merge record must fail recovery")

        # Switching a committed plan through standard mode must remove incompatible combined
        # review rounds only after persisted lanes are loaded, while retaining ordinary reviews
        # and the rest of the coordination. Switching back must not resurrect those rounds.
        recovered_coordination["lanes"]["only"]["reviews"].append({
            "round": 7,
            "lane": "only",
            "role": "code",
            "verdict": "PASS",
            "reviewed_commit": combined_head,
            "status": "current",
        })
        _write_coordination(
            _read_state(state_derive("combined", checkout=combined_repository)),
            recovered_coordination,
            combined_plan,
        )
        combined_plan_path = (
            combined_repository / "openspec" / "changes" / "combined" / "plan" / "graph-plan.json"
        )
        standard_plan = json.loads(json.dumps(combined_plan))
        standard_plan["review_mode"] = "standard"
        combined_plan_path.write_text(json.dumps(standard_plan), encoding="utf-8")
        commit(combined_repository, "switch combined plan to standard mode")
        standard_state = _read_state(state_derive("combined", checkout=combined_repository))
        standard_coordination, standard_runtime = _coordination_lane_for(
            standard_state, standard_plan, "only"
        )
        standard_reviews = standard_runtime["reviews"]
        assert any(item.get("round") == 7 for item in standard_reviews), standard_reviews
        assert not any(item.get("combined") is True for item in standard_reviews), standard_reviews
        assert standard_runtime["dispatches"], "mode switch must retain dispatch state"
        _write_coordination(standard_state, standard_coordination, standard_plan)
        persisted_standard = _read_state(_coordination_path("combined", combined_repository))
        assert not any(
            item.get("combined") is True for item in persisted_standard["lanes"]["only"]["reviews"]
        ), persisted_standard

        combined_plan_path.write_text(json.dumps(combined_plan), encoding="utf-8")
        commit(combined_repository, "switch standard plan back to combined mode")
        combined_again_state = _read_state(state_derive("combined", checkout=combined_repository))
        combined_again_coordination, combined_again_runtime = _coordination_lane_for(
            combined_again_state, combined_plan, "only"
        )
        combined_again_reviews = combined_again_runtime["reviews"]
        assert any(item.get("round") == 7 for item in combined_again_reviews), combined_again_reviews
        assert not any(item.get("combined") is True for item in combined_again_reviews), combined_again_reviews

        # Combined recovery keeps plan-driven UI/screen evidence and a one-use grant auditable.
        screen_plan = {
            "schema_version": 1,
            "change": "combined-screen",
            "review_mode": "combined-single-lane",
            "lanes": {
                "only": {"risk": "ordinary", "ui": True, "screen": True,
                          "sensitive": True, "depends_on": []},
            },
            "tasks": {"1.1": {"lane": "only", "deps": []}},
        }
        screen_repository = make_repository(
            "combined-screen-repository", "combined-screen", screen_plan, "- [ ] 1.1 screen work\n"
        )
        os.chdir(screen_repository)
        screen_base = git(screen_repository, "rev-parse", "HEAD")
        lane_open("only", "combined-screen")
        dispatch_run("only", "implementer", "provider/worker:low", "low", "1.1", change="combined-screen")
        dispatch_done("only", "implementer", "screen/implementer", 20, "0.1", "ok", "combined-screen")
        screen_coordination_path = _coordination_path("combined-screen", screen_repository)
        screen_coordination = _read_state(screen_coordination_path)
        screen_worktree = Path(screen_coordination["lanes"]["only"]["worktree"])
        screen_worktree.joinpath("implementation.txt").write_text("screen implementation\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(screen_worktree), "add", "implementation.txt"], check=True)
        subprocess.run(["git", "-C", str(screen_worktree), "commit", "-q", "-m", "screen code"], check=True)
        task_tick("only", "1.1", "combined-screen")
        subprocess.run(
            ["git", "-C", str(screen_worktree), "add", "openspec/changes/combined-screen/tasks.md"], check=True
        )
        subprocess.run(["git", "-C", str(screen_worktree), "commit", "-q", "-m", "screen tick"], check=True)
        screen_head = git(screen_worktree, "rev-parse", "HEAD")
        screen_report = root / "combined-screen-review.md"
        screen_evidence = root / "combined-screen-final-evidence.json"
        screen_acceptance = root / "combined-screen-acceptance.md"
        screen_ui_report = root / "combined-screen-ui-review.md"
        screen_ui_evidence = root / "combined-screen-ui-evidence.png"
        screen_gate_evidence = root / "combined-screen-gate-evidence.json"
        screen_standin_report = root / "combined-screen-stand-in.md"
        screen_checklist = root / "combined-screen-checklist.md"
        screen_report.write_text("combined screen code/test/final review\n", encoding="utf-8")
        screen_evidence.write_text('{"revision":"screen-r1"}\n', encoding="utf-8")
        screen_acceptance.write_text("classification: pre-release\nversion: 0.2+1\n", encoding="utf-8")
        dispatch_run(
            "only", "oracle", "provider/oracle:review", "normal", "1.1",
            key="screen/oracle", change="combined-screen", access="read-only",
            acceptance=screen_acceptance, evidence=screen_evidence,
        )
        dispatch_done("only", "oracle", "screen/oracle", 25, "0.3", "ok", "combined-screen")
        screen_ui_report.write_text("UI review\n", encoding="utf-8")
        screen_ui_evidence.write_text("screen evidence\n", encoding="utf-8")
        screen_gate_evidence.write_text('{"revision":"screen-r1"}\n', encoding="utf-8")
        screen_standin_report.write_text("Oracle stand-in acceptance review\n", encoding="utf-8")
        screen_checklist.write_text("- acceptance checklist complete\n", encoding="utf-8")
        lane_review(
            "only", "code", "PASS", screen_head, screen_report, "screen-r1", screen_evidence,
            change="combined-screen", combined=True, oracle_provenance="screen/oracle",
            feature_base=screen_base, review_base=screen_base, acceptance=screen_acceptance,
        )
        lane_review(
            "only", "ui", "PASS", screen_head, screen_ui_report, "screen-r1", screen_ui_evidence,
            change="combined-screen",
        )
        screen_plan_sha = hashlib.sha256(
            _committed_file(
                screen_repository, git(screen_repository, "rev-parse", "HEAD"),
                "openspec/changes/combined-screen/plan/graph-plan.json",
            ).encode("utf-8")
        ).hexdigest()
        screen_grant = {
            "id": "screen-grant",
            "granted_by": "human",
            "granted_at": "2026-09-12T00:00:00Z",
            "scope": ["only"],
            "plan_sha256": screen_plan_sha,
        }
        screen_grant_file = root / "combined-screen-grant.json"
        screen_grant_file.write_text(json.dumps(screen_grant), encoding="utf-8")
        human_gate(
            "only", "approved", screen_head, "screen-r1", screen_gate_evidence,
            "combined-screen", "oracle", screen_grant, {"verdict": "PASS", "report": screen_standin_report},
            screen_checklist,
        )
        lane_merge("only", "combined-screen")
        screen_coordination = _read_state(screen_coordination_path)
        assert screen_coordination["authorizations"]["screen-grant"]["status"] == "consumed", screen_coordination
        lane_close("only", "combined-screen")
        screen_state = _read_state(state_derive("combined-screen", checkout=screen_repository))

        screen_ui_evidence.unlink()
        try:
            lane_merge("only", "combined-screen")
        except StateError as exc:
            assert "evidence" in str(exc) and "missing" in str(exc), str(exc)
        else:
            raise AssertionError("missing completed screen evidence must block combined recovery")
        screen_ui_evidence.write_text("screen evidence\n", encoding="utf-8")
        lane_merge("only", "combined-screen")
        valid_screen_coordination = _read_state(screen_coordination_path)
        for case_name, mutate, expected in (
            (
                "revoked-grant",
                lambda value: value["authorizations"]["screen-grant"].__setitem__("status", "revoked"),
                "not active",
            ),
            (
                "foreign-consumer",
                lambda value: value["authorizations"]["screen-grant"].update(
                    {"status": "consumed", "consumed_by": "other-lane"}
                ),
                "not active",
            ),
            (
                "missing-grant",
                lambda value: value["authorizations"].pop("screen-grant"),
                "is missing from coordination data",
            ),
            (
                "tampered-stand-in",
                lambda value: screen_standin_report.write_text("tampered\n", encoding="utf-8"),
                "stand-in report for 'only' changed",
            ),
        ):
            case_coordination = json.loads(json.dumps(valid_screen_coordination))
            mutate(case_coordination)
            _write_coordination(screen_state, case_coordination, screen_plan)
            try:
                lane_merge("only", "combined-screen")
            except StateError as exc:
                assert expected in str(exc), (case_name, str(exc))
            else:
                raise AssertionError(f"{case_name} must block consumed-grant recovery")
            if case_name == "tampered-stand-in":
                screen_standin_report.write_text("Oracle stand-in acceptance review\n", encoding="utf-8")
            _write_coordination(screen_state, valid_screen_coordination, screen_plan)
        os.chdir(root)

        # Design D5/D6: risk-based review decisions and ancestor reuse are opt-in.
        # Every standard gate still blocks: shadow records never relax the standard gates,
        # the relaxed path honors only what a recorded decision selects, and the explicit
        # human override is restricted to waivable review duties.
        flex_plan = {
            "schema_version": 1,
            "change": "reviewflex",
            "lanes": {
                "bookkeeping": {"risk": "ordinary", "ui": False, "screen": False, "sensitive": False, "depends_on": []},
                "critical": {"risk": "high", "ui": False, "screen": False, "sensitive": False, "depends_on": []},
                "visible": {"risk": "ordinary", "ui": True, "screen": False, "sensitive": False, "depends_on": []},
                "plain": {"risk": "ordinary", "ui": False, "screen": False, "sensitive": False, "depends_on": []},
                "late": {"risk": "ordinary", "ui": False, "screen": False, "sensitive": False, "depends_on": []},
                "spare": {"risk": "ordinary", "ui": False, "screen": False, "sensitive": False, "depends_on": []},
                "authored": {"risk": "ordinary", "ui": False, "screen": False, "sensitive": False, "depends_on": []},
                "screened": {"risk": "ordinary", "ui": False, "screen": True, "sensitive": False, "depends_on": []},
            },
            "tasks": {
                "1.1": {"lane": "bookkeeping", "deps": []},
                "2.1": {"lane": "critical", "deps": []},
                "3.1": {"lane": "visible", "deps": []},
                "4.1": {"lane": "plain", "deps": []},
                "5.1": {"lane": "late", "deps": []},
                "6.1": {"lane": "spare", "deps": []},
                "7.1": {"lane": "authored", "deps": []},
                "8.1": {"lane": "screened", "deps": []},
            },
        }
        flex_repository = make_repository(
            "flex-repository", "reviewflex", flex_plan,
            "- [ ] 1.1 bookkeeping\n- [ ] 2.1 critical\n- [ ] 3.1 visible\n- [ ] 4.1 plain\n"
            "- [ ] 5.1 late\n- [ ] 6.1 spare\n- [ ] 7.1 authored\n- [ ] 8.1 screened\n",
        )
        os.chdir(flex_repository)
        for lane_id in ("bookkeeping", "critical", "visible", "plain", "late", "spare", "authored", "screened"):
            lane_result, _, lane_error = invoke("lane-open", lane_id, "--change", "reviewflex")
            assert lane_result == 0, (lane_id, lane_error)
        flex_coordination_path = _coordination_path("reviewflex", flex_repository)
        flex_coordination = _read_state(flex_coordination_path)
        flex_worktrees = {
            lane_id: Path(flex_coordination["lanes"][lane_id]["worktree"])
            for lane_id in ("bookkeeping", "critical", "visible", "plain", "late", "spare", "authored", "screened")
        }

        def flex_work(worktree: Path, tick_lane: str, task_id: str, message: str) -> str:
            subprocess.run(["git", "-C", str(worktree), "commit", "--allow-empty", "-q", "-m", message], check=True)
            task_tick(tick_lane, task_id, "reviewflex")
            subprocess.run(
                ["git", "-C", str(worktree), "add", "openspec/changes/reviewflex/tasks.md"], check=True
            )
            subprocess.run(["git", "-C", str(worktree), "commit", "-q", "-m", f"{tick_lane} tick"], check=True)
            return git(worktree, "rev-parse", "HEAD")

        # Invalid decision shapes are refused at record time; high-risk lanes cannot skip
        # independent review, and a lane without UI flags has no UI duty to select.
        try:
            review_decision(
                "bookkeeping", {"code": "sometimes", "test": "full", "ui": "skip"},
                [], "1.1", [], "why", False, "reviewflex",
            )
        except StateError as exc:
            assert "full, focused, or skip" in str(exc), str(exc)
        else:
            raise AssertionError("an unknown duty mode must be refused")
        expect_refusal(
            "review-decision", "critical", "--change", "reviewflex",
            "--code", "skip", "--test", "skip", "--ui", "skip",
            "--acceptance-coverage", "2.1", "--rationale", "narrow",
            contains="high-risk changes require independent review",
        )
        expect_refusal(
            "review-decision", "bookkeeping", "--change", "reviewflex",
            "--code", "skip", "--test", "skip", "--ui", "full",
            "--acceptance-coverage", "1.1", "--rationale", "n",
            contains="no UI review duty",
        )

        # Shadow recording: the record persists and displays as 'skipped', but the legacy
        # gates stay authoritative and lane-merge still refuses without reviews.
        flex_decision_arguments = (
            "review-decision", "bookkeeping", "--change", "reviewflex",
            "--code", "skip", "--test", "skip", "--ui", "skip",
            "--risk-factor", "narrow reversible bookkeeping",
            "--acceptance-coverage", "1.1 is a chores tick; acceptance covered by derived-state checks",
            "--check", "python3 featdev.py --self-check",
            "--rationale", "low-risk: no product code, narrow, reversible, no open findings",
        )
        decision_result, _, decision_error = invoke(*flex_decision_arguments)
        assert decision_result == 0, decision_error
        flex_coordination = _read_state(flex_coordination_path)
        assert flex_coordination["review_policy"] == "legacy", "shadow recording must not activate risk-based policy"
        flex_work(flex_worktrees["bookkeeping"], "bookkeeping", "1.1", "bookkeeping change")
        _, flex_status, _ = invoke("status", "--change", "reviewflex")
        assert "review decisions code=skipped, test=skipped, ui=skipped" in flex_status, flex_status
        expect_refusal("lane-merge", "bookkeeping", "--change", "reviewflex", contains="required review")

        # Opt-in activates the risk-based policy and the recorded skip satisfies the lane,
        # with the selected duty persisted beside the merge (never a PASS label).
        flex_result, _, flex_error = invoke(*flex_decision_arguments, "--opt-in")
        assert flex_result == 0, flex_error
        flex_coordination = _read_state(flex_coordination_path)
        assert flex_coordination["review_policy"] == "risk-based"
        lane_merge("bookkeeping", "reviewflex")
        flex_coordination = _read_state(flex_coordination_path)
        assert flex_coordination["lanes"]["bookkeeping"]["merge"]["review_duties"] == {"code": "skip"}

        # Parallel lanes opened at the same base pick up the merged feature ticks before
        # ticking their own tasks, so later lane merges stay conflict-free.
        for flex_lane in ("critical", "visible", "plain", "late", "spare", "authored", "screened"):
            subprocess.run(
                ["git", "-C", str(flex_worktrees[flex_lane]), "merge", "-q", "--no-edit", "main"],
                check=True, capture_output=True,
            )

        # RW-C04: screen reuse cannot be recorded without evidence, even if its
        # source PASS was malformed. Genuine bound evidence remains reusable.
        screened_head = flex_work(flex_worktrees["screened"], "screened", "8.1", "screen change")
        review_decision(
            "screened", {"code": "full", "test": "skip", "ui": "focused"},
            ["screen presentation"], "8.1", ["screen screenshot"],
            "screen-only layout needs UI inspection", False, "reviewflex",
        )
        assert "ui" in _effective_review_roles(
            flex_plan["lanes"]["screened"],
            _read_state(flex_coordination_path)["lanes"]["screened"]["decisions"],
        )
        screened_report = root / "screened-report.md"
        screened_report.write_text("screen review\n", encoding="utf-8")
        screened_evidence = root / "screened-evidence.json"
        screened_evidence.write_text('{"screenshot": "original"}\n', encoding="utf-8")
        lane_review("screened", "code", "PASS", screened_head, screened_report, "sc1",
                    screened_evidence, change="reviewflex")
        expect_refusal("lane-merge", "screened", "--change", "reviewflex", contains="required review 'ui'")
        (flex_worktrees["screened"] / "note.txt").write_text("bookkeeping\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(flex_worktrees["screened"]), "add", "note.txt"], check=True)
        subprocess.run(["git", "-C", str(flex_worktrees["screened"]), "commit", "-qm", "note"], check=True)
        screened_diff = root / "screened.diff"
        screened_diff.write_text(_run_git(flex_worktrees["screened"], "diff", screened_head, "HEAD").stdout,
                                 encoding="utf-8")
        screened_args = (
            "review-reuse", "screened", "code", "--change", "reviewflex", "--ancestor", screened_head,
            "--intervening-diff", str(screened_diff), "--assessment", "bookkeeping only",
        )
        screened_valid = _read_state(flex_coordination_path)
        screened_missing = json.loads(json.dumps(screened_valid))
        screened_missing["lanes"]["screened"]["reviews"][-1]["binding"].pop("evidence_path")
        _atomic_write_json(flex_coordination_path, screened_missing)
        expect_refusal(*screened_args, contains="requires path and digest for a screen lane")
        _atomic_write_json(flex_coordination_path, screened_valid)
        screened_result, _, screened_error = invoke(*screened_args)
        assert screened_result == 0, screened_error
        screened_lane = _read_state(flex_coordination_path)["lanes"]["screened"]
        screened_lane["screen"] = True
        screened_current = git(flex_worktrees["screened"], "rev-parse", "HEAD")
        _validate_lane_reviews("screened", screened_lane, screened_current,
                               repository=flex_repository, allow_reuse=True)
        for fields in (("evidence_path",), ("evidence_sha256",), ("evidence_path", "evidence_sha256")):
            missing = json.loads(json.dumps(screened_lane))
            for field in fields:
                missing["reviews"][-1]["binding"].pop(field)
            try:
                _validate_lane_reviews("screened", missing, screened_current,
                                       repository=flex_repository, allow_reuse=True)
            except StateError as exc:
                assert "requires path and digest for a screen lane" in str(exc), str(exc)
            else:
                raise AssertionError("screen reuse without an evidence binding must refuse")

        # A lane without a decision record falls back to the standard gate under the
        # risk-based policy; a high-risk lane's own decision keeps independent review.
        flex_work(flex_worktrees["plain"], "plain", "4.1", "plain change")
        expect_refusal("lane-merge", "plain", "--change", "reviewflex", contains="required review")

        # RW-C03 regression: a selected full/focused test duty binds an ordinary lane too.
        # The selected duty is enforced at merge and persisted beside it; without a valid
        # decision record the legacy role set is unchanged.
        review_decision(
            "plain", {"code": "skip", "test": "full", "ui": "skip"},
            ["narrow reversible bookkeeping with a selected test duty"], "4.1",
            ["python3 featdev.py --self-check"],
            "ordinary lane selecting a full test duty; the duty is then required",
            False, "reviewflex",
        )
        plain_head = git(flex_worktrees["plain"], "rev-parse", "HEAD")
        expect_refusal(
            "lane-merge", "plain", "--change", "reviewflex",
            contains="required review 'test'",
        )
        plain_test_report = root / "flex-plain-test-review.md"
        plain_test_report.write_text("independent ordinary-lane test review\n", encoding="utf-8")
        lane_review("plain", "test", "PASS", plain_head, plain_test_report, "pl1", change="reviewflex")
        lane_merge("plain", "reviewflex")
        flex_coordination = _read_state(flex_coordination_path)
        assert flex_coordination["lanes"]["plain"]["merge"]["review_duties"] == {"code": "skip", "test": "full"}
        flex_work(flex_worktrees["critical"], "critical", "2.1", "critical change")
        expect_refusal("lane-merge", "critical", "--change", "reviewflex", contains="required review")

        # Ancestor reuse (task 5.2). A fresh ordinary PASS is recorded at the critical
        # lane's first HEAD; unrelated bookkeeping follows, and only a recorded
        # intervening diff plus unchanged evidence inputs can reuse that verdict.
        critical_first_head = git(flex_worktrees["critical"], "rev-parse", "HEAD")
        critical_report = root / "flex-critical-code-review.md"
        critical_report.write_text("independent high-risk code review\n", encoding="utf-8")
        lane_review("critical", "code", "PASS", critical_first_head, critical_report, "cr1", change="reviewflex")
        (flex_worktrees["critical"] / "bookkeeping-note.txt").write_text("bookkeeping only\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(flex_worktrees["critical"]), "add", "bookkeeping-note.txt"], check=True
        )
        subprocess.run(
            ["git", "-C", str(flex_worktrees["critical"]), "commit", "-q", "-m", "unrelated bookkeeping"],
            check=True,
        )
        critical_head = git(flex_worktrees["critical"], "rev-parse", "HEAD")
        intervening_diff = root / "flex-intervening.diff"
        intervening_diff.write_text(
            subprocess.run(
                ["git", "-C", str(flex_worktrees["critical"]), "diff", critical_first_head, critical_head],
                capture_output=True, text=True, check=True,
            ).stdout,
            encoding="utf-8",
        )
        # A non-ancestor commit is never a valid reuse source.
        subprocess.run(["git", "-C", str(flex_worktrees["plain"]), "commit", "--allow-empty", "-q", "-m", "plain follow-up"], check=True)
        plain_follow_up = git(flex_worktrees["plain"], "rev-parse", "HEAD")
        expect_refusal(
            "review-reuse", "critical", "code", "--change", "reviewflex",
            "--ancestor", plain_follow_up, "--intervening-diff", str(intervening_diff),
            "--assessment", "nothing touched",
            contains="not an ancestor",
        )
        # A strict-ancestor citation is refused; record a fresh review at current HEAD instead.
        expect_refusal(
            "review-reuse", "critical", "code", "--change", "reviewflex",
            "--ancestor", critical_head, "--intervening-diff", str(intervening_diff),
            "--assessment", "nothing touched",
            contains="strict ancestor",
        )
        # Ancestor without a recorded PASS cannot be reused.
        planning_commit = git(flex_repository, "rev-list", "--max-parents=0", "HEAD")
        planning_diff = root / "flex-planning.diff"
        planning_diff.write_text(
            subprocess.run(
                ["git", "-C", str(flex_worktrees["critical"]), "diff", planning_commit, critical_head],
                capture_output=True, text=True, check=True,
            ).stdout,
            encoding="utf-8",
        )
        expect_refusal(
            "review-reuse", "critical", "code", "--change", "reviewflex",
            "--ancestor", planning_commit, "--intervening-diff", str(planning_diff),
            "--assessment", "nothing touched",
            contains="no completed ordinary",
        )
        # Changed evidence inputs require fresh re-review, not reuse.
        expect_refusal(
            "review-reuse", "critical", "code", "--change", "reviewflex",
            "--ancestor", critical_first_head, "--intervening-diff", str(intervening_diff),
            "--assessment", "nothing touched", "--evidence-inputs-changed", "yes",
            contains="fresh re-review",
        )
        reuse_result, _, reuse_error = invoke(
            "review-reuse", "critical", "code", "--change", "reviewflex",
            "--ancestor", critical_first_head, "--intervening-diff", str(intervening_diff),
            "--assessment", "unrelated bookkeeping: no behavior or evidence inputs changed",
        )
        assert reuse_result == 0, reuse_error
        _, flex_status, _ = invoke("status", "--change", "reviewflex")
        assert "code=reused" in flex_status and "=PASS" not in flex_status.split("critical")[1].split("lane visible")[0], flex_status
        # Reuse cannot chain through another reuse record.
        (flex_worktrees["critical"] / "bookkeeping-note2.txt").write_text("more bookkeeping\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(flex_worktrees["critical"]), "add", "bookkeeping-note2.txt"], check=True)
        subprocess.run(["git", "-C", str(flex_worktrees["critical"]), "commit", "-q", "-m", "more bookkeeping"], check=True)
        critical_head2 = git(flex_worktrees["critical"], "rev-parse", "HEAD")
        intervening_diff2 = root / "flex-intervening-2.diff"
        intervening_diff2.write_text(
            subprocess.run(
                ["git", "-C", str(flex_worktrees["critical"]), "diff", critical_head, critical_head2],
                capture_output=True, text=True, check=True,
            ).stdout,
            encoding="utf-8",
        )
        expect_refusal(
            "review-reuse", "critical", "code", "--change", "reviewflex",
            "--ancestor", critical_head, "--intervening-diff", str(intervening_diff2),
            "--assessment", "nothing touched",
            contains="no completed ordinary",
        )
        # A fresh assessed reuse re-binds the original PASS to the new HEAD.
        intervening_diff_wide = root / "flex-intervening-wide.diff"
        intervening_diff_wide.write_text(
            subprocess.run(
                ["git", "-C", str(flex_worktrees["critical"]), "diff", critical_first_head, critical_head2],
                capture_output=True, text=True, check=True,
            ).stdout,
            encoding="utf-8",
        )
        rebind_result, _, rebind_error = invoke(
            "review-reuse", "critical", "code", "--change", "reviewflex",
            "--ancestor", critical_first_head, "--intervening-diff", str(intervening_diff_wide),
            "--assessment", "still unrelated bookkeeping; evidence inputs unchanged",
        )
        assert rebind_result == 0, rebind_error
        critical_test_report = root / "flex-critical-test-review.md"
        critical_test_report.write_text("independent test review\n", encoding="utf-8")
        lane_review("critical", "test", "PASS", critical_head2, critical_test_report, "cr2", change="reviewflex")
        review_decision(
            "critical",
            {"code": "full", "test": "full", "ui": "skip"},
            ["touches conflict decisions"], "2.1", ["focused suites"], "high-risk lane keeps independent review",
            False, "reviewflex",
        )
        lane_merge("critical", "reviewflex")
        flex_coordination = _read_state(flex_coordination_path)
        assert flex_coordination["lanes"]["critical"]["merge"]["review_duties"] == {"code": "full", "test": "full"}

        # RW-C01/C04 regression: reuse binds the real ancestor..HEAD diff, refuses garbage,
        # refuses post-record tampering (file and digest), and is never honored by legacy.
        spare_first_head = flex_work(flex_worktrees["spare"], "spare", "6.1", "spare change")
        spare_report = root / "flex-spare-code-review.md"
        spare_report.write_text("independent ordinary code review\n", encoding="utf-8")
        spare_evidence = root / "flex-spare-evidence.json"
        spare_evidence.write_text('{"screenshot": "original"}\n', encoding="utf-8")
        lane_review("spare", "code", "PASS", spare_first_head, spare_report, "sp1", spare_evidence, change="reviewflex")
        # An empty intervening diff cannot bind a reuse record.
        subprocess.run(
            ["git", "-C", str(flex_worktrees["spare"]), "commit", "--allow-empty", "-q", "-m", "empty step"],
            check=True,
        )
        garbage_diff = root / "flex-garbage.diff"
        garbage_diff.write_text("NOT AN INTERVENING DIFF\n", encoding="utf-8")
        expect_refusal(
            "review-reuse", "spare", "code", "--change", "reviewflex",
            "--ancestor", spare_first_head, "--intervening-diff", str(garbage_diff),
            "--assessment", "nothing touched",
            contains="empty intervening diff",
        )
        # A fabricated diff is refused: the helper binds the actual git diff, not caller input.
        (flex_worktrees["spare"] / "spare-note.txt").write_text("spare only\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(flex_worktrees["spare"]), "add", "spare-note.txt"], check=True)
        subprocess.run(["git", "-C", str(flex_worktrees["spare"]), "commit", "-q", "-m", "spare note"], check=True)
        spare_head = git(flex_worktrees["spare"], "rev-parse", "HEAD")
        expect_refusal(
            "review-reuse", "spare", "code", "--change", "reviewflex",
            "--ancestor", spare_first_head, "--intervening-diff", str(garbage_diff),
            "--assessment", "nothing touched",
            contains="does not match the actual git diff",
        )
        spare_diff = root / "flex-spare.diff"
        spare_diff.write_text(
            subprocess.run(
                ["git", "-C", str(flex_worktrees["spare"]), "diff", spare_first_head, spare_head],
                capture_output=True, text=True, check=True,
            ).stdout,
            encoding="utf-8",
        )
        # TEST-R2-01: ordinary legacy-policy reuse must fail at record time too.
        spare_before_reuse = _read_state(flex_coordination_path)
        spare_legacy_record = json.loads(json.dumps(spare_before_reuse))
        spare_legacy_record["review_policy"] = "legacy"
        _atomic_write_json(flex_coordination_path, spare_legacy_record)
        expect_refusal(
            "review-reuse", "spare", "code", "--change", "reviewflex",
            "--ancestor", spare_first_head, "--intervening-diff", str(spare_diff),
            "--assessment", "unchanged inputs",
            contains="risk-based review policy",
        )
        _atomic_write_json(flex_coordination_path, spare_before_reuse)
        spare_evidence.unlink()
        expect_refusal(
            "review-reuse", "spare", "code", "--change", "reviewflex",
            "--ancestor", spare_first_head, "--intervening-diff", str(spare_diff),
            "--assessment", "unchanged inputs",
            contains="ancestor review evidence",
        )
        spare_evidence.write_text('{"screenshot": "original"}\n', encoding="utf-8")
        spare_evidence.write_text('{"screenshot": "changed before reuse"}\n', encoding="utf-8")
        expect_refusal(
            "review-reuse", "spare", "code", "--change", "reviewflex",
            "--ancestor", spare_first_head, "--intervening-diff", str(spare_diff),
            "--assessment", "unchanged inputs", contains="changed since direct review",
        )
        spare_evidence.write_text('{"screenshot": "original"}\n', encoding="utf-8")
        legacy_digest = _read_state(flex_coordination_path)
        legacy_digest["lanes"]["spare"]["reviews"][-1]["binding"].pop("evidence_sha256")
        _atomic_write_json(flex_coordination_path, legacy_digest)
        expect_refusal(
            "review-reuse", "spare", "code", "--change", "reviewflex",
            "--ancestor", spare_first_head, "--intervening-diff", str(spare_diff),
            "--assessment", "unchanged inputs", contains="changed since direct review",
        )
        legacy_digest["lanes"]["spare"]["reviews"][-1]["binding"]["evidence_sha256"] = _path_sha256(spare_evidence)
        _atomic_write_json(flex_coordination_path, legacy_digest)
        spare_reuse_result, _, spare_reuse_error = invoke(
            "review-reuse", "spare", "code", "--change", "reviewflex",
            "--ancestor", spare_first_head, "--intervening-diff", str(spare_diff),
            "--assessment", "spare note only; no behavior or evidence inputs changed",
        )
        assert spare_reuse_result == 0, spare_reuse_error
        # RW-C04: even optional bound evidence must keep identical bytes.
        spare_evidence.write_text('{"screenshot": "changed"}\n', encoding="utf-8")
        expect_refusal("lane-merge", "spare", "--change", "reviewflex", contains="evidence for 'code' changed")
        spare_evidence.unlink()
        expect_refusal("lane-merge", "spare", "--change", "reviewflex", contains="reused review evidence")
        spare_evidence.write_text('{"screenshot": "original"}\n', encoding="utf-8")
        valid_evidence_coordination = _read_state(flex_coordination_path)
        for field, value in (("evidence_sha256", None), ("evidence_path", None)):
            tampered_evidence = json.loads(json.dumps(valid_evidence_coordination))
            tampered_evidence["lanes"]["spare"]["reviews"][-1]["binding"][field] = value
            _atomic_write_json(flex_coordination_path, tampered_evidence)
            expect_refusal("lane-merge", "spare", "--change", "reviewflex", contains="evidence")
        # Erasing both optional fields or substituting another valid file must not
        # detach reuse from the direct ancestor's evidence (normal merge and recovery).
        def refuse_detached_evidence(valid: dict[str, Any]) -> None:
            alternate = root / "alternate-evidence.json"
            alternate.write_text('different evidence\n', encoding="utf-8")
            for substitute in (False, True):
                tampered = json.loads(json.dumps(valid))
                binding = tampered["lanes"]["spare"]["reviews"][-1]["binding"]
                binding.pop("evidence_path")
                binding.pop("evidence_sha256")
                if substitute:
                    binding.update(evidence_path=str(alternate), evidence_sha256=_path_sha256(alternate))
                _atomic_write_json(flex_coordination_path, tampered)
                spare_evidence.write_text('changed source evidence\n', encoding="utf-8")
                expect_refusal("lane-merge", "spare", "--change", "reviewflex",
                               contains="differs from its direct ancestor PASS")
            spare_evidence.write_text('{"screenshot": "original"}\n', encoding="utf-8")
            _atomic_write_json(flex_coordination_path, valid)

        refuse_detached_evidence(valid_evidence_coordination)
        # A diff mutated after the record refuses the merge; a mutated digest does too.
        spare_diff.write_text("TAMPERED AFTER RECORD\n", encoding="utf-8")
        expect_refusal(
            "lane-merge", "spare", "--change", "reviewflex",
            contains="changed after the reuse record",
        )
        spare_diff.write_text(
            subprocess.run(
                ["git", "-C", str(flex_worktrees["spare"]), "diff", spare_first_head, spare_head],
                capture_output=True, text=True, check=True,
            ).stdout,
            encoding="utf-8",
        )
        valid_spare_coordination = _read_state(flex_coordination_path)
        spare_digest_tamper = json.loads(json.dumps(valid_spare_coordination))
        spare_digest_tamper["lanes"]["spare"]["reviews"][-1]["binding"]["intervening_diff_sha256"] = "0" * 64
        _atomic_write_json(flex_coordination_path, spare_digest_tamper)
        expect_refusal(
            "lane-merge", "spare", "--change", "reviewflex",
            contains="changed after the reuse record",
        )
        _atomic_write_json(flex_coordination_path, valid_spare_coordination)
        # Legacy policy never honors a reuse record: the standard gate must still block.
        spare_legacy = json.loads(json.dumps(valid_spare_coordination))
        spare_legacy["review_policy"] = "legacy"
        _atomic_write_json(flex_coordination_path, spare_legacy)
        expect_refusal(
            "lane-merge", "spare", "--change", "reviewflex",
            contains="ancestor-reuse record",
        )
        _atomic_write_json(flex_coordination_path, valid_spare_coordination)
        # The genuine, unchanged bookkeeping diff satisfies the gate end to end.
        lane_merge("spare", "reviewflex")
        flex_coordination = _read_state(flex_coordination_path)
        assert "review_duties" not in flex_coordination["lanes"]["spare"]["merge"]
        # Completed-merge recovery revalidates evidence, not just its path/revision.
        spare_evidence.write_text('{"screenshot": "changed after merge"}\n', encoding="utf-8")
        expect_refusal("lane-merge", "spare", "--change", "reviewflex", contains="evidence for 'code' changed")
        spare_evidence.unlink()
        expect_refusal("lane-merge", "spare", "--change", "reviewflex", contains="reused review evidence")
        spare_evidence.write_text('{"screenshot": "original"}\n', encoding="utf-8")
        lane_merge("spare", "reviewflex")
        recovered_spare = _read_state(flex_coordination_path)
        refuse_detached_evidence(recovered_spare)
        authored_recovery = json.loads(json.dumps(recovered_spare))
        authored_recovery["lanes"]["spare"].setdefault("dispatches", []).append(
            {"role": "oracle", "access": "write"}
        )
        _atomic_write_json(flex_coordination_path, authored_recovery)
        expect_refusal(
            "lane-merge", "spare", "--change", "reviewflex",
            contains="direct independent current-HEAD review",
        )
        _atomic_write_json(flex_coordination_path, recovered_spare)

        # RW-C05 regression: Oracle-authored write work cannot lose independent review.
        # Pick up the merged feature ticks before ticking this lane's own task.
        subprocess.run(
            ["git", "-C", str(flex_worktrees["authored"]), "merge", "-q", "--no-edit", "main"],
            check=True, capture_output=True,
        )
        authored_decision_arguments = (
            "review-decision", "authored", "--change", "reviewflex",
            "--code", "skip", "--test", "skip", "--ui", "skip",
            "--risk-factor", "narrow authored follow-up",
            "--acceptance-coverage", "7.1 follow-up tick; acceptance covered by derived-state checks",
            "--check", "python3 featdev.py --self-check",
            "--rationale", "ordinary risk: narrow, reversible, no open findings",
        )
        authored_decision_result, _, authored_decision_error = invoke(*authored_decision_arguments)
        assert authored_decision_result == 0, authored_decision_error
        authored_first_head = flex_work(flex_worktrees["authored"], "authored", "7.1", "pre-Oracle change")
        authored_report = root / "flex-authored-code-review.md"
        authored_report.write_text("independent pre-Oracle review\n", encoding="utf-8")
        lane_review("authored", "code", "PASS", authored_first_head, authored_report, "au0", change="reviewflex")
        (flex_worktrees["authored"] / "authored-note.txt").write_text("correction\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(flex_worktrees["authored"]), "add", "authored-note.txt"], check=True)
        subprocess.run(["git", "-C", str(flex_worktrees["authored"]), "commit", "-q", "-m", "correction"], check=True)
        authored_head = git(flex_worktrees["authored"], "rev-parse", "HEAD")
        authored_diff = root / "flex-authored.diff"
        authored_diff.write_text(
            _run_git(flex_worktrees["authored"], "diff", f"{authored_first_head}..{authored_head}", "--").stdout,
            encoding="utf-8",
        )
        # Record reuse before dispatch to prove merge independently rejects old records.
        review_reuse("authored", "code", authored_first_head, authored_diff, "unchanged inputs", False, "reviewflex")
        dispatch_result, _, dispatch_error = invoke(
            "dispatch", "authored", "oracle", "--model", "oracle-x", "--level", "low",
            "--task", "7.1", "--access", "write", "--change", "reviewflex",
        )
        assert dispatch_result == 0, dispatch_error
        # TEST-R2-01: write dispatch must also block a newly recorded skip decision.
        expect_refusal(*authored_decision_arguments, "--opt-in", contains="Oracle-authored")
        expect_refusal(
            "review-reuse", "authored", "code", "--change", "reviewflex",
            "--ancestor", authored_first_head, "--intervening-diff", str(authored_diff),
            "--assessment", "unchanged inputs", contains="direct independent current-HEAD review",
        )
        # The skip decision recorded before the write dispatch can never relax this merge.
        expect_refusal(
            "lane-merge", "authored", "--change", "reviewflex",
            contains="Oracle-authored",
        )
        # A fresh decision that keeps the duty plus a current independent PASS satisfies it.
        review_decision(
            "authored", {"code": "full", "test": "skip", "ui": "skip"},
            [], "7.1", [], "authored work keeps an independent code review",
            False, "reviewflex",
        )
        expect_refusal(
            "lane-merge", "authored", "--change", "reviewflex",
            contains="direct independent current-HEAD review",
        )
        authored_report.write_text("independent review of Oracle-authored work\n", encoding="utf-8")
        lane_review("authored", "code", "PASS", authored_head, authored_report, "au1", change="reviewflex")
        lane_merge("authored", "reviewflex")
        flex_coordination = _read_state(flex_coordination_path)
        assert flex_coordination["lanes"]["authored"]["merge"]["review_duties"] == {"code": "full"}

        # The visible lane picks up the merged critical tick before ticking its own task.
        subprocess.run(
            ["git", "-C", str(flex_worktrees["visible"]), "merge", "-q", "--no-edit", "main"],
            check=True, capture_output=True,
        )
        # The visible lane: a decision record never substitutes for the human checkpoint,
        # and a human override still binds ticks, tree, base, and the human gate.
        try:
            review_decision(
                "visible", {"code": "focused", "test": "skip", "ui": "full"},
                ["single-screen copy"], "3.1", ["rendered screenshot review"], "focused UI duty, tests untouched",
                False, "reviewflex",
            )
        except StateError as exc:
            raise AssertionError(f"visible lane decision must be recordable: {exc}")
        visible_head = flex_work(flex_worktrees["visible"], "visible", "3.1", "visible change")
        expect_refusal("lane-merge", "visible", "--change", "reviewflex", contains="required review")
        visible_code_report = root / "flex-visible-code-review.md"
        visible_code_report.write_text("focused code review\n", encoding="utf-8")
        visible_ui_report = root / "flex-visible-ui-review.md"
        visible_ui_report.write_text("ui review\n", encoding="utf-8")
        lane_review("visible", "code", "PASS", visible_head, visible_code_report, "vr1", change="reviewflex")
        lane_review("visible", "ui", "PASS", visible_head, visible_ui_report, "vr1", change="reviewflex")
        expect_refusal("lane-merge", "visible", "--change", "reviewflex", contains="human gate")
        # A human override cannot waive the human checkpoint.
        expect_refusal(
            "lane-merge", "visible", "--human-override", "--override-reason", "human decision",
            "--change", "reviewflex", contains="human gate",
        )
        human_gate("visible", "approved", visible_head, "vr1", change="reviewflex")
        # A dirty lane worktree still blocks an override merge.
        (flex_worktrees["visible"] / "scratch.txt").write_text("dirty\n", encoding="utf-8")
        expect_refusal(
            "lane-merge", "visible", "--human-override", "--override-reason", "human decision",
            "--change", "reviewflex", contains="dirty",
        )
        (flex_worktrees["visible"] / "scratch.txt").unlink()
        # A tampered decision record blocks the gate until a fresh decision is recorded.
        valid_flex_coordination = _read_state(flex_coordination_path)
        tampered = json.loads(json.dumps(valid_flex_coordination))
        tampered["lanes"]["visible"]["decisions"]["duties"]["code"]["mode"] = "skip"
        _atomic_write_json(flex_coordination_path, tampered)
        expect_refusal(
            "lane-merge", "visible", "--change", "reviewflex",
            contains="review decision record changed",
        )
        _atomic_write_json(flex_coordination_path, valid_flex_coordination)
        lane_merge("visible", "reviewflex")
        flex_coordination = _read_state(flex_coordination_path)
        assert flex_coordination["lanes"]["visible"]["merge"]["review_duties"] == {"code": "focused", "ui": "full"}

        # Unticked tasks and an unreconcilable worktree still block an override merge.
        subprocess.run(
            ["git", "-C", str(flex_worktrees["late"]), "merge", "-q", "--no-edit", "main"],
            check=True, capture_output=True,
        )
        subprocess.run(["git", "-C", str(flex_worktrees["late"]), "commit", "--allow-empty", "-q", "-m", "late work"], check=True)
        expect_refusal(
            "lane-merge", "late", "--human-override", "--override-reason", "human decision",
            "--change", "reviewflex", contains="unfinished tasks",
        )
        task_tick("late", "5.1", "reviewflex")
        subprocess.run(["git", "-C", str(flex_worktrees["late"]), "add", "openspec/changes/reviewflex/tasks.md"], check=True)
        subprocess.run(["git", "-C", str(flex_worktrees["late"]), "commit", "-q", "-m", "late tick"], check=True)
        late_ref = flex_coordination["lanes"]["late"]["ref"]
        subprocess.run(
            ["git", "-C", str(flex_repository), "worktree", "remove", "--force", str(flex_worktrees["late"])],
            check=True,
        )
        subprocess.run(["git", "-C", str(flex_repository), "branch", "-D", late_ref], check=True)
        expect_refusal(
            "lane-merge", "late", "--human-override", "--override-reason", "human decision",
            "--change", "reviewflex", contains="reconcile it first",
        )

        os.chdir(root)

        # RW-C02 regression: the change that introduces the risk-based gates cannot use
        # them on its own lanes (design D6). Own-change --opt-in refuses, reuse without the
        # policy refuses, and even a hand-set risk-based policy cannot relax the merge.
        self_plan = {
            "schema_version": 1,
            "change": "optimize-testing-and-review-velocity",
            "lanes": {
                "only": {"risk": "ordinary", "ui": False, "screen": False, "sensitive": False, "depends_on": []},
            },
            "tasks": {"1.1": {"lane": "only", "deps": []}},
        }
        self_repository = make_repository(
            "self-repository", "optimize-testing-and-review-velocity", self_plan, "- [ ] 1.1 only\n"
        )
        self_change = "optimize-testing-and-review-velocity"
        os.chdir(self_repository)
        self_open_result, _, self_open_error = invoke("lane-open", "only", "--change", self_change)
        assert self_open_result == 0, self_open_error
        self_coordination_path = _coordination_path(self_change, self_repository)
        self_coordination = _read_state(self_coordination_path)
        only_worktree = Path(self_coordination["lanes"]["only"]["worktree"])
        subprocess.run(["git", "-C", str(only_worktree), "commit", "--allow-empty", "-q", "-m", "only work"], check=True)
        task_tick("only", "1.1", self_change)
        subprocess.run(
            ["git", "-C", str(only_worktree), "add", f"openspec/changes/{self_change}/tasks.md"], check=True
        )
        subprocess.run(["git", "-C", str(only_worktree), "commit", "-q", "-m", "only tick"], check=True)
        only_head = git(only_worktree, "rev-parse", "HEAD")
        expect_refusal(
            "review-decision", "only", "--change", self_change,
            "--code", "skip", "--test", "skip", "--ui", "skip",
            "--acceptance-coverage", "1.1", "--rationale", "self-relaxation attempt", "--opt-in",
            contains="relaxing its own review gates",
        )
        garbage_self_diff = root / "self-garbage.diff"
        garbage_self_diff.write_text("NOT AN INTERVENING DIFF\n", encoding="utf-8")
        expect_refusal(
            "review-reuse", "only", "code", "--change", self_change,
            "--ancestor", only_head, "--intervening-diff", str(garbage_self_diff),
            "--assessment", "nothing touched",
            contains="risk-based review policy",
        )
        shadow_result, _, shadow_error = invoke(
            "review-decision", "only", "--change", self_change,
            "--code", "skip", "--test", "skip", "--ui", "skip",
            "--risk-factor", "shadow-only recording stays observable",
            "--acceptance-coverage", "1.1", "--rationale", "shadow-only under legacy authority",
        )
        assert shadow_result == 0, shadow_error
        self_coordination = _read_state(self_coordination_path)
        assert self_coordination["review_policy"] == "legacy"
        self_coordination["review_policy"] = "risk-based"
        _atomic_write_json(self_coordination_path, self_coordination)
        subprocess.run(
            ["git", "-C", str(only_worktree), "commit", "--allow-empty", "-q", "-m", "only follow-up"],
            check=True,
        )
        expect_refusal(
            "lane-merge", "only", "--change", self_change,
            contains="required review 'code' is missing",
        )
        os.chdir(root)

    os.chdir(original_cwd)
    os.environ.clear()
    os.environ.update(saved_environment)
    print("self-check: PASS")
    return 0


def _add_change_option(command: argparse.ArgumentParser) -> None:
    command.add_argument("--change", dest="state_change", metavar="<change>")


def _selected_change(args: argparse.Namespace) -> str | None:
    return getattr(args, "state_change", None) or getattr(args, "global_change", None)


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        description="Validate committed feature-development state and record review/finish decisions. See docs/testing.md for impact-based test selection; the standard review gates apply unless a change opts in via review-decision --opt-in.",
        epilog=FIELD_TABLE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--self-check", action="store_true", help="run built-in checks and exit")
    parser.add_argument("--change", dest="global_change", metavar="<change>", help="select the change state")
    commands = parser.add_subparsers(dest="command")

    validate = commands.add_parser("plan-validate", help="run the four plan checks")
    validate.add_argument("plan", type=Path, metavar="<plan.json>")
    validate.add_argument("--tasks", type=Path, metavar="<tasks.md>")

    graph = commands.add_parser("plan-graph", help="print a read-only plan view")
    graph.add_argument("plan", type=Path, metavar="<plan.json>")
    graph.add_argument("--format", choices=("md", "mermaid"), default="md")

    derive = commands.add_parser("state-derive", help="derive change state from committed artifacts")
    derive.add_argument("change", metavar="<change>")
    derive.add_argument("--ref", dest="source_ref", metavar="<committed-ref>")

    tick = commands.add_parser("task-tick", help="check a plan-assigned task in a lane worktree")
    tick.add_argument("lane", metavar="<lane>")
    tick.add_argument("task", metavar="<task>")
    _add_change_option(tick)

    send = commands.add_parser("dispatch", help="record a temporary worker dispatch intent")
    send.add_argument("lane", metavar="<lane>")
    send.add_argument("role", metavar="<role>")
    send.add_argument("--model", required=True, metavar="<model>")
    send.add_argument("--level", required=True, metavar="<level>")
    send.add_argument("--task", required=True, metavar="<task>")
    send.add_argument("--key", metavar="<key>")
    send.add_argument(
        "--access", choices=("read-only", "write"),
        help="dispatch access; implementers default to write, reviewers and Oracle advice to read-only",
    )
    send.add_argument(
        "--acceptance", metavar="<path>",
        help="combined-review acceptance document to snapshot at Oracle dispatch",
    )
    send.add_argument(
        "--evidence", metavar="<path>",
        help="combined-review evidence document to snapshot at Oracle dispatch",
    )
    _add_change_option(send)

    sent = commands.add_parser("dispatch-done", help="record a temporary worker outcome")
    sent.add_argument("lane", metavar="<lane>")
    sent.add_argument("role", metavar="<role>")
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

    reviewed = commands.add_parser("lane-review", help="record a temporary lane review")
    reviewed.add_argument("lane", metavar="<lane>")
    reviewed.add_argument("role", metavar="<role>")
    reviewed.add_argument("verdict", choices=("PASS", "MORE WORK"), metavar="<PASS|MORE WORK>")
    reviewed.add_argument("--commit", required=True, metavar="<sha>")
    reviewed.add_argument("--report", required=True, metavar="<path>")
    reviewed.add_argument("--evidence-revision", metavar="<rev>")
    reviewed.add_argument("--evidence", metavar="<path>")
    reviewed.add_argument(
        "--combined", "--combined-final", dest="combined", action="store_true",
        help="record the eligible single-lane Oracle/code/final review",
    )
    reviewed.add_argument(
        "--oracle-provenance", "--reviewer", "--oracle", dest="oracle_provenance", metavar="<id>",
        help="Oracle reviewer/session provenance (required with --combined)",
    )
    reviewed.add_argument("--feature-base", metavar="<sha>", help="unchanged feature-branch base (combined mode)")
    reviewed.add_argument(
        "--review-base", "--reviewed-base", dest="review_base", metavar="<sha>",
        help="whole-change review base (combined mode)",
    )
    reviewed.add_argument(
        "--acceptance", "--acceptance-path", dest="acceptance", metavar="<path>",
        help="final acceptance document (combined mode)",
    )
    _add_change_option(reviewed)

    gate = commands.add_parser(
        "human-gate",
        help="record a temporary human gate (interactive, or an Oracle stand-in under a grant)",
        epilog=(
            "A grant is JSON with: id, granted_by ('human'), granted_at, scope (['all'] or lane ids)\n"
            "and plan_sha256 (the approved plan digest). With --approved-by oracle the per-workstream\n"
            "checkpoint is satisfied by that grant plus a PASS Oracle stand-in whose report covers\n"
            "--checklist; the grant is then consumed. Merging into the target branch is never\n"
            "authorizable and is not performed by this tool."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    gate.add_argument("lane", metavar="<lane>")
    gate.add_argument(
        "--state", required=True, choices=("waiting", "approved", "rejected", "revoked"), metavar="<state>"
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

    mediate = commands.add_parser(
        "lane-mediate", help="record an Oracle mediation after the two-round review limit"
    )
    mediate.add_argument("lane", metavar="<lane>")
    mediate.add_argument("--report", required=True, metavar="<path>")
    mediate.add_argument("--role", metavar="<role>", help="limit the mediation to one review role")
    _add_change_option(mediate)

    decision = commands.add_parser(
        "review-decision",
        help="record the Coordinator's per-lane review-duty decision for code/test/ui",
        epilog=(
            "Without --opt-in the record is shadow-only under the standard review gates. With\n"
            "--opt-in the change's review policy becomes risk-based and lane-merge honors the\n"
            "selected duties (the archived self-review change is always refused). Skips are\n"
            "low-risk only, never PASS."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    decision.add_argument("lane", metavar="<lane>")
    decision.add_argument("--code", required=True, choices=_DECISION_MODES, metavar="<full|focused|skip>")
    decision.add_argument("--test", required=True, choices=_DECISION_MODES, metavar="<full|focused|skip>")
    decision.add_argument("--ui", required=True, choices=_DECISION_MODES, metavar="<full|focused|skip>")
    decision.add_argument("--risk-factor", action="append", default=[], metavar="<factor>")
    decision.add_argument("--acceptance-coverage", required=True, metavar="<text>")
    decision.add_argument("--check", dest="checks", action="append", default=[], metavar="<check>")
    decision.add_argument("--rationale", required=True, metavar="<text>")
    decision.add_argument(
        "--opt-in", action="store_true",
        help="activate risk-based review for this change's lanes (otherwise the record is shadow-only and the standard gates apply)",
    )
    _add_change_option(decision)

    reuse = commands.add_parser(
        "review-reuse",
        help="record assessed reuse of an ordinary agent PASS recorded at an ancestor commit",
        epilog=(
            "Requires the intervening diff, an assessment of what it does not touch, and\n"
            "unchanged evidence inputs; affected/uncertain changes require fresh re-review.\n"
            "Human and combined-Oracle approvals stay exact-bound and are never reused."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    reuse.add_argument("lane", metavar="<lane>")
    reuse.add_argument("role", metavar="<role>")
    reuse.add_argument("--ancestor", required=True, metavar="<sha>")
    reuse.add_argument("--intervening-diff", required=True, metavar="<path>")
    reuse.add_argument("--assessment", required=True, metavar="<text>")
    reuse.add_argument(
        "--evidence-inputs-changed", choices=("no", "yes"), default="no",
        help="'yes' refuses the record: affected/uncertain changes require re-review",
    )
    _add_change_option(reuse)

    defer = commands.add_parser("human-defer", help="record bounded low-risk UI deferral to direct three-runtime integrated human inspection (opt-in)")
    defer.add_argument("lane")
    defer.add_argument("--commit", required=True)
    defer.add_argument("--evidence-revision", required=True)
    defer.add_argument("--rationale", required=True, help="bounded/reversible risk and excluded sensitive effects")
    defer.add_argument("--risk-assessment", required=True, choices=("low", "high", "uncertain"))
    defer.add_argument("--evidence", help="required for screen lanes; current changed-screen receipt")
    _add_change_option(defer)

    merged = commands.add_parser("lane-merge", help="merge a reviewed lane")
    merged.add_argument("lane", metavar="<lane>")
    merged.add_argument(
        "--human-override",
        action="store_true",
        help="merge on an explicit human decision, waiving only review duties (ticks/base/tree/gate still bind)",
    )
    merged.add_argument(
        "--override-reason",
        default=None,
        metavar="<reason>",
        help="required with --human-override; the recorded human decision",
    )
    _add_change_option(merged)

    finish = commands.add_parser("finish-record", help="bind classification/version before final capture; restage after changes and recapture before PR")
    finish.add_argument("action", choices=("classify", "restage", "capture", "check", "inspect", "pr-check"))
    finish.add_argument("--classification", choices=("pre-release", "minor-update", "feature-update"))
    finish.add_argument("--version", help="human-classified expected marketing version")
    finish.add_argument("--check", help="selected check id")
    finish.add_argument("--artifact", help="gitignored receipt or manifest")
    finish.add_argument("--input", action="append", dest="inputs", help="tracked input path affected by check (repeat)")
    _add_change_option(finish)

    dashboard_command = commands.add_parser("dashboard", help="render the offline derived-state dashboard")
    dashboard_command.add_argument("--output", type=Path, metavar="<path>")
    _add_change_option(dashboard_command)

    sync_command = commands.add_parser(
        "sync", help="refresh lane and task status in the coordinator store from lane refs"
    )
    _add_change_option(sync_command)

    task_status = commands.add_parser(
        "task-status", help="set one task's coordinator-side status (blocked with a reason)"
    )
    task_status.add_argument("lane")
    task_status.add_argument("task")
    task_status.add_argument("--status", required=True, choices=_TASK_STATES)
    task_status.add_argument("--reason")
    _add_change_option(task_status)

    current = commands.add_parser("status", help="show the current derived task and lane state")
    current.add_argument("--json", action="store_true", dest="as_json", help="emit machine-readable JSON")
    _add_change_option(current)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.self_check:
        return _self_check()
    selected_change = _selected_change(args)
    try:
        if args.command == "plan-validate":
            validate_plan(args.plan, args.tasks)
            print(f"plan-validate: PASS ({args.plan})")
            return 0
        if args.command == "plan-graph":
            plan = _load_json(args.plan)
            output = render_markdown(plan) if args.format == "md" else render_mermaid(plan)
            print(output, end="")
            return 0
        if args.command == "state-derive":
            state_derive(args.change, args.source_ref)
            print(f"state-derive: derived {args.change} in memory (coordination.json is the store)")
            return 0
        if args.command == "task-tick":
            path = task_tick(args.lane, args.task, selected_change)
            print(f"task-tick: checked {args.lane}/{args.task} ({path})")
            return 0
        if args.command == "dispatch":
            path = dispatch_run(
                args.lane, args.role, args.model, args.level, args.task, args.key, selected_change,
                args.access, args.acceptance, args.evidence,
            )
            print(f"dispatch: recorded {args.lane}/{args.role} ({path})")
            return 0
        if args.command == "dispatch-done":
            path = dispatch_done(
                args.lane, args.role, args.worker_run_id, args.tokens, args.cost, args.result, selected_change
            )
            print(f"dispatch-done: recorded {args.lane}/{args.role} ({path})")
            return 0
        if args.command == "lane-open":
            path = lane_open(args.lane, selected_change)
            print(f"lane-open: recorded {args.lane} ({path})")
            return 0
        if args.command == "lane-close":
            path = lane_close(args.lane, selected_change)
            print(f"lane-close: recorded {args.lane} ({path})")
            return 0
        if args.command == "lane-review":
            path = lane_review(
                args.lane, args.role, args.verdict, args.commit, args.report,
                args.evidence_revision, args.evidence, selected_change,
                args.combined, args.oracle_provenance, args.feature_base, args.review_base, args.acceptance,
            )
            print(f"lane-review: recorded {args.lane}/{args.role}={args.verdict} ({path})")
            return 0
        if args.command == "review-reuse":
            path = review_reuse(
                args.lane, args.role, args.ancestor, args.intervening_diff, args.assessment,
                args.evidence_inputs_changed == "yes", selected_change,
            )
            print(f"review-reuse: recorded {args.lane}/{args.role} reuse from {args.ancestor} ({path})")
            return 0
        if args.command == "human-gate":
            stand_in = None
            if args.stand_in_report is not None or args.stand_in_verdict is not None:
                stand_in = {"verdict": args.stand_in_verdict, "report": args.stand_in_report}
            path = human_gate(
                args.lane, args.state, args.commit, args.evidence_revision, args.evidence, selected_change,
                args.approved_by, args.authorization, stand_in, args.checklist,
            )
            print(f"human-gate: recorded {args.lane}/{args.state} ({path})")
            return 0
        if args.command == "lane-mediate":
            path = lane_mediate(args.lane, args.report, args.role, selected_change)
            print(f"lane-mediate: recorded {args.lane} ({path})")
            return 0
        if args.command == "review-decision":
            path = review_decision(
                args.lane, {"code": args.code, "test": args.test, "ui": args.ui},
                args.risk_factor, args.acceptance_coverage, args.checks, args.rationale,
                args.opt_in, selected_change,
            )
            policy = "risk-based" if args.opt_in else "legacy (shadow-only record)"
            print(f"review-decision: recorded {args.lane} [{policy}] ({path})")
            return 0
        if args.command == "human-defer":
            path = human_defer(args.lane, args.commit, args.evidence_revision, args.rationale,
                               selected_change, args.risk_assessment, args.evidence)
            print(f"human-defer: recorded {args.lane} deferred-to-integrated ({path})")
            return 0
        if args.command == "lane-merge":
            path = lane_merge(
                args.lane, selected_change, args.human_override, args.override_reason
            )
            suffix = " (human override)" if args.human_override else ""
            print(f"lane-merge: recorded {args.lane}{suffix} ({path})")
            return 0
        if args.command == "finish-record":
            path = finish_record(args.action, args.classification, args.version,
                                 args.check, args.artifact, args.inputs, selected_change)
            print(f"finish-record: {args.action} ({path})")
            return 0
        if args.command == "dashboard":
            path = dashboard(selected_change, args.output)
            print(f"dashboard: rendered ({path})")
            return 0
        if args.command == "sync":
            result = coordination_sync(selected_change, None)
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "task-status":
            path = coordination_task_status(
                args.lane, args.task, args.status, args.reason, selected_change, None
            )
            print(f"task-status: recorded {args.lane}/{args.task} {args.status} ({path})")
            return 0
        if args.command == "status":
            print(status(selected_change, args.as_json), end="")
            return 0
    except (PlanError, StateError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Repository storage and retained-checkout migration regressions (standard library only)."""
import contextlib
import copy
import io
import importlib.util
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("featdev", Path(__file__).resolve().parents[1] / "bin/featdev.py")
f = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(f)


class RepositoryObjectsTest(unittest.TestCase):
    def setUp(self):
        self.cwd = Path.cwd()
        self.environment = os.environ.copy()
        self.temporary = tempfile.TemporaryDirectory(prefix="featdev-repo-test-")
        root = Path(self.temporary.name)
        self.repo = (root / "repo").resolve()
        self.repo.mkdir()
        self.home = root / "home"
        self.home.mkdir()
        for key in list(os.environ):
            if key.startswith("GIT_"):
                os.environ.pop(key)
        os.environ.update(HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"),
                          GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                          FEATDEV_LANE_WORKTREE_ROOT=str(root / "worktrees"))
        os.chdir(self.repo)
        self.git("init", "-q", "-b", "feature")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.test")
        f._ensure_workflow_ignored(self.repo)
        lane = {"risk": "ordinary", "ui": False, "sensitive": False,
                "depends_on": [], "tasks": {"1.1": {"deps": []}}}
        self.document = {"schema_version": 3, "changes": {
            name: {"lanes": {"work": copy.deepcopy(lane)}} for name in ("alpha", "beta")
        }}
        self.plan_path = self.repo / ".agents/workflow/plan-graph.json"
        self.coord_path = self.repo / ".agents/workflow/coordination.json"
        self.write(self.plan_path, self.document)
        for name in self.document["changes"]:
            tasks = self.repo / "openspec/changes" / name / "tasks.md"
            tasks.parent.mkdir(parents=True)
            tasks.write_text("- [ ] 1.1 implement\n")
            (tasks.parent / "proposal.md").write_text("# Proposal\nImplement the scoped greeting.\n")
            (tasks.parent / "design.md").write_text("# Design\nKeep the greeting local.\n")
        self.commit(self.repo, "planning checkpoint approved by human")

    def tearDown(self):
        os.chdir(self.cwd)
        os.environ.clear()
        os.environ.update(self.environment)
        self.temporary.cleanup()

    def git(self, *args, checkout=None):
        return subprocess.run(["git", "-C", str(checkout or self.repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def commit(self, checkout, message):
        self.git("add", ".", checkout=checkout)
        self.git("commit", "-qm", message, checkout=checkout)
        return self.git("rev-parse", "HEAD", checkout=checkout)

    def read(self):
        return json.loads(self.coord_path.read_text())

    def archive_change(self, change, date="2026-01-01"):
        target = self.repo / "openspec/changes/archive" / f"{date}-{change}"
        target.parent.mkdir(parents=True, exist_ok=True)
        self.git("mv", f"openspec/changes/{change}", str(target))
        return target

    def mark_complete(self, worktree, change, task):
        path = worktree / "openspec/changes" / change / "tasks.md"
        text = path.read_text()
        marker = f"- [ ] {task} "
        self.assertEqual(text.count(marker), 1)
        path.write_text(text.replace(marker, f"- [x] {task} ", 1))

    def finish_lane(self, change):
        f.lane_open("work", change)
        lane = self.read()["changes"][change]["lanes"]["work"]
        worktree = Path(lane["worktree"])
        self.mark_complete(worktree, change, "1.1")
        head = self.commit(worktree, "implement and complete task")
        report = self.repo / ".agents/workflow" / change / "reviews/code-reviewer.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("PASS: isolated test fixture review\n")
        f.lane_review("work", "code-reviewer", "PASS", head, report, change=change)
        return worktree, head

    def test_conflicts_are_resolved_in_lane_and_reviewed_at_the_new_head(self):
        (self.repo / "shared.txt").write_text("base\n")
        self.commit(self.repo, "shared source fixture")
        worktrees = {}
        for change in ("alpha", "beta"):
            f.lane_open("work", change)
            worktree = Path(self.read()["changes"][change]["lanes"]["work"]["worktree"])
            worktrees[change] = worktree
            (worktree / "shared.txt").write_text(change + "\n")
            self.mark_complete(worktree, change, "1.1")
            head = self.commit(worktree, "reviewed " + change)
            report = self.repo / ".agents/workflow" / change / "reviews/code.md"
            report.parent.mkdir(parents=True)
            report.write_text("PASS: fixture\n")
            f.lane_review("work", "code-reviewer", "PASS", head, report, change=change)
        f.lane_merge("work", "beta")
        feature = self.git("rev-parse", "HEAD")
        with self.assertRaisesRegex(f.StateError, "into the lane worktree"):
            f.lane_merge("work", "alpha")
        self.assertEqual(self.git("rev-parse", "HEAD"), feature)
        self.assertEqual(self.git("status", "--porcelain"), "")
        worktree = worktrees["alpha"]
        conflict = subprocess.run(["git", "-C", str(worktree), "merge", "feature"], capture_output=True)
        self.assertNotEqual(conflict.returncode, 0)
        (worktree / "shared.txt").write_text("reviewed combined resolution\n")
        resolved = self.commit(worktree, "resolve conflict in lane")
        with self.assertRaisesRegex(f.StateError, "superseded"):
            f.lane_merge("work", "alpha")
        report = self.repo / ".agents/workflow/alpha/reviews/resolution.md"
        report.write_text("PASS: independent resolution fixture\n")
        f.lane_review("work", "code-reviewer", "PASS", resolved, report, change="alpha")
        f.lane_merge("work", "alpha")
        self.assertEqual((self.repo / "shared.txt").read_text(), "reviewed combined resolution\n")

    def test_effective_lane_cycles_include_cross_lane_task_edges(self):
        alpha = self.document["changes"]["alpha"]
        alpha["lanes"]["work"]["tasks"]["1.2"] = {"deps": ["2.1"]}
        other = copy.deepcopy(alpha["lanes"]["work"])
        other["tasks"] = {"2.1": {"deps": ["1.1"]}}
        alpha["lanes"]["other"] = other
        self.write(self.plan_path, self.document)
        (self.repo / "openspec/changes/alpha/tasks.md").write_text(
            "- [ ] 1.1 first\n- [ ] 1.2 needs other\n- [ ] 2.1 needs first\n")
        with self.assertRaisesRegex(f.PlanError, "effective lane dependency cycle"):
            f.validate_plan(self.plan_path)

    def test_finish_workers_are_tracked_without_reopening_lanes(self):
        with self.assertRaisesRegex(f.StateError, "not merged"):
            f.dispatch_run(None, "runner", "checks", change="alpha")
        self.finish_lane("alpha")
        f.lane_merge("work", "alpha")
        f.lane_close("work", "alpha")
        f.coordination_sync("beta")
        beta = copy.deepcopy(self.read()["changes"]["beta"])
        with self.assertRaisesRegex(f.StateError, "separate --worktree"):
            f.dispatch_run(None, "implementer", "prepare", change="alpha")
        prep = self.repo.parent / "preparation"
        self.git("worktree", "add", "--detach", str(prep), "HEAD")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(f.main(["--change", "alpha", "dispatch", "--scope", "finish", "implementer",
                                     "--task", "prepare", "--worktree", str(prep), "--key", "prep"]), 0)
        f.dispatch_run(None, "implementer", "prepare", key="prep", change="alpha", worktree=prep)
        with self.assertRaisesRegex(f.StateError, "active worker"):
            f.dispatch_run(None, "implementer", "prepare", key="second-writer", change="alpha", worktree=prep)
        view = json.loads(f.status("alpha", as_json=True))
        self.assertEqual(view["open_dispatches"][0]["scope"], "finish")
        (prep / "version.txt").write_text("1.0\n")
        prepared = self.commit(prep, "prepare release")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(f.main(["--change", "alpha", "dispatch-done", "--scope", "finish", "implementer",
                                     "--key", "prep", "--worker-run-id", "prep-worker", "--result", "ok"]), 0)
        f.dispatch_run(None, "code-reviewer", "review-preparation", key="prep-review", change="alpha", worktree=prep)
        self.assertEqual(json.loads(f.status("alpha", True))["open_dispatches"][0]["status"], "current")
        f.dispatch_done(None, "code-reviewer", key="prep-review", result="ok", change="alpha")
        self.git("merge", "--ff-only", prepared)
        f.dispatch_run(None, "runner", "monitor", key="monitor", change="alpha")
        self.assertEqual(json.loads(f.status("alpha", True))["open_dispatches"][0]["status"], "current")
        self.commit_empty("new integrated revision")
        self.assertEqual(json.loads(f.status("alpha", True))["open_dispatches"][0]["status"], "stale")
        with self.assertRaisesRegex(f.StateError, "different intent"):
            f.dispatch_run(None, "runner", "monitor", key="monitor", change="alpha")
        f.dispatch_done(None, "runner", key="monitor", result="ok", change="alpha")
        node = self.read()["changes"]["alpha"]
        self.assertEqual(node["lanes"]["work"]["phase"], "closed")
        self.assertEqual(len(node["finish_dispatches"]), 3)
        self.assertEqual(self.read()["changes"]["beta"], beta)

    def commit_empty(self, message):
        self.git("commit", "--allow-empty", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def test_archived_change_accepts_new_corrective_lane(self):
        self.finish_lane("alpha")
        f.lane_merge("work", "alpha")
        f.lane_close("work", "alpha")
        archive = self.archive_change("alpha")
        self.commit(self.repo, "archive completed alpha")
        closed = copy.deepcopy(self.read()["changes"]["alpha"]["lanes"]["work"])
        correction = copy.deepcopy(self.document["changes"]["alpha"]["lanes"]["work"])
        correction["tasks"] = {"1.2": {"deps": ["1.1"]}}
        self.document["changes"]["alpha"]["lanes"]["correction"] = correction
        self.write(self.plan_path, self.document)
        with (archive / "tasks.md").open("a") as file:
            file.write("- [ ] 1.2 correct archived implementation\n")
        self.commit(self.repo, "human approved archived amendment fixture")
        f.lane_open("correction", "alpha")
        worktree = Path(self.read()["changes"]["alpha"]["lanes"]["correction"]["worktree"])
        task_file = f._openspec_change_directory(worktree, "alpha") / "tasks.md"
        task_file.write_text(task_file.read_text().replace("- [ ] 1.2", "- [x] 1.2"))
        head = self.commit(worktree, "implement archived correction")
        report = self.repo / ".agents/workflow/alpha/reviews/correction.md"
        report.write_text("PASS: archived amendment fixture\n")
        f.coordination_sync("alpha")
        self.assertEqual(self.read()["changes"]["alpha"]["lanes"]["correction"]["tasks"]["1.2"]["status"], "done")
        f.lane_review("correction", "code-reviewer", "PASS", head, report, change="alpha")
        f.lane_merge("correction", "alpha")
        f.lane_close("correction", "alpha")
        retained = self.read()["changes"]["alpha"]["lanes"]["work"]
        for field in ("phase", "reviews", "merge"):
            self.assertEqual(retained[field], closed[field])
        self.assertTrue(f.state_derive("alpha")["lanes"]["correction"]["complete"])
        self.assertFalse((self.repo / "openspec/changes/alpha").exists())

    def test_planning_authorship_is_not_approval_and_pass_resets_the_cycle(self):
        self.artifact_pass("alpha")
        self.artifact_pass("beta")
        report = self.repo / ".agents/workflow/reviews/graph.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("MORE WORK: fixture\n")
        head = self.git("rev-parse", "HEAD")
        with self.assertRaisesRegex(f.StateError, "two MORE WORK"):
            f.plan_dispatch("oracle", scope="graph", access="write", report=report)
        for i in range(2):
            f.plan_dispatch("oracle", scope="graph", key=f"review-{i}", report=report)
            f.plan_review("oracle", "MORE WORK", head, report, scope="graph")
        author = report.with_name("correction.md")
        f.plan_dispatch("oracle", scope="graph", access="write", key="author", report=author)
        self.document["changes"]["alpha"]["lanes"]["work"]["risk"] = "high"
        self.write(self.plan_path, self.document)
        author.write_text("Bounded authorized correction and mediation fixture\n")
        f.plan_dispatch("oracle", scope="graph", access="write", key="author", report=author)
        with self.assertRaisesRegex(f.StateError, "no open plan dispatch"):
            f.plan_review("oracle", "PASS", head, author, scope="graph", mediation=author)
        f.plan_dispatch("oracle", scope="graph", key="independent", report=report)
        with self.assertRaisesRegex(f.StateError, "two-review limit"):
            f.plan_review("oracle", "PASS", head, report, scope="graph")
        f.plan_review("oracle", "PASS", head, report, scope="graph", mediation=author)
        self.document["changes"]["alpha"]["lanes"]["work"]["risk"] = "ordinary"
        self.write(self.plan_path, self.document)
        f.plan_dispatch("oracle", scope="graph", key="later-work", report=report)
        f.plan_review("oracle", "PASS", head, report, scope="graph")
        ledger = json.loads((self.repo / ".agents/workflow/plan-graph-review.json").read_text())
        self.assertEqual([row["round"] for row in ledger["reviews"]], [1, 2, 3, 1])
        self.assertEqual(next(row for row in ledger["dispatches"] if row["key"] == "author")["result"], "corrected")

    def test_replaced_ui_receipt_requires_review_and_human_renewal(self):
        self.document["changes"]["alpha"]["lanes"]["work"]["ui"] = True
        self.write(self.plan_path, self.document)
        f.lane_open("work", "alpha")
        worktree = Path(self.read()["changes"]["alpha"]["lanes"]["work"]["worktree"])
        self.mark_complete(worktree, "alpha", "1.1")
        head = self.commit(worktree, "ui fixture")
        evidence = self.repo / ".agents/workflow/alpha/evidence/ui.md"
        evidence.parent.mkdir(parents=True)
        evidence.write_text("Original UI evidence\n")
        report = evidence.with_name("review.md")
        report.write_text("PASS\n")
        f.lane_review("work", "code-reviewer", "PASS", head, report, evidence=evidence, change="alpha")
        f.lane_review("work", "ui-reviewer", "PASS", head, report, evidence=evidence, change="alpha")
        f.human_gate("work", "approved", head, "r1", evidence=evidence, change="alpha")
        evidence.write_text("Regenerated UI evidence at the same path\n")
        with self.assertRaisesRegex(f.StateError, "evidence changed"):
            f.lane_merge("work", "alpha")
        f.lane_review("work", "code-reviewer", "PASS", head, report, evidence=evidence, change="alpha")
        f.lane_review("work", "ui-reviewer", "PASS", head, report, evidence=evidence, change="alpha")
        with self.assertRaisesRegex(f.StateError, "changed evidence"):
            f.lane_merge("work", "alpha")
        f.human_gate("work", "approved", head, "r1", evidence=evidence, change="alpha")
        f.lane_merge("work", "alpha")

    def test_migrate_collapses_screen_into_ui(self):
        self.document["schema_version"] = 2
        for node in self.document["changes"].values():
            node["lanes"]["work"].pop("ui")
            node["lanes"]["work"]["screen"] = True
        self.write(self.plan_path, self.document)
        f.migrate_repository()
        migrated = json.loads(self.plan_path.read_text())
        lane = migrated["changes"]["alpha"]["lanes"]["work"]
        self.assertEqual(migrated["schema_version"], 3)
        self.assertTrue(lane["ui"])
        self.assertNotIn("screen", lane)

    def test_ui_lane_requires_ui_review_and_evidence(self):
        self.document["changes"]["alpha"]["lanes"]["work"]["ui"] = True
        self.write(self.plan_path, self.document)
        f.lane_open("work", "alpha")
        worktree = Path(self.read()["changes"]["alpha"]["lanes"]["work"]["worktree"])
        self.mark_complete(worktree, "alpha", "1.1")
        head = self.commit(worktree, "ui gate fixture")
        evidence = self.repo / ".agents/workflow/alpha/evidence/ui-gate.txt"
        evidence.parent.mkdir(parents=True)
        evidence.write_text("current rendered evidence\n")
        code_report = evidence.with_name("code.md")
        code_report.write_text("PASS\n")
        f.lane_review("work", "code-reviewer", "PASS", head, code_report, evidence=evidence, change="alpha")
        with self.assertRaisesRegex(f.StateError, "ui-reviewer"):
            f.lane_merge("work", "alpha")
        ui_report = evidence.with_name("ui.md")
        ui_report.write_text("PASS\n")
        with self.assertRaisesRegex(f.StateError, "requires evidence"):
            f.lane_review("work", "ui-reviewer", "PASS", head, ui_report, change="alpha")
        f.lane_review("work", "ui-reviewer", "PASS", head, ui_report, evidence=evidence, change="alpha")
        with self.assertRaisesRegex(f.StateError, "requires evidence for a human gate"):
            f.human_gate("work", "approved", head, "r1", change="alpha")
        f.human_gate("work", "approved", head, "r1", evidence=evidence, change="alpha")
        f.lane_merge("work", "alpha")

    def test_plan_approval_records_committed_artifacts_and_reviewed_graph(self):
        self.artifact_pass("alpha")
        self.artifact_pass("beta")
        with self.assertRaisesRegex(f.StateError, "current repo graph PASS"):
            f.plan_approve()
        head = self.git("rev-parse", "HEAD")
        report = self.repo / ".agents/workflow/reviews/graph.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("PASS\n")
        f.plan_dispatch("oracle", scope="graph", report=report)
        f.plan_review("oracle", "PASS", head, report, scope="graph")
        tasks = self.repo / "openspec/changes/alpha/tasks.md"
        original = tasks.read_text()
        tasks.write_text(original.replace("- [ ] 1.1", "- [x] 1.1"))
        with self.assertRaisesRegex(f.StateError, "commit the OpenSpec artifacts"):
            f.plan_approve()
        tasks.write_text(original)
        f.plan_approve("Test Human")
        record = json.loads((self.repo / ".agents/workflow/plan-approval.json").read_text())
        self.assertEqual(record["approver"], "Test Human")
        self.assertEqual(record["head"], head)
        self.assertEqual(record["plan_sha256"], f._plan_context(None, None, "graph")[3])
        self.assertEqual(set(record["artifacts"]), {"alpha", "beta"})
        view = json.loads(f.status("alpha", as_json=True))
        self.assertEqual(view["plan_approval"]["state"], "current")

    def test_plan_rejects_retired_screen_flag_and_schema_v2(self):
        self.document["schema_version"] = 2
        self.write(self.plan_path, self.document)
        with self.assertRaisesRegex(f.PlanError, "schema_version 2 is retired"):
            f.validate_plan(self.plan_path)
        self.document["schema_version"] = 3
        self.document["changes"]["alpha"]["lanes"]["work"]["screen"] = False
        self.write(self.plan_path, self.document)
        with self.assertRaisesRegex(f.PlanError, "retired screen flag"):
            f.validate_plan(self.plan_path)

    def test_override_consumes_oracle_standin_grant(self):
        self.document["changes"]["alpha"]["lanes"]["work"]["sensitive"] = True
        self.write(self.plan_path, self.document)
        _, head = self.finish_lane("alpha")
        state = f.state_derive("alpha")
        root = self.repo / ".agents/workflow/alpha/evidence"
        root.mkdir(parents=True)
        authorization, checklist, report = (root / name for name in ("grant.json", "checklist.md", "standin.md"))
        self.write(authorization, {"id": "one-use", "granted_by": "human", "granted_at": "2026-01-01",
                                   "scope": ["all"], "plan_sha256": state["plan_sha256"]})
        checklist.write_text("Human acceptance checklist fixture\n")
        report.write_text("PASS: independent acceptance fixture\n")
        standin = {"verdict": "PASS", "report": str(report)}
        f.human_gate("work", "approved", head, "r1", change="alpha", approved_by="oracle",
                     authorization=authorization, stand_in=standin, checklist=checklist)
        f.lane_merge("work", "alpha", human_override=True, override_reason="Explicit human fixture instruction")
        node = self.read()["changes"]["alpha"]
        self.assertEqual(node["authorizations"]["one-use"]["status"], "consumed")
        with self.assertRaisesRegex(f.StateError, "consumed"):
            f._record_authorization_approval(node, state, "another", node["lanes"]["work"]["ref"],
                                             head, "r1", authorization, standin, checklist)

    def test_removed_finish_check_is_not_a_parser_action(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            f._parser().parse_args(["finish-record", "check", "--check", "unit"])

    def test_validate_and_render_all_changes_with_repeated_ids(self):
        f.validate_plan(self.plan_path)
        markdown = f.render_markdown(self.document)
        self.assertIn("alpha", markdown)
        self.assertIn("beta", markdown)
        graph = f.render_mermaid(self.document)
        for change in self.document["changes"]:
            self.assertIn(f._mermaid_id("change", change) + "_task_312e31", graph)
        with self.assertRaisesRegex(f.StateError, "multiple active changes"):
            f.lane_open("work")
        with self.assertRaisesRegex(f.PlanError, "--tasks requires --change"):
            f.validate_plan(self.plan_path, self.repo / "openspec/changes/alpha/tasks.md")

    def test_nested_shape_and_task_identity(self):
        for mutation in ("changes", "lanes", "tasks"):
            document = copy.deepcopy(self.document)
            if mutation == "changes":
                document["changes"] = {}
            elif mutation == "lanes":
                document["changes"]["alpha"]["lanes"] = {}
            else:
                document["changes"]["alpha"]["lanes"]["work"]["tasks"] = {}
            self.write(self.plan_path, document)
            with self.assertRaises(f.PlanError):
                f.validate_plan(self.plan_path)
        document = copy.deepcopy(self.document)
        document["changes"]["alpha"]["lanes"]["other"] = copy.deepcopy(document["changes"]["alpha"]["lanes"]["work"])
        self.write(self.plan_path, document)
        with self.assertRaisesRegex(f.PlanError, "duplicate task"):
            f.validate_plan(self.plan_path)

    def test_finish_and_planning_checks_continue_after_openspec_archive(self):
        (self.repo / "app.py").write_text("value = 1\n")
        self.commit(self.repo, "release input fixture")
        self.artifact_pass("alpha")
        self.artifact_pass("beta")
        original_artifacts = f._change_artifacts(self.repo, "alpha")[1]
        self.finish_lane("alpha")
        f.lane_merge("work", "alpha")
        f.lane_close("work", "alpha")
        store = self.repo / ".agents/workflow/alpha/evidence"
        store.mkdir(parents=True)
        preparation, evidence, inspection = [store / name for name in ("preparation.md", "unit.md", "human.md")]
        preparation.write_text("Fixture release preparation\n")
        evidence.write_text("Fixture selected check: PASS\n")
        inspection.write_text("inspector: human\ncheckpoint: view\nverdict: accept\n")
        f.finish_record("classify", classification="patch", check=["unit"], artifact=str(preparation),
                        inputs=["app.py"], change="alpha", inspections=["view"])
        f.finish_record("capture", check="unit", artifact=str(evidence), inputs=["app.py"], change="alpha")
        f.finish_record("inspect", check="view", artifact=str(inspection), change="alpha")
        f.finish_record("pr-check", change="alpha")
        archive = self.archive_change("alpha")
        baseline = self.repo / "openspec/specs/greeting/spec.md"
        baseline.parent.mkdir(parents=True)
        baseline.write_text("Synced baseline specification fixture\n")
        self.commit(self.repo, "sync baseline specs and archive alpha before PR merge")
        state = f.state_derive("alpha")
        self.assertEqual(state["tasks_path"], str((archive / "tasks.md").relative_to(self.repo)))
        self.assertTrue(state["lanes"]["work"]["complete"])
        self.assertEqual(f._change_artifacts(self.repo, "alpha")[1], original_artifacts)
        f.validate_plan(self.plan_path)
        f.coordination_sync("alpha")
        with self.assertRaisesRegex(f.StateError, "inspection missing/stale"):
            f.finish_record("pr-check", change="alpha")
        f.finish_record("inspect", check="view", artifact=str(inspection), change="alpha")
        f.finish_record("pr-check", change="alpha")
        f.plan_dispatch("oracle", scope="graph", key="post-archive-graph-review")

    def test_archive_lookup_does_not_guess_between_duplicate_change_ids(self):
        archive = self.archive_change("alpha")
        shutil.copytree(archive, archive.with_name("2026-01-02-alpha"))
        self.commit(self.repo, "ambiguous archive fixture")
        with self.assertRaisesRegex(f.StateError, "multiple committed archives"):
            f.state_derive("alpha")
        with self.assertRaisesRegex(f.StateError, "multiple archives"):
            f.validate_plan(self.plan_path)

    def test_dependencies_stay_in_the_selected_change(self):
        self.document["changes"]["alpha"]["lanes"]["work"]["tasks"]["1.1"]["deps"] = ["beta/1.1"]
        self.write(self.plan_path, self.document)
        with self.assertRaisesRegex(f.PlanError, "unknown task"):
            f.validate_plan(self.plan_path)

    def test_shared_coordination_preserves_other_change_and_scopes_task_status(self):
        f.lane_open("work", "alpha")
        f.lane_open("work", "beta")
        beta = copy.deepcopy(self.read()["changes"]["beta"])
        f.coordination_task_status("work", "1.1", "blocked", "fixture", "alpha")
        f.coordination_sync("alpha")
        data = self.read()
        self.assertEqual(data["schema_version"], 2)
        self.assertEqual(data["changes"]["beta"], beta)
        self.assertEqual(data["changes"]["alpha"]["lanes"]["work"]["tasks"]["1.1"]["status"], "blocked")
        self.assertNotIn("task_states", data["changes"]["alpha"]["lanes"]["work"])
        self.assertFalse((self.repo / ".agents/workflow/alpha/coordination.json").exists())

    def test_merge_and_close_do_not_touch_other_change(self):
        f.lane_open("work", "beta")
        beta = copy.deepcopy(self.read()["changes"]["beta"])
        self.finish_lane("alpha")
        f.lane_merge("work", "alpha")
        f.lane_close("work", "alpha")
        self.assertEqual(self.read()["changes"]["beta"], beta)
        self.assertTrue(f.state_derive("alpha")["lanes"]["work"]["complete"])
        self.assertFalse(f.state_derive("beta")["lanes"]["work"]["complete"])

    def test_unrelated_feature_plan_update_does_not_invalidate_selected_change(self):
        original = f.state_derive("alpha")["plan_sha256"]
        self.finish_lane("alpha")
        self.document["changes"]["beta"]["lanes"]["work"]["tasks"]["1.2"] = {"deps": ["1.1"]}
        self.write(self.plan_path, self.document)
        tasks = self.repo / "openspec/changes/beta/tasks.md"
        tasks.write_text(tasks.read_text() + "- [ ] 1.2 follow-up\n")
        self.commit(self.repo, "beta plan amendment approved by human")
        self.assertEqual(f.state_derive("alpha")["plan_sha256"], original)
        f.lane_merge("work", "alpha")
        f.validate_plan(self.plan_path)
        self.assertIn("1.2", json.loads(self.plan_path.read_text())["changes"]["beta"]["lanes"]["work"]["tasks"])

    def test_combined_mode_is_scoped_to_one_change_in_the_shared_graph(self):
        self.document["changes"]["alpha"]["review_mode"] = "combined-single-lane"
        self.write(self.plan_path, self.document)
        self.git("commit", "--allow-empty", "-qm", "combined alpha plan approved by human")
        base = self.git("rev-parse", "HEAD")
        f.lane_open("work", "beta")
        beta = copy.deepcopy(self.read()["changes"]["beta"])
        f.lane_open("work", "alpha")
        worktree = Path(self.read()["changes"]["alpha"]["lanes"]["work"]["worktree"])
        f.dispatch_run("work", "implementer",  "1.1", change="alpha")
        self.mark_complete(worktree, "alpha", "1.1")
        head = self.commit(worktree, "complete alpha implementation")
        f.dispatch_done("work", "implementer", worker_run_id="fixture-author", result="ok", change="alpha")
        reports = self.repo / ".agents/workflow/alpha/reviews"
        reports.mkdir(parents=True)
        acceptance, evidence, report = [reports / name for name in ("acceptance.md", "evidence.md", "combined.md")]
        for path in (acceptance, evidence, report):
            path.write_text("Isolated combined-review fixture\n")
        f.dispatch_run("work", "oracle",  "review", change="alpha",
                       acceptance=acceptance, evidence=evidence)
        f.dispatch_done("work", "oracle", worker_run_id="fixture-reviewer", result="ok", change="alpha")
        f.lane_review("work", "code-reviewer", "PASS", head, report, evidence_revision="r1",
                      evidence=evidence, change="alpha", combined=True, oracle_provenance="fixture-reviewer",
                      feature_base=base, review_base=base, acceptance=acceptance)
        f.lane_merge("work", "alpha")
        f.lane_merge("work", "alpha")
        f.lane_close("work", "alpha")
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.read()["changes"]["beta"], beta)
        self.archive_change("alpha")
        self.commit(self.repo, "archive combined alpha after integration")
        f.lane_merge("work", "alpha")
        self.assertTrue(f.state_derive("alpha")["lanes"]["work"]["complete"])
        self.assertEqual(self.read()["changes"]["beta"], beta)

    def test_lane_worktree_reads_owning_store_without_copying_the_plan(self):
        f.lane_open("work", "alpha")
        worktree = Path(self.read()["changes"]["alpha"]["lanes"]["work"]["worktree"])
        os.chdir(worktree)
        self.mark_complete(worktree, "alpha", "1.1")
        with self.assertRaisesRegex(f.StateError, "Coordinator checkout|feature checkout"):
            f.coordination_sync("alpha")
        head = self.commit(worktree, "complete from the lane worktree")
        os.chdir(self.repo)
        self.assertFalse((worktree / ".agents/workflow/plan-graph.json").exists())
        report = self.repo / ".agents/workflow/alpha/reviews/code-reviewer.md"
        report.parent.mkdir(parents=True)
        report.write_text("Isolated worker-context review fixture\n")
        f.lane_review("work", "code-reviewer", "PASS", head, report, change="alpha")
        f.lane_merge("work", "alpha")

    def test_local_plan_edit_invalidates_review_without_a_git_commit(self):
        _, head = self.finish_lane("alpha")
        feature_head = self.git("rev-parse", "HEAD")
        self.document["changes"]["alpha"]["lanes"]["work"]["risk"] = "high"
        self.write(self.plan_path, self.document)
        self.assertEqual(self.git("rev-parse", "HEAD"), feature_head)
        with self.assertRaisesRegex(f.StateError, "stale plan binding"):
            f.lane_merge("work", "alpha")
        report = self.repo / ".agents/workflow/alpha/reviews/code-reviewer.md"
        f.lane_review("work", "code-reviewer", "PASS", head, report, change="alpha")
        f.lane_review("work", "test-reviewer", "PASS", head, report, change="alpha")
        f.lane_merge("work", "alpha")

    def test_repository_objects_are_ignored_and_never_read_from_git(self):
        f.lane_open("work", "alpha")
        self.assertEqual(self.git("ls-files", ".agents/workflow"), "")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.git("check-ignore", ".agents/workflow/plan-graph.json", ".agents/workflow/coordination.json")
        for path in (".agents/workflow/plan-graph.json", ".agents/workflow/coordination.json"):
            result = subprocess.run(["git", "-C", str(self.repo), "show", "HEAD:" + path], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
        self.assertEqual(f.state_derive("alpha")["plan_sha256"], self.read()["changes"]["alpha"]["plan_sha256"])

    def test_migrate_retains_ledgers_and_is_idempotent(self):
        expected = {}
        for change, plan in f._change_plans(self.document).items():
            legacy_plan = self.repo / "openspec/changes" / change / "plan/graph-plan.json"
            self.write(legacy_plan, plan)
        self.plan_path.unlink()
        before = self.commit(self.repo, "retained legacy plans approved by human")
        for change, plan in f._change_plans(self.document).items():
            node = f._new_coordination(f.state_derive(change), plan)
            node["lanes"]["work"]["reviews"] = [{"role": "code", "report": "retained-report.md",
                                                       "binding": {"commit": self.git("rev-parse", "HEAD")}}]
            self.write(self.repo / ".agents/workflow" / change / "coordination.json", node)
            expected[change] = f._stored_change_coordination(node)
            expected[change]["lanes"]["work"]["reviews"][0]["role"] = "code-reviewer"
            expected[change]["plan_sha256"] = f._plan_digest(self.document, change, "")
            expected[change]["lanes"]["work"]["reviews"][0]["binding"]["lane_plan_sha256"] = f._lane_plan_digest(plan, "work")
        writer = f._atomic_write_json
        def interrupt_coordination(path, value):
            if path == self.coord_path:
                raise f.StateError("fixture interruption after plan write")
            writer(path, value)
        with mock.patch.object(f, "_atomic_write_json", side_effect=interrupt_coordination):
            with self.assertRaisesRegex(f.StateError, "fixture interruption"):
                f.migrate_repository()
        self.assertTrue(self.plan_path.is_file())
        self.assertFalse(self.coord_path.exists())
        f.migrate_repository()
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        f.validate_plan(self.plan_path)
        self.assertEqual(self.read()["changes"], expected)
        first = self.coord_path.read_bytes(), self.plan_path.read_bytes()
        f.migrate_repository()
        self.assertEqual(first, (self.coord_path.read_bytes(), self.plan_path.read_bytes()))
        self.assertTrue((self.repo / ".agents/workflow/alpha/coordination.json").is_file())
        self.assertTrue((self.repo / "openspec/changes/alpha/plan/graph-plan.json").is_file())

    def test_cli_dispatch_reads_the_selected_repository_node(self):
        f.lane_open("work", "alpha")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = f.main(["dispatch", "work", "implementer", "--change", "alpha",
                               "--task", "1.1"])
        self.assertEqual(result, 0)
        self.assertIn("key=alpha/work/implementer/1.1/", output.getvalue())
        record = self.read()["changes"]["alpha"]["lanes"]["work"]["dispatches"][-1]
        self.assertEqual(record["role"], "implementer")

    def test_finish_is_scoped_to_the_submitted_change(self):
        f.lane_open("work", "beta")
        beta = copy.deepcopy(self.read()["changes"]["beta"])
        self.finish_lane("alpha")
        f.lane_merge("work", "alpha")
        receipt = self.repo / ".agents/workflow/alpha/evidence/preparation.md"
        receipt.parent.mkdir(parents=True)
        receipt.write_text("Fixture release preparation; no generated changes.\n")
        f.finish_record("classify", classification="patch", check=["unit"], artifact=str(receipt),
                        inputs=["openspec/changes/alpha/tasks.md"], change="alpha")
        self.assertEqual(self.read()["changes"]["beta"], beta)
        self.assertEqual(self.read()["changes"]["alpha"]["finish"]["classification"], "patch")

    def test_migration_preserves_a_verified_closed_legacy_lane_and_consumed_grant(self):
        self.document["changes"]["alpha"]["lanes"]["work"]["sensitive"] = True
        plans = f._change_plans(self.document)
        for change, plan in plans.items():
            self.write(self.repo / "openspec/changes" / change / "plan/graph-plan.json", plan)
        self.plan_path.unlink()
        self.commit(self.repo, "legacy planning checkpoint approved by human")
        _, head = self.finish_lane("alpha")
        evidence = self.repo / ".agents/workflow/alpha/evidence"
        evidence.mkdir(parents=True)
        report, checklist = evidence / "stand-in.md", evidence / "checklist.md"
        report.write_text("Isolated Oracle stand-in fixture: PASS\n")
        checklist.write_text("Isolated human-authorized acceptance checklist\n")
        grant = {"id": "legacy-grant", "scope": ["work"], "granted_by": "human",
                 "granted_at": "2026-01-01T00:00:00Z", "plan_sha256": f.state_derive("alpha")["plan_sha256"]}
        f.human_gate("work", "approved", head, "r1", change="alpha", approved_by="oracle",
                     authorization=grant, stand_in={"verdict": "PASS", "report": str(report)}, checklist=checklist)
        f.lane_merge("work", "alpha")
        f.lane_close("work", "alpha")
        old = self.read()
        old["changes"]["alpha"]["lanes"]["work"]["reviews"][-1]["binding"].pop("lane_plan_sha256", None)
        old["changes"]["alpha"]["human_gates"]["work"]["binding"].pop("lane_plan_sha256", None)
        self.write(self.coord_path, old)
        f.migrate_repository()
        migrated = self.read()["changes"]["alpha"]
        self.assertEqual(migrated["authorizations"]["legacy-grant"]["status"], "consumed")
        self.assertEqual(migrated["authorizations"]["legacy-grant"]["plan_sha256"], f.state_derive("alpha")["plan_sha256"])
        receipt = evidence / "preparation.md"
        receipt.write_text("Fixture preparation after storage migration\n")
        f.finish_record("classify", classification="patch", check=["unit"], artifact=str(receipt),
                        inputs=["openspec/changes/alpha/tasks.md"], change="alpha")

    def test_migration_adds_missing_legacy_nodes_without_replacing_shared_data(self):
        f.lane_open("work", "alpha")
        alpha = copy.deepcopy(self.read()["changes"]["alpha"])
        plan = f._selected_plan(self.document, "beta")
        beta = f._new_coordination(f.state_derive("beta"), plan)
        self.write(self.repo / ".agents/workflow/beta/coordination.json", beta)
        self.document["note"] = "Retained repository metadata"
        self.write(self.plan_path, self.document)
        f.migrate_repository()
        self.assertEqual(self.read()["changes"]["alpha"], alpha)
        self.assertEqual(self.read()["changes"]["beta"], f._stored_change_coordination(beta))
        self.assertEqual(json.loads(self.plan_path.read_text())["note"], "Retained repository metadata")

    def artifact_pass(self, change):
        ledger, _ = f._load_plan_ledger(change)
        round_number = len(ledger["reviews"]) + 1
        report = self.repo / ".agents/workflow" / change / "reviews" / f"artifacts-{round_number}.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("Isolated Oracle artifact review fixture: PASS\n")
        f.plan_dispatch("oracle",  change=change, scope="change")
        f.plan_review("oracle", "PASS", self.git("rev-parse", "HEAD"), report, change=change, scope="change")

    def test_artifact_review_does_not_require_a_graph(self):
        self.plan_path.unlink()
        self.artifact_pass("alpha")
        ledger, path = f._load_plan_ledger("alpha")
        self.assertEqual(path, self.repo / ".agents/workflow/alpha/plan-reviews.json")
        self.assertEqual(ledger["reviews"][-1]["scope"], "change")
        self.assertFalse(self.plan_path.exists())

    def test_graph_review_requires_all_artifact_passes_and_uses_its_own_ledger(self):
        self.artifact_pass("alpha")
        with self.assertRaisesRegex(f.StateError, "beta.*artifact PASS"):
            f.plan_dispatch("oracle",  scope="graph")
        self.artifact_pass("beta")
        report = self.repo / ".agents/workflow/reviews/plan-graph-oracle-r1.md"
        report.parent.mkdir(parents=True)
        report.write_text("Isolated repo graph review fixture: PASS\n")
        f.plan_dispatch("oracle",  scope="graph")
        f.plan_review("oracle", "PASS", self.git("rev-parse", "HEAD"), report, scope="graph")
        ledger, path = f._load_plan_ledger(None)
        self.assertEqual(path, self.repo / ".agents/workflow/plan-graph-review.json")
        self.assertEqual(ledger["reviews"][-1]["scope"], "graph")
        self.assertEqual(ledger["reviews"][-1]["plan_path"], ".agents/workflow/plan-graph.json")
        with self.assertRaisesRegex(f.StateError, "omit --change"):
            f.plan_dispatch("oracle",  change="alpha", scope="graph")

    def test_changed_artifacts_invalidate_a_pending_repo_graph_review(self):
        self.artifact_pass("alpha")
        self.artifact_pass("beta")
        f.plan_dispatch("oracle",  scope="graph", key="graph-first")
        design = self.repo / "openspec/changes/beta/design.md"
        design.write_text(design.read_text() + "Changed acceptance behavior.\n")
        report = self.repo / ".agents/workflow/reviews/graph.md"
        report.parent.mkdir(parents=True)
        report.write_text("Isolated graph review fixture\n")
        with self.assertRaisesRegex(f.StateError, "beta.*artifact PASS"):
            f.plan_review("oracle", "PASS", self.git("rev-parse", "HEAD"), report, scope="graph")
        self.artifact_pass("beta")
        f.plan_dispatch("oracle",  scope="graph", key="graph-retry")
        f.plan_review("oracle", "PASS", self.git("rev-parse", "HEAD"), report, scope="graph")

    def test_artifact_contract_excludes_legacy_graph_and_task_status_marks(self):
        _, original = f._change_artifacts(self.repo, "alpha")
        self.write(self.repo / "openspec/changes/alpha/plan/graph-plan.json", {"legacy": True})
        (self.repo / "openspec/changes/alpha/tasks.md").write_text("- [x] 1.1 implement\n")
        self.assertEqual(f._change_artifacts(self.repo, "alpha")[1], original)
        (self.repo / "openspec/changes/alpha/design.md").write_text("Changed design\n")
        self.assertNotEqual(f._change_artifacts(self.repo, "alpha")[1], original)

    def test_old_graph_only_verdict_is_not_an_artifact_pass(self):
        self.write(self.repo / ".agents/workflow/alpha/plan-reviews.json", {
            "change": "alpha", "dispatches": [],
            "reviews": [{"role": "oracle", "verdict": "PASS", "plan_sha256": "legacy"}]})
        with self.assertRaisesRegex(f.StateError, "alpha.*artifact PASS"):
            f.plan_dispatch("oracle",  scope="graph")

    def test_public_commands_use_roles_without_model_overrides_or_manual_derivation(self):
        parser = f._parser()
        commands = next(action.choices for action in parser._actions if hasattr(action, "choices")
                        and isinstance(action.choices, dict))
        for retired in ("state-derive", "task-tick", "review-decision", "review-reuse"):
            self.assertNotIn(retired, commands)
        for command in ("dispatch", "plan-dispatch"):
            self.assertNotIn("--model", commands[command]._option_string_actions)
            self.assertNotIn("--level", commands[command]._option_string_actions)
        f.lane_open("work", "alpha")
        f.dispatch_run("work", "implementer", "1.1", change="alpha")
        node = self.read()["changes"]["alpha"]
        for record in (node["lanes"]["work"]["dispatches"][-1], node["subagents"][-1]):
            self.assertNotIn("model", record)
            self.assertNotIn("level", record)

    def test_manual_ticks_for_other_changes_cannot_merge(self):
        worktree, _ = self.finish_lane("alpha")
        self.mark_complete(worktree, "beta", "1.1")
        head = self.commit(worktree, "accidental foreign change tick")
        report = self.repo / ".agents/workflow/alpha/reviews/code-reviewer.md"
        f.lane_review("work", "code-reviewer", "PASS", head, report, change="alpha")
        feature_head = self.git("rev-parse", "HEAD")
        with self.assertRaisesRegex(f.StateError, "task files for other changes"):
            f.lane_merge("work", "alpha")
        self.assertEqual(self.git("rev-parse", "HEAD"), feature_head)

    def test_manual_ticks_for_other_lanes_cannot_merge(self):
        lane = copy.deepcopy(self.document["changes"]["alpha"]["lanes"]["work"])
        lane["tasks"] = {"1.2": {"deps": []}}
        self.document["changes"]["alpha"]["lanes"]["other"] = lane
        self.write(self.plan_path, self.document)
        tasks = self.repo / "openspec/changes/alpha/tasks.md"
        tasks.write_text(tasks.read_text() + "- [ ] 1.2 other lane\n")
        self.commit(self.repo, "new task artifacts approved by human")
        worktree, _ = self.finish_lane("alpha")
        self.mark_complete(worktree, "alpha", "1.2")
        head = self.commit(worktree, "accidental foreign lane tick")
        report = self.repo / ".agents/workflow/alpha/reviews/code-reviewer.md"
        f.lane_review("work", "code-reviewer", "PASS", head, report, change="alpha")
        with self.assertRaisesRegex(f.StateError, "added ticks"):
            f.lane_merge("work", "alpha")

    def test_old_skip_decisions_cannot_waive_standard_review(self):
        f.lane_open("work", "alpha")
        data = self.read()
        node = data["changes"]["alpha"]
        node["review_policy"] = "risk-based"
        node["lanes"]["work"]["decisions"] = {"duties": {"code": {"mode": "skip"}}}
        self.write(self.coord_path, data)
        worktree = Path(node["lanes"]["work"]["worktree"])
        self.mark_complete(worktree, "alpha", "1.1")
        self.commit(worktree, "complete without review")
        with self.assertRaisesRegex(f.StateError, "required review"):
            f.lane_merge("work", "alpha")

    def test_ancestor_and_legacy_carried_reviews_require_a_fresh_head_verdict(self):
        worktree, original = self.finish_lane("alpha")
        (worktree / "follow-up.txt").write_text("material follow-up\n")
        head = self.commit(worktree, "change after reviewed commit")
        with self.assertRaisesRegex(f.StateError, "superseded|re-review"):
            f.lane_merge("work", "alpha")
        data = self.read()
        review = data["changes"]["alpha"]["lanes"]["work"]["reviews"][-1]
        review.update(reused=True, reused_from=original)
        review["binding"]["commit"] = head
        self.write(self.coord_path, data)
        with self.assertRaisesRegex(f.StateError, "direct current-HEAD"):
            f.lane_merge("work", "alpha")
        report = self.repo / ".agents/workflow/alpha/reviews/code-reviewer.md"
        f.lane_review("work", "code-reviewer", "PASS", head, report, change="alpha")
        f.lane_merge("work", "alpha")

    def test_documentation_lint_rejects_retired_repository_paths(self):
        skill = self.repo / "bad-skill"
        skill.mkdir()
        for retired in ("openspec/plan-graph.json", ".agents/workflow/plan-reviews.json"):
            (skill / "SKILL.md").write_text("Retired location: " + retired)
            with self.assertRaisesRegex(AssertionError, "retired path"):
                f._check_docs(skill)

    def test_ambiguous_plan_review_requires_selection(self):
        with self.assertRaisesRegex(f.StateError, "provide --change"):
            f.plan_dispatch("oracle", )
        f.plan_dispatch("oracle",  change="alpha")
        ledger, _ = f._load_plan_ledger("alpha")
        self.assertEqual(ledger["dispatches"][0]["artifacts_path"], "openspec/changes/alpha")
        self.assertNotIn("plan_sha256", ledger["dispatches"][0])


if __name__ == "__main__":
    unittest.main()

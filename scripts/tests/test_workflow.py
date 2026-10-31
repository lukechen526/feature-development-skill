"""Behavioral coverage of the public CLI and workflow gates in isolated Git repositories."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HELPER = Path(__file__).resolve().parents[1] / 'featdev.py'
spec = importlib.util.spec_from_file_location('featdev', HELPER)
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='featdev-test-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        environment = {k: v for k, v in os.environ.items()
                       if not k.startswith('GIT_') and k != 'FEATDEV_LANE_WORKTREE_ROOT'}
        environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
                           FEATDEV_LANE_WORKTREE_ROOT=str(self.root / 'lanes'))
        self.environment = patch.dict(os.environ, environment, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.previous = Path.cwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, self.previous)
        self.git('init', '-q', '-b', 'feature')
        self.git('config', 'user.name', 'Workflow Test')
        self.git('config', 'user.email', 'workflow@example.test')
        (self.repo / '.gitignore').write_text('.agents/workflow/\n')
        self.tasks = self.repo / 'openspec/changes/example/tasks.md'
        self.tasks.parent.mkdir(parents=True)
        self.tasks.write_text('## Tasks\n- [ ] 1.1 Add behavior\n- [ ] 1.2 Cover behavior\n')
        self.proposal = self.tasks.parent / 'proposal.md'
        self.proposal.write_text('Add a greeting.\n')
        (self.repo / 'app.txt').write_text('before\n')
        self.commit('initial plan')
        self.plan = {'schema_version': 3, 'changes': {'example': {'lanes': {'api': {
            'risk': 'ordinary', 'ui': False, 'sensitive': False,
            'depends_on': [], 'tasks': {'1.1': {'deps': []}, '1.2': {'deps': ['1.1']}}
        }}}}}
        self.write(self.repo / f._REPO_PLAN, self.plan)

    def git(self, *args, repo=None):
        return subprocess.run(['git', '-C', str(repo or self.repo), *args],
                              check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, message, repo=None):
        self.git('add', '.', repo=repo)
        self.git('commit', '-qm', message, repo=repo)
        return self.git('rev-parse', 'HEAD', repo=repo)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + '\n')

    def report(self, name='review', content='PASS: independently checked acceptance criteria\n'):
        path = self.repo / '.agents/workflow/reviews' / (name + '.md')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def cli(self, *args, ok=True):
        result = subprocess.run([os.sys.executable, str(HELPER), *map(str, args)],
                                capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def initial_pass(self):
        self.cli('dispatch', '--scope', 'plan', 'oracle', '--task', 'plan', '--key', 'initial')
        self.cli('review', '--scope', 'plan', 'oracle', 'PASS', '--commit',
                 self.git('rev-parse', 'HEAD'), '--report', self.report('oracle'))

    def approve(self):
        self.initial_pass()
        self.cli('plan-approve')

    def open_lane(self, risk='ordinary', ui=False, sensitive=False):
        self.plan['changes']['example']['lanes']['api'].update(risk=risk, ui=ui, sensitive=sensitive)
        self.write(self.repo / f._REPO_PLAN, self.plan)
        self.approve()
        self.cli('lane-open', 'api')
        return Path(self.coordination()['changes']['example']['lanes']['api']['worktree'])

    def coordination(self):
        return json.loads((self.repo / '.agents/workflow/coordination.json').read_text())

    def save_coordination(self, value):
        self.write(self.repo / '.agents/workflow/coordination.json', value)

    def implement(self, lane):
        (lane / 'app.txt').write_text('after\n')
        tasks = lane / self.tasks.relative_to(self.repo)
        tasks.write_text(tasks.read_text().replace('[ ]', '[x]'))
        return self.commit('implement both tasks', lane)

    def lane_review(self, sha, role='code-reviewer', evidence=None):
        args = ['review', 'api', role, 'PASS', '--commit', sha,
                '--report', self.report(role + sha[:7])]
        if evidence:
            args += ['--evidence', evidence, '--evidence-revision', 'render-1']
        self.cli(*args)

    def amend(self, request='Human requested updated greeting behavior'):
        value = self.coordination() if (self.repo / '.agents/workflow/coordination.json').exists() else {
            'schema_version': 2, 'feature_checkout': str(self.repo), 'changes': {}}
        value.setdefault('plan_amendments', []).append({
            'human_request': request, 'inputs': f._planning_inputs(self.repo)})
        self.save_coordination(value)

    def integrated(self):
        lane = self.open_lane()
        sha = self.implement(lane)
        self.lane_review(sha)
        self.cli('lane-merge', 'api')
        self.cli('lane-close', 'api')
        return sha

    def test_command_surface_and_retired_commands(self):
        parser = f._parser()
        commands = next(a for a in parser._actions if isinstance(a, f.argparse._SubParsersAction)).choices
        self.assertEqual(set(commands), {'plan-validate', 'review', 'plan-approve', 'dispatch',
            'dispatch-done', 'lane-open', 'lane-merge', 'lane-close', 'human-gate',
            'status', 'dashboard', 'finish-record'})
        for command in ('migrate-repo', 'plan-graph', 'plan-dispatch', 'plan-review', 'lane-review',
                        'plan-waive', 'lane-mediate', 'human-defer', 'sync', 'task-status'):
            self.cli(command, ok=False)
        for command in commands:
            self.cli(command, '--help')

    def test_plan_mapping_cycles_and_required_flags(self):
        self.cli('plan-validate')
        lane = self.plan['changes']['example']['lanes']['api']
        lane['tasks']['1.1']['deps'] = ['1.2']
        self.write(self.repo / f._REPO_PLAN, self.plan)
        self.cli('plan-validate', ok=False)
        lane['tasks']['1.1']['deps'] = []
        del lane['ui']
        self.write(self.repo / f._REPO_PLAN, self.plan)
        self.cli('plan-validate', ok=False)

    def test_initial_review_until_first_pass_without_round_limit(self):
        self.cli('plan-approve', ok=False)
        self.cli('lane-open', 'api', ok=False)
        for i in range(4):
            self.cli('dispatch', '--scope', 'plan', 'oracle', '--task', 'plan', '--key', f'oracle-{i}')
            verdict = 'PASS' if i == 3 else 'MORE WORK'
            self.cli('review', '--scope', 'plan', 'oracle', verdict, '--commit',
                     self.git('rev-parse', 'HEAD'), '--report', self.report(f'oracle-{i}', verdict))
            if verdict == 'MORE WORK':
                self.cli('plan-approve', ok=False)
                self.proposal.write_text(self.proposal.read_text() + f'Correction {i}\n')
        self.cli('plan-approve', ok=False)  # corrected artifacts are uncommitted
        self.commit('corrected planning')
        self.cli('plan-approve')
        self.cli('dispatch', '--scope', 'plan', 'oracle', '--task', 'plan', ok=False)
        self.cli('lane-open', 'api')

    def test_initial_review_cannot_record_changed_inputs_or_missing_report(self):
        self.cli('dispatch', '--scope', 'plan', 'oracle', '--task', 'plan', '--key', 'first')
        self.proposal.write_text('Changed while review ran\n')
        self.cli('review', '--scope', 'plan', 'oracle', 'PASS', '--commit',
                 self.git('rev-parse', 'HEAD'), '--report', self.report(), ok=False)
        self.cli('dispatch-done', '--scope', 'plan', 'oracle', '--key', 'first', '--result', 'failed')
        self.cli('dispatch', '--scope', 'plan', 'oracle', '--task', 'plan', '--key', 'second')
        self.cli('review', '--scope', 'plan', 'oracle', 'PASS', '--commit',
                 self.git('rev-parse', 'HEAD'), '--report', self.repo / 'missing.md', ok=False)

    def test_human_requested_artifact_and_graph_changes_need_no_reapproval(self):
        self.approve()
        approval = (self.repo / '.agents/workflow/plan-approval.json').read_bytes()
        self.proposal.write_text('Human requested a different greeting\n')
        self.plan['changes']['example']['lanes']['api']['risk'] = 'high'
        self.write(self.repo / f._REPO_PLAN, self.plan)
        self.amend()
        self.cli('lane-open', 'api', ok=False)  # must commit changed artifact
        self.commit('human requested plan change')
        self.cli('plan-validate', '--workflow')
        self.cli('lane-open', 'api')
        self.assertEqual(approval, (self.repo / '.agents/workflow/plan-approval.json').read_bytes())
        self.assertEqual(len(json.loads((self.repo / '.agents/workflow/plan-graph-review.json').read_text())['reviews']), 1)
        self.assertEqual(json.loads(self.cli('status', '--json').stdout)['plan_approval']['state'], 'current')

    def test_unattributed_plan_edit_does_not_inherit_authorization(self):
        self.approve()
        self.proposal.write_text('Unrequested feature\n')
        self.commit('changed plan')
        self.cli('lane-open', 'api', ok=False)
        self.amend('Human requested changed feature')
        self.cli('lane-open', 'api')

    def test_task_sessions_writer_ownership_and_live_status(self):
        lane = self.open_lane()
        self.cli('dispatch', 'api', 'implementer', '--task', '1.1', '--key', 'one')
        self.cli('dispatch', 'api', 'implementer', '--task', '1.2', '--key', 'overlap', ok=False)
        self.cli('dispatch', 'api', 'code-reviewer', '--task', 'review', '--key', 'overlap-read', ok=False)
        task_path = lane / self.tasks.relative_to(self.repo)
        task_path.write_text(task_path.read_text().replace('[ ] 1.1', '[x] 1.1'))
        self.commit('first task', lane)
        self.cli('dispatch-done', 'api', 'implementer', '--key', 'one', '--worker-run-id', 'same-worker', '--result', 'ok')
        self.cli('dispatch', 'api', 'implementer', '--task', '1.2', '--key', 'two')
        view = json.loads(self.cli('status', '--json').stdout)
        self.assertEqual(view['lanes']['api']['task_states'], {'1.1': 'done', '1.2': 'in_progress'})
        task_path.write_text(task_path.read_text().replace('[ ]', '[x]'))
        self.commit('second task', lane)
        self.cli('dispatch-done', 'api', 'implementer', '--key', 'two', '--worker-run-id', 'same-worker', '--result', 'ok')
        rows = self.coordination()['changes']['example']['lanes']['api']['dispatches']
        self.assertEqual([r['worker_run_id'] for r in rows], ['same-worker', 'same-worker'])
        self.assertEqual(json.loads(self.cli('status', '--json').stdout)['lanes']['api']['task_states']['1.2'], 'done')
        # A stale annotation cannot fabricate committed completion.
        value = self.coordination()
        value['changes']['example']['lanes']['api']['tasks']['1.1'] = {'status': 'blocked', 'reason': 'old'}
        self.save_coordination(value)
        self.assertEqual(json.loads(self.cli('status', '--json').stdout)['lanes']['api']['task_states']['1.1'], 'done')

    def test_lane_reviews_after_many_corrections_and_stale_candidate(self):
        lane = self.open_lane()
        sha = self.implement(lane)
        for i in range(4):
            verdict = 'PASS' if i == 3 else 'MORE WORK'
            self.cli('review', 'api', 'code-reviewer', verdict, '--commit', sha,
                     '--report', self.report(f'code-{i}', verdict))
            if i < 3:
                self.cli('lane-merge', 'api', ok=False)
                (lane / 'app.txt').write_text(f'correction {i}\n')
                sha = self.commit(f'correction {i}', lane)
        self.cli('lane-merge', 'api')
        self.cli('lane-close', 'api')
        self.assertEqual(self.coordination()['changes']['example']['lanes']['api']['phase'], 'closed')

    def test_high_risk_requires_separate_test_review(self):
        lane = self.open_lane(risk='high')
        sha = self.implement(lane)
        self.lane_review(sha)
        self.cli('lane-merge', 'api', ok=False)
        self.lane_review(sha, 'test-reviewer')
        self.cli('lane-merge', 'api')

    def test_ui_review_evidence_and_human_gate(self):
        lane = self.open_lane(ui=True)
        sha = self.implement(lane)
        self.lane_review(sha)
        self.cli('review', 'api', 'ui-reviewer', 'PASS', '--commit', sha,
                 '--report', self.report('ui-no-evidence'), ok=False)
        evidence = self.report('render', 'Rendered candidate screenshot manifest\n')
        self.lane_review(sha, 'ui-reviewer', evidence)
        self.cli('lane-merge', 'api', ok=False)
        self.cli('human-gate', 'api', '--state', 'approved', '--commit', sha,
                 '--evidence-revision', 'render-1', '--evidence', evidence)
        evidence.write_text('Evidence changed\n')
        self.cli('lane-merge', 'api', ok=False)
        self.lane_review(sha, 'ui-reviewer', evidence)
        self.cli('human-gate', 'api', '--state', 'approved', '--commit', sha,
                 '--evidence-revision', 'render-1', '--evidence', evidence)
        self.cli('lane-merge', 'api')

    def test_eligible_ui_deferral_uses_human_gate_command(self):
        lane = self.open_lane(ui=True)
        sha = self.implement(lane)
        evidence = self.report('render', 'Current rendered evidence\n')
        self.lane_review(sha)
        self.lane_review(sha, 'ui-reviewer', evidence)
        self.cli('human-gate', 'api', '--state', 'deferred-to-integrated', '--commit', sha,
                 '--evidence-revision', 'render-1', '--evidence', evidence,
                 '--rationale', 'Bounded reversible color change; no safety privacy medical legal financial permission or content effects',
                 '--risk-assessment', 'low')
        self.cli('lane-merge', 'api')
        gate = self.coordination()['changes']['example']['human_gates']['api']
        self.assertEqual(gate['state'], 'deferred-to-integrated')

    def test_stale_contract_and_candidate_reviews_block_merge(self):
        lane = self.open_lane()
        sha = self.implement(lane)
        self.lane_review(sha)
        (lane / 'app.txt').write_text('New candidate\n')
        sha = self.commit('new candidate', lane)
        self.cli('lane-merge', 'api', ok=False)
        self.lane_review(sha)
        self.plan['changes']['example']['lanes']['api']['risk'] = 'high'
        self.write(self.repo / f._REPO_PLAN, self.plan)
        self.amend()
        self.cli('lane-merge', 'api', ok=False)
        self.lane_review(sha)
        self.lane_review(sha, 'test-reviewer')
        self.cli('lane-merge', 'api')

    def test_scope_task_definition_and_tick_ownership(self):
        lane = self.open_lane()
        self.implement(lane)
        path = lane / self.tasks.relative_to(self.repo)
        path.write_text(path.read_text().replace('Add behavior', 'Changed requirements'))
        sha = self.commit('unauthorized task definition', lane)
        self.lane_review(sha)
        self.cli('lane-merge', 'api', ok=False)

    def test_dirty_candidate_and_missing_report_block_merge(self):
        lane = self.open_lane()
        sha = self.implement(lane)
        self.lane_review(sha)
        (lane / 'app.txt').write_text('dirty\n')
        self.cli('lane-merge', 'api', ok=False)
        self.git('restore', 'app.txt', repo=lane)
        self.report('code-reviewer' + sha[:7]).unlink()
        self.cli('lane-merge', 'api', ok=False)

    def test_plan_waiver_scope_is_distinct_from_approval(self):
        self.initial_pass()
        self.cli('plan-approve', '--waiver', 'Human waived initial approval', '--lane', 'example/api')
        self.cli('lane-open', 'api')
        self.assertEqual(json.loads(self.cli('status', '--json').stdout)['plan_approval']['state'], 'waived')

    def test_status_graph_and_dashboard(self):
        self.approve()
        # Read commands must not write records or ignore configuration.
        def snapshot():
            return {str(path.relative_to(self.repo)): path.read_bytes()
                    for path in self.repo.rglob('*') if path.is_file() and '.git/objects' not in str(path)}
        before = snapshot()
        self.cli('status')
        self.cli('status', '--json')
        self.assertIn('flowchart', self.cli('status', '--format', 'mermaid').stdout)
        self.assertIn('api', self.cli('status', '--format', 'md').stdout)
        self.assertEqual(before, snapshot())
        output = self.root / 'dashboard.html'
        self.cli('dashboard', '--output', output)
        self.assertIn('Feature development dashboard', output.read_text())

    def test_reconstruction_breaking_schema_preserves_integrations(self):
        self.integrated()
        coordination = self.coordination()
        old = json.loads(json.dumps(coordination))
        old['schema_version'] = 900
        self.save_coordination(old)
        self.cli('plan-validate', '--workflow', ok=False)
        backup = self.root / 'workflow-backup.json'
        self.write(backup, old)
        coordination['reconstruction'] = {'at': f._utc_timestamp(), 'sources': [str(backup), self.git('rev-parse', 'HEAD')],
                                          'transformations': ['reconstructed schema 2'], 'unknowns': []}
        self.save_coordination(coordination)
        self.cli('plan-validate', '--workflow')
        view = json.loads(self.cli('status', '--json').stdout)
        self.assertEqual(view['lanes']['api']['phase'], 'closed')
        self.cli('lane-open', 'api', ok=False)
        self.cli('finish-record', 'classify', '--classification', 'patch', '--artifact', self.report('prep'),
                 '--input', 'app.txt', '--check', 'tests')

    def test_reconstruction_missing_judgment_remains_blocked(self):
        lane = self.open_lane()
        self.implement(lane)
        value = self.coordination()
        value['reconstruction'] = {'at': f._utc_timestamp(), 'sources': ['Git and surviving worktree'],
                                   'unknowns': ['No independent review report survives']}
        value['changes']['example']['lanes']['api']['reviews'] = []
        self.save_coordination(value)
        self.cli('plan-validate', '--workflow')
        self.cli('lane-merge', 'api', ok=False)

    def test_finish_checks_human_inspection_and_affected_inputs(self):
        self.cli('finish-record', 'classify', '--classification', 'patch', '--artifact', self.report('prep'),
                 '--input', 'app.txt', '--check', 'tests', ok=False)
        self.integrated()
        prep = self.report('prep')
        self.cli('finish-record', 'classify', '--classification', 'patch', '--artifact', prep,
                 '--input', 'app.txt', '--check', 'tests', '--inspection', 'output')
        self.cli('finish-record', 'pr-check', ok=False)
        self.cli('finish-record', 'capture', '--check', 'tests', '--artifact', self.report('tests'), '--input', 'app.txt')
        self.cli('finish-record', 'pr-check', ok=False)
        report = self.report('human', 'inspector: human\ncheckpoint: output\nverdict: accept\n')
        self.cli('finish-record', 'inspect', '--check', 'output', '--artifact', report, '--input', 'app.txt')
        self.cli('finish-record', 'pr-check')
        (self.repo / 'unrelated.md').write_text('Unrelated documentation\n')
        self.commit('unrelated docs')
        self.cli('finish-record', 'pr-check')
        (self.repo / 'app.txt').write_text('changed release input\n')
        self.commit('changed release input')
        self.cli('finish-record', 'pr-check', ok=False)
        self.cli('plan-validate', '--workflow')

    def test_required_records_and_malformed_reconstruction(self):
        self.approve()
        approval = self.repo / '.agents/workflow/plan-approval.json'
        value = json.loads(approval.read_text())
        value['reconstruction'] = {'unknowns': []}
        self.write(approval, value)
        self.cli('plan-validate', '--workflow', ok=False)
        del value['reconstruction']
        value['schema_version'] = 1
        self.write(approval, value)
        self.cli('lane-open', 'api', ok=False)

    def test_combined_planning_covers_multiple_changes(self):
        extra = self.repo / 'openspec/changes/other/tasks.md'
        extra.parent.mkdir(parents=True)
        extra.write_text('- [ ] 1.1 Independent task\n')
        self.plan['changes']['other'] = {'lanes': {'other': {
            'risk': 'ordinary', 'ui': False, 'sensitive': False,
            'tasks': {'1.1': {'deps': []}}}}}
        self.write(self.repo / f._REPO_PLAN, self.plan)
        self.commit('second change')
        self.approve()
        inputs = json.loads((self.repo / '.agents/workflow/plan-graph-review.json').read_text())['reviews'][0]['inputs']
        self.assertEqual(set(inputs['artifacts']), {'example', 'other'})
        self.cli('lane-open', 'api', ok=False)  # selection is now ambiguous
        self.cli('--change', 'example', 'lane-open', 'api')
        self.cli('--change', 'other', 'lane-open', 'other')
        self.cli('plan-validate', '--workflow')

    def test_archive_retains_identity_and_initial_authorization(self):
        self.integrated()
        archive = self.repo / 'openspec/changes/archive/2026-10-31-example'
        archive.parent.mkdir(parents=True)
        self.tasks.parent.rename(archive)
        self.commit('archive completed specification')
        self.cli('plan-validate', '--workflow')
        self.assertEqual(json.loads(self.cli('status', '--json').stdout)['plan_approval']['state'], 'current')
        self.cli('finish-record', 'classify', '--classification', 'patch', '--artifact', self.report('prep'),
                 '--input', 'app.txt', '--check', 'tests')

    def test_reconstructed_active_lane_keeps_actual_worktree_location(self):
        lane = self.open_lane()
        coordination = self.coordination()
        path = self.repo / '.agents/workflow/coordination.json'
        path.unlink()
        self.cli('lane-open', 'api', ok=False)
        coordination['reconstruction'] = {'at': f._utc_timestamp(), 'sources': ['Surviving worktree and initial records'],
                                          'unknowns': ['Implementation outcome not yet known']}
        self.save_coordination(coordination)
        # New default location must not move a recovered active lane.
        os.environ['FEATDEV_LANE_WORKTREE_ROOT'] = str(self.root / 'new-default')
        self.cli('plan-validate', '--workflow')
        self.cli('lane-open', 'api')
        self.assertEqual(self.coordination()['changes']['example']['lanes']['api']['worktree'], str(lane))
        sha = self.implement(lane)
        self.lane_review(sha)
        self.cli('lane-merge', 'api')
        self.cli('lane-close', 'api')
        self.assertFalse(lane.exists())

    def test_authorized_oracle_standin_is_consumed_once(self):
        lane = self.open_lane(sensitive=True)
        sha = self.implement(lane)
        self.lane_review(sha)
        view = json.loads(self.cli('status', '--json').stdout)
        grant = self.repo / '.agents/workflow/grant.json'
        self.write(grant, {'id': 'human-grant', 'granted_by': 'human', 'granted_at': f._utc_timestamp(),
                           'scope': ['api'], 'plan_sha256': view['plan_sha256']})
        args = ['human-gate', 'api', '--state', 'approved', '--commit', sha,
                '--evidence-revision', 'r1', '--approved-by', 'oracle', '--authorization', grant,
                '--stand-in-report', self.report('stand-in'), '--checklist', self.report('checklist')]
        self.cli(*args, '--stand-in-verdict', 'MORE WORK', ok=False)
        self.cli('lane-merge', 'api', ok=False)
        self.cli(*args, '--stand-in-verdict', 'PASS')
        self.cli('lane-merge', 'api')
        self.assertEqual(self.coordination()['changes']['example']['authorizations']['human-grant']['status'], 'consumed')

    def test_explicit_human_override_records_waiver_without_fabricating_pass(self):
        lane = self.open_lane(sensitive=True)
        sha = self.implement(lane)
        self.cli('lane-merge', 'api', '--human-override', '--override-head', sha,
                 '--override-reason', 'Human explicitly waived lane review and sensitive checkpoint',
                 '--waive', 'reviews', '--waive', 'human-gate')
        value = self.coordination()['changes']['example']
        self.assertEqual(value['lanes']['api']['reviews'], [])
        self.assertEqual(value['human_gates']['api']['state'], 'waived')



if __name__ == '__main__':
    unittest.main()

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from simple_ar.core.capabilities import ArtifactStore, AttemptManifest, CapabilityContext
from simple_ar.experiment.execution.backend import RunRequest, RunResult
from simple_ar.research.preparation import PreparationRequest, run_preparation_capability
from simple_ar.research.project_environment import environment_profile
from simple_ar.cli.parser import build_parser
from simple_ar.cli.start import prepare_start
from simple_ar.cli.research_config import research_defaults
from simple_ar.research.task_plan import TaskPlanRequest, default_task_steps, build_task_plan


class ProjectEnvironmentTests(unittest.TestCase):
    def test_application_preparation_requires_declared_scope_not_fake_research_design(self):
        from simple_ar.app.research_application import create_session, ResearchApplicationServices
        from simple_ar.research.workflow_contracts import ResearchBrief
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = {'execution': {'command': ['python', 'run.py'], 'cwd': str(root), 'timeout_sec': 7,
                                   'environment': {'mode': 'venv'}}, 'research_task_kind': 'measurement'}
            app = create_session(ResearchBrief(request_text='Prepare declared environment', requested_outputs=('experiments',)),
                                 root=root / 'session', services=ResearchApplicationServices(config=config))
            with patch.object(app, '_execute', return_value=True) as execute:
                self.assertTrue(app._run_prepare_execution_action())
            self.assertNotIn('design', [ref.kind for ref in execute.call_args.args[3]])
            self.assertIs(execute.call_args.kwargs['backend'].budget_ledger, app.budget_ledger)
            self.assertEqual(execute.call_args.args[2].execution['environment']['mode'], 'venv')

    def context(self, root):
        return CapabilityContext(ArtifactStore(root / 'attempt'), AttemptManifest('prepare-1'))

    def request(self, root, **changes):
        config = {'command': ['python', 'run.py', '--fixed', '42'], 'cwd': str(root), 'timeout_sec': 19,
                  'environment': {'mode': 'venv', 'requirements': ['requirements.txt'], 'timeout_sec': 7}}
        config.update(changes)
        return PreparationRequest(config, 'Preserve the fixed method', RunRequest(config['command'], root, 19, session_id='session', attempt_id='prepare-1'))

    def test_profile_requires_explicit_bounded_scope_without_install_commands(self):
        for value in (None, {}, {'mode': 'current'}, {'mode': 'venv', 'commands': ['pip install x']},
                      {'mode': 'venv', 'requirements': 'requirements.txt'}, {'mode': 'venv', 'timeout_sec': True},
                      {'mode': 'venv', 'requirements': ['../outside.txt']}, {'mode': 'venv', 'requirements': ['-r outside']},
                      {'mode': 'venv', 'requirements': ['a\\b.txt']}, {'mode': 'venv', 'python_executable': ''}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                environment_profile(value)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(ValueError, 'not found inside'):
                environment_profile({'mode': 'venv', 'requirements': ['absent.txt']}, project=root)
        self.assertEqual(environment_profile({'mode': 'venv'})['requirements'], [])

    def test_each_step_uses_same_backend_and_final_command_preserves_scientific_arguments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'requirements.txt').write_text('numpy==2.0.0\n')
            context = self.context(root)
            calls = []
            class Backend:
                def run(self, request):
                    calls.append(request)
                    if len(calls) == 1:
                        import os
                        python = Path(request.command[-1]) / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
                        python.parent.mkdir(parents=True)
                        python.touch()
                    return RunResult(0, False, 'observed stdout', '', command=request.command, cwd=str(request.cwd))
            result = run_preparation_capability(context=context, request=self.request(root), backend=Backend())
            self.assertEqual(result.status, 'completed')
            self.assertEqual(len(calls), 3)
            self.assertTrue(all(row.timeout_sec == 7 and row.session_id == 'session' and row.attempt_id == 'prepare-1' for row in calls))
            self.assertEqual(calls[0].command[:3], [sys.executable, '-m', 'venv'])
            self.assertEqual(calls[1].command[-2:], ['-r', str(root / 'requirements.txt')])
            self.assertEqual(calls[2].command[-3:], ['-m', 'pip', 'check'])
            payload = context.store.read_json(next(ref for ref in result.artifacts if ref.kind == 'prepared_execution'))
            self.assertNotIn('environment', payload['execution'])
            self.assertEqual(payload['execution']['command'][1:], ['run.py', '--fixed', '42'])
            self.assertTrue(payload['execution']['command'][0].startswith(str(context.store.root)))
            self.assertEqual(payload['execution']['timeout_sec'], 19)
            self.assertIn('scientific validity', ' '.join(payload['limitations']))

    def test_project_install_is_opt_in_and_requires_a_declared_package_before_launch(self):
        self.assertFalse(environment_profile({'mode': 'venv'})['install_project'])
        for value in ('true', 1, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'boolean'):
                environment_profile({'mode': 'venv', 'install_project': value})
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            context = self.context(root)
            with self.assertRaisesRegex(ValueError, 'packaging|pyproject'), patch(
                    'simple_ar.research.project_environment.LocalExecutionBackend.run', side_effect=AssertionError('no launch')):
                run_preparation_capability(context=context, request=self.request(root,
                    environment={'mode': 'venv', 'install_project': True}))
            self.assertFalse((context.store.root / 'environment').exists())
            for name in ('pyproject.toml', 'setup.py', 'setup.cfg'):
                (root / name).touch()
                self.assertTrue(environment_profile({'mode': 'venv', 'install_project': True}, project=root)['install_project'])
                (root / name).unlink()

    def test_project_and_requirements_share_one_resolver_invocation(self):
        for requirements in ([], ['requirements.txt']):
            with self.subTest(requirements=requirements), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / 'pyproject.toml').write_text('[project]\nname="fixture"\nversion="0.1"\n')
                (root / 'requirements.txt').write_text('numpy==2.0.0\n')
                calls = []
                class Backend:
                    def run(self, request):
                        calls.append(request)
                        if len(calls) == 1:
                            import os
                            python = Path(request.command[-1]) / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
                            python.parent.mkdir(parents=True)
                            python.touch()
                        return RunResult(0, False, 'observed', '', command=request.command)
                context = self.context(root)
                result = run_preparation_capability(context=context, request=self.request(root,
                    environment={'mode': 'venv', 'requirements': requirements, 'install_project': True}), backend=Backend())
                self.assertEqual(result.status, 'completed')
                self.assertEqual(len(calls), 3)
                self.assertEqual(calls[1].command[1:5], ['-m', 'pip', 'install', '--disable-pip-version-check'])
                self.assertEqual(calls[1].command[5:],
                                 (['-r', str(root / 'requirements.txt')] if requirements else []) + [str(root)])
                payload = context.store.read_json('execution.json')
                self.assertTrue(payload['environment']['install_project'])
                self.assertEqual(payload['execution']['command'][1:], ['run.py', '--fixed', '42'])

    def test_project_build_failure_cannot_publish_a_prepared_scientific_command(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'setup.py').write_text('raise RuntimeError("build error")')
            calls = []
            class Backend:
                def run(self, request):
                    calls.append(request)
                    return RunResult(0, False, '', '') if len(calls) == 1 else RunResult(1, False, '', 'build error')
            context = self.context(root)
            result = run_preparation_capability(context=context, request=self.request(root,
                environment={'mode': 'venv', 'install_project': True}), backend=Backend())
            self.assertEqual(result.status, 'failed')
            self.assertEqual(len(calls), 2)
            self.assertFalse((context.store.root / 'execution.json').exists())
            self.assertTrue(context.store.read_json('environment_setup.json')['profile']['install_project'])

    def test_failed_or_timed_out_install_stops_before_science_and_preserves_observations(self):
        for failure in (RunResult(1, False, '', 'install failed'), RunResult(None, True, '', 'timeout')):
            with self.subTest(failure=failure.status), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / 'requirements.txt').write_text('some-dependency\n')
                context = self.context(root)
                calls = []
                class Backend:
                    def run(self, request):
                        calls.append(request)
                        return RunResult(0, False, '', '') if len(calls) == 1 else failure
                result = run_preparation_capability(context=context, request=self.request(root), backend=Backend())
                self.assertEqual(result.status, 'failed')
                self.assertEqual(len(calls), 2)
                self.assertFalse(any(ref.kind == 'prepared_execution' for ref in result.artifacts))
                self.assertEqual(context.store.read_json('environment_setup.json')['steps'][-1]['status'], failure.status)

    def test_interruption_records_unknown_step_and_no_in_place_environment_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'requirements.txt').write_text('')
            context = self.context(root)
            class Interrupted:
                def run(self, request):
                    (context.store.root / 'environment').mkdir()
                    raise RuntimeError('worker stopped after allocation')
            with self.assertRaisesRegex(RuntimeError, 'worker stopped'):
                run_preparation_capability(context=context, request=self.request(root), backend=Interrupted())
            self.assertEqual(context.store.read_json('environment_setup.json')['steps'][0]['status'], 'allocated_result_unknown')
            with self.assertRaisesRegex(ValueError, 'already exists'):
                run_preparation_capability(context=context, request=self.request(root), backend=Interrupted())

    def test_explicit_interpreter_and_combined_workflows_are_not_silently_rewritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'requirements.txt').write_text('')
            for changes in ({'command': [sys.executable, 'run.py']}, {'code_task': {}}, {'dataset': 'x.csv'}, {'pairs': [{}]}):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    run_preparation_capability(context=self.context(root), request=self.request(root, **changes))

    def test_guided_option_serializes_same_profile_and_counts_preparation_invocations(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'paper.md').write_text('A supplied reference')
            (root / 'requirements.txt').write_text('numpy==2.0.0\n')
            args = build_parser().parse_args(['start', '--kind', 'reproduction', '--goal', 'Check source conclusion',
                '--document', str(root / 'paper.md'), '--project', str(root), '--environment', 'venv',
                '--hypothesis', 'Claim', '--dataset', 'Fixed data', '--expected-outcome', 'Compare observed score',
                '--metric', 'score', '--timeout-sec', '11', '--prepare-only', '--output-root', str(root / 'runs'),
                '--command', 'python', 'run.py'])
            with patch('sys.stdin.isatty', return_value=False), patch('simple_ar.cli.start.print_line'):
                config = prepare_start(args)
            defaults = research_defaults(['research-session', '--config', str(config)])
            profile = defaults['execution_details']['environment']
            self.assertEqual(profile['requirements'], ['requirements.txt'])
            self.assertEqual(profile['timeout_sec'], 11)
            self.assertEqual(defaults['process_invocations'], 4)
            self.assertEqual(defaults['process_wall_seconds'], 44)
            self.assertEqual(defaults['command_argv'], ['python', 'run.py'])

    def test_measurement_plan_has_only_confirmed_preparation_then_measurement_analysis(self):
        request = TaskPlanRequest(goal='Observe fixed runtime', request_text='Observe fixed runtime', task_kind='measurement', requested_outputs=('experiments',),
            execution={'command': ['python', 'run.py'], 'environment': {'mode': 'venv'}}, execution_protocol_accepted=True)
        self.assertEqual([row['action'] for row in default_task_steps(request)], ['prepare_execution', 'experiment', 'analysis'])
        plan = build_task_plan(request)
        self.assertEqual([row.action for row in plan.steps], ['prepare_execution', 'experiment', 'analysis'])

    def test_explicit_cli_project_install_serializes_and_counts_the_shared_install_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'paper.md').write_text('Supplied reference')
            (root / 'setup.cfg').write_text('[metadata]\nname=fixture\n')
            args = build_parser().parse_args(['start', '--kind', 'reproduction', '--goal', 'Check fixed conclusion',
                '--document', str(root / 'paper.md'), '--project', str(root), '--environment', 'venv',
                '--install-project', '--hypothesis', 'Claim', '--dataset', 'Fixed data', '--expected-outcome', 'Compare score',
                '--metric', 'score', '--timeout-sec', '11', '--prepare-only', '--output-root', str(root / 'runs'),
                '--command', 'python', 'run.py'])
            with patch('sys.stdin.isatty', return_value=False), patch('simple_ar.cli.start.print_line'):
                config = prepare_start(args)
                args.environment = 'current'
                with self.assertRaisesRegex(ValueError, 'requires --kind reproduction --environment venv'):
                    prepare_start(args)
            defaults = research_defaults(['research-session', '--config', str(config)])
            self.assertTrue(defaults['execution_details']['environment']['install_project'])
            self.assertEqual(defaults['execution_details']['environment']['requirements'], [])
            self.assertEqual(defaults['process_invocations'], 4)
            self.assertEqual(defaults['process_wall_seconds'], 44)

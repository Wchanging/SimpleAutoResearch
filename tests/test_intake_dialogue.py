import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from simple_ar.cli.intake_dialogue import discuss_start, validate_proposal
from simple_ar.cli.parser import build_parser
from simple_ar.cli.research_config import research_defaults
from simple_ar.cli.start import prepare_start
from simple_ar.core.artifacts import read_json


class Client:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def ask_json(self, system, payload, **kwargs):
        self.requests.append(json.loads(payload))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def proposal(kind="data_analysis", **changes):
    return {"kind": kind, "summary": "Describe matched observations without inferential claims",
            "questions": [], "assumptions": [], "options": {}, **changes}


class AnalysisReportProposalTests(unittest.TestCase):
    def test_report_can_propose_local_dictionary_without_online_scope(self):
        args = build_parser().parse_args(['start'])
        assets = [{'role': 'material', 'path_quote': 'dictionary.md'},
                  {'role': 'paper', 'path_quote': 'reference.pdf'}]
        result = validate_proposal(proposal(assets=assets, options={'with_report': True, 'sources': 'materials'}), args, set())
        self.assertEqual(result['assets'], assets)
        with self.assertRaisesRegex(ValueError, 'selected function'):
            validate_proposal(proposal(assets=assets), args, set())
        with self.assertRaisesRegex(ValueError, 'supplied material only'):
            validate_proposal(proposal(assets=assets, options={'with_report': True, 'sources': 'search'}), args, set())

    def test_report_intent_is_boolean_confirmed_and_cannot_change_explicit_choice(self):
        args = build_parser().parse_args(['start', '--with-report'])
        result = validate_proposal(proposal(options={'with_report': True}), args, {'with_report'})
        self.assertTrue(result['options']['with_report'])
        with self.assertRaisesRegex(ValueError, 'override explicit'):
            validate_proposal(proposal(options={'with_report': False}), args, {'with_report'})
        with self.assertRaisesRegex(ValueError, 'boolean'):
            validate_proposal(proposal(options={'with_report': 'true'}), args, set())
        with self.assertRaisesRegex(ValueError, 'data_analysis'):
            validate_proposal(proposal('bug_fix', options={'with_report': True}), args, set())


class IntakeDialogueTests(unittest.TestCase):
    def test_empty_requirements_retain_current_environment_without_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'paper.md').write_text('Reference')
            args = self.args(root, '--kind', 'reproduction', '--project', str(root), '--document', str(root / 'paper.md'),
                '--hypothesis', 'Claim', '--dataset', 'Fixed data', '--expected-outcome', 'Compare score',
                '--metric', 'score', '--command', 'python', 'run.py')
            settings = {'environment': 'current', 'requirements': [], 'install_project': False}
            resolved = self.converse(args, Client(proposal('reproduction', options=settings)), ['y'])
            resolved.prepare_only = True
            with patch('sys.stdin.isatty', return_value=False), patch('simple_ar.cli.start.print_line'):
                config = prepare_start(resolved)
            defaults = research_defaults(['research-session', '--config', str(config)])
            self.assertNotIn('environment', defaults['execution_details'])
            for invalid in ('requirements.txt', [''], [None]):
                with self.assertRaisesRegex(ValueError, 'list of names'):
                    validate_proposal(proposal('reproduction', options={'requirements': invalid}), args, set())
            with self.assertRaisesRegex(ValueError, 'nonempty'):
                validate_proposal(proposal('data_analysis', options={'value_column': []}), args, set())

    def test_confirmed_project_install_reuses_inspected_declaration_and_canonical_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'pyproject.toml').write_text('[project]\nname="fixture"\nversion="0.1"\n')
            (root / 'paper.md').write_text('Supplied reference')
            args = self.args(root, '--kind', 'reproduction', '--project', str(root), '--document', str(root / 'paper.md'),
                '--hypothesis', 'Claim', '--dataset', 'Fixed data', '--expected-outcome', 'Compare observed score',
                '--metric', 'score', '--command', 'python', 'run.py')
            settings = {'environment': 'venv', 'install_project': True}
            client = Client(proposal('reproduction', options=settings))
            resolved = self.converse(args, client, ['y'])
            resolved.prepare_only = True
            with patch('sys.stdin.isatty', return_value=False), patch('simple_ar.cli.start.print_line'):
                config = prepare_start(resolved)
            defaults = research_defaults(['research-session', '--config', str(config)])
            self.assertTrue(defaults['execution_details']['environment']['install_project'])
            self.assertEqual(defaults['process_invocations'], 4)
            facts = client.requests[0]['assets']
            with self.assertRaisesRegex(ValueError, 'override explicit'):
                validate_proposal(proposal('reproduction', options=settings), args, {'install_project'}, facts=facts)
            with self.assertRaisesRegex(ValueError, 'inspected root'):
                validate_proposal(proposal('reproduction', options=settings), args, set())
            with self.assertRaisesRegex(ValueError, 'boolean'):
                validate_proposal(proposal('reproduction', options={'install_project': 'true'}), args, set(), facts=facts)
            with self.assertRaisesRegex(ValueError, 'explicit venv'):
                validate_proposal(proposal('reproduction', options={'install_project': True}), args, set(), facts=facts)
            with self.assertRaisesRegex(ValueError, 'requires reproduction'):
                validate_proposal(proposal('writing', options=settings), args, set(), facts=facts)

    def test_confirmed_venv_choice_uses_inspected_requirements_and_existing_serializer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'requirements.txt').write_text('numpy==2.0.0\n')
            (root / 'paper.md').write_text('Supplied reference')
            args = self.args(root, '--kind', 'reproduction', '--project', str(root), '--document', str(root / 'paper.md'),
                '--hypothesis', 'Claim', '--dataset', 'Fixed data', '--expected-outcome', 'Compare observed score',
                '--metric', 'score', '--command', 'python', 'run.py')
            settings = {'environment': 'venv', 'requirements': ['requirements.txt']}
            client = Client(proposal('reproduction', options=settings, assumptions=['Install confirmed project requirements in a task venv; build code may execute.']))
            resolved = self.converse(args, client, ['y'])
            resolved.prepare_only = True
            with patch('sys.stdin.isatty', return_value=False), patch('simple_ar.cli.start.print_line'):
                config = prepare_start(resolved)
            defaults = research_defaults(['research-session', '--config', str(config)])
            self.assertEqual(defaults['execution_details']['environment']['requirements'], ['requirements.txt'])
            self.assertEqual(defaults['process_invocations'], 4)
            with self.assertRaisesRegex(ValueError, 'override explicit'):
                validate_proposal(proposal('reproduction', options=settings), args, {'environment'})
            facts = client.requests[0]['assets']
            with self.assertRaisesRegex(ValueError, 'inspected requirements'):
                validate_proposal(proposal('reproduction', options={'environment': 'venv', 'requirements': ['missing.txt']}), args, set(), facts=facts)
            with self.assertRaisesRegex(ValueError, 'requires reproduction'):
                validate_proposal(proposal('writing', options={'environment': 'venv'}), args, set())
    def test_reproduction_data_directory_flows_from_conversation_to_preparation_and_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / 'shared data'
            data.mkdir()
            (data / 'records.bin').write_bytes(b'not a table or trusted instructions')
            (root / 'README.md').write_text('Run python run.py --data DATA\n')
            paper = root / 'paper.md'
            paper.write_text('A supplied conclusion; no measured result yet.')
            args = self.args(root, '--kind', 'reproduction', '--project', str(root), '--document', str(paper))
            args.goal = f'Check this conclusion with the data at {data}'
            execution = {'hypothesis': 'Check source conclusion', 'dataset': 'Supplied binary directory; split unknown',
                         'expected_outcome': 'Compare declared metric', 'metrics': ['score'],
                         'argv': ['python', 'run.py', '--data', str(data)],
                         'basis': [{'path': 'README.md', 'quote': 'python run.py --data DATA'}]}
            client = Client(proposal('reproduction', assets=[{'role': 'data', 'path_quote': str(data)}]),
                            proposal('reproduction', execution_proposal=execution))
            with patch('subprocess.run', side_effect=AssertionError('intake never executes')):
                resolved = self.converse(args, client, ['y', 'y'])
                resolved.prepare_only = True
                with patch('sys.stdin.isatty', return_value=False), patch('simple_ar.cli.start.print_line'):
                    config = prepare_start(resolved)
            inspected = client.requests[1]['assets']['project_preparation']['data_paths']
            self.assertEqual(inspected, [{'path': str(data.resolve()), 'available': True, 'kind': 'directory'}])
            self.assertIsNone(resolved.data_file)
            self.assertEqual(resolved.data_path, [data.resolve()])
            defaults = research_defaults(['research-session', '--config', str(config)])
            self.assertEqual(defaults['data_path'], [str(data.resolve())])
            self.assertEqual(defaults['command_argv'], execution['argv'])
            self.assertEqual(read_json(config.parent / 'preparation.json')['data_paths'], inspected)

    def test_reproduction_explicit_data_is_not_reauthorized_or_interpreted_as_table(self):
        from simple_ar.cli.intake_dialogue import _asset_preview
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / 'large.npz'
            data.write_bytes(b'not parsed')
            args = self.args(root, '--kind', 'reproduction', '--cwd', str(root), '--data-path', str(data))
            parsed = validate_proposal(proposal('reproduction', assets=[{'role': 'data', 'path_quote': str(data)}]), args, set())
            self.assertEqual(parsed['assets'], [])
            with patch('simple_ar.result_analysis.table.read_table_source', side_effect=AssertionError('no table parsing')):
                facts = _asset_preview(args)
            self.assertEqual(facts['project_preparation']['data_paths'][0]['size_bytes'], 10)
            self.assertNotIn('data_preview', facts)

    def test_unselected_function_still_inspects_explicit_execution_directory(self):
        from simple_ar.cli.intake_dialogue import _asset_preview
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'README.md').write_text('Run python run.py with the declared data.')
            args = self.args(root, '--cwd', str(root))
            self.assertIsNone(args.kind)
            with patch('subprocess.run', side_effect=AssertionError('metadata only')):
                facts = _asset_preview(args)
            self.assertEqual(facts['project_preparation']['project'], str(root.resolve()))
            self.assertIn('Run python run.py', facts['project_preparation']['excerpts'][0]['text'])

    def test_old_setup_without_data_paths_recovers_in_shared_dialogue(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args = self.args(root, '--kind', 'survey')
            self.converse(args, Client(proposal('survey', questions=['Which scope?'])), ['stop'])
            path = self.draft(root)
            state = read_json(path)
            state['arguments'].pop('data_path')
            state['arguments'].pop('environment')
            state['arguments'].pop('requirements')
            state['arguments'].pop('install_project')
            from simple_ar.core.artifacts import write_json
            write_json(path, state)
            resumed = self.args(root, '--resume-setup', str(path.parent))
            resolved = self.converse(resumed, Client(proposal('survey')), ['Local only', 'y'])
            self.assertEqual(resolved.data_path, [])

    def test_requested_association_enters_same_data_config_and_respects_explicit_none(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / 'points.csv'
            data.write_text('x,y\n1,2\n2,3\n')
            args = self.args(root, '--data-file', str(data), locked={'data_file'})
            settings = {'value_column': ['y'], 'x_column': 'x', 'data_mode': 'values',
                'data_plot': 'scatter', 'observation_unit': 'one supplied pair', 'data_association': 'pearson'}
            resolved = self.converse(args, Client(proposal(options=settings)), ['y'])
            resolved.prepare_only = True
            with patch('sys.stdin.isatty', return_value=False), patch('simple_ar.cli.start.print_line'):
                config = prepare_start(resolved)
            self.assertEqual(research_defaults(['research-session', '--config', str(config)])['data_association'], 'pearson')
            args.data_association = 'none'
            with self.assertRaisesRegex(ValueError, 'override explicit'):
                validate_proposal(proposal(options=settings), args, {'data_association'})
            with self.assertRaisesRegex(ValueError, 'Data semantics'):
                validate_proposal(proposal('survey', options={'data_association': 'pearson'}), args, set())

    def test_mode_contract_explains_operations_not_the_natural_meaning_of_observations(self):
        from simple_ar.result_analysis.table import TABLE_MODE_DESCRIPTIONS
        with tempfile.TemporaryDirectory() as folder:
            client = Client(proposal('survey', options={'sources': 'search'}))
            self.converse(self.args(Path(folder)), client, ['stop'])
            mode = client.requests[0]['response_contract']['options']['data_mode']
            self.assertEqual(mode['operations'], TABLE_MODE_DESCRIPTIONS)
            self.assertEqual(set(mode['allowed_values']), set(mode['operations']))
            self.assertIn('Aggregate', mode['operations']['observations'])
            self.assertIn('individual observations', mode['operations']['values'])
            mode_default = build_parser().parse_args(['start']).data_mode
            self.assertEqual(mode_default, 'observations')  # Compatibility: no default/config migration.

    def test_complete_data_proposal_serializes_before_confirmation_or_asks_a_real_question(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / 'data.csv'
            data.write_text('value\n1\n2\n')
            args = self.args(root, '--data-file', str(data))
            incomplete = proposal(options={'value_column': ['value']}, assumptions=['Each row is one measurement'])
            with self.assertRaises(ValueError):
                validate_proposal(incomplete, args, set())
            # A genuinely unresolved semantic choice remains a conversation,
            # rather than being guessed or blocking the incomplete draft.
            pending = {**incomplete, 'questions': ['What does one row represent?'],
                       'options': {'value_column': ['value'], 'observation_unit': None, 'value_unit': None}}
            draft = validate_proposal(pending, args, set())
            self.assertEqual(draft['questions'], pending['questions'])
            self.assertEqual(draft['options'], {'value_column': ['value']})
            self.assertIsNone(pending['options']['observation_unit'])
            # Resolve pending semantics before validating execution combinations.
            coordinate = proposal(options={'data_plot': 'line', 'value_column': ['value'],
                'observation_unit': 'one coordinate', 'data_mode': 'values'}, questions=['Which column is x?'])
            client = Client(coordinate, proposal(options={'value_column': ['value'], 'observation_unit': 'one measurement'}))
            self.assertEqual(self.converse(args, client, ['Compare the values instead.', 'y']).data_plot, 'bar')
            self.assertFalse(read_json(self.draft(root)).get('rejected_proposals'))
            first = proposal(options={'value_column': ['value']})
            corrected = proposal(options={'value_column': ['value'], 'observation_unit': 'one measurement'})
            client = Client(first, corrected)
            resolved = self.converse(args, client, ['y'])
            self.assertEqual(resolved.observation_unit, 'one measurement')
            self.assertEqual(len(client.requests), 2)
            self.assertIn('validation_error', client.requests[-1])

    def test_attribution_is_optional_and_must_come_from_human_not_model(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args = self.args(root)
            declared = "Observatory catalogue https://example.org/catalogue release 2025"
            args.goal += "; Data source: " + declared
            resolved = self.converse(args, Client(proposal(options={"data_attribution": declared})), ["y"])
            self.assertEqual(resolved.data_attribution, declared)
        with tempfile.TemporaryDirectory() as folder:
            args = self.args(Path(folder))
            invented = proposal(options={"data_attribution": "Invented university dataset"})
            with self.assertRaisesRegex(ValueError, "human-provided"):
                self.converse(args, Client(invented, invented), [])

    def args(self, root, *options, locked=()):
        args = build_parser().parse_args(["start", "--chat", "--goal", "Describe my measurements",
                                         "--output-root", str(root / "runs"), *options])
        args._explicit_start_destinations = set(locked)
        return args

    def converse(self, args, client, answers):
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=answers), \
             patch("simple_ar.cli.intake_dialogue.print_line"):
            return discuss_start(args, client=client)

    def draft(self, root):
        return next((root / "runs").glob("*/setup.json"))

    def test_semantic_clarification_retains_human_words_and_uses_canonical_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "paired.csv"
            data.write_text("group,old,new\nA,3,2\nA,4,3\n")
            args = self.args(root, "--data-file", str(data), locked={"data_file"})
            settings = {"value_column": ["old", "new"], "group_column": "group", "value_unit": "seconds",
                        "observation_unit": "one matched run", "paired_baseline": "old"}
            client = Client(proposal(questions=["What does each row mean and are timings matched?"]),
                            proposal(options=settings))
            reply = "One row is one matched run; old and new are times in seconds."
            resolved = self.converse(args, client, [reply, "y"])
            self.assertEqual(len(client.requests), 2)
            self.assertEqual(client.requests[0]["assets"]["data_preview"]["columns"], ["group", "old", "new"])
            self.assertIn(reply, resolved.goal)
            self.assertNotIn("Describe matched observations without inferential claims", resolved.goal)
            with patch("sys.stdin.isatty", return_value=False), patch("simple_ar.cli.start.print_line"):
                resolved.prepare_only = True  # Testing canonical serialization, not dialogue's no-API promise.
                config = prepare_start(resolved)
            defaults = research_defaults(["research-session", "--config", str(config)])
            self.assertEqual(defaults["paired_baseline"], "old")
            self.assertEqual(defaults["value_column"], ["old", "new"])
            self.assertEqual(read_json(config.parent / "setup.json")["status"], "configured")
            self.assertNotIn("execution", tomllib.loads(config.read_text()))

    def test_waiting_proposal_resumes_without_model_replay_or_budget_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args = self.args(root)
            first = Client(proposal("survey", options={"sources": "search"}))
            self.assertIsNone(self.converse(args, first, ["stop"]))
            draft = self.draft(root)
            ledger = draft.parent / "setup_budget.json"
            old = ledger.read_bytes()
            resumed = self.args(root, "--resume-setup", str(draft.parent))
            no_calls = Client()
            result = self.converse(resumed, no_calls, ["y"])
            self.assertEqual(result.kind, "survey")
            self.assertEqual(len(no_calls.requests), 0)
            self.assertEqual(ledger.read_bytes(), old)
            self.assertEqual(len(read_json(draft)["proposals"]), 1)

    def test_accepting_saved_proposal_does_not_require_model_connection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.converse(self.args(root), Client(proposal('survey', options={'sources': 'search'})), ['stop'])
            draft = self.draft(root)
            args = self.args(root, '--resume-setup', str(draft.parent))
            with patch('simple_ar.cli.intake_dialogue.LLMClient.from_env', side_effect=AssertionError('offline acceptance')):
                result = self.converse(args, None, ['y'])
            self.assertEqual(result.sources, 'search')

    def test_outstanding_question_does_not_trigger_unchanged_model_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            client = Client(proposal(questions=["Are rows measurements or summaries?"]), proposal())
            result = self.converse(self.args(root), client, ["y", "", "They are measurements.", "y"])
            self.assertEqual(len(client.requests), 2)
            self.assertIn("They are measurements.", result.goal)

    def test_named_asset_access_is_confirmed_separately_from_original_human_text(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "supplied.csv"
            data.write_text("value\n1\n2\n")
            original = f"Describe observations from {data}"
            args = self.args(root)
            args.goal = original
            client = Client(proposal(assets=[{"role": "data", "path_quote": str(data)}]),
                            proposal(options={"value_column": ["value"], "observation_unit": "one observation"}))
            result = self.converse(args, client, ["y", "y"])
            self.assertEqual(result.data_file, data.resolve())
            self.assertEqual(result.goal, original)
            self.assertEqual(client.requests[0]["assets"]["data_file"], "")
            self.assertEqual(client.requests[1]["assets"]["data_preview"]["row_count"], 2)
            self.assertTrue(client.requests[1]["asset_decisions"][0]["approved"])

    def test_explicit_change_on_resume_is_retained_and_reconsidered(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.converse(self.args(root), Client(proposal("survey", options={"sources": "search"})), ["stop"])
            draft = self.draft(root)
            args = self.args(root, "--resume-setup", str(draft.parent), "--goal", "Only review the theory",
                             locked={"goal"})
            client = Client(proposal("survey", options={"sources": "search"}))
            result = self.converse(args, client, ["y"])
            self.assertIn("Only review the theory", result.goal)
            self.assertEqual(read_json(draft)["arguments"]["goal"], "Only review the theory")
            self.assertEqual(len(client.requests), 1)

    def test_invalid_second_proposal_and_transport_failure_preserve_the_draft(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            client = Client(proposal(options={"command": "delete everything"}),
                            proposal(options={"timeout_sec": 10000}))
            with self.assertRaisesRegex(ValueError, "no paths or commands"):
                self.converse(self.args(root), client, [])
            state = read_json(self.draft(root))
            self.assertEqual(len(state['rejected_proposals']), 2)
            self.assertIn("timeout_sec", state["rejected_proposals"][-1]["response"]["options"])
            self.assertEqual(state["user_messages"], ["Describe my measurements"])
            self.assertIn("validation_error", client.requests[1])
            self.assertIn("command", client.requests[1]['validation_error'])
            self.assertIn("Allowed option keys", client.requests[1]['validation_error'])
            self.assertIn("group_column", client.requests[1]['validation_error'])
            from simple_ar.result_analysis.table import TABLE_PLOT_DESCRIPTIONS
            self.assertEqual(client.requests[0]['response_contract']['options']['data_plot']['operations'], TABLE_PLOT_DESCRIPTIONS)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(RuntimeError, "network"):
                self.converse(self.args(root), Client(RuntimeError("network")), [])
            self.assertTrue(self.draft(root).is_file())

    def test_unquoted_asset_is_rejected_before_access(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            malicious = proposal(assets=[{"role": "data", "path_quote": "/etc/passwd"}])
            client = Client(malicious, malicious)
            with self.assertRaisesRegex(ValueError, "verbatim"):
                self.converse(self.args(root), client, [])
            self.assertEqual(read_json(self.draft(root))["arguments"]["data_file"], None)

    def test_locked_defaults_and_function_specific_options_cannot_be_overridden(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.args(Path(folder), "--data-missing", "reject")
            unchanged = validate_proposal(proposal(options={'data_missing': None}), args, {'data_missing'})
            self.assertEqual(unchanged['options'], {})
            with self.assertRaisesRegex(ValueError, "explicit data_missing"):
                validate_proposal(proposal(options={"data_missing": "omit"}), args, {"data_missing"})
            with self.assertRaisesRegex(ValueError, "belong"):
                validate_proposal(proposal("writing", assets=[{"role": "data", "path_quote": "x.csv"}]), args, set())
            value = proposal('reproduction', options={'template': 'reproduction'})
            resolved = validate_proposal(value, args, set())
            self.assertEqual(resolved['options'], {})
            self.assertEqual(value['options'], {'template': 'reproduction'})
            with self.assertRaisesRegex(ValueError, 'requires writing'):
                validate_proposal(proposal('reproduction', options={'template': 'experiment'}), args, set())

    def test_prepare_only_cannot_make_a_model_call_and_configured_resume_does_not_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(ValueError, "no model calls"):
                self.converse(self.args(root, "--prepare-only"), Client(), [])
            result = self.converse(self.args(root), Client(proposal("survey", options={"sources": "search"})), ["y"])
            with patch("sys.stdin.isatty", return_value=False), patch("simple_ar.cli.start.print_line"):
                result.prepare_only = True
                config = prepare_start(result)
            self.assertIsNone(self.converse(self.args(root, "--resume-setup", str(config.parent)), Client(), []))

    def test_large_text_cells_are_not_sent_whole_as_data_preview(self):
        from simple_ar.cli.intake_dialogue import _asset_preview
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / 'wide.csv'
            data.write_text('value,note\n1,' + 'x' * 50000 + '\n')
            facts = _asset_preview(self.args(root, '--data-file', str(data)))['data_preview']
            self.assertTrue(facts['cell_values_truncated'])
            self.assertEqual(len(facts['example_rows'][0]['note']), 200)
            self.assertEqual(facts['row_count'], 1)
            self.assertEqual(len(data.read_text()), 50014)

    def test_repeated_cli_asset_does_not_ask_for_access_twice(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / 'supplied.csv'
            data.write_text('value\n1\n')
            args = self.args(root, '--data-file', str(data))
            checked = validate_proposal(proposal(assets=[{'role': 'data', 'path_quote': str(data)}],
                questions=['What does one row represent?']), args, set())
            self.assertEqual(checked['assets'], [])
            with self.assertRaisesRegex(ValueError, 'one string.*list.*bar'):
                validate_proposal(proposal(options={'data_plot': ['bar', 'scatter']}), args, set())

    def test_individually_valid_settings_share_the_canonical_combination_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / 'supplied.csv'
            data.write_text('step,value\n0,1\n1,2\n')
            args = self.args(root, '--data-file', str(data))
            with self.assertRaisesRegex(ValueError, 'line/scatter require'):
                validate_proposal(proposal(options={'data_plot': 'line', 'value_column': ['value'],
                    'observation_unit': 'one coordinate', 'x_column': 'step', 'data_mode': 'observations'}), args, set())

    def test_code_repair_discovers_confirmed_validation_and_reuses_canonical_configuration(self):
        import shlex
        import tomllib
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            command = 'python -m unittest discover -s "tests with spaces"'
            (root / 'README.md').write_text('Testing: ' + command + '\n')
            (root / 'model.py').write_text('def compute():\n    return 1\n')
            args = self.args(root, '--kind', 'bug_fix', '--project', str(root), '--allow', 'model.py')
            execution = {'argv': shlex.split(command), 'basis': [{'path': 'README.md', 'quote': command}]}
            client = Client(proposal('bug_fix', execution_proposal=execution))
            with patch('subprocess.run', side_effect=AssertionError('setup must not execute')):
                resolved = self.converse(args, client, ['y'])
                resolved.prepare_only = True
                with patch('sys.stdin.isatty', return_value=False), patch('simple_ar.cli.start.print_line'):
                    config = prepare_start(resolved)
            task_config = tomllib.loads((config.parent / 'code_task.toml').read_text())
            self.assertEqual(shlex.split(task_config['benchmark']['command']), execution['argv'])
            self.assertEqual(task_config['edit_scope']['allowed_patterns'], ['model.py'])
            self.assertEqual(task_config['environment']['mode'], 'current')
            self.assertIsNone(resolved.run_argv)
            self.assertEqual(resolved.goal, args.goal)
            facts = client.requests[0]['assets']
            for changed in ({**execution, 'basis': []}, {**execution, 'timeout_sec': 900},
                            {**execution, 'basis': [{'path': 'README.md', 'quote': 'python train.py'}]}):
                with self.subTest(changed=changed), self.assertRaises(ValueError):
                    validate_proposal(proposal('bug_fix', execution_proposal=changed), args, set(), facts=facts)
            args.validate = 'python different.py'
            with self.assertRaisesRegex(ValueError, 'explicit validate'):
                validate_proposal(proposal('bug_fix', execution_proposal=execution), args, {'validate'}, facts=facts)

    def test_reproduction_proposal_from_inspected_project_serializes_one_confirmed_command(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'README.md').write_text('First check inputs: python check.py\nCheck fixture mean: python run.py --data values.csv\n')
            (root / 'run.py').write_text("# Writes rows.json under SIMPLE_AR_OUTPUT_DIR\nif __name__ == '__main__':\n    print('METRIC mean=3.0')\n")
            paper = root / 'paper.md'
            paper.write_text('# Source\nA mean calculation for a constructed example, not a published paper.\n')
            args = self.args(root, '--kind', 'reproduction', '--project', str(root), '--document', str(paper),
                '--timeout-sec', '17', locked={'kind', 'project', 'document', 'timeout_sec'})
            execution = {'hypothesis': 'Fixture mean is three', 'dataset': 'Supplied values.csv; constructed example',
                'expected_outcome': 'Compare mean with three, not a paper reproduction score', 'metrics': ['mean'],
                'argv': ['python', 'run.py', '--data', 'values.csv'],
                'check_argv': ['python', 'check.py'],
                'output_files': {'raw': 'rows.json'},
                'basis': [{'path': 'README.md', 'quote': 'python run.py --data values.csv'},
                          {'path': 'README.md', 'quote': 'python check.py'}]}
            client = Client(proposal('reproduction', execution_proposal=execution))
            with patch('subprocess.run', side_effect=AssertionError('setup must not execute')):
                resolved = self.converse(args, client, ['y'])
                resolved.prepare_only = True
                with patch('sys.stdin.isatty', return_value=False), patch('simple_ar.cli.start.print_line'):
                    config = prepare_start(resolved)
            values = research_defaults(['research-session', '--config', str(config)])
            self.assertEqual(values['command_argv'], execution['argv'])
            self.assertEqual(values['cwd'], str(root.resolve()))
            self.assertEqual(values['metric'], ['mean'])
            self.assertEqual(values['execution_details']['output_files'], {'raw': 'rows.json'})
            self.assertEqual(values['report_outline_strategy'], 'adaptive')
            self.assertEqual(values['timeout_sec'], 17)
            self.assertEqual(values['process_invocations'], 2)
            self.assertEqual(values['execution_details']['environment']['check_command'], ['python', 'check.py'])
            self.assertEqual(values['process_wall_seconds'], 34)
            self.assertTrue((config.parent / 'preparation.md').is_file())
            self.assertFalse((config.parent / 'code_task.toml').exists())
            self.assertTrue(any(row['role'] == 'entry_source' for row in client.requests[0]['assets']['project_preparation']['excerpts']))

    def test_reproduction_proposal_requires_actual_source_not_unread_or_model_authority(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args = self.args(root, '--project', str(root))
            execution = {'hypothesis': 'Claim', 'dataset': 'Data', 'expected_outcome': 'Criterion', 'metrics': ['score'],
                'argv': ['python', 'run.py'], 'basis': [{'path': 'README.md', 'quote': 'python run.py'}]}
            facts = {'project_preparation': {'excerpts': [{'path': 'README.md', 'text': 'python run.py'}]}}
            validate_proposal(proposal('reproduction', execution_proposal=execution), args, set(), facts=facts)
            for check in ([], 'python check.py', [42]):
                with self.subTest(check=check), self.assertRaisesRegex(ValueError, 'check_argv'):
                    validate_proposal(proposal('reproduction', execution_proposal={**execution,
                        'check_argv': check}), args, set(), facts=facts)
            args.check_argv = ['python', 'my_check.py']
            self.assertEqual(validate_proposal(proposal('reproduction', execution_proposal=execution),
                args, {'check_argv'}, facts=facts)['execution_proposal']['check_argv'], args.check_argv)
            with self.assertRaisesRegex(ValueError, 'explicit check_argv'):
                validate_proposal(proposal('reproduction', execution_proposal={**execution,
                    'check_argv': ['python', 'other.py']}), args, {'check_argv'}, facts=facts)
            args.run_argv = execution['argv']
            with self.assertRaisesRegex(ValueError, 'inspected source basis'):
                validate_proposal(proposal('reproduction', execution_proposal={**execution,
                    'check_argv': ['python', 'new.py'], 'basis': []}), args, set(), facts=facts)
            for attachments in ({'raw': '../outside.json'}, {'raw': '/outside.json'}, []):
                with self.subTest(attachments=attachments), self.assertRaises(ValueError):
                    validate_proposal(proposal('reproduction', execution_proposal={**execution,
                        'output_files': attachments}), args, set(), facts=facts)
            args.output_files = {'raw': 'rows.json'}
            with self.assertRaisesRegex(ValueError, 'explicit output_files'):
                validate_proposal(proposal('reproduction', execution_proposal={**execution,
                    'output_files': {'raw': 'other.json'}}), args, {'output_files'}, facts=facts)
            self.assertEqual(validate_proposal(proposal('reproduction', execution_proposal=execution),
                args, {'output_files'}, facts=facts)['execution_proposal']['output_files'], args.output_files)
            with self.assertRaisesRegex(ValueError, 'inspected source'):
                validate_proposal(proposal('reproduction', execution_proposal=execution), args, set(), facts={})
            with self.assertRaisesRegex(ValueError, 'timeout'):
                validate_proposal(proposal('reproduction', execution_proposal={**execution, 'timeout_sec': 99999}), args, set(), facts=facts)
            args.run_argv = ['python', 'different.py']
            with self.assertRaisesRegex(ValueError, 'explicit run_argv'):
                validate_proposal(proposal('reproduction', execution_proposal=execution), args, {'run_argv'}, facts=facts)
            with self.assertRaisesRegex(ValueError, 'only to reproduction'):
                validate_proposal(proposal('writing', execution_proposal=execution), args, set(), facts=facts)
            wrapped = {'project_preparation': {'excerpts': [{'path': 'README.md', 'text': 'python\n  run.py'}]}}
            resolved = validate_proposal(proposal('reproduction', execution_proposal=execution), args, set(), facts=wrapped)
            self.assertEqual(resolved['execution_proposal']['basis'][0]['quote'], 'python\n  run.py')
            with self.assertRaisesRegex(ValueError, 'does not match inspected source'):
                validate_proposal(proposal('reproduction', execution_proposal={**execution,
                    'basis': [{'path': 'README.md', 'quote': 'python not-run.py'}]}), args, set(), facts=wrapped)

    def test_reproduction_reads_indexed_configuration_before_asking_human_and_retains_reads_on_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'README.md').write_text('python run.py\n')
            (root / 'run.py').write_text("if __name__ == '__main__':\n    pass\n")
            (root / 'metrics.py').write_text("OUTPUT_NAME = 'coverage'\n")
            args = self.args(root, '--kind', 'reproduction', '--project', str(root))
            first = Client(proposal('reproduction', read_requests=['metrics.py']),
                           proposal('reproduction', questions=['Which published conclusion?']))
            self.assertIsNone(self.converse(args, first, ['stop']))
            self.assertEqual(len(first.requests), 2)
            self.assertTrue(any(row['path'] == 'metrics.py' and "'coverage'" in row['text']
                for row in first.requests[1]['assets']['project_preparation']['excerpts']))
            draft = self.draft(root)
            self.assertEqual(read_json(draft)['project_read_paths'], ['metrics.py'])
            resumed = self.args(root, '--resume-setup', str(draft.parent))
            client = Client(proposal('reproduction'))
            self.converse(resumed, client, ['Check the declared coverage, no new experiments.', 'y'])
            self.assertTrue(any(row['path'] == 'metrics.py' for row in client.requests[0]['assets']['project_preparation']['excerpts']))
            with self.assertRaisesRegex(ValueError, 'indexed'):
                validate_proposal(proposal('reproduction', read_requests=['../outside.py']), args, set(), facts={})

    def test_complete_file_lookup_is_idempotent_not_a_billable_json_correction_or_unbounded_loop(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'README.md').write_text('Documented instruction\n')
            args = self.args(root, '--kind', 'reproduction', '--project', str(root))
            client = Client(proposal('reproduction', read_requests=['README.md']), proposal('reproduction'))
            self.converse(args, client, ['y'])
            self.assertEqual(client.requests[1]['last_read_result']['already_supplied'], ['README.md'])
            self.assertEqual(read_json(self.draft(root)).get('project_read_paths'), [])
            self.assertFalse(read_json(self.draft(root)).get('rejected_proposals'))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'README.md').write_text('Documented instruction\n')
            args = self.args(root, '--kind', 'reproduction', '--project', str(root))
            client = Client(proposal('reproduction', read_requests=['README.md']), proposal('reproduction', read_requests=['README.md']))
            self.assertIsNone(self.converse(args, client, []))
            self.assertEqual(len(client.requests), 2)


if __name__ == "__main__":
    unittest.main()

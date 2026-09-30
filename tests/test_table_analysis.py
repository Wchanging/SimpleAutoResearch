import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from simple_ar.app.research_application import create_session, load_session, ResearchApplicationServices
from simple_ar.research.workflow_contracts import ResearchBrief
from simple_ar.result_analysis.table import TableSpec, parse_table, describe_table, rebuild
from simple_ar.cli.main import main
from simple_ar.cli.parser import build_parser
from simple_ar.cli.research_config import research_defaults
from simple_ar.cli.start import prepare_start


class TableAnalysisTests(unittest.TestCase):
    def test_descriptive_observations_have_explicit_counts_and_sample_std(self):
        rows = parse_table('group,value\nA,1\nA,3\nB,-2\n', '.csv')
        result = describe_table(rows, TableSpec(('value',), 'one run', 'group'))
        a, b = result['records']
        self.assertEqual(a['count'], 2)
        self.assertEqual(a['mean'], 2)
        self.assertAlmostEqual(a['sample_std'], 2 ** .5)
        self.assertIsNone(b['sample_std'])
        self.assertNotIn('execution_status', result)
        self.assertNotIn('significance', result)

    def test_missing_is_not_zero_and_omissions_are_per_column(self):
        rows = parse_table('group,x,y\nA,1,\nA,,4\n', '.csv')
        with self.assertRaisesRegex(ValueError, 'Missing'):
            describe_table(rows, TableSpec(('x', 'y'), 'one observation', 'group'))
        result = describe_table(rows, TableSpec(('x', 'y'), 'one observation', 'group', missing='omit'))
        self.assertEqual([r['mean'] for r in result['records']], [1, 4])
        self.assertEqual([r['missing'] for r in result['records']], [1, 1])
        with self.assertRaisesRegex(ValueError, 'No numeric'):
            describe_table([{'x': None}], TableSpec(('x',), 'one row', missing='omit'))

    def test_summary_values_are_not_averaged_or_given_inferred_errors(self):
        spec = TableSpec(('mean',), 'one supplied summary', 'method', mode='values')
        result = describe_table(parse_table('[{"method":"A","mean":3.1},{"method":"B","mean":2.2}]', '.json'), spec)
        self.assertEqual(result['records'][0]['value'], 3.1)
        self.assertNotIn('sample_std', result['records'][0])
        with self.assertRaisesRegex(ValueError, 'unique labels'):
            describe_table([{'method': 'A', 'mean': 1}, {'method': 'A', 'mean': 3}], spec)

    def test_bad_input_fails_explicitly_across_formats(self):
        for text, suffix in [('x,x\n1,2', '.csv'), ('x\ty\n1', '.tsv'), ('x\n', '.csv'),
                             ('[{"x":1,"x":2}]', '.json'), ('{"x":[1]}', '.json'),
                             ('[{"x":1},{"y":2}]', '.json'), ('[{"x":NaN}]', '.json'),
                             ('[{"x":[1]}]', '.json')]:
            with self.subTest(text=text), self.assertRaises((ValueError, json.JSONDecodeError)):
                parse_table(text, suffix)
        for value in ('NaN', 'inf', True, 'word'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                describe_table([{'x': value}], TableSpec(('x',), 'one row'))
        with self.assertRaisesRegex(ValueError, 'not found'):
            describe_table([{'id': 1}], TableSpec(('value',), 'one row'))

    def test_data_actions_cannot_be_inserted_into_a_research_plan(self):
        from unittest.mock import MagicMock
        from simple_ar.research.task_plan import TaskPlanRequest, build_task_plan, default_task_steps
        request = TaskPlanRequest(task_kind='survey', goal='Review sources', request_text='Review sources',
                                  config={'research_materials_only': True, 'research_local_documents': ['paper.md']},
                                  requested_outputs=('summary',))
        client = MagicMock(model='fake')
        client.ask_json.return_value = {'steps': [*default_task_steps(request), {'action': 'data_analysis'}]}
        from dataclasses import replace
        with self.assertRaisesRegex(ValueError, 'Data actions require'):
            build_task_plan(replace(request, use_llm=True, llm_client=client))

    def test_snapshot_survives_changed_deleted_input_and_moved_rebuild(self):
        import shutil
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'observations.csv'
            source.write_text('group,value\nA,1\nA,3\nB,-2\n', encoding='utf-8')
            config = {'research_task_kind': 'data_analysis', 'data_analysis': {
                'file': str(source), 'value_columns': ['value'], 'group_column': 'group', 'observation_unit': 'one run'}}
            brief = ResearchBrief(request_text='Describe supplied values', requested_outputs=('data_analysis',),
                                  asset_requests=({'locator': str(source), 'kind': 'file', 'role': 'dataset'},))
            app = create_session(brief, root=root / 'session', services=ResearchApplicationServices(config=config))
            view = app.advance(max_actions=2)
            self.assertEqual(view.next_action, 'data_analysis', view.status_reason)
            source.write_text('group,value\nA,999\n', encoding='utf-8')
            source.unlink()
            app = load_session(root / 'session')
            view = app.advance()
            self.assertEqual(view.status, 'completed', view.status_reason)
            self.assertEqual([step['action'] for step in view.work_plan['steps']], ['data_ingest', 'data_analysis'])
            self.assertNotIn('experiment', view.state_refs)
            ref = view.state_refs['data_analysis']
            result = app.controller.store.read_json(ref)
            self.assertEqual(result['records'][0]['mean'], 2)
            output = (root / 'session' / ref.path).parent
            self.assertEqual((output / 'input.csv').read_bytes(), b'group,value\nA,1\nA,3\nB,-2\n')
            for figure in result['figures']:
                ET.parse(output / figure['path'])
            moved = root / 'delivery'
            shutil.copytree(output, moved)
            shutil.rmtree(root / 'session')
            rebuild(moved / 'analysis.json')
            (moved / 'input.csv').write_text('group,value\nA,999\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'no longer matches'):
                rebuild(moved / 'analysis.json')

    def test_start_and_toml_use_the_same_settings_and_no_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'table with spaces.tsv'
            source.write_text('label\tscore\nA\t1\nB\t2\n', encoding='utf-8')
            argv = ['start', '--kind', 'data_analysis', '--goal', 'Describe results', '--data-file', str(source),
                    '--value-column', 'score', '--group-column', 'label', '--observation-unit', 'one summary',
                    '--data-mode', 'values', '--output-root', str(root / 'runs'), '--prepare-only']
            with patch('sys.stdin.isatty', return_value=False), contextlib.redirect_stdout(io.StringIO()):
                config = prepare_start(build_parser().parse_args(argv))
            defaults = research_defaults(['research-session', '--config', str(config)])
            self.assertEqual(defaults['data_file'], str(source))
            self.assertEqual(defaults['data_mode'], 'values')
            with patch('simple_ar.cli.main._optional_research_llm_client', side_effect=AssertionError('No API/model expected')), contextlib.redirect_stdout(io.StringIO()):
                self.assertIsNone(main(['research-session', '--config', str(config)]))
                sessions = list((config.parent / 'sessions').iterdir())
                source.unlink()
                self.assertIsNone(main(['research-session', '--session-root', str(sessions[0]), '--model', 'env']))

    def test_guided_analysis_rejects_unrelated_options_before_saving(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.csv'
            source.write_text('x\n1\n', encoding='utf-8')
            base = ['start', '--kind', 'data_analysis', '--goal', 'Describe', '--data-file', str(source),
                    '--value-column', 'x', '--observation-unit', 'one row', '--output-root', str(root / 'runs'), '--prepare-only']
            for extra in (['--document', str(source)], ['--sources', 'search'], ['--validate', 'python test.py'],
                          ['--command', 'python', 'train.py'], ['--template', 'experiment'], ['--data-max-figures', '0']):
                with self.subTest(extra=extra), patch('sys.stdin.isatty', return_value=False), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                    prepare_start(build_parser().parse_args([*base, *extra]))
            self.assertFalse((root / 'runs').exists())

    def test_byte_snapshot_keeps_bom_crlf_and_quoted_multiline_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = b'\xef\xbb\xbfgroup,value\r\n"line one\r\nline two",2\r\n'
            source = root / 'source.csv'
            source.write_bytes(raw)
            brief = ResearchBrief(request_text='Describe', requested_outputs=('data_analysis',))
            config = {'research_task_kind': 'data_analysis', 'data_analysis': {
                'file': str(source), 'value_columns': ['value'], 'group_column': 'group', 'observation_unit': 'one observation'}}
            app = create_session(brief, root=root / 'session', services=ResearchApplicationServices(config=config))
            view = app.advance(max_actions=10)
            self.assertEqual(view.status, 'completed', view.status_reason)
            result = app.controller.store.read_json(view.state_refs['data_analysis'])
            output = (root / 'session' / view.state_refs['data_analysis'].path).parent
            self.assertEqual((output / 'input.csv').read_bytes(), raw)
            self.assertEqual(result['records'][0]['group'], 'line one\r\nline two')

    def test_figures_keep_all_categories_negative_values_and_xml_escaping(self):
        from simple_ar.report.figures import render_table_figures
        rows = [{'group': f'A<& very long category {i}' * 3, 'value': i - 10} for i in range(25)]
        for width in ('column', 'wide'):
            with self.subTest(width=width), tempfile.TemporaryDirectory() as directory:
                result = describe_table(rows, TableSpec(('value',), 'one run', 'group', width=width))
                figures = render_table_figures(result, Path(directory))
                self.assertEqual(len(figures), 3)
                titles = []
                for figure in figures:
                    tree = ET.parse(Path(directory) / figure['path'])
                    titles.extend(node.text for node in tree.findall('.//{http://www.w3.org/2000/svg}g/{http://www.w3.org/2000/svg}g/{http://www.w3.org/2000/svg}title'))
                self.assertEqual(len(titles), 25)
        with tempfile.TemporaryDirectory() as directory:
            result = describe_table(rows, TableSpec(('value',), 'one run', 'group', max_figures=1))
            with self.assertRaisesRegex(ValueError, 'physical max_figures'):
                render_table_figures(result, Path(directory))
            self.assertFalse((Path(directory) / 'figures').exists())


if __name__ == '__main__':
    unittest.main()

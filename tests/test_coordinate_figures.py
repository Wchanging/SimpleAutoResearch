"""Coordinate figures retain supplied data, share the existing task and package."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from simple_ar.cli.main import main
from simple_ar.cli.parser import build_parser
from simple_ar.cli.research_config import research_defaults
from simple_ar.cli.start import prepare_start
from simple_ar.result_analysis.figures import render_table_figures
from simple_ar.result_analysis.table import (
    TableSpec, describe_table, load_analysis_package, rebuild, table_markdown, table_values_markdown,
)


class CoordinateFigureTests(unittest.TestCase):
    def spec(self, **overrides):
        return TableSpec.from_config(dict(value_columns=['loss'], observation_unit='one supplied checkpoint',
                                         mode='values', plot='line', x_column='step', **overrides))

    def test_no_coordinate_semantics_or_aggregation_is_guessed(self):
        settings = dict(value_columns=['loss'], observation_unit='one row', mode='values', plot='line', x_column='step')
        for override in ({'mode': 'observations'}, {'group_column': 'method'}, {'x_column': ''},
                         {'x_column': 'loss'}, {'plot': 'unknown'}, {'max_points': 0}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                TableSpec.from_config({**settings, **override})
        for value in ('inf', 'word', True, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                describe_table([{'step': value, 'loss': 2}], self.spec(missing='omit'))
        with self.assertRaisesRegex(ValueError, 'unique numeric x'):
            describe_table([{'step': 1, 'loss': 2}, {'step': 1, 'loss': 3}], self.spec())
        scatter = TableSpec.from_config({**settings, 'plot': 'scatter'})
        self.assertEqual(len(describe_table([{'step': 1, 'loss': 2}, {'step': 1, 'loss': 3}], scatter)['records']), 2)

    def test_line_sorted_for_display_only_and_missing_y_breaks_connections(self):
        rows = [{'step': 4, 'loss': 0}, {'step': 1, 'loss': 3}, {'step': 3, 'loss': None}, {'step': 2, 'loss': 2}]
        result = describe_table(rows, self.spec(missing='omit', x_unit='iteration <&>', value_unit='arbitrary units'))
        self.assertEqual([row['x'] for row in result['records']], [4, 1, 3, 2])
        self.assertIsNone(result['records'][2]['value'])
        self.assertEqual(result['records'][2]['missing'], 1)
        self.assertIn('missing', table_markdown({**result, 'source_name': 'data.csv', 'figures': []}))
        self.assertIn('| x | value |', table_values_markdown(result))
        with tempfile.TemporaryDirectory() as directory:
            figures = render_table_figures(result, Path(directory))
            svg = ET.parse(Path(directory) / figures[0]['path'])
            ns = {'s': 'http://www.w3.org/2000/svg'}
            self.assertEqual(len(svg.findall('.//s:circle', ns)), 3)
            curve = svg.findall('.//s:path', ns)[1].attrib['d']
            self.assertEqual(curve.count('M '), 2)
            self.assertEqual(curve.count('L '), 1)
            self.assertIn('1 missing y', figures[0]['caption'])
            self.assertIn('iteration <&>', ''.join(svg.getroot().itertext()))
        self.assertEqual([row['x'] for row in result['records']], [4, 1, 3, 2])

    def test_each_metric_has_own_axis_and_limits_fail_without_sampling(self):
        spec = TableSpec(('a', 'b'), 'one coordinate', mode='values', plot='scatter', x_column='x')
        result = describe_table([{'x': -1e308, 'a': 1e308, 'b': 0},
                                 {'x': 1e308, 'a': -1e308, 'b': 0}], spec)
        with tempfile.TemporaryDirectory() as directory:
            figures = render_table_figures(result, Path(directory))
            self.assertEqual(len(figures), 2)
            for figure in figures:
                path = Path(directory) / figure['path']
                svg = ET.parse(path)
                self.assertEqual(len(svg.findall('.//{http://www.w3.org/2000/svg}circle')), 2)
                self.assertNotRegex(path.read_text().lower(), r'(?<![a-z])(nan|[+-]?inf)(?![a-z])')
        with self.assertRaisesRegex(ValueError, 'max_points'):
            describe_table([{'step': 1, 'loss': 2}, {'step': 2, 'loss': 3}], self.spec(max_points=1))
        with tempfile.TemporaryDirectory() as directory:
            limited = {**result, 'spec': {**result['spec'], 'max_figures': 1}}
            with self.assertRaisesRegex(ValueError, 'max_figures'):
                render_table_figures(limited, Path(directory))
            self.assertFalse((Path(directory) / 'figures').exists())

    def test_cli_toml_freeze_recovery_move_and_recheck_share_one_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / 'curve.tsv'
            data.write_text('step\tloss\n2\t1\n1\t3\n3\t\n', encoding='utf-8')
            args = ['start', '--kind', 'data_analysis', '--goal', 'Plot supplied checkpoints',
                    '--data-file', str(data), '--value-column', 'loss', '--observation-unit', 'one checkpoint',
                    '--data-mode', 'values', '--data-plot', 'line', '--x-column', 'step', '--x-unit', 'iteration',
                    '--data-missing', 'omit', '--data-max-points', '30', '--output-root', str(root / 'run'), '--prepare-only']
            with patch('sys.stdin.isatty', return_value=False), contextlib.redirect_stdout(io.StringIO()):
                config = prepare_start(build_parser().parse_args(args))
            defaults = research_defaults(['research-session', '--config', str(config)])
            self.assertEqual((defaults['data_plot'], defaults['x_column'], defaults['data_max_points']), ('line', 'step', 30))
            with patch('simple_ar.cli.main._optional_research_llm_client', side_effect=AssertionError('No API required')), contextlib.redirect_stdout(io.StringIO()):
                main(['research-session', '--config', str(config)])
                package = next(config.parent.glob('sessions/*/attempts/data_analysis-*/analysis.json'))
                session = package.parents[2]
                original = package.read_bytes()
                data.unlink()
                main(['research-session', '--session-root', str(session), '--model', 'env'])
                self.assertEqual(package.read_bytes(), original)
            moved = root / 'delivery'
            shutil.copytree(package.parent, moved)
            imported, _, _ = load_analysis_package(moved / 'analysis.json')
            self.assertEqual(imported['records'][0]['x'], 2)
            rebuild(moved / 'analysis.json')
            payload = json.loads((moved / 'analysis.json').read_text())
            payload['records'][0]['x'] = 999
            (moved / 'analysis.json').write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, 'no longer matches'):
                rebuild(moved / 'analysis.json')

    def test_coordinate_flags_do_not_silently_apply_to_other_tasks(self):
        base = ['start', '--kind', 'survey', '--goal', 'Survey', '--prepare-only']
        for flag in (['--data-plot', 'scatter'], ['--x-column', 'time'], ['--x-unit', 'seconds'], ['--data-max-points', '5']):
            with self.subTest(flag=flag), patch('sys.stdin.isatty', return_value=False), contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, 'Data options require'):
                prepare_start(build_parser().parse_args([*base, *flag]))


if __name__ == '__main__':
    unittest.main()

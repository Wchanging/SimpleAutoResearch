"""Tabular plots: distributions, matrices and format-independent delivery semantics."""
import json
import math
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from simple_ar.result_analysis.figures import render_table_figures
from simple_ar.result_analysis.table import TableSpec, describe_table, table_values_markdown
from tests import captured_data_figures


class BoxFigureTests(unittest.TestCase):
    def test_paired_box_uses_shared_observed_range_not_a_bar_zero_anchor(self):
        for sign in (1, -1):
            rows = [{'baseline': sign * a, 'candidate': sign * b}
                    for a, b in ((100, 102), (103, 105), (101, 99))]
            result = describe_table(rows, TableSpec(('baseline', 'candidate'), 'matched repetition',
                paired_baseline='baseline', plot='box'))
            with tempfile.TemporaryDirectory() as folder, captured_data_figures() as drawn:
                render_table_figures(result, Path(folder))
                a, b = [fig.axes[0] for fig in drawn[:2]]
                self.assertEqual(a.get_xlim(), b.get_xlim())
                low, high = a.get_xlim()
                self.assertFalse(low <= 0 <= high)
                self.assertLess(low, min(sign * 99, sign * 105))
                self.assertGreater(high, max(sign * 99, sign * 105))

    def test_paired_box_shows_marginal_distributions_and_separately_matched_differences(self):
        rows = [{'baseline': a, 'candidate': b} for a, b in ((0, 1), (100, 99), (2, 3), (3, 4))]
        result = describe_table(rows, TableSpec(('baseline', 'candidate'), 'matched repetition',
            paired_baseline='baseline', plot='box'))
        frozen = json.dumps(result, sort_keys=True)
        with tempfile.TemporaryDirectory() as folder:
            figures = render_table_figures(result, Path(folder))
            self.assertEqual(len(figures), 3)
            for figure in figures[:2]:
                source = (Path(folder) / figure['path']).read_text()
                ET.fromstring(source)
                self.assertEqual(figure['encoding']['plot'], 'box')
                self.assertEqual(figure['encoding']['statistics_shown'], ['min', 'q1', 'median', 'q3', 'max'])
                self.assertNotIn('Paired mean;', source)
            paired = figures[-1]
            source = (Path(folder) / paired['path']).read_text()
            self.assertIn('Paired mean; whiskers', source)
            self.assertNotIn('Box = Q1', source)
            self.assertEqual(paired['encoding']['plot'], 'bar')
            self.assertEqual(paired['encoding']['statistics_shown'], ['mean_difference', 'standard_error'])
            self.assertEqual(paired['encoding']['role'], 'matched_difference')
        self.assertEqual(json.dumps(result, sort_keys=True), frozen)

    def test_paired_box_keeps_missing_marginals_distinct_and_single_pair_has_no_se(self):
        rows = [{'group': 'A', 'baseline': 4, 'candidate': 5},
                {'group': 'A', 'baseline': 10, 'candidate': None},
                {'group': 'B', 'baseline': None, 'candidate': 7},
                {'group': 'B', 'baseline': 5, 'candidate': None}]
        result = describe_table(rows, TableSpec(('baseline', 'candidate'), 'matched repetition', 'group',
            missing='omit', paired_baseline='baseline', plot='box'))
        with tempfile.TemporaryDirectory() as folder:
            figures = render_table_figures(result, Path(folder))
            self.assertEqual(figures[-1]['encoding']['statistics_shown'], ['mean_difference'])
            self.assertEqual(figures[-1]['encoding']['groups'], ['A'])
            self.assertEqual(figures[0]['encoding']['groups'], ['A', 'B'])
            source = (Path(folder) / figures[-1]['path']).read_text()
            self.assertIn('n=1; missing=1', source)
        self.assertEqual(result['paired_comparisons'][0]['count'], 1)
        self.assertEqual(result['paired_comparisons'][1]['count'], 0)

    def test_summary_values_coordinates_pairing_and_association_are_not_reinterpreted(self):
        settings = dict(value_columns=['value'], observation_unit='one observed specimen', plot='box')
        for extra in ({'mode': 'values'}, {'x_column': 'time'}, {'x_unit': 'seconds'},
                      {'series_layout': 'shared'}, {'association': 'pearson'}, {'paired_baseline': 'value'}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                TableSpec.from_config({**settings, **extra})
        spec = TableSpec.from_config(settings)
        self.assertEqual(spec.plot, 'box')
        self.assertEqual(spec.mode, 'observations')

    def test_all_observations_drive_quartiles_and_original_result_is_unchanged(self):
        rows = [{'g': 'A<&', 'value': value} for value in range(1, 14)]
        rows.extend([{'g': 'A<&', 'value': None}, {'g': 'B', 'value': -10}])
        result = describe_table(rows, TableSpec(('value',), 'one specimen', 'g', plot='box', missing='omit'))
        before = json.dumps(result, sort_keys=True)
        with tempfile.TemporaryDirectory() as folder, captured_data_figures() as drawn:
            figures = render_table_figures(result, Path(folder))
            source = (Path(folder) / figures[0]['path']).read_text()
            ET.fromstring(source)
            self.assertEqual(result['observation_summaries'][0]['value'], {'min': 1, 'q1': 4, 'median': 7, 'q3': 10, 'max': 13})
            coordinates = {round(float(v), 12) for line in drawn[0].axes[0].lines for v in line.get_xdata()}
            self.assertTrue({1, 4, 7, 10, 13}.issubset(coordinates))
            self.assertIn('n=13; missing=1', source)
            self.assertIn('A<&', ''.join(ET.fromstring(source).itertext()))
            self.assertIn('min–max', source)
            self.assertIn('not confidence intervals', figures[0]['caption'])
            self.assertIn('no observations are classified as outliers', figures[0]['caption'])
        self.assertEqual(json.dumps(result, sort_keys=True), before)
        self.assertIn('Box plots show Q1–Q3', table_values_markdown(result))
        self.assertNotIn('Figures still display their labeled means', table_values_markdown(result))

    def test_constant_and_large_values_fit_the_svg_axis(self):
        for value in (0, -10, 10, 1e308):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as folder, captured_data_figures() as drawn:
                result = describe_table([{'value': value}], TableSpec(('value',), 'one observation', plot='box'))
                figure = render_table_figures(result, Path(folder))[0]
                svg = ET.parse(Path(folder) / figure['path']).getroot()
                self.assertNotIn('nan', ET.tostring(svg).decode().lower())
                ax = drawn[0].axes[0]
                low, high = ax.get_xlim()
                for line in ax.lines:
                    self.assertTrue(all(math.isfinite(v) and low <= v <= high for v in line.get_xdata()))

    def test_all_categories_paginate_on_one_scale_without_silent_drop(self):
        rows = [{'g': f'group-{index}', 'value': index} for index in range(15)]
        result = describe_table(rows, TableSpec(('value',), 'one observation', 'g', plot='box'))
        with tempfile.TemporaryDirectory() as folder, captured_data_figures() as drawn:
            figures = render_table_figures(result, Path(folder))
            self.assertEqual(len(figures), 2)
            self.assertEqual([g for f in figures for g in f['encoding']['groups']], [f'group-{i}' for i in range(15)])
            self.assertEqual(drawn[0].axes[0].get_xlim(), drawn[1].axes[0].get_xlim())
            result['spec']['max_figures'] = 1
            with self.assertRaisesRegex(ValueError, 'no data was silently dropped'):
                render_table_figures(result, Path(folder))

class HeatmapFigureTests(unittest.TestCase):
    def spec(self, **extra):
        return TableSpec.from_config(dict(value_columns=['a', 'b'], observation_unit='one labeled sample',
            group_column='sample', mode='values', plot='heatmap', value_unit='cm', **extra))

    def test_matrix_requires_explicit_semantics_and_keeps_other_modes(self):
        settings = self.spec().to_config()
        for extra in ({'mode': 'observations'}, {'group_column': ''}, {'x_column': 'time'},
                      {'series_layout': 'shared'}, {'association': 'pearson'}, {'paired_baseline': 'a'}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                TableSpec.from_config({**settings, **extra})
        with self.assertRaisesRegex(ValueError, 'unique labels'):
            describe_table([{'sample': 'same', 'a': 1, 'b': 2}] * 2, self.spec())
        with self.assertRaisesRegex(ValueError, 'No numeric values'):
            describe_table([{'sample': 'one', 'a': None, 'b': 2}],
                TableSpec.from_config({**settings, 'plot': 'bar', 'missing': 'omit'}))

    def test_missing_cells_are_retained_not_zero_and_exported_to_writing(self):
        result = describe_table([{'sample': '<one>', 'a': 0, 'b': None},
                                 {'sample': 'two', 'a': 2, 'b': None}], self.spec(missing='omit'))
        self.assertEqual([row['value'] for row in result['records']], [0, None, 2, None])
        self.assertEqual([row['count'] for row in result['records']], [1, 0, 1, 0])
        self.assertIn('not defined', table_values_markdown(result))
        with tempfile.TemporaryDirectory() as folder, captured_data_figures() as drawn:
            figure = render_table_figures(result, Path(folder))[0]
            tree = ET.parse(Path(folder) / figure['path'])
            mesh = drawn[0].axes[0].collections[0]
            values = mesh.get_array()
            self.assertEqual(values.size, 4)
            self.assertEqual(values.mask.tolist(), [[False, True], [False, True]])
            self.assertEqual(values[0, 0], 0)
            colors = mesh.get_facecolors()
            self.assertEqual(colors[1].tolist(), colors[3].tolist())
            self.assertNotEqual(colors[0].tolist(), colors[1].tolist())
            self.assertIn('<one>', ''.join(tree.getroot().itertext()))
            self.assertFalse(tree.findall('.//{http://www.w3.org/2000/svg}image'))

    def test_pagination_preserves_every_cell_and_one_global_scale(self):
        cols = [f'c{i}' for i in range(8)]
        spec = TableSpec.from_config({**self.spec().to_config(), 'value_columns': cols})
        rows = [{'sample': f'row-{r}', **{col: r * 8 + c for c, col in enumerate(cols)}} for r in range(14)]
        result = describe_table(rows, spec)
        with tempfile.TemporaryDirectory() as folder, captured_data_figures() as drawn:
            figures = render_table_figures(result, Path(folder))
            self.assertEqual(len(figures), 4)
            count = 0
            for figure in figures:
                ET.parse(Path(folder) / figure['path'])
                mesh = drawn[figures.index(figure)].axes[0].collections[0]
                values = mesh.get_array()
                count += values.size
                self.assertEqual(mesh.get_clim(), (0, 111))
                expected = [[rows[int(group.split('-')[1])][col] for col in figure['encoding']['value_columns']]
                            for group in figure['encoding']['groups']]
                self.assertEqual(values.tolist(), expected)
                self.assertIn('[0, 111]', figure['caption'])
            self.assertEqual(count, 112)
            for limit in ({'max_figures': 3}, {'max_points': 71}):
                with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, 'physical'):
                    render_table_figures({**result, 'spec': {**result['spec'], **limit}}, Path(folder) / 'limited')

    def test_extreme_constant_and_all_missing_values_do_not_invent_a_range(self):
        for values in ((-1e308, 1e308), (3, 3), (0, 0)):
            result = describe_table([{'sample': 'one', 'a': values[0], 'b': values[1]}], self.spec())
            with tempfile.TemporaryDirectory() as folder:
                figure = render_table_figures(result, Path(folder))[0]
                text = (Path(folder) / figure['path']).read_text()
                ET.fromstring(text)
                self.assertNotIn('nan', text.lower())
                self.assertNotIn('inf', text.lower())
        missing = describe_table([{'sample': 'one', 'a': None, 'b': None}], self.spec(missing='omit'))
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ValueError, 'all cells are missing'):
            render_table_figures(missing, Path(folder))

class FigureDeliveryTests(unittest.TestCase):
    def render(self, rows, spec):
        with tempfile.TemporaryDirectory() as folder:
            result = describe_table(rows, spec)
            figures = render_table_figures(result, Path(folder))
            for figure in figures:
                self.assertEqual(set(figure['exports']), {'pdf', 'png'})
                self.assertNotIn('<!DOCTYPE', (Path(folder) / figure['path']).read_text())
                for name in figure['exports'].values():
                    self.assertGreater((Path(folder) / name).stat().st_size, 100)
            return result, figures

    def test_exports_captions_and_encoding_agree_with_actual_data_semantics(self):
        rows = [{'label': str(i), 'x': i, 'value': i + 1, 'candidate': i + 2} for i in range(3)]
        cases = [
            (TableSpec(('value',), 'specimen', value_unit='grams'), ['mean'], False),
            (TableSpec(('value',), 'specimen', value_unit='grams', plot='box'), ['min', 'q1', 'median', 'q3', 'max'], False),
            (TableSpec(('value', 'candidate'), 'matched specimen', value_unit='grams', paired_baseline='value'), ['mean'], False),
        ]
        cases.extend((TableSpec(('value',), 'record', 'label', mode='values', plot=plot, value_unit='grams',
                               x_column='x' if plot in {'line', 'scatter'} else ''), ['value'], True)
                     for plot in ('bar', 'line', 'scatter', 'heatmap'))
        for spec, statistics, row_level in cases:
            with self.subTest(plot=spec.plot, paired=spec.paired_baseline):
                result, figures = self.render(rows, spec)
                self.assertEqual(result['row_count'], len(rows))
                shown_columns = set()
                for figure in figures:
                    self.assertIn('grams', figure['caption'])
                    self.assertNotIn('analysis.json', figure['caption'])
                    encoding = figure['encoding']
                    if encoding.get('role') == 'matched_difference':
                        continue  # Matched mean/SE are covered with missing/single-pair cases above.
                    self.assertEqual(encoding['statistics_shown'], statistics)
                    self.assertEqual(encoding['row_level_values_shown'], row_level)
                    shown_columns.update(encoding['value_columns'])
                    if spec.plot in {'line', 'scatter'}:
                        self.assertEqual(encoding['x_column'], 'x')
                self.assertEqual(shown_columns, set(spec.value_columns))


if __name__ == '__main__':
    unittest.main()

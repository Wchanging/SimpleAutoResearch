from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.report.ports import DeterministicFigureRenderer, FigureRenderer
from simple_ar.report.document_plan import resolve_document_plan
from simple_ar.report.schema import ReportFigureConfig, ReportRuntimeConfig, ReportSectionPlan


class ReportPortTests(unittest.TestCase):
    def planned_figure(self, *, config=None, contract=None):
        return resolve_document_plan(sections=[ReportSectionPlan(section_id='comparison',
            heading='Comparison', goal='Compare the observed methods.', evidence_handles=['a', 'b'])],
            config=config or ReportRuntimeConfig(), contract=contract, visual_candidates=[{
                'kind': 'figure', 'view': 'system-construction-flow', 'section_id': 'comparison',
                'title': 'Named components', 'purpose': 'Orient the reader without implying causality.',
                'evidence_handles': ['a', 'b']}])

    def test_automatic_plan_keeps_supported_intent_and_renders_in_short_report(self):
        plan = self.planned_figure()
        self.assertEqual(len(plan.visual_intents), 1)
        with tempfile.TemporaryDirectory() as directory:
            result = DeterministicFigureRenderer().render(report_markdown=
                '# Report\n\n## Comparison\n\n- Component A\n- Component B\n- Component C\n',
                report_dir=Path(directory), config=ReportFigureConfig(),
                template_name='material_report', document_plan=plan)
            self.assertEqual(len(result.figures), 1)
            self.assertIn('figures/system-construction-flow.svg', result.report_markdown)

    def test_planned_diagram_can_coexist_with_supplied_figure_without_duplication(self):
        config = ReportRuntimeConfig(figures=ReportFigureConfig(max_figures=1))
        plan = self.planned_figure(config=config)
        body = '# Report\n\n## Comparison\n\n![User data](figures/user-data.svg)\n\n- Component A\n- Component B\n- Component C\n'
        with tempfile.TemporaryDirectory() as directory:
            renderer = DeterministicFigureRenderer()
            result = renderer.render(report_markdown=body, report_dir=Path(directory),
                config=config.figures, template_name='material_report', document_plan=plan)
            self.assertEqual(len(result.figures), 1)
            self.assertIn('![User data](figures/user-data.svg)', result.report_markdown)
            replay = renderer.render(report_markdown=result.report_markdown, report_dir=Path(directory),
                config=config.figures, template_name='material_report', document_plan=plan)
            self.assertEqual(replay.report_markdown, result.report_markdown)
            self.assertEqual(replay.figures, [])

    def test_explicit_disabled_or_zero_contract_keeps_no_generated_intent(self):
        for config, contract in ((ReportRuntimeConfig(figures=ReportFigureConfig(mode='off', max_figures=2)), None),
                (ReportRuntimeConfig(figures=ReportFigureConfig(enabled=False)), None),
                (ReportRuntimeConfig(), {'visual_budget': {'figures': 0}})):
            with self.subTest(config=config.figures, contract=contract):
                self.assertEqual(self.planned_figure(config=config, contract=contract).visual_intents, [])

    def test_planner_caps_only_supported_proposals_and_renderer_obeys_frozen_plan(self):
        sections = [ReportSectionPlan(section_id='comparison', heading='Comparison',
            goal='Compare methods.', evidence_handles=['a', 'b'])]
        proposals = [{'kind': 'figure', 'view': view, 'section_id': 'comparison',
            'title': view, 'purpose': 'Overview.', 'evidence_handles': ['a', 'b']}
            for view in ('system-construction-flow', 'taxonomy-map', 'unsupported', 'taxonomy-map')]
        for config, contract, expected in (
            (ReportRuntimeConfig(), None, 2),
            (ReportRuntimeConfig(), {'visual_budget': {'figures': 1}}, 1),
            (ReportRuntimeConfig(figures=ReportFigureConfig(max_figures=1)), None, 1),
        ):
            with self.subTest(config=config.figures, contract=contract):
                plan = resolve_document_plan(sections=sections, config=config,
                    contract=contract, visual_candidates=proposals)
                self.assertEqual(len(plan.visual_intents), expected)
                body = '# Report\n\n## Comparison\n\n- Method A\n- Method B\n- Method C\n'
                with tempfile.TemporaryDirectory() as directory:
                    renderer = DeterministicFigureRenderer()
                    result = renderer.render(report_markdown=body, report_dir=Path(directory),
                        config=config.figures, template_name='material_report', document_plan=plan)
                    self.assertEqual(len(result.figures), expected)
                    empty = renderer.render(report_markdown=body, report_dir=Path(directory),
                        config=config.figures, template_name='survey_long',
                        document_plan=plan.model_copy(update={'visual_intents': []}))
                    self.assertEqual(empty.figures, [])
                    legacy = renderer.render(report_markdown=body + '\n![Existing](figures/user.svg)\n',
                        report_dir=Path(directory), config=config.figures, template_name='survey_long')
                    self.assertEqual(legacy.figures, [])

    def test_default_figure_renderer_is_replaceable_and_writes_artifact(self) -> None:
        renderer = DeterministicFigureRenderer()
        self.assertIsInstance(renderer, FigureRenderer)

        with tempfile.TemporaryDirectory() as tmp:
            result = renderer.render(
                report_markdown=(
                    "# Report\n\n## Taxonomy\n\n"
                    "- Method family A\n- Method family B\n- Method family C\n"
                ),
                report_dir=Path(tmp),
                config=ReportFigureConfig(enabled=True, max_figures=1),
                template_name="survey_long",
            )

            self.assertEqual(renderer.name, "deterministic_svg")
            self.assertEqual(len(result.figures), 1)
            self.assertTrue((Path(tmp) / "figures" / "taxonomy-map.svg").is_file())
            svg = (Path(tmp) / "figures" / "taxonomy-map.svg").read_text()
            self.assertNotIn("marker-end", svg)
            self.assertIn("Method family A", svg)
            self.assertIn("![Conceptual taxonomy map]", result.report_markdown)

    def test_insufficient_labels_do_not_manufacture_a_diagram(self) -> None:
        report = "# Report\n\n## Taxonomy\n\nWe do not yet have an established taxonomy.\n"
        with tempfile.TemporaryDirectory() as tmp:
            result = DeterministicFigureRenderer().render(report_markdown=report,
                report_dir=Path(tmp), config=ReportFigureConfig(max_figures=1), template_name="survey_long")
            self.assertEqual(result.report_markdown, report)
            self.assertEqual(result.figures, [])
            self.assertFalse((Path(tmp) / "figures").exists())

    def test_table_column_headers_are_not_scientific_entities(self) -> None:
        from simple_ar.report.figures import _table_first_column_items
        self.assertEqual(_table_first_column_items(
            "| Protein | Function |\n| --- | --- |\n| A | x |\n| B | y |\n\n"
            "| Compound | Property |\n| :--- | ---: |\n| C | z |\n"), ["A", "B", "C"])

    def test_disabled_renderer_preserves_report(self) -> None:
        renderer = DeterministicFigureRenderer()
        report = "# Report\n"
        result = renderer.render(
            report_markdown=report,
            report_dir=Path(tempfile.mkdtemp()),
            config=ReportFigureConfig(enabled=False),
        )

        self.assertEqual(result.report_markdown, report)
        self.assertFalse(result.figures)


if __name__ == "__main__":
    unittest.main()

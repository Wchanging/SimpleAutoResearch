from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.report.ports import DeterministicFigureRenderer, FigureRenderer
from simple_ar.report.schema import ReportFigureConfig


class ReportPortTests(unittest.TestCase):
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

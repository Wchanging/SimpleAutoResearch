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
                    "method family benchmark evaluation challenge\n"
                ),
                report_dir=Path(tmp),
                config=ReportFigureConfig(enabled=True, max_figures=1),
                template_name="survey_long",
            )

            self.assertEqual(renderer.name, "deterministic_svg")
            self.assertEqual(len(result.figures), 1)
            self.assertTrue((Path(tmp) / "figures" / "taxonomy-map.svg").is_file())
            self.assertIn("![Conceptual taxonomy map]", result.report_markdown)

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

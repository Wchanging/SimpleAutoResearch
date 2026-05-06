from __future__ import annotations

import unittest
from pathlib import Path

from simple_ar.literature.models import Paper
from simple_ar.literature.verify import validate_citations
from simple_ar.pipeline import Context
from simple_ar.stage_handlers import (
    _append_references_section,
    _build_report,
    _strip_references_section,
)


class ReportSafetyTests(unittest.TestCase):
    def test_model_written_references_are_replaced_with_known_papers(self) -> None:
        draft = (
            "# A Draft\n\n"
            "## Abstract\n\n"
            "Known citation [@paper-1].\n\n"
            "## References\n\n"
            "- fabricated reference [@fake-paper]\n"
        )
        paper = Paper(
            id="paper-1",
            title="Known Paper",
            authors=["Ada Lovelace"],
            abstract="Known metadata.",
            url="https://example.com/paper-1",
        )

        report = _append_references_section(_strip_references_section(draft), [paper])

        self.assertIn("[@paper-1]", report)
        self.assertNotIn("@fake-paper", report)
        validate_citations(report, {"paper-1"})

    def test_fallback_report_states_fixture_limitations(self) -> None:
        ctx = Context(
            Path("run"),
            "Agent Simulation",
            config={"max_papers": 1, "experiment_timeout_sec": 30},
        )
        paper = Paper(
            id="fixture-001",
            title="Placeholder Paper for Pipeline Testing",
            authors=["SimpleAutoResearch"],
            abstract="Fixture metadata.",
            url="https://example.com/fixture-001",
            source="fixture",
        )
        report = _append_references_section(
            _build_report(
                ctx,
                goal="# Goal\nStudy agent simulation.",
                problem="# Problem\nHow can agent simulation be studied?",
                search_meta={
                    "query": "Agent Simulation",
                    "source": "fixture",
                    "status": "fallback",
                    "returned": 1,
                },
                synthesis="# Synthesis\nStage outputs can become later inputs.",
                hypothesis="# Hypothesis\nA file-first pipeline is inspectable.",
                plan={
                    "template": "toy_text_classification",
                    "dataset": "built_in_toy_spam",
                    "baseline": "keyword_rules",
                    "method": "bag_of_words_logistic_regression",
                    "metrics": ["accuracy"],
                },
                results={
                    "returncode": 0,
                    "timed_out": False,
                    "metrics": {"accuracy": 0.75},
                    "command": ["python", "experiment.py"],
                },
                papers=[paper],
            ),
            [paper],
        )

        self.assertIn("## Literature Search", report)
        self.assertIn("fixture metadata", report)
        self.assertIn("| `accuracy` | 0.75 |", report)
        self.assertNotIn("Raw result metadata", report)
        validate_citations(report, {"fixture-001"})


if __name__ == "__main__":
    unittest.main()

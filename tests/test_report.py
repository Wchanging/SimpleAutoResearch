from __future__ import annotations

import unittest
from pathlib import Path

from simple_ar.literature.models import Paper
from simple_ar.literature.verify import validate_citations
from simple_ar.pipeline import Context
from simple_ar.report_quality import build_report_quality
from simple_ar.stage_handlers import (
    _append_references_section,
    _build_report,
    _body_citation_ids,
    _cited_papers,
    _report_bound_errors,
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
        self.assertIn("[@fixture-001]", report.split("## References", maxsplit=1)[0])
        self.assertNotIn("Raw result metadata", report)
        validate_citations(report, {"fixture-001"})

    def test_body_citation_ids_ignore_reference_list_only_citations(self) -> None:
        markdown = (
            "# Draft\n\n"
            "## Abstract\n\n"
            "No body citation.\n\n"
            "## References\n\n"
            "- [@paper-1] Known Paper.\n"
        )

        self.assertEqual(_body_citation_ids(markdown, {"paper-1"}), set())

    def test_cited_papers_prunes_uncited_references(self) -> None:
        papers = [
            Paper(
                id="paper-1",
                title="Cited Paper",
                authors=[],
                abstract="",
                url="https://example.com/1",
            ),
            Paper(
                id="paper-2",
                title="Uncited Paper",
                authors=[],
                abstract="",
                url="https://example.com/2",
            ),
        ]

        cited = _cited_papers("# Draft\n\nKnown prior work [@paper-1].", papers)
        report = _append_references_section("# Draft\n\nKnown prior work [@paper-1].", cited)

        self.assertEqual([paper.id for paper in cited], ["paper-1"])
        self.assertIn("[@paper-1]", report)
        self.assertNotIn("[@paper-2]", report)

    def test_report_quality_records_metrics_and_runtime_limits(self) -> None:
        paper = Paper(
            id="paper-1",
            title="Known Paper",
            authors=[],
            abstract="",
            url="https://example.com/1",
            source="fixture",
        )
        report_body = (
            "# Draft\n\n"
            "The run uses fixture metadata [@paper-1].\n\n"
            "## Results\n\n"
            "| Metric | Value |\n"
            "|---|---:|\n"
            "| `accuracy` | 0.75 |\n\n"
            "## Limitations\n\n"
            "The literature stage used fixture metadata and the experiment timed out."
        )
        report = _append_references_section(report_body, [paper])

        quality = build_report_quality(
            report,
            report_body,
            search_meta={"source": "fixture", "status": "fallback"},
            results={"metrics": {"accuracy": 0.75}, "returncode": None, "timed_out": True},
            papers=[paper],
            cited_papers=[paper],
        )

        self.assertEqual(quality["status"], "passed")
        self.assertEqual(quality["summary"]["metric_count"], 1)
        self.assertEqual(quality["body_citation_ids"], ["paper-1"])

    def test_report_bounds_reject_fixture_overclaims(self) -> None:
        report = (
            "# Draft\n\n"
            "## Related Work\n\n"
            "Prior research has established groundwork for practical solutions "
            "in spam filtering [@fixture-001].\n\n"
            "## Limitations\n\n"
            "The run used fixture metadata."
        )

        errors = _report_bound_errors(
            report,
            search_meta={"source": "fixture", "status": "offline_fixture"},
            plan={"template": "llm_code_task_toy_spam"},
        )

        self.assertTrue(any("overclaims" in error for error in errors))

    def test_report_bounds_accept_conservative_fixture_disclosure(self) -> None:
        report = (
            "# Draft\n\n"
            "## Related Work\n\n"
            "The only available citation is fixture metadata used to keep the "
            "pipeline deterministic [@fixture-001].\n\n"
            "## Results\n\n"
            "The benchmark passed after one source-file patch."
        )

        self.assertEqual(
            _report_bound_errors(
                report,
                search_meta={"source": "fixture", "status": "offline_fixture"},
                plan={"template": "llm_code_task_toy_spam"},
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()

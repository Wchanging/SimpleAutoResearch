from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

from benchmark.survey_bench.adapter import (
    WITHOUT_REVIEW_GUIDED_REVISION,
    _method_for_topic,
    _restore_canonical_report_package,
    _snapshot_canonical_report_package,
    report_variant_dir,
    resolve_variant,
    score_results_root,
    topic_results_root,
)


class SurveyBenchAblationTests(unittest.TestCase):
    def test_review_revision_ablation_uses_an_isolated_thorough_namespace(self) -> None:
        variant = resolve_variant(
            argparse.Namespace(
                variant="",
                without_review_guided_revision=True,
            )
        )

        self.assertEqual(variant, WITHOUT_REVIEW_GUIDED_REVISION)
        self.assertIn("ablations", str(topic_results_root(thorough=True, variant=variant)))
        self.assertIn("topics-thorough", str(topic_results_root(thorough=True, variant=variant)))
        self.assertIn("score-thorough", str(score_results_root(thorough=True, variant=variant)))

    def test_review_revision_ablation_rejects_a_conflicting_namespace(self) -> None:
        with self.assertRaises(SystemExit):
            resolve_variant(
                argparse.Namespace(
                    variant="another-ablation",
                    without_review_guided_revision=True,
                )
            )

    def test_variant_is_reflected_in_exported_topic_method(self) -> None:
        from benchmark.survey_bench.adapter import TopicRef

        method = _method_for_topic(
            "SimpleAutoResearch",
            TopicRef(topic_id="topic01", key="topic01-example", name="Example"),
            thorough=True,
            variant=WITHOUT_REVIEW_GUIDED_REVISION,
        )

        self.assertEqual(method, "topic01-example-thorough-w-o-review-guided-revision")

    def test_reused_run_keeps_ablation_report_in_a_sibling_variant_package(self) -> None:
        path = report_variant_dir(
            Path("runs/topic01/example"),
            WITHOUT_REVIEW_GUIDED_REVISION,
        )

        self.assertEqual(
            path.as_posix(),
            "runs/topic01/example/08-report/variants/w-o-review-guided-revision",
        )

    def test_variant_snapshot_restores_canonical_report_without_touching_variant(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stage_dir = Path(temp_dir) / "08-report"
            stage_dir.mkdir(parents=True)
            (stage_dir / "report.md").write_text("original report\n", encoding="utf-8")
            (stage_dir / "sections").mkdir()
            (stage_dir / "sections" / "methods.md").write_text("original section\n", encoding="utf-8")
            snapshot_dir = Path(temp_dir) / "snapshot"
            _snapshot_canonical_report_package(stage_dir, snapshot_dir)

            (stage_dir / "report.md").write_text("mutated report\n", encoding="utf-8")
            (stage_dir / "sections" / "methods.md").write_text("mutated section\n", encoding="utf-8")
            variant = stage_dir / "variants" / "candidate"
            variant.mkdir(parents=True)
            (variant / "report.md").write_text("variant report\n", encoding="utf-8")

            _restore_canonical_report_package(stage_dir, snapshot_dir)

            self.assertEqual((stage_dir / "report.md").read_text(encoding="utf-8"), "original report\n")
            self.assertEqual(
                (stage_dir / "sections" / "methods.md").read_text(encoding="utf-8"),
                "original section\n",
            )
            self.assertEqual((variant / "report.md").read_text(encoding="utf-8"), "variant report\n")


if __name__ == "__main__":
    unittest.main()

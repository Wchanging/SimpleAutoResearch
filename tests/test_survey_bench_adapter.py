from __future__ import annotations

import argparse
import unittest
from pathlib import Path

from benchmark.survey_bench.adapter import (
    WITHOUT_REVIEW_GUIDED_REVISION,
    _method_for_topic,
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



if __name__ == "__main__":
    unittest.main()

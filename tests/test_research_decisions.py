from __future__ import annotations

import unittest

from simple_ar.core import TransitionPolicy
from simple_ar.research.decisions import transition_request_from_analysis
from simple_ar.result_analysis.schema import AnalysisResult


class ResearchDecisionAdapterTests(unittest.TestCase):
    """Verify the one research-specific adapter still used by the session."""

    def test_passed_analysis_can_continue_to_report(self) -> None:
        request = transition_request_from_analysis(
            AnalysisResult(
                readme_markdown="done",
                status="passed",
                status_reasons=["Execution passed."],
            ),
            target="report",
        )

        decision = TransitionPolicy().decide(request)

        self.assertEqual(request.result_status, "completed")
        self.assertEqual(decision.action, "accept")
        self.assertEqual(decision.target, "report")

    def test_metric_failure_requests_experiment_revision(self) -> None:
        request = transition_request_from_analysis(
            {
                "analysis": {
                    "readme_markdown": "below target",
                    "status": "metric_below_target",
                    "status_reasons": ["The candidate regressed."],
                }
            },
            target="experiment",
        )

        decision = TransitionPolicy().decide(request)

        self.assertEqual(request.result_status, "failed")
        self.assertEqual(request.failure_kind, "metric")
        self.assertTrue(request.experiment_needed)
        self.assertEqual(decision.action, "revise")
        self.assertEqual(decision.target, "experiment")

    def test_incomplete_analysis_is_evidence_revision(self) -> None:
        request = transition_request_from_analysis(
            AnalysisResult(
                readme_markdown="incomplete",
                status="incomplete",
                status_reasons=["Required metrics are missing."],
            ),
            target="report",
        )

        decision = TransitionPolicy().decide(request)

        self.assertEqual(request.result_status, "partial")
        self.assertFalse(request.evidence_sufficient)
        self.assertEqual(decision.action, "revise")
        self.assertEqual(decision.target, "report")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from simple_ar.core import TransitionPolicy
from simple_ar.report.schema import CitationAudit, ReportAudit, ReviewerFinding
from simple_ar.research.decisions import (
    transition_request_from_analysis,
    transition_request_from_synthesis,
    transition_request_from_report_audit,
)
from simple_ar.research.synthesis import SynthesisResult
from simple_ar.result_analysis.schema import AnalysisResult


class ResearchDecisionAdapterTests(unittest.TestCase):
    def test_ready_synthesis_can_be_given_to_the_existing_policy(self) -> None:
        request = transition_request_from_synthesis(
            SynthesisResult(
                status="ready",
                gap_summary="ready",
                ideas=(),
                novelty_checks=(),
            ),
            target="experiment",
        )

        decision = TransitionPolicy().decide(request)

        self.assertEqual(request.result_status, "completed")
        self.assertTrue(request.evidence_sufficient)
        self.assertEqual(decision.action, "accept")
        self.assertEqual(decision.target, "experiment")

    def test_reviewable_synthesis_preserves_evidence_diagnostic(self) -> None:
        synthesis = SynthesisResult(
            status="needs_review",
            gap_summary="incomplete evidence",
            ideas=(),
            novelty_checks=(),
            diagnostics=("Missing evidence facets: benchmark.",),
        )

        request = transition_request_from_synthesis(
            synthesis.to_handoff_dict(),
            target="search",
            expected_delta="cover the missing benchmark facet",
        )
        decision = TransitionPolicy().decide(request)

        self.assertEqual(request.result_status, "partial")
        self.assertFalse(request.evidence_sufficient)
        self.assertEqual(request.signals, ("Missing evidence facets: benchmark.",))
        self.assertEqual(decision.action, "revise")
        self.assertEqual(decision.target, "search")

    def test_passed_analysis_can_be_given_to_the_existing_policy(self) -> None:
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

    def test_metric_below_target_requests_bounded_revision(self) -> None:
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

    def test_report_audit_warning_preserves_audit_signal(self) -> None:
        request = transition_request_from_report_audit(
            ReportAudit(
                status="warning",
                citation_audit=CitationAudit(
                    status="warning",
                    warnings=["Some papers are not cited."],
                ),
                reviewer_findings=[
                    ReviewerFinding(
                        finding_id="finding-1",
                        type="coverage",
                        message="A section needs evidence.",
                    )
                ],
            ),
            target="report",
        )

        decision = TransitionPolicy().decide(request)

        self.assertEqual(request.result_status, "partial")
        self.assertFalse(request.report_auditable)
        self.assertEqual(
            request.signals,
            ("Some papers are not cited.", "A section needs evidence."),
        )
        self.assertEqual(decision.action, "revise")
        self.assertEqual(decision.target, "report")

    def test_passed_report_audit_is_accepted_without_inventing_signals(self) -> None:
        request = transition_request_from_report_audit(
            {"status": "passed"},
            target="report_audit",
        )

        decision = TransitionPolicy().decide(request)

        self.assertEqual(request.result_status, "completed")
        self.assertTrue(request.report_auditable)
        self.assertEqual(request.signals, ())
        self.assertEqual(decision.action, "accept")


if __name__ == "__main__":
    unittest.main()

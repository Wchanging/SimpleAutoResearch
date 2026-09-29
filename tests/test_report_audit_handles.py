from __future__ import annotations

import unittest

from simple_ar.report.audit import build_report_audit
from simple_ar.report.schema import ReportContext, ReportMemory


class ReaderFacingHandleTests(unittest.TestCase):
    def test_internal_artifact_handle_is_flagged_without_erasing_provenance(self) -> None:
        body = "# Review\n\nThe synthesis card count (artifact:synthesis) is small.\n"
        audit = build_report_audit(
            report=body, report_body=body,
            context=ReportContext(topic="Review", report_mode="survey"),
            memory=ReportMemory(),
        )

        self.assertEqual(audit.status, "warning")
        self.assertTrue(any(
            finding.finding_id == "internal-artifact-handle"
            for finding in audit.reviewer_findings
        ))

    def test_normal_source_citation_is_not_flagged(self) -> None:
        body = "# Review\n\nThe supplied source is only a proposal [@P1].\n"
        audit = build_report_audit(
            report=body, report_body=body,
            context=ReportContext(topic="Review", report_mode="survey"),
            memory=ReportMemory(),
        )
        self.assertFalse(any(
            finding.finding_id == "internal-artifact-handle"
            for finding in audit.reviewer_findings
        ))


if __name__ == "__main__":
    unittest.main()

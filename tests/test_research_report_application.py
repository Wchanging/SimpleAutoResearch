from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from simple_ar.app.research_brief import ResearchBriefSessionRequest
from simple_ar.app.research_report import (
    ResearchReportSessionRequest,
    run_research_report_session,
)
from simple_ar.app.research_session import ResearchSessionRequest, run_research_session
from simple_ar.report.schema import ReportContext, ReportSectionDraft


class ResearchReportApplicationTests(unittest.TestCase):
    def test_session_can_append_report_and_audit_without_copying_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "reliable_agents.md"
            paper.write_text(
                "# Method\n\nValidation improves reliable agent behavior.\n\n"
                "# Results\n\nThe fixture reports accuracy: 0.75.\n",
                encoding="utf-8",
            )
            session = run_research_session(
                ResearchSessionRequest(
                    brief=ResearchBriefSessionRequest(
                        topic="reliable agents",
                        session_root=root / "session",
                        local_documents=(paper,),
                        max_results=2,
                        max_chunks=20,
                    ),
                    command=(sys.executable, "-c", "print('accuracy: 0.75')"),
                    cwd=root,
                    timeout_sec=5,
                    result_schema={"primary_metric": "accuracy"},
                )
            )

            result = run_research_report_session(
                ResearchReportSessionRequest(
                    session_root=session.session_root,
                    title="Reliable agents",
                    sections=(
                        ReportSectionDraft(
                            section_id="findings",
                            heading="Findings",
                            draft_markdown="Validation improves accuracy [@paper-1].",
                        ),
                    ),
                    source_refs=(session.analysis_ref,),
                    context=ReportContext(
                        topic="reliable agents",
                        report_mode="research_only",
                        papers=[{"id": "paper-1"}],
                    ),
                )
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.audit.status, "passed")
            self.assertTrue(
                (root / "session" / "attempts" / "report-001" / "report.md").is_file()
            )
            self.assertTrue(
                (
                    root
                    / "session"
                    / "attempts"
                    / "report-audit-001"
                    / "report_audit.json"
                ).is_file()
            )
            self.assertEqual(len(result.attempts), 8)
            self.assertEqual(result.decisions[-2].action, "accept")
            self.assertEqual(result.decisions[-1].action, "accept")
            report_text = (
                root / "session" / "attempts" / "report-001" / "report.md"
            ).read_text(encoding="utf-8")
            self.assertIn("Validation improves accuracy", report_text)
            manifest = json.loads(
                (root / "session" / "session_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "completed")


if __name__ == "__main__":
    unittest.main()

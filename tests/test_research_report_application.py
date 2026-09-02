from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simple_ar.app.research_brief import ResearchBriefSessionRequest
from simple_ar.app.research_report import (
    ResearchReportSessionError,
    ResearchReportSessionRequest,
    build_research_session_report_inputs,
    run_research_session_report_agent,
    run_research_report_agent_session,
    run_research_report_session,
)
from simple_ar.app.research_session import ResearchSessionRequest, run_research_session
from simple_ar.report.schema import (
    AgentReportResult,
    ReportContext,
    ReportMemory,
    ReportRuntimeConfig,
    ReportSectionDraft,
    ReportTemplateBundle,
)


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
            self.assertEqual(len(result.attempts), 10)
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

    def test_agent_writer_handoff_reuses_report_audit_and_records_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "reliable_agents.md"
            paper.write_text(
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
            paper_id = session.search.papers[0].id
            agent_result = AgentReportResult(
                report_body=f"Reliable agents [@{paper_id}].",
                memory=ReportMemory(objective="Validate reliable agents."),
                sections=[
                    ReportSectionDraft(
                        section_id="findings",
                        heading="Findings",
                        draft_markdown=(
                            f"Reliable agents improve accuracy to 0.75 [@{paper_id}]."
                        ),
                        used_sources=[paper_id],
                    )
                ],
                used_agent=True,
            )
            with patch(
                "simple_ar.app.research_report.run_report_agent",
                return_value=agent_result,
            ) as writer:
                result = run_research_session_report_agent(
                    session,
                    template=ReportTemplateBundle(
                        name="experiment",
                        mode="experiment",
                        template_path="template.md",
                        criteria_path="criteria.md",
                        template_markdown="# Findings",
                        criteria_markdown="Use evidence.",
                    ),
                    config=ReportRuntimeConfig(reviewer="disabled"),
                    client=object(),
                )

            self.assertEqual(result.status, "completed")
            self.assertIsNotNone(result.writer_ref)
            assert result.writer_ref is not None
            writer.assert_called_once()
            self.assertTrue(writer.call_args.kwargs["memory"].section_plan)
            writer_path = root / "session" / result.writer_ref.path
            self.assertTrue(writer_path.is_file())
            writer_payload = json.loads(writer_path.read_text(encoding="utf-8"))
            self.assertEqual(writer_payload["schema_version"], "report_agent_result.v1")
            self.assertNotIn("report_body", writer_payload)
            report_attempt = json.loads(
                (
                    root
                    / "session"
                    / "attempts"
                    / "report-001"
                    / "attempt_manifest.json"
                ).read_text(encoding="utf-8")
            )
            self.assertIn(
                result.writer_ref.path,
                [item["path"] for item in report_attempt["inputs"]],
            )

            trace_before = writer_path.read_text(encoding="utf-8")
            with patch(
                "simple_ar.app.research_report.run_report_agent",
                side_effect=AssertionError("duplicate continuation invoked Writer"),
            ) as duplicate_writer:
                with self.assertRaisesRegex(
                    ResearchReportSessionError,
                    "continuation already exists",
                ):
                    run_research_session_report_agent(
                        session,
                        template=ReportTemplateBundle(
                            name="experiment",
                            mode="experiment",
                            template_path="template.md",
                            criteria_path="criteria.md",
                            template_markdown="# Findings",
                            criteria_markdown="Use evidence.",
                        ),
                        config=ReportRuntimeConfig(reviewer="disabled"),
                        client=object(),
                    )
            duplicate_writer.assert_not_called()
            self.assertEqual(trace_before, writer_path.read_text(encoding="utf-8"))

    def test_agent_report_requires_a_passed_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "reliable_agents.md"
            paper.write_text(
                "# Results\n\nThe fixture reports accuracy: 0.25.\n",
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
                    command=(
                        sys.executable,
                        "-c",
                        "print('accuracy: 0.25'); raise SystemExit(2)",
                    ),
                    cwd=root,
                    timeout_sec=5,
                    result_schema={"primary_metric": "accuracy"},
                )
            )

            self.assertFalse(session.report_ready)
            with patch("simple_ar.app.research_report.run_report_agent") as writer:
                with self.assertRaisesRegex(
                    ResearchReportSessionError,
                    "not ready for formal report generation",
                ):
                    run_research_session_report_agent(
                        session,
                        template=ReportTemplateBundle(
                            name="experiment",
                            mode="experiment",
                            template_path="template.md",
                            criteria_path="criteria.md",
                            template_markdown="# Findings",
                            criteria_markdown="Use evidence.",
                        ),
                        config=ReportRuntimeConfig(reviewer="disabled"),
                        client=object(),
                    )
                writer.assert_not_called()

    def test_research_session_report_inputs_keep_execution_and_analysis_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "reliable_agents.md"
            paper.write_text(
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
                    result_schema={
                        "primary_metric": "accuracy",
                        "metric_directions": {"accuracy": "higher"},
                    },
                )
            )

            context, memory = build_research_session_report_inputs(session)

            self.assertEqual(context.topic, "reliable agents")
            self.assertEqual(context.results["status"], "passed")
            self.assertTrue(any(item.name == "accuracy" for item in context.metric_sources))
            self.assertTrue(any(item.kind == "analysis" for item in memory.source_handles))

    def test_agent_writer_failure_does_not_assemble_a_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_root = root / "session"
            session_root.mkdir()
            (session_root / "session_manifest.json").write_text(
                '{"schema_version":"session_manifest.v1","session_id":"session",'
                '"topic":"reliable agents","status":"running","budget":{},'
                '"decisions":[]}',
                encoding="utf-8",
            )
            with patch(
                "simple_ar.app.research_report.run_report_agent",
                return_value=None,
            ) as writer:
                with self.assertRaisesRegex(
                    RuntimeError,
                    "did not return a validated result",
                ):
                    run_research_report_agent_session(
                        session_root=session_root,
                        context=ReportContext(
                            topic="reliable agents",
                            report_mode="research_only",
                        ),
                        memory=ReportMemory(),
                        template=ReportTemplateBundle(
                            name="experiment",
                            mode="research_only",
                            template_path="template.md",
                            criteria_path="criteria.md",
                            template_markdown="# Findings",
                            criteria_markdown="Use evidence.",
                        ),
                        config=ReportRuntimeConfig(reviewer="disabled"),
                        client=object(),
                    )

                self.assertTrue(writer.call_args.kwargs["memory"].section_plan)

            self.assertFalse((session_root / "attempts" / "report-001").exists())


if __name__ == "__main__":
    unittest.main()

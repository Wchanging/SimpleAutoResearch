from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from simple_ar.core import CapabilityRegistry, SessionController
from simple_ar.report.audit import (
    ReportAuditCapabilityRequest,
    run_report_audit_capability,
)
from simple_ar.report.capability import ReportAssemblyRequest, run_report_capability
from simple_ar.report.figures import ReportFigureRecord, ReportFigureResult
from simple_ar.report.schema import (
    ReportContext,
    ReportDocumentPlan,
    ReportFigureConfig,
    ReportMemory,
    ReportRuntimeConfig,
    ReportSectionDraft,
    ReportSectionPlan,
    ReportVisualIntent,
)


class ReportAuditCapabilityTests(unittest.TestCase):
    def test_report_capability_assembles_explicit_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            registry.register("report", run_report_capability)
            controller = SessionController.create(
                tmp,
                session_id="report-session",
                topic="offline report",
                profile="paper_audit",
                registry=registry,
            )

            result, decision = controller.execute(
                "report",
                attempt_id="attempt-001",
                request=ReportAssemblyRequest(
                    title="Offline report",
                    sections=(
                        ReportSectionDraft(
                            section_id="introduction",
                            heading="Introduction",
                            draft_markdown="Evidence-backed claim [@paper-1].",
                        ),
                    ),
                ),
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")
            self.assertIn("Evidence-backed claim", controller.store.read_text(
                "attempts/attempt-001/report.md"
            ))

    def test_report_capability_persists_verified_reference_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            registry.register("report", run_report_capability)
            controller = SessionController.create(
                tmp,
                session_id="report-reference-session",
                topic="offline report",
                profile="paper_audit",
                registry=registry,
            )

            result, _ = controller.execute(
                "report",
                attempt_id="attempt-001",
                request=ReportAssemblyRequest(
                    title="Offline report",
                    papers=(
                        {"id": "paper-1", "title": "Unused paper"},
                        {
                            "id": "paper-2",
                            "title": "Used paper",
                            "url": "https://example.test/paper-2",
                        },
                    ),
                    sections=(
                        ReportSectionDraft(
                            section_id="findings",
                            heading="Findings",
                            draft_markdown="The result is supported [@paper-2].",
                        ),
                    ),
                ),
            )

            self.assertEqual(result.status, "completed")
            attempt_root = Path(tmp) / "attempts" / "attempt-001"
            report = (attempt_root / "report.md").read_text(encoding="utf-8")
            body = (attempt_root / "report_body.md").read_text(encoding="utf-8")
            citation_map = json.loads(
                (attempt_root / "citation_map.json").read_text(encoding="utf-8")
            )

            self.assertIn("## References", report)
            self.assertIn("[1] Used paper", report)
            self.assertNotIn("Unused paper", report)
            self.assertIn("[@paper-2]", body)
            self.assertNotIn("## References", body)
            self.assertEqual(citation_map["entries"][0]["paper_id"], "paper-2")
            self.assertIn(
                "@misc{paper-2",
                (attempt_root / "references.bib").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                [artifact.kind for artifact in result.artifacts[:4]],
                ["report", "report_body", "report_references", "citation_map"],
            )

    def test_report_capability_renders_only_planned_figures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            registry.register("report", run_report_capability)
            controller = SessionController.create(
                tmp,
                session_id="report-figure-session",
                topic="offline report",
                profile="paper_audit",
                registry=registry,
            )

            result, _ = controller.execute(
                "report",
                attempt_id="attempt-001",
                request=ReportAssemblyRequest(
                    title="Offline report",
                    template_name="paper",
                    config=ReportRuntimeConfig(
                        figures=ReportFigureConfig(enabled=True, max_figures=1)
                    ),
                    document_plan=ReportDocumentPlan(
                        sections=[
                            ReportSectionPlan(
                                section_id="introduction",
                                heading="Introduction",
                                goal="Orient the reader.",
                                evidence_handles=["paper-1", "paper-2"],
                            )
                        ],
                        visual_intents=[
                            ReportVisualIntent(
                                visual_id="taxonomy",
                                kind="figure",
                                title="Conceptual taxonomy",
                                purpose="Show the organizing concepts.",
                                section_id="introduction",
                                evidence_handles=["paper-1", "paper-2"],
                                view="taxonomy-map",
                            )
                        ],
                    ),
                    sections=(
                        ReportSectionDraft(
                            section_id="introduction",
                            heading="Introduction",
                            draft_markdown="Evidence-backed claim [@paper-1].",
                        ),
                    ),
                ),
            )

            self.assertEqual(result.status, "completed")
            self.assertTrue(
                controller.store.resolve(
                    "attempts/attempt-001/figures/figures_manifest.json"
                ).is_file()
            )
            figure_refs = [artifact for artifact in result.artifacts if artifact.kind == "figure"]
            self.assertEqual(len(figure_refs), 1)
            self.assertEqual(figure_refs[0].path, "figures/taxonomy-map.svg")
            self.assertEqual(figure_refs[0].status, "available")
            attempt = controller.store.read_attempt_manifest(
                "attempts/attempt-001/attempt_manifest.json"
            )
            self.assertEqual(
                [artifact.path for artifact in attempt.outputs if artifact.kind == "figure"],
                ["figures/taxonomy-map.svg"],
            )

    def test_missing_renderer_output_is_explicitly_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            registry.register("report", run_report_capability)
            controller = SessionController.create(
                tmp,
                session_id="report-missing-figure",
                topic="offline report",
                profile="paper_audit",
                registry=registry,
            )

            result, _ = controller.execute(
                "report",
                attempt_id="attempt-001",
                request=ReportAssemblyRequest(
                    title="Offline report",
                    sections=(
                        ReportSectionDraft(
                            section_id="introduction",
                            heading="Introduction",
                            draft_markdown="A short report.",
                        ),
                    ),
                ),
                figure_renderer=_MissingFigureRenderer(),
            )

            self.assertEqual(result.status, "partial")
            self.assertTrue(any("missing artifact" in item for item in result.diagnostics))
            figure_refs = [artifact for artifact in result.artifacts if artifact.kind == "figure"]
            self.assertEqual(len(figure_refs), 1)
            self.assertEqual(figure_refs[0].status, "missing")
            self.assertEqual(controller.manifest.status, "running")

    def test_report_capability_uses_document_plan_order_without_dropping_drafts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            registry.register("report", run_report_capability)
            controller = SessionController.create(
                tmp,
                session_id="report-order-session",
                topic="offline report",
                profile="paper_audit",
                registry=registry,
            )

            result, _ = controller.execute(
                "report",
                attempt_id="attempt-001",
                request=ReportAssemblyRequest(
                    title="Ordered report",
                    document_plan=ReportDocumentPlan(
                        sections=[
                            ReportSectionPlan(
                                section_id="results",
                                heading="Results",
                                goal="Present results.",
                                final_order=2,
                            ),
                            ReportSectionPlan(
                                section_id="introduction",
                                heading="Introduction",
                                goal="Orient the reader.",
                                final_order=1,
                            ),
                        ]
                    ),
                    sections=(
                        ReportSectionDraft(
                            section_id="results",
                            heading="Results",
                            draft_markdown="The result is reproducible.",
                        ),
                        ReportSectionDraft(
                            section_id="appendix",
                            heading="Appendix",
                            draft_markdown="Additional detail.",
                        ),
                        ReportSectionDraft(
                            section_id="introduction",
                            heading="Introduction",
                            draft_markdown="The task is bounded.",
                        ),
                    ),
                ),
            )

            self.assertEqual(result.status, "completed")
            report = controller.store.read_text("attempts/attempt-001/report.md")
            self.assertLess(report.index("## Introduction"), report.index("## Results"))
            self.assertLess(report.index("## Results"), report.index("## Appendix"))

    def test_session_adapter_reads_explicit_report_inputs_and_preserves_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            registry.register("report_audit", run_report_audit_capability)
            controller = SessionController.create(
                tmp,
                session_id="report-audit-session",
                topic="offline report",
                profile="paper_audit",
                registry=registry,
            )
            report_ref = controller.store.write_text(
                "input/report.md",
                "# Report\n\nSupported claim [@paper-1].\n",
                kind="report",
            )
            body_ref = controller.store.write_text(
                "input/report-body.md",
                "# Report\n\nSupported claim [@paper-1].\n",
                kind="report_body",
            )

            result, decision = controller.execute(
                "report_audit",
                attempt_id="attempt-001",
                inputs=(report_ref, body_ref),
                request=ReportAuditCapabilityRequest(
                    report_ref=report_ref,
                    report_body_ref=body_ref,
                    context=ReportContext(
                        topic="offline report",
                        report_mode="research_only",
                        papers=[{"id": "paper-1"}],
                    ),
                    memory=ReportMemory(),
                ),
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")
            output = controller.store.read_json(
                "attempts/attempt-001/report_audit.json"
            )
            self.assertEqual(output["status"], "passed")
            self.assertEqual(output["schema_version"], 1)
            self.assertEqual(
                result.provenance["report_ref"], "input/report.md"
            )

    def test_failed_audit_is_not_reported_as_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            registry.register("report_audit", run_report_audit_capability)
            controller = SessionController.create(
                Path(tmp),
                session_id="report-audit-failure",
                topic="offline report",
                profile="paper_audit",
                registry=registry,
            )
            report_ref = controller.store.write_text(
                "input/report.md",
                "# Report\n\nUnsupported citation [@missing].\n",
            )

            result, decision = controller.execute(
                "report_audit",
                attempt_id="attempt-001",
                inputs=(report_ref,),
                request=ReportAuditCapabilityRequest(
                    report_ref=report_ref,
                    context=ReportContext(
                        topic="offline report",
                        report_mode="research_only",
                        papers=[{"id": "paper-1"}],
                    ),
                    memory=ReportMemory(),
                ),
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(decision.action, "repair")
            self.assertTrue(result.diagnostics)
            self.assertEqual(
                controller.store.read_json(
                    "attempts/attempt-001/report_audit.json"
                )["status"],
                "failed",
            )


class _MissingFigureRenderer:
    name = "missing_fixture"

    def render(
        self,
        *,
        report_markdown: str,
        report_dir: Path,
        config: ReportFigureConfig,
        template_name: str = "",
        document_plan: ReportDocumentPlan | None = None,
        emit=None,
    ) -> ReportFigureResult:
        return ReportFigureResult(
            report_markdown=report_markdown,
            figures=[
                ReportFigureRecord(
                    figure_id="missing",
                    title="Missing figure",
                    path="figures/missing.svg",
                    anchor="Introduction",
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()

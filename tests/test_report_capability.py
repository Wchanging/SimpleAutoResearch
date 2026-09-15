from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from simple_ar.core import CapabilityRegistry, SessionController
from simple_ar.core.capabilities import ArtifactStore, AttemptManifest, CapabilityContext
from simple_ar.report.audit import (
    ReportAuditCapabilityRequest,
    run_report_audit_capability,
)
from simple_ar.report.capability import ReportAssemblyRequest, run_report_capability, assemble_report_document
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
    def test_experiment_figures_are_enabled_by_default_and_can_be_disabled(self):
        self.assertTrue(ReportFigureConfig().enabled)
        self.assertEqual(ReportFigureConfig(mode="off").mode, "off")

    def test_measured_figures_use_declared_groups_and_keep_source_refs(self):
        import xml.etree.ElementTree as ET
        pair = {"seed": 0, "comparability": "declared_match", "baseline": {"status": "passed"},
            "candidate": {"status": "passed"}, "baseline_ref": {"path": "baseline.json"},
            "candidate_ref": {"path": "candidate.json"}, "metrics": [{"name": "accuracy", "baseline": 0.5, "candidate": 1.0}]}
        summary = {"group_id": 0, "metric": "accuracy", "unit": "fraction",
                   "sources": [{"baseline_ref": pair["baseline_ref"], "candidate_ref": pair["candidate_ref"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = ReportAssemblyRequest(title="Paired evaluation", sections=(ReportSectionDraft(
                section_id="results", heading="Results", draft_markdown="Measured results."),),
                config=ReportRuntimeConfig(figures=ReportFigureConfig(enabled=True)),
                paired_comparisons=(pair,), paired_summaries=(summary,))
            result = assemble_report_document(request, report_dir=root)
            self.assertEqual(len(result.figures), 1)
            figure = result.figures[0]
            self.assertEqual(figure.source_artifacts, ["baseline.json", "candidate.json"])
            svg = ET.parse(root / figure.path)
            circles = svg.findall(".//{http://www.w3.org/2000/svg}circle")
            self.assertEqual([c.attrib["cx"] for c in circles], ["460.00", "700.00"])
            self.assertIn("fraction", figure.title)
            self.assertIn(figure.path, result.report_markdown)
            from dataclasses import replace
            absent = assemble_report_document(replace(request, paired_summaries=()), report_dir=root / "no-data")
            self.assertEqual(absent.figures, ())

    def test_default_paired_figures_stay_compact_and_prioritize_core_metrics(self):
        metrics = [
            "accuracy", "forgetting", "average_incremental_accuracy",
            "backward_transfer", "accuracy_after_task_10_on_task_1",
        ]
        pair = {
            "seed": 0,
            "comparability": "declared_match",
            "baseline": {"status": "passed"},
            "candidate": {"status": "passed"},
            "baseline_ref": {"path": "baseline.json"},
            "candidate_ref": {"path": "candidate.json"},
            "metrics": [
                {"name": name, "baseline": 0.4, "candidate": 0.5}
                for name in metrics
            ],
        }
        summaries = [
            {
                "group_id": 0,
                "metric": name,
                "unit": "fraction",
                "sources": [{
                    "baseline_ref": pair["baseline_ref"],
                    "candidate_ref": pair["candidate_ref"],
                }],
            }
            for name in metrics
        ]
        with tempfile.TemporaryDirectory() as tmp:
            result = assemble_report_document(
                ReportAssemblyRequest(
                    title="Compact paired evaluation",
                    sections=(ReportSectionDraft(
                        section_id="results",
                        heading="Results",
                        draft_markdown="Measured results.",
                    ),),
                    paired_comparisons=(pair,),
                    paired_summaries=tuple(summaries),
                ),
                report_dir=Path(tmp),
            )

        self.assertEqual(len(result.figures), 4)
        titles = " ".join(figure.title for figure in result.figures)
        self.assertIn("accuracy", titles)
        self.assertIn("forgetting", titles)
        self.assertNotIn("accuracy_after_task_10_on_task_1", titles)

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

            result = controller.execute_attempt(
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

            result = controller.execute_attempt(
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
                            draft_markdown="The result is supported [@paper-2]. Another claim [@missing].",
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
            self.assertNotIn("@missing", body)
            self.assertEqual(json.loads((attempt_root / "citation_cleanup.json").read_text(encoding="utf-8")),
                             {"removed_citations": ["missing"]})
            refs = {
                ref.kind: controller.store.ref(f"attempts/attempt-001/{ref.path}", kind=ref.kind)
                for ref in result.artifacts
            }
            audit_store = ArtifactStore(Path(tmp) / "audit")
            audit_result = run_report_audit_capability(
                context=CapabilityContext(store=audit_store, attempt=AttemptManifest("audit"),
                                          inputs=tuple(refs.values()), input_store=controller.store),
                request=ReportAuditCapabilityRequest(report_ref=refs["report"], report_body_ref=refs["report_body"],
                    citation_cleanup_ref=refs["citation_cleanup"],
                    context=ReportContext(topic="Offline report", report_mode="research_only",
                                          papers=[{"id": "paper-2", "title": "Used paper"}]), memory=ReportMemory()),
            )
            self.assertEqual(audit_result.status, "failed")
            audit = audit_store.read_json("report_audit.json")
            self.assertIn("missing", audit["citation_audit"]["unknown_citations"])

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

            result = controller.execute_attempt(
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

            result = controller.execute_attempt(
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

            result = controller.execute_attempt(
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

            result = controller.execute_attempt(
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

            result = controller.execute_attempt(
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

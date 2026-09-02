from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from simple_ar.core import (
    BudgetState,
    CapabilityRegistry,
    SessionController,
)
from simple_ar.experiment.execution.backend import RunRequest
from simple_ar.literature.models import Paper
from simple_ar.research import register_research_capabilities
from simple_ar.research.brief import evidence_pack_from_read
from simple_ar.research.contracts import DocumentRecord, SourcePlan, TextChunk
from simple_ar.research.documents.ingest import DocumentBundle, DocumentIngestRequest
from simple_ar.research.evidence.reader import ReadRequest, ReadResult
from simple_ar.research.experiment import (
    ExperimentRequest,
    experiment_request_from_synthesis,
)
from simple_ar.research.sources import SearchProviderRegistry, SearchRequest, SearchResult
from simple_ar.research.sources.base import SearchQuery, SearchResponse
from simple_ar.research.synthesis import SynthesisRequest, SynthesisResult
from simple_ar.report.audit import (
    ReportAuditCapabilityRequest,
)
from simple_ar.report.capability import ReportAssemblyRequest
from simple_ar.report.schema import ReportContext, ReportMemory, ReportSectionDraft


class VerticalCapabilityFlowTests(unittest.TestCase):
    def test_research_to_audit_handoff_stays_explicit_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = CapabilityRegistry()
            register_research_capabilities(
                registry,
                names=("read", "synthesize", "experiment", "analysis", "report", "report_audit"),
            )
            controller = SessionController.create(
                root / "session",
                session_id="vertical-flow",
                topic="reliable agents",
                profile="full_research",
                registry=registry,
                budget=BudgetState(max_attempts=6),
            )

            result, decision = controller.execute(
                "read",
                attempt_id="attempt-001",
                next_capability="synthesize",
                request=ReadRequest(bundle=_bundle()),
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")
            read_ref = controller.attempt_output_refs("attempt-001")[0]
            read_result = ReadResult.from_handoff_dict(
                controller.store.read_json(read_ref),
                bundle=_bundle(),
            )

            result, decision = controller.execute(
                "synthesize",
                attempt_id="attempt-002",
                inputs=(read_ref,),
                next_capability="experiment",
                request=SynthesisRequest(
                    evidence_pack=evidence_pack_from_read(
                        "reliable agents",
                        read_result,
                    )
                ),
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")
            synthesis_ref = controller.attempt_output_refs("attempt-002")[0]

            result, decision = controller.execute(
                "experiment",
                attempt_id="attempt-003",
                inputs=(synthesis_ref,),
                next_capability="analysis",
                request=ExperimentRequest(
                    run=RunRequest(
                        command=[sys.executable, "-c", "print('accuracy: 0.75')"],
                        cwd=root,
                        timeout_sec=5,
                    ),
                    result_schema={
                        "primary_metric": "accuracy",
                        "direction": "higher",
                    },
                ),
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")
            result_ref = controller.attempt_output_refs("attempt-003")[0]

            result, decision = controller.execute(
                "analysis",
                attempt_id="attempt-004",
                inputs=(result_ref,),
                next_capability="report",
                result_ref=result_ref,
                analysis_context={"task_id": "vertical-flow"},
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")

            result, decision = controller.execute(
                "report",
                attempt_id="attempt-005",
                next_capability="report_audit",
                request=ReportAssemblyRequest(
                    title="Reliable agents",
                    sections=(
                        ReportSectionDraft(
                            section_id="findings",
                            heading="Findings",
                            draft_markdown="Validation improves benchmark success [@paper-1].",
                        ),
                    ),
                ),
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")
            report_ref = controller.attempt_output_refs("attempt-005")[0]

            result, decision = controller.execute(
                "report_audit",
                attempt_id="attempt-006",
                inputs=(report_ref,),
                request=ReportAuditCapabilityRequest(
                    report_ref=report_ref,
                    context=ReportContext(
                        topic="reliable agents",
                        report_mode="research_only",
                        papers=[{"id": "paper-1"}],
                    ),
                    memory=ReportMemory(),
                ),
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")
            self.assertEqual(controller.status_snapshot()["status"], "completed")
            self.assertEqual(
                controller.store.read_json(
                    "attempts/attempt-006/report_audit.json"
                )["status"],
                "passed",
            )

    def test_builtin_capabilities_complete_literature_to_audit_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper_path = root / "paper.md"
            paper_path.write_text(
                "# Method\n\nValidation improves reliable agent behavior.\n\n"
                "# Results\n\nThe fixture reports accuracy: 0.75.\n",
                encoding="utf-8",
            )
            registry = CapabilityRegistry()
            register_research_capabilities(
                registry,
                names=(
                    "search",
                    "document_ingest",
                    "read",
                    "synthesize",
                    "experiment",
                    "analysis",
                    "report",
                    "report_audit",
                ),
            )
            controller = SessionController.create(
                root / "session",
                session_id="builtin-vertical-flow",
                topic="reliable agents",
                profile="full_research",
                registry=registry,
                budget=BudgetState(max_attempts=10),
            )

            result, decision = controller.execute(
                "search",
                attempt_id="search-001",
                next_capability="document_ingest",
                request=SearchRequest(
                    queries=("reliable agents",),
                    providers=("fixture",),
                    max_results_per_query=1,
                ),
                registry=SearchProviderRegistry(
                    {"fixture": lambda: _FixtureConnector(paper_path)}
                ),
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")
            search_ref = controller.attempt_output_refs("search-001")[0]
            search_result = SearchResult.from_handoff_dict(
                controller.store.read_json(search_ref)
            )

            source_plan = SourcePlan(
                queries=["reliable agents"],
                sources=["local_files"],
                max_results_per_query=1,
                require_fulltext=True,
                budget={"max_fulltext_documents": 1},
            )
            result, decision = controller.execute(
                "document_ingest",
                attempt_id="document-001",
                inputs=(search_ref,),
                next_capability="read",
                request=DocumentIngestRequest(
                    papers=search_result.papers,
                    source_plan=source_plan,
                    cache_dir=root / "cache",
                    extraction_dir=root / "extracted",
                ),
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")
            document_ref = controller.attempt_output_refs("document-001")[0]
            bundle = DocumentBundle.from_handoff_dict(
                controller.store.read_json(document_ref)
            )

            result, decision = controller.execute(
                "read",
                attempt_id="read-001",
                inputs=(document_ref,),
                next_capability="synthesize",
                request=ReadRequest(bundle=bundle),
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")
            read_ref = controller.attempt_output_refs("read-001")[0]
            read_result = ReadResult.from_handoff_dict(
                controller.store.read_json(read_ref),
                bundle=bundle,
            )

            result, decision = controller.execute(
                "synthesize",
                attempt_id="synthesis-001",
                inputs=(read_ref,),
                next_capability="experiment",
                request=SynthesisRequest(
                    evidence_pack=evidence_pack_from_read(
                        "reliable agents",
                        read_result,
                    )
                ),
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")

            synthesis_ref = controller.attempt_output_ref(
                "synthesis-001",
                kind="synthesis_result",
                schema="synthesis_result.v1",
            )
            synthesis_result = SynthesisResult.from_handoff_dict(
                controller.store.read_json(synthesis_ref)
            )
            self.assertIsNotNone(synthesis_result.experiment_contract)

            result, decision = controller.execute(
                "experiment",
                attempt_id="experiment-001",
                next_capability="analysis",
                request=experiment_request_from_synthesis(
                    synthesis_result,
                    run=RunRequest(
                        command=[sys.executable, "-c", "print('accuracy: 0.75')"],
                        cwd=root,
                        timeout_sec=5,
                    ),
                    result_schema={
                        "primary_metric": "accuracy",
                        "direction": "higher",
                    },
                ),
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")
            result_ref = controller.attempt_output_ref(
                "experiment-001",
                kind="experiment_result",
                schema="canonical_results.2.5",
            )
            experiment_payload = controller.store.read_json(result_ref)
            self.assertEqual(
                experiment_payload["experiment_contract"]["schema_version"],
                "experiment_contract.v1",
            )

            result, decision = controller.execute(
                "analysis",
                attempt_id="analysis-001",
                inputs=(result_ref,),
                next_capability="report",
                result_ref=result_ref,
                analysis_context={"task_id": "builtin-vertical-flow"},
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")
            analysis_ref = controller.attempt_output_refs("analysis-001")[0]
            analysis_payload = controller.store.read_json(analysis_ref)
            metric = analysis_payload["analysis"]["metric_summary"]["metrics"][0]

            result, decision = controller.execute(
                "report",
                attempt_id="report-001",
                inputs=(analysis_ref,),
                next_capability="report_audit",
                request=ReportAssemblyRequest(
                    title="Reliable agents",
                    sections=(
                        ReportSectionDraft(
                            section_id="findings",
                            heading="Findings",
                            draft_markdown=(
                                f"The fixture reports {metric['name']} "
                                f"{metric['value']} [@paper-1]."
                            ),
                        ),
                    ),
                ),
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")
            report_ref = controller.attempt_output_refs("report-001")[0]
            self.assertIn(
                f"{metric['name']} {metric['value']}",
                controller.store.read_text(report_ref),
            )

            result, decision = controller.execute(
                "report_audit",
                attempt_id="audit-001",
                inputs=(report_ref,),
                request=ReportAuditCapabilityRequest(
                    report_ref=report_ref,
                    context=ReportContext(
                        topic="reliable agents",
                        report_mode="research_only",
                        papers=[{"id": "paper-1"}],
                    ),
                    memory=ReportMemory(),
                ),
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")
            self.assertEqual(controller.manifest.status, "completed")
            self.assertEqual(len(controller.list_attempts()), 8)


class _FixtureConnector:
    source_name = "fixture"

    def __init__(self, path: Path) -> None:
        self.path = path

    def search(self, request: SearchQuery) -> SearchResponse:
        return SearchResponse(
            source=self.source_name,
            query=request.query,
            papers=[
                Paper(
                    id="paper-1",
                    title="Reliable agents",
                    authors=["Fixture Author"],
                    abstract="Validation improves reliable agent behavior.",
                    url=self.path.as_uri(),
                    source="local_files",
                    source_id=str(self.path),
                    fulltext_url=self.path.as_uri(),
                )
            ],
        )


def _bundle() -> DocumentBundle:
    return DocumentBundle(
        records=[
            DocumentRecord(
                document_id="paper-1",
                source_id="paper-1",
                title="Reliable agents",
                source="fixture",
                abstract="A validation method improves benchmark success.",
            )
        ],
        fulltext_manifest={},
        fulltext_extraction={},
        sections=[],
        chunks=[
            TextChunk(
                chunk_id="paper-1#chunk-1",
                document_id="paper-1",
                text="Method: validation improves benchmark success.",
                metadata={"section": "method"},
            )
        ],
    )
if __name__ == "__main__":
    unittest.main()

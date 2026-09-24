from __future__ import annotations

import tempfile
import unittest

from tests.legacy_session_fixture import historical_session
from pathlib import Path

from simple_ar.app.research_report import build_research_session_report_inputs
from simple_ar.report.projection import metric_sources_from_execution
from simple_ar.report.schema import (
    ReportContext,
    ReportMemory,
)


class ResearchReportApplicationTests(unittest.TestCase):
    def test_delivery_uses_goal_not_process_success_and_preserves_explicit_template(self):
        from simple_ar.report.templates import resolve_experiment_delivery, load_report_template_bundle
        from simple_ar.report.schema import ReportRuntimeConfig
        config = ReportRuntimeConfig()
        for task_type, status, execution_status, disposition, expected in (
            ("improvement", "not_met", "passed", "deliver_observed_result", "analysis_report"),
            ("improvement", "inconclusive", "passed", "deliver_observed_result", "analysis_report"),
            ("improvement", "met", "passed", "deliver_observed_result", "experiment"),
            ("improvement", "met", "failed", "deliver_observed_result", "analysis_report"),
            ("improvement", "met", "passed", "deliver_with_limits", "analysis_report"),
            ("reproduction", "not_met", "metric_below_target", "deliver_with_limits", "reproduction"),
        ):
            with self.subTest(task_type=task_type, status=status, execution=execution_status):
                analysis = {"status": execution_status, "goal_assessment": {
                    "task_type": task_type, "status": status, "evidence_refs": ["accuracy"]}}
                selected, delivery = resolve_experiment_delivery(config, analysis, {"disposition": disposition})
                self.assertEqual(selected.template, expected)
                self.assertEqual(delivery["goal_assessment"], analysis["goal_assessment"])
                self.assertEqual(load_report_template_bundle(report_mode="experiment", config=selected).name, expected)
        self.assertEqual(config.template, "auto")
        selected, _ = resolve_experiment_delivery(ReportRuntimeConfig(template="experiment"), {}, {})
        self.assertEqual(selected.template, "experiment")
        selected, _ = resolve_experiment_delivery(config, {"status": "passed"}, {})
        self.assertEqual(selected.template, "analysis_report")
        selected, _ = resolve_experiment_delivery(config, {"goal_assessment": {
            "status": "not_met", "requested_delivery": "paper"}}, {})
        self.assertEqual(selected.template, "experiment")

    def test_paired_metrics_preserve_measurement_and_comparison_origins(self):
        from simple_ar.report.projection import attach_paired_report_measurements
        from simple_ar.core.capabilities import ArtifactRef

        context, memory = attach_paired_report_measurements(
            ReportContext(topic="Classification", report_mode="experiment"), ReportMemory(), [
                (7, "baseline", ArtifactRef("baseline.json"),
                 {"status": "passed", "metrics": {"accuracy": 0.7}}),
                (7, "candidate", ArtifactRef("candidate.json"),
                 {"status": "passed", "metrics": {"accuracy": 0.8}}),
            ], comparisons=[{"seed": 7, "metrics": [{"name": "accuracy", "delta": 0.1}]}],
            comparison_ref=ArtifactRef("comparison.json"),
        )
        self.assertEqual(
            [(m.label, m.value, m.artifact) for m in context.metric_sources],
            [("baseline:seed=7", 0.7, "baseline.json"),
             ("candidate:seed=7", 0.8, "candidate.json"),
             ("comparison_delta:seed=7", 0.1, "comparison.json")],
        )
        self.assertEqual(memory.metric_sources, context.metric_sources)
        self.assertEqual({h.artifact for h in memory.source_handles},
                         {"baseline.json", "candidate.json"})

    def test_summary_sources_keep_units_and_do_not_invent_singleton_std(self):
        from simple_ar.report.projection import attach_paired_report_measurements
        from simple_ar.core.capabilities import ArtifactRef
        context, _ = attach_paired_report_measurements(
            ReportContext(topic="Classification", report_mode="experiment"), ReportMemory(), [],
            comparisons=[], comparison_ref=ArtifactRef("paired_analysis.json"), summaries=[{
                "group_id": 0, "metric": "accuracy", "unit": "fraction", "n": 1, "seeds": [7],
                "baseline_mean": 0.5, "candidate_mean": 0.6, "delta_mean": 0.1, "delta_sample_std": None}])
        self.assertEqual(len(context.metric_sources), 4)
        self.assertFalse(any(m.name.endswith("std") for m in context.metric_sources))
        self.assertEqual({m.unit for m in context.metric_sources}, {"fraction", "count"})
        self.assertTrue(all(m.source_kind == "derived_summary" and m.artifact == "paired_analysis.json"
                            for m in context.metric_sources))

    def test_paired_report_excludes_failed_measurement_and_its_delta(self):
        from simple_ar.report.projection import attach_paired_report_measurements
        from simple_ar.core.capabilities import ArtifactRef
        baseline = {"status": "passed", "metrics": {"accuracy": 0.75}}
        failed = {"status": "failed", "metrics": {"accuracy": 0.99}}
        context, memory = attach_paired_report_measurements(ReportContext(topic="Classification", report_mode="experiment"), ReportMemory(), [
            (0, "baseline", ArtifactRef("baseline.json"), baseline),
            (0, "candidate", ArtifactRef("failed.json"), failed)],
            comparisons=[{"seed": 0, "metrics": [{"name": "accuracy", "delta": 0.24}]}],
            comparison_ref=ArtifactRef("paired_analysis.json"))
        self.assertEqual([m.value for m in context.metric_sources], [0.75])
        self.assertEqual(context.metric_sources[0].artifact, "baseline.json")
        self.assertTrue(any(h.artifact == "failed.json" for h in context.source_handles))
        self.assertTrue(any("did not pass" in message for message in memory.limitations))

    def test_report_tools_receive_notes_by_document_identity_not_title(self):
        from simple_ar.report.projection import attach_report_read_evidence
        from simple_ar.core.capabilities import ArtifactRef
        from simple_ar.research.contracts import DocumentRecord
        from simple_ar.research.documents.ingest import DocumentBundle
        from simple_ar.research.evidence.reader import ReadResult
        from simple_ar.report.schema import SourceHandle, ReportToolCall
        from simple_ar.report.tool_gateway import ReportToolGateway

        handles = [SourceHandle(handle=f"paper:{key}", kind="paper", paper_id=key, title="Same title")
                   for key in ("p1", "p2")]
        context = ReportContext(topic="Calibration", report_mode="research_only", source_handles=handles)
        documents = DocumentBundle(records=[DocumentRecord(document_id=f"doc-{key}", source="fixture",
            title="Same title", abstract=f"Abstract for {key}", metadata={"paper_id": key}) for key in ("p1", "p2")],
            fulltext_manifest={}, fulltext_extraction={}, sections=[], chunks=[])
        read = ReadResult(status="completed", bundle=documents, paper_notes=tuple(
            {"paper_id": f"doc-{key}", "method": f"Method for {key}", "limitations": ["Abstract only"],
             "evidence_refs": [f"doc-{key}#abstract"]} for key in ("p2", "p1")))
        projected, memory = attach_report_read_evidence(context, ReportMemory(), documents=documents, read=read,
                                                       read_ref=ArtifactRef("read/result.json"))
        for key in ("p1", "p2"):
            result = ReportToolGateway(projected).call(ReportToolCall(tool_name="get_paper_brief", arguments={"paper_id": key}))
            evidence = result.content["handles"][0]
            self.assertEqual(evidence["summary"], f"Abstract for {key}")
            self.assertEqual(evidence["metadata"]["reading_notes"]["method"], f"Method for {key}")
            self.assertEqual(evidence["metadata"]["extraction_status"], "metadata_only")
            self.assertEqual(evidence["metadata"]["reading_artifact"], "read/result.json")
        self.assertEqual(memory.source_handles, projected.source_handles)
        self.assertEqual(context.source_handles[0].summary, "")

    def test_metric_projection_keeps_each_conditions_own_units_and_direction(self):
        candidate = {"metrics": {"accuracy": 0.8},
                     "result_schema": {"primary_metric": "accuracy", "direction": "higher"},
                     "measurement": {"measurement_id": "candidate-2", "condition_id": "seed-2",
                                     "protocol_id": "protocol", "protocol_revision": 2,
                                     "protocol_fingerprint": "candidate-fingerprint", "source_kind": "measured"},
                     "experiment_contract": {"metric_specs": [{"name": "accuracy", "unit": "fraction"}]},
                     "baseline": {"metrics": {"accuracy": 80},
                                  "result_schema": {"metric_directions": {"accuracy": "lower"}},
                                  "measurement": {"measurement_id": "baseline-1", "condition_id": "seed-1", "source_kind": "measured"},
                                  "experiment_contract": {"metric_specs": [{"name": "accuracy", "unit": "percent"}]}}}
        rows = metric_sources_from_execution(candidate, artifact="result.json")
        by_label = {row.label: row for row in rows}
        self.assertEqual((by_label["candidate"].unit, by_label["candidate"].direction), ("fraction", "higher"))
        self.assertEqual((by_label["baseline"].unit, by_label["baseline"].direction), ("percent", "lower"))
        self.assertEqual(by_label["candidate"].measurement_id, "candidate-2")
        self.assertEqual(by_label["baseline"].measurement_id, "baseline-1")
        legacy = metric_sources_from_execution({"metrics": {"accuracy": 0.5}}, artifact="old.json")[0]
        self.assertEqual(legacy.source_kind, "legacy_unverified")
        self.assertIsNone(legacy.measurement_id)
        self.assertEqual(legacy.unit, "")


    def test_research_session_report_inputs_keep_execution_and_analysis_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = historical_session(root / "session")

            context, memory = build_research_session_report_inputs(session)

            self.assertEqual(context.topic, "reliable agents")
            self.assertEqual(context.results["status"], "passed")
            self.assertTrue(any(item.name == "accuracy" for item in context.metric_sources))
            self.assertTrue(any(item.kind == "analysis" for item in memory.source_handles))

    def test_report_projection_assigns_model_citation_keys(self) -> None:
        from simple_ar.core.capabilities import ArtifactRef
        from simple_ar.literature.models import Paper
        from simple_ar.report.projection import build_literature_report_inputs
        from simple_ar.research.documents.ingest import DocumentBundle
        from simple_ar.research.sources.capability import SearchResult
        from simple_ar.research.synthesis import SynthesisResult

        paper = Paper(
            id="openalex-W1", title="A retrieved paper", authors=[], abstract="Evidence.", url="https://example.test/paper"
        )
        search = SearchResult(status="completed", responses=(), papers=(paper,), selected_papers=(paper,))
        brief = SynthesisResult(status="ready", gap_summary="", ideas=(), novelty_checks=())
        documents = DocumentBundle(records=[], fulltext_manifest={}, fulltext_extraction={}, sections=[], chunks=[])

        context, memory = build_literature_report_inputs(
            topic="A topic", brief=brief, search=search, documents=documents,
            brief_ref=ArtifactRef("synthesis.json"),
        )

        self.assertEqual(context.citation_key_map, {"P1": "openalex-W1"})
        self.assertEqual(context.source_handles[1].citation_key, "P1")
        self.assertEqual(memory.source_handles[1].citation_key, "P1")


if __name__ == "__main__":
    unittest.main()

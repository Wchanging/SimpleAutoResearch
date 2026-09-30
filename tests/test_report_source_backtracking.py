"""Source rereading is bounded, anchored and uses only registered artifacts."""
import tempfile
import json
import unittest
from unittest.mock import patch

from simple_ar.core.capabilities import ArtifactStore, AttemptManifest, CapabilityContext
from simple_ar.report.schema import ReportContext, ReportMemory, ReportRuntimeConfig, ReportToolCall, SourceHandle
from simple_ar.report.templates import load_report_template_bundle
from simple_ar.report.agent import run_report_agent
from simple_ar.report.schema import ReportSectionPlan
from simple_ar.report.tool_gateway import ReportToolGateway
from simple_ar.report.writing import ReportWritingRequest, run_report_writing_capability
from simple_ar.research.contracts import DocumentRecord, TextChunk
from simple_ar.research.documents.ingest import DocumentBundle


class SourceBacktrackingTests(unittest.TestCase):
    def setUp(self):
        self.context = ReportContext(topic="Source check", report_mode="survey", source_handles=[SourceHandle(
            handle="paper:p", kind="paper", paper_id="p", metadata={"document_id": "doc",
                "evidence_passages": [{"chunk_id": "c3", "text": "previously truncated"}]})])
        self.documents = DocumentBundle(records=[DocumentRecord("doc", "Paper", "local", extraction_status="parsed")],
            fulltext_manifest={}, fulltext_extraction={}, sections=[], chunks=[
                *[TextChunk(f"c{i}", "doc", f"Original passage {i}", metadata={"extraction_status": "parsed"}) for i in range(5)],
                TextChunk("foreign", "other", "Other paper")])

    def call(self, gateway, **args):
        return gateway.call(ReportToolCall(tool_name="get_neighbor_chunks", arguments={"handle": "paper:p", **args}))

    def test_actual_passages_are_centered_on_cited_anchor(self):
        result = self.call(ReportToolGateway(self.context, documents=self.documents))
        self.assertEqual(result.status, "ok")
        self.assertEqual([r["chunk_id"] for r in result.content["chunks"]], ["c2", "c3", "c4"])
        self.assertEqual(result.content["chunks"][1]["text"], "Original passage 3")
        self.assertEqual(result.content["source_kind"], "persisted_extracted_text")

    def test_requested_anchor_cannot_cross_source_identity(self):
        gateway = ReportToolGateway(self.context, documents=self.documents)
        self.assertEqual(self.call(gateway, chunk_id="foreign").status, "not_found")
        self.assertEqual(self.call(gateway, chunk_id="missing").status, "not_found")
        result = self.call(gateway, chunk_id="c0", before=3, after=0)
        self.assertEqual([r["chunk_id"] for r in result.content["chunks"]], ["c0"])

    def test_output_is_bounded_and_truncation_visible(self):
        bundle = DocumentBundle([], {}, {}, [], [TextChunk(f"c{i}", "doc", "x" * 6000) for i in range(7)])
        result = self.call(ReportToolGateway(self.context, documents=bundle), before=3, after=3)
        self.assertEqual(sum(len(r["text"]) for r in result.content["chunks"]), 4800)
        self.assertTrue(all(r["truncated"] for r in result.content["chunks"]))
        self.assertTrue(all(r["total_characters"] == 6000 for r in result.content["chunks"]))

    def test_missing_original_is_not_claimed_as_source_read(self):
        result = self.call(ReportToolGateway(self.context))
        self.assertEqual(result.content["source_kind"], "metadata_or_cached_excerpt")
        self.assertNotIn("chunks", result.content)

    def test_writer_reads_only_registered_bundle_and_old_snapshots_still_work(self):
        config = ReportRuntimeConfig()
        template = load_report_template_bundle(report_mode="survey", config=config)
        request = ReportWritingRequest(self.context, ReportMemory(), config, template, object())
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(tmp)
            ref = store.write_json("documents.json", self.documents.to_handoff_dict(), schema="document_bundle.v1")
            for refs in ((ref,), ()):
                with patch("simple_ar.report.writing.run_report_agent", return_value=None) as run:
                    run_report_writing_capability(context=CapabilityContext(store=store,
                        attempt=AttemptManifest("writer"), inputs=refs), request=request)
                    gateway = run.call_args.kwargs["gateway"]
                    kind = self.call(gateway).content["source_kind"]
                    self.assertEqual(kind, "persisted_extracted_text" if refs else "metadata_or_cached_excerpt")

    def test_revision_and_both_verifiers_share_original_source_window(self):
        seen = []
        class Client:
            def ask_json(client_self, system, prompt, *, label="", **kwargs):
                seen.append(label)
                if label == "report-document-reviewer":
                    payload = json.loads(prompt)
                    spec = next(row for row in payload["context_tools"] if row["name"] == "get_neighbor_chunks")
                    self.assertIn("chunk_id", spec["input_schema"]["properties"])
                    return {"section_reviews": [{"section_id": "method", "verdict": "revise_required",
                        "findings": [{"finding_id": "f", "type": "evidence_gap", "severity": "major",
                                      "message": "Check original passage", "section_id": "method"}],
                        "context_requests": [{"tool_name": "get_neighbor_chunks", "arguments": {"handle": "paper:p"}}]}]}
                if label in {"report-document-reviser-method", "report-document-verifier-method"}:
                    self.assertIn("Original passage 3", prompt)
                if label == "report-document-verifier":
                    payload = json.loads(prompt)
                    self.assertEqual(payload["supplementary_evidence"][0]["content"]["source_kind"], "persisted_extracted_text")
                    return {"section_reviews": []}
                if "reviewer" in label or "verifier" in label:
                    return {"verdict": "pass", "findings": []}
                section_id = "method" if "method" in label else "conclusion"
                return {"section_id": section_id, "heading": section_id, "draft_markdown": "Checked content."}
        memory = ReportMemory(source_handles=self.context.source_handles, section_plan=[
            ReportSectionPlan(section_id="method", heading="Method", goal="Check"),
            ReportSectionPlan(section_id="conclusion", heading="Conclusion", goal="Summarize")])
        config = ReportRuntimeConfig(document_review=True, outline_strategy="template")
        result = run_report_agent(client=Client(), context=self.context, memory=memory, config=config,
            template=load_report_template_bundle(report_mode="survey", config=config),
            gateway=ReportToolGateway(self.context, documents=self.documents))
        self.assertTrue(next(row for row in result.iterations if row.action == "document_revise").adopted)
        self.assertIn("report-document-verifier", seen)


if __name__ == "__main__":
    unittest.main()

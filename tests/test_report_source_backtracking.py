"""Source rereading is bounded, anchored and uses only registered artifacts."""
import tempfile
import json
import unittest
from unittest.mock import patch

from simple_ar.core.capabilities import ArtifactStore, AttemptManifest, CapabilityContext
from simple_ar.report.schema import ReportContext, ReportMemory, ReportRuntimeConfig, ReportToolCall, SourceHandle
from simple_ar.report.templates import load_report_template_bundle
from simple_ar.report.agent import run_report_agent, _review_section_with_recovery, _writer_prompt, _writer_recovery_prompt
from simple_ar.report.schema import ReportSectionDraft, ReportToolResult
from simple_ar.report.schema import ReportSectionPlan
from simple_ar.report.tool_gateway import ReportToolGateway
from simple_ar.report.writing import ReportWritingRequest, run_report_writing_capability
from simple_ar.report.audit import build_report_audit
from simple_ar.integrations.llm import LLMError
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

    def test_neighbors_use_original_positions_after_ingest_front_matter_deferral(self):
        self.documents.chunks[:] = [
            TextChunk("middle", "doc", "Body", source_path="source.txt", line_start=10),
            TextChunk("last", "doc", "Last page", source_path="source.txt", line_start=100),
            TextChunk("first", "doc", "Front matter", source_path="source.txt", line_start=1)]
        result = self.call(ReportToolGateway(self.context, documents=self.documents), chunk_id="first", before=0, after=1)
        self.assertEqual([row["chunk_id"] for row in result.content["chunks"]], ["first", "middle"])
        self.assertIn("do not guarantee contiguous", result.summary)

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

    def search(self, gateway, query, **kwargs):
        return gateway.call(ReportToolCall(tool_name="search_source_chunks",
            arguments={"handle": "paper:p", "query": query, **kwargs}))

    def test_search_finds_unshown_source_passage_without_crossing_identity(self):
        self.documents.chunks[-2] = TextChunk("c4", "doc", "Calibration uncertainty is 2 percent.")
        self.documents.chunks[-1] = TextChunk("foreign", "other", "Calibration uncertainty is 99 percent.")
        gateway = ReportToolGateway(self.context, documents=self.documents)
        result = self.search(gateway, "calibration uncertainty")
        self.assertEqual([row["chunk_id"] for row in result.content["chunks"]], ["c4"])
        self.assertEqual(result.content["semantic_verification"], "not_performed")
        self.assertEqual(result.content["document_chunk_count"], 5)
        neighbor = self.call(gateway, chunk_id=result.content["chunks"][0]["chunk_id"])
        self.assertIn("2 percent", json.dumps(neighbor.content))
        self.assertNotIn("99 percent", json.dumps(neighbor.content))

    def test_search_window_contains_late_hit_and_exposes_original_character_offsets(self):
        for query in ("calibration uncertainty", "温度下降"):
            with self.subTest(query=query):
                text = "ß前言 " + "filler " * 1100 + query + " is recorded here."
                self.documents.chunks[:] = [TextChunk("late", "doc", text)]
                result = self.search(ReportToolGateway(self.context, documents=self.documents), query)
                row = result.content["chunks"][0]
                self.assertIn(query, row["text"])
                self.assertEqual(row["text"], text[row["character_start"]:row["character_end"]])
                self.assertTrue(row["truncated"])
                self.assertLessEqual(len(row["text"]), 4800)

    def test_search_prefers_query_phrase_and_preserves_short_matching_chunks(self):
        overview = "An encoder and decoder use attention."
        detail = "intro " * 150 + "The encoder-decoder attention sublayer attends to encoder outputs."
        self.documents.chunks[:] = [TextChunk("overview", "doc", overview), TextChunk("detail", "doc", detail)]
        result = self.search(ReportToolGateway(self.context, documents=self.documents), "encoder decoder attention")
        self.assertEqual(result.content["chunks"][0]["chunk_id"], "detail")
        self.assertEqual(result.content["chunks"][0]["text"], detail)
        self.assertFalse(result.content["chunks"][0]["truncated"])

    def test_search_missing_original_unmatched_or_invalid_query_is_not_absence_proof(self):
        missing = self.search(ReportToolGateway(self.context), "calibration")
        self.assertFalse(missing.content["search_performed"])
        gateway = ReportToolGateway(self.context, documents=self.documents)
        absent = self.search(gateway, "unknown-condition")
        self.assertEqual(absent.status, "not_found")
        self.assertIn("not proof", absent.summary)
        self.assertEqual(self.search(gateway, "x" * 501).status, "error")
        self.assertEqual(gateway.call(ReportToolCall(tool_name="search_source_chunks",
            arguments={"handle": "/private/path", "query": "key"})).status, "not_found")

    def test_search_reuses_existing_tool_call_budget(self):
        gateway = ReportToolGateway(self.context, documents=self.documents)
        for _ in range(8):
            self.assertEqual(self.search(gateway, "original passage").status, "ok")
        self.assertEqual(self.search(gateway, "original passage").status, "blocked")

    def test_newly_fetched_evidence_survives_bounded_prompt_selection(self):
        extra = [ReportToolResult(tool_name="search_source_chunks", status="ok",
            summary=f"evidence-{i}", content={"text": f"evidence-{i}"}) for i in range(8)]
        class Client:
            def ask_json(inner, system, prompt, **kwargs):
                self.assertIn("evidence-7", prompt)
                self.assertNotIn("evidence-0", prompt)
                self.assertIn('"extra_tool_context_omitted": 2', prompt)
                return {"verdict": "pass"}
        kwargs = self.report_kwargs(Client())
        section = kwargs["memory"].section_plan[0]
        _review_section_with_recovery(client=kwargs["client"], context=self.context,
            template=kwargs["template"], memory=kwargs["memory"], section=section,
            draft=ReportSectionDraft(section_id="method", heading="Method", draft_markdown="A claim."),
            config=kwargs["config"], label="review", extra_context=extra)
        prompt = _writer_prompt(context=self.context, template=kwargs["template"],
            memory=kwargs["memory"], section=section, config=kwargs["config"], extra_context=extra,
            previous_draft=None, review=None, source_batch_index=0, source_batch_count=1,
            include_previous_draft=False, draft_mode="initial")
        prompt = json.loads(prompt[prompt.index("{"):])
        self.assertEqual(prompt["extra_tool_context_omitted"], 2)
        self.assertEqual([row["summary"] for row in prompt["extra_tool_context"]],
            [f"evidence-{i}" for i in range(2, 8)])
        recovery = _writer_recovery_prompt(context=self.context, memory=kwargs["memory"],
            section=section, config=kwargs["config"], previous_draft=None, review=None,
            draft_mode="initial", extra_context=extra)
        self.assertEqual(json.loads(recovery[recovery.index("{"):])["extra_tool_context"],
            prompt["extra_tool_context"])

    def report_kwargs(self, client, **settings):
        memory = ReportMemory(source_handles=self.context.source_handles, section_plan=[
            ReportSectionPlan(section_id="method", heading="Method", goal="Check source", evidence_handles=["paper:p"])])
        config = ReportRuntimeConfig(outline_strategy="template", **settings)
        return dict(client=client, context=self.context, memory=memory, config=config,
            template=load_report_template_bundle(report_mode="survey", config=config),
            gateway=ReportToolGateway(self.context, documents=self.documents))

    def test_provisional_pass_reads_then_rejudges_and_revises_the_same_draft(self):
        self.documents.chunks[-2] = TextChunk("c4", "doc", "Calibration uncertainty is 2 percent.")
        labels = []
        class Client:
            def ask_json(inner, system, prompt, *, label="", **kwargs):
                labels.append(label)
                if label == "report-reviewer-method":
                    return {"verdict": "pass", "context_requests": [{"tool_name": "search_source_chunks",
                        "arguments": {"handle": "paper:p", "query": "calibration uncertainty"}}]}
                if label == "report-reviewer-method-evidence":
                    self.assertIn("2 percent", prompt)
                    return {"verdict": "revise_required", "findings": [{"finding_id": "wrong-number",
                        "type": "unsupported_claim", "severity": "major", "message": "Source says 2, not 99."}]}
                if "reviewer" in label:
                    self.assertIn("2 percent", prompt)
                    return {"verdict": "pass", "findings": []}
                return {"section_id": "method", "heading": "Method",
                    "draft_markdown": "Calibration uncertainty is 2 percent." if "reviser" in label else "Calibration uncertainty is 99 percent."}
        result = run_report_agent(**self.report_kwargs(Client(), max_review_iterations=1))
        self.assertIn("2 percent", result.report_body)
        self.assertNotIn("99 percent", result.report_body)
        self.assertEqual(labels.count("report-reviewer-method-evidence"), 1)
        self.assertEqual(result.memory.reviewer_findings, [])
        self.assertTrue(any(row.action == "review_context" for row in result.iterations))

    def test_pending_or_disabled_lookup_does_not_become_a_verified_pass(self):
        for enabled in (True, False):
            labels = []
            class Client:
                def ask_json(inner, system, prompt, *, label="", **kwargs):
                    labels.append(label)
                    if "reviewer" in label:
                        return {"verdict": "pass", "context_requests": [{"tool_name": "search_source_chunks",
                            "arguments": {"handle": "paper:p", "query": "missing-condition"}}]}
                    return {"section_id": "method", "draft_markdown": "A claim still awaiting evidence."}
            with self.subTest(enabled=enabled):
                result = run_report_agent(**self.report_kwargs(Client(), max_review_iterations=0, allow_source_backtracking=enabled))
                self.assertTrue(any(row.type == "source_verification_incomplete" for row in result.memory.reviewer_findings))
                audit = build_report_audit(report=result.report_body, report_body=result.report_body,
                    context=self.context, memory=result.memory)
                self.assertEqual(audit.status, "failed")
                self.assertEqual(labels.count("report-reviewer-method-evidence"), int(enabled))

    def test_evidence_followup_failure_saves_source_results_and_resumes_without_refetch(self):
        saved = []
        labels = []
        class Client:
            def ask_json(inner, system, prompt, *, label="", **kwargs):
                labels.append(label)
                if label == "report-reviewer-method-evidence":
                    raise LLMError("Provider unavailable")
                if "reviewer" in label:
                    if "extra_tool_context" in prompt:
                        return {"verdict": "pass"}
                    return {"verdict": "pass", "context_requests": [{"tool_name": "search_source_chunks",
                        "arguments": {"handle": "paper:p", "query": "original passage"}}]}
                return {"section_id": "method", "draft_markdown": "Descriptive source check."}
        kwargs = self.report_kwargs(Client(), max_review_iterations=0)
        with self.assertRaises(LLMError):
            run_report_agent(**kwargs, checkpoint_sink=lambda row: saved.append(json.loads(json.dumps(row))))
        self.assertTrue(saved[-1]["tool_results"])
        before = len(labels)
        resumed_kwargs = self.report_kwargs(Client(), max_review_iterations=0)
        with patch.object(resumed_kwargs["gateway"], "call", wraps=resumed_kwargs["gateway"].call) as call:
            result = run_report_agent(**resumed_kwargs, completed_checkpoint=saved[-1])
            call.assert_not_called()
        self.assertNotIn("report-writer-method", labels[before:])
        self.assertEqual(resumed_kwargs["gateway"].call_counts["search_source_chunks"], 1)
        self.assertEqual(len(result.tool_results), 1)

    def test_resume_does_not_reset_the_source_tool_allowance(self):
        saved = []
        class Client:
            fail = True
            def ask_json(inner, system, prompt, *, label="", **kwargs):
                if label.endswith("-evidence") and inner.fail:
                    raise LLMError("Provider unavailable")
                if "reviewer" in label:
                    return {"verdict": "pass", "context_requests": [{"tool_name": "search_source_chunks",
                        "arguments": {"handle": "paper:p", "query": "original passage"}}] * (8 if inner.fail else 1)}
                return {"section_id": "method", "draft_markdown": "A source claim."}
        client = Client()
        with self.assertRaises(LLMError):
            run_report_agent(**self.report_kwargs(client, max_review_iterations=0),
                checkpoint_sink=lambda row: saved.append(json.loads(json.dumps(row))))
        self.assertEqual(len(saved[-1]["tool_results"]), 8)
        client.fail = False
        kwargs = self.report_kwargs(client, max_review_iterations=0)
        result = run_report_agent(**kwargs, completed_checkpoint=saved[-1])
        self.assertEqual(result.tool_results[-1].status, "blocked")
        self.assertEqual(kwargs["gateway"].call_counts["search_source_chunks"], 9)
        self.assertTrue(any(row.type == "source_verification_incomplete" for row in result.memory.reviewer_findings))

    def test_document_pass_with_requests_rejudges_after_source_lookup(self):
        for pending in (False, True):
            seen = []
            class Client:
                def ask_json(inner, system, prompt, *, label="", **kwargs):
                    seen.append(label)
                    if label in {"report-document-reviewer", "report-document-reviewer-evidence"}:
                        payload = json.loads(prompt)
                        if label.endswith("-evidence"):
                            self.assertIn("Original passage", json.dumps(payload["supplementary_evidence"]))
                            if not pending:
                                return {"section_reviews": []}
                        return {"section_reviews": [{"section_id": "method", "verdict": "pass",
                            "context_requests": [{"tool_name": "search_source_chunks",
                                "arguments": {"handle": "paper:p", "query": "original passage"}}]}]}
                    if "reviewer" in label:
                        return {"verdict": "pass"}
                    return {"section_id": "method" if "method" in label else "conclusion",
                        "draft_markdown": "A descriptive source check."}
            with self.subTest(pending=pending):
                kwargs = self.report_kwargs(Client(), max_review_iterations=0, document_review=True)
                kwargs["memory"].section_plan.append(ReportSectionPlan(section_id="conclusion", heading="Conclusion", goal="Summarize"))
                result = run_report_agent(**kwargs)
                self.assertEqual(seen.count("report-document-reviewer-evidence"), 1)
                self.assertEqual(bool(result.memory.reviewer_findings), pending)
                self.assertTrue(any(row.action == "document_context" for row in result.iterations))

    def test_document_revision_verifier_cannot_adopt_a_pass_awaiting_evidence(self):
        seen = []
        class Client:
            def ask_json(inner, system, prompt, *, label="", **kwargs):
                seen.append(label)
                if label == "report-document-reviewer":
                    return {"section_reviews": [{"section_id": "method", "verdict": "revise_required",
                        "findings": [{"finding_id": "f", "type": "unsupported_claim", "severity": "major",
                            "message": "Check source before adopting a rewrite."}]}]}
                if label.startswith("report-document-verifier-method"):
                    return {"verdict": "pass", "context_requests": [{"tool_name": "search_source_chunks",
                        "arguments": {"handle": "paper:p", "query": "missing condition"}}]}
                if "reviewer" in label:
                    return {"verdict": "pass"}
                return {"section_id": "method" if "method" in label else "conclusion",
                    "draft_markdown": "Unadopted rewrite." if "reviser" in label else "Original section."}
        kwargs = self.report_kwargs(Client(), max_review_iterations=1, document_review=True)
        kwargs["memory"].section_plan.append(ReportSectionPlan(section_id="conclusion", heading="Conclusion", goal="Summarize"))
        result = run_report_agent(**kwargs)
        candidate = next(row for row in result.iterations if row.action == "document_revise")
        self.assertFalse(candidate.adopted)
        self.assertIn("Original section", result.report_body)
        self.assertNotIn("Unadopted rewrite", result.report_body)
        self.assertTrue(any(row.type == "source_verification_incomplete" for row in result.memory.reviewer_findings))
        self.assertEqual(seen.count("report-document-verifier-method-evidence"), 1)

    def test_final_document_recheck_does_not_accept_pending_evidence(self):
        seen = []
        class Client:
            def ask_json(inner, system, prompt, *, label="", **kwargs):
                seen.append(label)
                if label == "report-document-reviewer":
                    return {"section_reviews": [{"section_id": "method", "verdict": "revise_required",
                        "findings": [{"finding_id": "f", "type": "unsupported_claim", "severity": "major",
                            "message": "Qualify the claim."}]}]}
                if label.startswith("report-document-verifier") and "method" not in label:
                    return {"section_reviews": [{"section_id": "method", "verdict": "pass",
                        "context_requests": [{"tool_name": "search_source_chunks",
                            "arguments": {"handle": "paper:p", "query": "missing condition"}}]}]}
                if "reviewer" in label or "verifier" in label:
                    return {"verdict": "pass"}
                return {"section_id": "method" if "method" in label else "conclusion",
                    "draft_markdown": "Qualified description."}
        kwargs = self.report_kwargs(Client(), max_review_iterations=1, document_review=True)
        kwargs["memory"].section_plan.append(ReportSectionPlan(section_id="conclusion", heading="Conclusion", goal="Summarize"))
        result = run_report_agent(**kwargs)
        self.assertTrue(next(row for row in result.iterations if row.action == "document_revise").adopted)
        self.assertEqual(seen.count("report-document-verifier-evidence"), 1)
        self.assertTrue(any(row.type == "source_verification_incomplete" for row in result.memory.reviewer_findings))

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

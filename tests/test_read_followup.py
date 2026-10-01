"""Source-driven correction stays bounded and survives shared delivery."""
import json
import unittest

from simple_ar.core.capabilities import ArtifactRef
from simple_ar.research.contracts import DocumentRecord, TextChunk
from simple_ar.research.documents.ingest import DocumentBundle
from simple_ar.research.evidence.reader import ReadRequest, ReadResult, read_documents, select_reading_chunks
from simple_ar.research.brief import evidence_pack_from_read
from simple_ar.research.synthesis import SynthesisRequest, _evidence_notes_markdown, synthesize_evidence
from simple_ar.research.store.retrieval import order_source_chunks, rank_source_chunks, source_chunk_views
from simple_ar.report.projection import attach_report_read_evidence
from simple_ar.report.schema import ReportContext, ReportMemory, SourceHandle
from simple_ar.report.agent import _prompt_handle_view


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def ask_json_many(self, requests, **kwargs):
        self.requests.extend(requests)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return [response for request in requests]


class ReadingFollowupTests(unittest.TestCase):
    def bundle(self):
        chunks = [TextChunk(chunk_id=f"c-{index}", document_id="p", text=f"Ordinary discussion {index}.")
                  for index in range(40)]
        shown = {row.chunk_id for row in select_reading_chunks(chunks, max_chunks=12)}
        target = next(index for index in range(1, 39) if f"c-{index}" not in shown)
        chunks[target] = TextChunk(chunk_id=f"c-{target}", document_id="p",
                                  text="Heldout comparison reports accuracy 0.81 with matched conditions.")
        return DocumentBundle([DocumentRecord(document_id="p", title="Same title", source="local_files",
                                              extraction_status="parsed")], {}, {}, [], chunks), f"c-{target}"

    def read(self, bundle, responses):
        client = FakeClient(responses)
        result = read_documents(ReadRequest(bundle=bundle, use_llm=True, llm_client=client,
                                            config={"read_screening": "deterministic"}))
        return result, client

    def test_missing_comparison_is_corrected_with_actual_new_source_then_delivered(self):
        bundle, target = self.bundle()
        result, client = self.read(bundle, [
            {"paper_id": "p", "limitations": ["Comparison not visible"], "followup_queries": ["Heldout comparison"]},
            {"paper_id": "p", "key_claims": ["Accuracy 0.81"], "evidence_refs": [target], "followup_queries": []},
        ])
        note = result.paper_notes[0]
        trace = note["reading_followup"]
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(result.status, "completed")
        self.assertIn("accuracy 0.81", client.requests[1].user)
        self.assertIn(target, note["reading_coverage"]["followup_shown_chunk_ids"])
        self.assertEqual(trace["prior_note"]["limitations"], ["Comparison not visible"])
        self.assertTrue(trace["revision_performed"])
        self.assertLessEqual(len(trace["passages"]), 6)
        restored = ReadResult.from_handoff_dict(json.loads(json.dumps(result.to_handoff_dict())), bundle=bundle)
        self.assertEqual(restored.paper_notes, result.paper_notes)
        pack = evidence_pack_from_read("source review", result)
        self.assertNotIn("prior_note", pack["paper_notes"][0]["reading_followup"])
        self.assertIn("accuracy 0.81", _evidence_notes_markdown(pack))
        self.assertIn("retained_text_only", _evidence_notes_markdown(pack))
        self.assertNotIn("Comparison not visible", _evidence_notes_markdown(pack))
        context = ReportContext(topic="review", report_mode="survey", source_handles=[
            SourceHandle(handle="paper:p", kind="paper", paper_id="p", citation_key="P1")])
        context, _ = attach_report_read_evidence(context, ReportMemory(), documents=bundle, read=result,
                                                read_ref=ArtifactRef(path="attempts/read/read.json", kind="read_result"))
        view = _prompt_handle_view(context.source_handles[0])
        self.assertIn("accuracy 0.81", json.dumps(view))
        self.assertTrue(view["metadata"]["reading_notes"]["reading_followup"]["revision_performed"])
        # A modified handoff cannot masquerade as a saved original passage.
        trace["passages"][0]["text"] = "Invented result"
        context, _ = attach_report_read_evidence(context, ReportMemory(), documents=bundle, read=result,
                                                read_ref=ArtifactRef(path="attempts/read/read.json", kind="read_result"))
        self.assertNotIn("Invented result", json.dumps(context.source_handles[0].metadata))

    def test_no_query_or_old_response_needs_no_extra_call(self):
        bundle, _ = self.bundle()
        result, client = self.read(bundle, [{"paper_id": "p", "method": "Recorded method"}])
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(result.paper_notes[0]["followup_queries"], [])
        self.assertNotIn("reading_followup", result.paper_notes[0])

    def test_abstract_only_and_foreign_same_title_do_not_supply_source_evidence(self):
        bundle, _ = self.bundle()
        for chunks in ([], [TextChunk(chunk_id="foreign", document_id="q", text="Heldout comparison")]):
            with self.subTest(chunks=chunks):
                bundle.chunks[:] = chunks
                result, client = self.read(bundle, [{"paper_id": "p", "followup_queries": ["Heldout comparison"]}])
                self.assertEqual(len(client.requests), 1)
                self.assertEqual(result.status, "partial")
                trace = result.paper_notes[0]["reading_followup"]
                self.assertEqual(trace["lookups"][0]["status"], "no_lexical_match")
                self.assertEqual(trace["passages"], [])
                self.assertEqual(trace["pending_queries"], ["Heldout comparison"])

    def test_late_window_of_already_shown_chunk_survives_report_projection(self):
        chunk = TextChunk(chunk_id="late", document_id="p", text="Initial context. " * 160 + "Heldout comparison accuracy 0.81.")
        bundle, _ = self.bundle()
        bundle.chunks[:] = [chunk]
        result, client = self.read(bundle, [
            {"paper_id": "p", "followup_queries": ["Heldout comparison"]},
            {"paper_id": "p", "evidence_refs": ["late"], "followup_queries": []},
        ])
        self.assertEqual(len(client.requests), 2)
        passage = result.paper_notes[0]["reading_followup"]["passages"][0]
        self.assertGreater(passage["character_start"], 0)
        self.assertIn("accuracy 0.81", passage["text"])
        self.assertLessEqual(len(passage["text"]), 1400)
        context = ReportContext(topic="review", report_mode="survey", source_handles=[
            SourceHandle(handle="paper:p", kind="paper", paper_id="p")])
        context, _ = attach_report_read_evidence(context, ReportMemory(), documents=bundle, read=result,
                                                read_ref=ArtifactRef(path="read.json", kind="read_result"))
        self.assertIn("accuracy 0.81", json.dumps(_prompt_handle_view(context.source_handles[0])))

    def test_unresolved_revision_stops_after_one_round(self):
        bundle, _ = self.bundle()
        result, client = self.read(bundle, [
            {"paper_id": "p", "followup_queries": ["Heldout comparison", "Matched conditions", "third"]},
            {"paper_id": "p", "followup_queries": ["Further source condition"]},
        ])
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(result.status, "partial")
        trace = result.paper_notes[0]["reading_followup"]
        self.assertEqual(len(trace["lookups"]), 2)
        self.assertLessEqual(sum(len(row["text"]) for row in trace["passages"]), 8400)
        self.assertEqual(trace["pending_queries"], ["Further source condition"])
        synthesis = synthesize_evidence(SynthesisRequest(evidence_pack=evidence_pack_from_read("review", result),
                                         purpose="evidence_review", include_experiment_contract=False))
        self.assertEqual(synthesis.status, "needs_review")
        self.assertIn("Unresolved source-reading questions", synthesis.diagnostics[0])

    def test_repeated_view_does_not_repeat_model_call_and_failure_is_not_success(self):
        bundle, _ = self.bundle()
        bundle.chunks[:] = [TextChunk(chunk_id="short", document_id="p", text="Heldout comparison")]
        result, client = self.read(bundle, [{"paper_id": "p", "followup_queries": ["Heldout comparison"]}])
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(result.status, "partial")
        bundle, _ = self.bundle()
        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            self.read(bundle, [{"paper_id": "p", "followup_queries": ["Heldout comparison"]},
                               RuntimeError("provider unavailable")])

    def test_source_view_budgets_are_explicit(self):
        with self.assertRaises(ValueError):
            source_chunk_views([], max_chars=0)
        chunk = TextChunk(chunk_id="long", document_id="p", text="x" * 4000)
        views = source_chunk_views([chunk], max_chars=4200, max_chunk_chars=1400)
        self.assertEqual(len(views[0]["text"]), 1400)

    def test_retained_source_order_is_not_ingest_priority_or_identifier_order(self):
        rows = [TextChunk("body-z", "p", "Later body", source_path="source.txt", line_start=20),
                TextChunk("front", "p", "First page", source_path="source.txt", line_start=1),
                TextChunk("body-a", "p", "Earlier body", source_path="source.txt", line_start=10)]
        self.assertEqual([row.chunk_id for row in order_source_chunks(rows)], ["front", "body-a", "body-z"])
        self.assertEqual(rows[0].chunk_id, "body-z")  # No artifact mutation.
        for unknown in (TextChunk("unknown", "p", "No position"),
                        TextChunk("other-path", "p", "Other extraction", source_path="different.txt", line_start=2),
                        TextChunk("other-source", "q", "Same title", source_path="source.txt", line_start=2)):
            mixed = [*rows, unknown]
            self.assertEqual(order_source_chunks(mixed), mixed)

    def test_remote_repeated_measurement_and_its_context_share_existing_window_budget(self):
        bundle, _ = self.bundle()
        shown = {row.chunk_id for row in select_reading_chunks(bundle.chunks, max_chunks=12)}
        targets = [index for index in range(2, 38) if f"c-{index}" not in shown]
        first, second = targets[0], targets[-1]
        bundle.chunks[first] = TextChunk(chunk_id=f"c-{first}", document_id="p", text="Table 7: Assay recovery\nTreatment 0.82")
        bundle.chunks[second] = TextChunk(chunk_id=f"c-{second}", document_id="p", text="Table 7 summarizes assay recovery and treatment conditions.")
        bundle.chunks[second - 1] = TextChunk(chunk_id=f"c-{second - 1}", document_id="p", text="Treatment recovery was 0.79 under the same stated conditions.")
        result, client = self.read(bundle, [
            {"paper_id": "p", "followup_queries": ["Table 7 summarizes assay recovery and treatment conditions"]},
            {"paper_id": "p", "limitations": ["Two source passages disagree; cause unresolved."], "followup_queries": []},
        ])
        self.assertEqual(len(client.requests), 2)
        revision = client.requests[1].user
        self.assertIn("Treatment 0.82", revision)
        self.assertIn("recovery was 0.79", revision)
        trace = result.paper_notes[0]["reading_followup"]
        self.assertEqual(len(trace["lookups"][0]["matched_chunk_ids"]), 2)
        self.assertLessEqual(len(trace["passages"]), 6)

    def test_questions_share_window_budget_without_starving_second_hit(self):
        bundle, _ = self.bundle()
        bundle.chunks[:] = [TextChunk(chunk_id=f"c-{i}", document_id="p", text=("Assay alpha." if i < 20 else "Instrument beta."))
                            for i in range(40)]
        result, _ = self.read(bundle, [
            {"paper_id": "p", "followup_queries": ["Assay alpha", "Instrument beta"]},
            {"paper_id": "p", "followup_queries": []},
        ])
        trace = result.paper_notes[0]["reading_followup"]
        self.assertLessEqual(len(trace["passages"]), 6)
        self.assertTrue(all(row["new_window_count"] > 0 for row in trace["lookups"]))
        self.assertTrue(all(row["omitted_window_count"] >= 0 for row in trace["lookups"]))

    def test_explicit_labels_locate_caption_not_quoted_cross_reference(self):
        for label in ("Table 7", "Figure S3", "Fig. 2.1", "TABLE IV"):
            with self.subTest(label=label):
                query = f"{label} summarizes our results and compares alternatives"
                mention = TextChunk(chunk_id="mention", document_id="p", text=query)
                caption = TextChunk(chunk_id="caption", document_id="p",
                                    text="Prior unrelated paragraphs. " * 100 + f"\n{label}: Measured conditions\nA 0.81 B 0.76")
                wrong = TextChunk(chunk_id="wrong", document_id="p", text="Table 70: unrelated values")
                chunks = [mention, wrong, caption]
                self.assertEqual(rank_source_chunks(chunks, query, limit=1), [caption])
                view = source_chunk_views([caption], query=query, max_chars=400)[0]
                self.assertIn("A 0.81 B 0.76", view["text"])
                self.assertEqual(rank_source_chunks(chunks, "compares alternatives", limit=1), [mention])


if __name__ == "__main__":
    unittest.main()

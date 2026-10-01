"""Scope transport is not certification, and no paper-name branches are needed."""
import json
import unittest

from simple_ar.core import ArtifactRef
from simple_ar.research.contracts import ClaimCard, DocumentRecord, TextChunk
from simple_ar.research.documents.ingest import DocumentBundle
from simple_ar.research.evidence.reader import ReadResult, validate_read_evidence
from simple_ar.research.evidence.screening import _normalize_paper_note
from simple_ar.research.synthesis import _evidence_notes_markdown
from simple_ar.report.agent import _prompt_handle_view
from simple_ar.report.narrative import narrative_context
from simple_ar.report.projection import attach_report_read_evidence
from simple_ar.report.schema import ReportContext, ReportMemory, ReportSectionDraft, ReportSectionPlan, SourceHandle


class ClaimScopeTests(unittest.TestCase):
    def note(self, scopes):
        return _normalize_paper_note({"paper_id": "p", "title": "Source"},
            {"paper_id": "p", "problem": "A limited setting", "claim_scopes": scopes}, 1)

    def bundle(self):
        return DocumentBundle(
            records=[DocumentRecord(document_id="p", source="local_files", source_id="source.txt", title="Source"),
                     DocumentRecord(document_id="other", source="local_files", source_id="other.txt", title="Other")],
            fulltext_manifest={}, fulltext_extraction={}, sections=[],
            chunks=[TextChunk(chunk_id="p:1", document_id="p", text="Original scoped passage."),
                    TextChunk(chunk_id="other:1", document_id="other", text="Different source.")])

    def test_scopes_preserve_objects_properties_and_conditions_across_domains(self):
        for object_name, property_name, kind in (
            ("predictive distribution", "probabilistic calibration", "conjecture"),
            ("cell culture", "observed viability", "empirical"),
        ):
            with self.subTest(domain=object_name):
                note = self.note([{"claim": "A bounded claim", "object": object_name,
                    "property": property_name, "conditions": ["declared condition"],
                    "evidence_kind": kind, "evidence_refs": ["p:1"]}])
                claim = note["claim_scopes"][0]
                self.assertEqual(claim["paper_id"], "p")
                self.assertEqual(claim["object"], object_name)
                self.assertEqual(claim["property"], property_name)
                self.assertEqual(claim["conditions"], ["declared condition"])
                self.assertEqual(claim["evidence_kind"], kind)
                self.assertIn("not_independently_verified", claim["scope"])
                prose = _evidence_notes_markdown({"paper_notes": [note]})
                self.assertIn(object_name, prose)
                self.assertIn(property_name, prose)

    def test_legacy_notes_and_claim_cards_do_not_acquire_inferred_scope(self):
        note = _normalize_paper_note({"paper_id": "p"}, {"key_claims": ["Legacy claim"]}, 1)
        self.assertEqual(note["claim_scopes"], [])
        card = ClaimCard(claim_id="old", paper_id="p", claim="Old claim")
        self.assertEqual(card.object, "unknown")
        self.assertEqual(card.conditions, [])
        sparse = self.note([{"claim": "Claim with missing details"}])["claim_scopes"][0]
        self.assertEqual(sparse["object"], "unknown")
        self.assertEqual(sparse["evidence_kind"], "unknown")
        self.assertEqual(sparse["evidence_refs"], [])

    def test_malformed_or_unbounded_claim_lists_do_not_silently_disappear(self):
        for scopes in ({"claim": "wrong shape"}, ["wrong row"], [{}], [{"claim": "x"}] * 9):
            with self.subTest(scopes=scopes), self.assertRaises(ValueError):
                self.note(scopes)

    def test_scoped_references_must_be_same_source_passages(self):
        bundle = self.bundle()
        for refs, expected in ((["p:1"], False), (["other:1"], True), (["p"], True), ([], True)):
            note = self.note([{"claim": "Claim", "evidence_refs": refs}])
            read = ReadResult(status="completed", bundle=bundle, paper_notes=(note,))
            self.assertEqual(bool(validate_read_evidence(read)), expected)
            restored = ReadResult.from_handoff_dict(json.loads(json.dumps(read.to_handoff_dict())), bundle=bundle)
            self.assertEqual(restored.paper_notes, read.paper_notes)
            self.assertEqual(restored.status, "partial" if expected else "completed")

    def test_projection_carries_scope_and_separates_extraction_from_reading(self):
        note = self.note([{"claim": "Claim", "object": "culture", "property": "viability",
            "conditions": ["in vitro only"], "evidence_kind": "empirical", "evidence_refs": ["p:1"]}])
        bundle = self.bundle()
        read = ReadResult(status="completed", bundle=bundle, paper_notes=(note,))
        context = ReportContext(topic="Compare evidence", report_mode="survey", papers=[{"id": "p", "title": "Source"},
            {"id": "other", "title": "Other"}], source_handles=[
                SourceHandle(handle="paper:p", kind="paper", paper_id="p", citation_key="P1"),
                SourceHandle(handle="paper:other", kind="paper", paper_id="other", citation_key="P2")])
        context, _ = attach_report_read_evidence(context, ReportMemory(), documents=bundle, read=read,
            read_ref=ArtifactRef(path="attempts/read/read_result.json", kind="read_result"))
        view, unread = [_prompt_handle_view(handle) for handle in context.source_handles]
        claim = view["metadata"]["reading_notes"]["claim_scopes"][0]
        self.assertEqual(claim["conditions"], ["in vitro only"])
        self.assertEqual(claim["evidence_kind"], "empirical")
        self.assertEqual(view["metadata"]["reading_state"], "bounded_model_note")
        self.assertEqual(unread["metadata"]["reading_state"], "no_model_note")
        self.assertNotIn("reading_notes", unread["metadata"])
        self.assertIn("reading notes: 1/2", context.evidence_summary)
        self.assertIn("do not certify", context.evidence_summary)

    def test_restored_malformed_scopes_remain_partial_instead_of_crashing(self):
        for scopes in ("bad shape", ["bad row"]):
            read = ReadResult(status="completed", bundle=self.bundle(),
                paper_notes=({"paper_id": "p", "claim_scopes": scopes},))
            restored = ReadResult.from_handoff_dict(read.to_handoff_dict(), bundle=self.bundle())
            self.assertEqual(restored.status, "partial")
            self.assertTrue(any("malformed" in warning for warning in restored.diagnostics))

    def test_prompt_scope_clipping_is_visible_not_unconditional_support(self):
        scopes = self.note([{"claim": "界" * 800, "conditions": ["x"] * 7,
                             "object": "object", "evidence_refs": ["p:1"]}] * 5)["claim_scopes"]
        view = _prompt_handle_view(SourceHandle(handle="paper:p", kind="paper",
            metadata={"reading_notes": {"claim_scopes": scopes}}))["metadata"]
        self.assertTrue(view["reading_notes_truncated"])
        self.assertEqual(view["reading_notes"]["claim_scopes_omitted"], 1)
        self.assertEqual(len(view["reading_notes"]["claim_scopes"][0]["claim"]), 600)

    def test_malformed_reference_fields_stay_unverified_and_prompt_visible(self):
        note = {"paper_id": "p", "claim_scopes": [{"claim": "Claim", "conditions": None, "evidence_refs": None}]}
        restored = ReadResult.from_handoff_dict(ReadResult(status="completed", bundle=self.bundle(),
            paper_notes=(note,)).to_handoff_dict(), bundle=self.bundle())
        self.assertEqual(restored.status, "partial")
        view = _prompt_handle_view(SourceHandle(handle="paper:p", kind="paper",
            metadata={"reading_notes": note}))["metadata"]
        self.assertTrue(view["reading_notes_truncated"])
        self.assertEqual(view["reading_notes"]["claim_scopes"][0]["evidence_refs"], [])

    def test_middle_tables_remain_visible_without_becoming_evidence(self):
        plan = ReportSectionPlan(section_id="conclusion", heading="Conclusion", goal="Answer briefly")
        text = "Input framing. " * 200 + "\n| Step | Value |\n|---|---|\n| 1 | 0.42 |\n" + "Qualification. " * 200
        draft = ReportSectionDraft(section_id="findings", heading="Findings", draft_markdown=text)
        view = narrative_context(ReportMemory(section_plan=[plan]), plan, [draft])
        adopted = view["adopted_sections"][0]
        self.assertNotIn("0.42", json.dumps(adopted["prose_windows"]))
        table = adopted["table_excerpt"]
        self.assertIn("0.42", json.dumps(table))
        for window in table["windows"]:
            self.assertEqual(window["text"], text[window["start"]:window["end"]])
        self.assertIn("not_independently_verified", adopted["support_status"])
        self.assertTrue(any("object, property, conditions" in rule for rule in view["writing_rules"]))

    def test_table_view_has_explicit_bounds_for_large_custom_drafts(self):
        plan = ReportSectionPlan(section_id="summary", heading="Summary", goal="Summarize")
        text = "\n".join("| " + str(index) + " | " + "界" * 150 + " |" for index in range(40))
        draft = ReportSectionDraft(section_id="results", heading="Results", draft_markdown=text)
        table = narrative_context(ReportMemory(), plan, [draft])["adopted_sections"][0]["table_excerpt"]
        self.assertEqual(table["table_rows_available"], 40)
        self.assertLessEqual(sum(len(row["text"]) for row in table["windows"]), 1000)
        self.assertGreater(table["characters_omitted"], 0)


if __name__ == "__main__":
    unittest.main()

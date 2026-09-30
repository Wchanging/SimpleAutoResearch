from __future__ import annotations

import unittest

from simple_ar.research.contracts import DocumentRecord, TextChunk
from simple_ar.research.documents.ingest import DocumentBundle
from simple_ar.research.evidence.reader import (
    ReadRequest,
    ReadResult,
    format_bundle_evidence_snippets,
    query_evidence,
    read_documents,
    select_representative_chunks,
    select_reading_chunks,
)


class ReadBoundaryTests(unittest.TestCase):
    def test_task_focus_retrieves_unsampled_passage_without_expanding_budget(self) -> None:
        chunks = [TextChunk(chunk_id=f"c-{index}", document_id="p", text=f"General discussion {index}.")
                  for index in range(80)]
        baseline = {chunk.chunk_id for chunk in select_representative_chunks(chunks, max_chunks=12)}
        target = next(index for index in range(1, 79) if f"c-{index}" not in baseline)
        chunks[target] = TextChunk(chunk_id=f"c-{target}", document_id="p",
                                   text="Calibration uncertainty depends on heteroscedastic noise.")
        selected = select_reading_chunks(chunks, max_chunks=12, focus="Explain heteroscedastic calibration uncertainty")
        self.assertEqual(len(selected), 12)
        self.assertIn(f"c-{target}", {chunk.chunk_id for chunk in selected})
        self.assertTrue(any(chunk.chunk_id in {f"c-{target-1}", f"c-{target+1}"} for chunk in selected))
        self.assertEqual(selected, select_reading_chunks(chunks, max_chunks=12, focus="Explain heteroscedastic calibration uncertainty"))

    def test_focus_no_matches_keeps_overview_and_ignores_bibliography(self) -> None:
        chunks = [TextChunk(chunk_id=f"c-{index}", document_id="p", text="Ordinary content.") for index in range(30)]
        chunks.append(TextChunk(chunk_id="ref", document_id="p", text="Heteroscedastic noise.", metadata={"section": "references"}))
        self.assertEqual(select_reading_chunks(chunks, max_chunks=6, focus="Heteroscedastic noise"),
                         select_representative_chunks(chunks, max_chunks=6))
        self.assertEqual(select_reading_chunks(chunks, max_chunks=2, focus="noise"),
                         select_representative_chunks(chunks, max_chunks=2))

    def test_chinese_focus_and_source_scoping(self) -> None:
        bundle = self._bundle(with_chunks=False)
        bundle.chunks.extend(TextChunk(chunk_id=f"c-{index}", document_id="openalex-p1", text="普通正文内容。") for index in range(40))
        bundle.chunks[7] = TextChunk(chunk_id="c-7", document_id="openalex-p1", text="校准误差与仪器噪声影响置信区间。")
        bundle.chunks.append(TextChunk(chunk_id="other", document_id="openalex-p2", text="校准误差属于另一个来源。"))
        excerpts = format_bundle_evidence_snippets(bundle, document_id="openalex-p1", focus="校准误差", max_chunks=6)
        self.assertIn("[c-7]", excerpts)
        self.assertNotIn("[other]", excerpts)
        self.assertIn("does not establish complete coverage", excerpts)

    def test_default_overview_keeps_ingest_sized_chunks_and_labels_further_clipping(self) -> None:
        bundle = self._bundle(with_chunks=False)
        bundle.chunks.append(TextChunk(chunk_id="complete", document_id="openalex-p1",
            text="x" * 1000 + " A late qualification of the main conclusion."))
        self.assertIn("late qualification", format_bundle_evidence_snippets(bundle))
        clipped = format_bundle_evidence_snippets(bundle, max_chars=900)
        self.assertNotIn("late qualification", clipped)
        self.assertIn("1 chunk excerpt(s) shortened", clipped)
        self.assertIn("not evidence of absence", clipped)
        with self.assertRaises(ValueError):
            format_bundle_evidence_snippets(bundle, max_chars=0)

    def test_bounded_reading_covers_late_sections_and_multiple_documents(self) -> None:
        bundle = self._bundle(with_chunks=False)
        bundle.chunks.extend(TextChunk(
            chunk_id=f"p1-{index}", document_id="openalex-p1", text=f"Body {index}",
            metadata={"section": "body", "section_id": "p1-body", "heading": "Main text"},
        ) for index in range(30))
        bundle.chunks.extend(TextChunk(
            chunk_id=f"p1-ref-{index}", document_id="openalex-p1", text=f"Citation {index}",
            metadata={"section": "references", "section_id": "p1-references"},
        ) for index in range(20))
        bundle.chunks.extend(TextChunk(
            chunk_id=f"p2-{index}", document_id="openalex-p2", text=f"Other {index}",
            metadata={"section": "method", "section_id": "p2-method"},
        ) for index in range(3))
        selected = select_representative_chunks(bundle.chunks, max_chunks=6)
        ids = {row.chunk_id for row in selected}
        self.assertEqual(len(selected), 6)
        self.assertTrue(any(row.document_id == "openalex-p2" for row in selected))
        self.assertIn("p1-0", ids)
        self.assertIn("p1-29", ids)
        self.assertFalse(any("ref" in row.chunk_id for row in selected))
        snippets = format_bundle_evidence_snippets(bundle, max_chunks=6)
        self.assertIn("p1-29", snippets)
        self.assertIn("6 of 53 chunks", snippets)

    def test_section_overview_preserves_method_experiment_and_result_evidence(self) -> None:
        kinds = ["abstract"] + ["body"] * 8 + ["method"] + ["body"] * 7
        kinds += ["experiments", "body", "results", "references"]
        chunks = [TextChunk(
            chunk_id=f"section-{index}", document_id="paper", text=f"Section {kind} {index}",
            metadata={"section": kind, "section_id": f"paper-{index}"},
        ) for index, kind in enumerate(kinds)]
        selected = select_representative_chunks(chunks, max_chunks=4)
        self.assertEqual({chunk.metadata["section"] for chunk in selected},
                         {"abstract", "method", "experiments", "results"})

    def test_paper_notes_receive_only_their_own_source_excerpts(self) -> None:
        class NoteClient:
            def ask_json_many(self, requests, *, max_workers):
                self.prompts = [request.user for request in requests]
                return [
                    {"paper_id": paper_id, "title": f"Paper {index}"}
                    for index, paper_id in enumerate(("openalex-p1", "openalex-p2"), start=1)
                ]

        bundle = self._bundle()
        bundle.chunks.append(TextChunk(
            chunk_id="openalex-p2#chunk-001", document_id="openalex-p2",
            text="A distinct second-paper method.",
        ))
        client = NoteClient()
        result = read_documents(ReadRequest(
            bundle=bundle, use_llm=True, llm_client=client,
            topic="Investigate measurement uncertainty",
            problem_markdown="Explain limitations relevant to my task.",
            config={"read_screening": "deterministic"},
        ))
        self.assertEqual(len(result.paper_notes), 2)
        self.assertEqual(result.paper_notes[0]["reading_coverage"]["shown_chunk_ids"], ["openalex-p1#chunk-001"])
        self.assertEqual(result.paper_notes[0]["reading_coverage"]["semantic_verification"], "not_performed")
        self.assertIn("openalex-p1#chunk-001", client.prompts[0])
        self.assertNotIn("openalex-p2#chunk-001", client.prompts[0])
        self.assertIn("openalex-p2#chunk-001", client.prompts[1])
        self.assertNotIn("openalex-p1#chunk-001", client.prompts[1])
        for prompt in client.prompts:
            self.assertIn("Investigate measurement uncertainty", prompt)
            self.assertIn("Explain limitations relevant to my task.", prompt)
            self.assertIn("user request, not source evidence", prompt)

    def test_model_cannot_relabel_a_note_as_another_paper(self) -> None:
        class WrongIdentityClient:
            def ask_json_many(self, requests, *, max_workers):
                return [{"paper_id": "another-paper"} for _ in requests]

        with self.assertRaisesRegex(ValueError, "Paper note identity mismatch"):
            read_documents(ReadRequest(bundle=self._bundle(), use_llm=True,
                llm_client=WrongIdentityClient(), config={"read_screening": "deterministic"}))

    def test_incomplete_note_batch_cannot_silently_drop_sources(self) -> None:
        class IncompleteClient:
            def ask_json_many(self, requests, *, max_workers):
                return []

        with self.assertRaisesRegex(ValueError, "incomplete response set"):
            read_documents(ReadRequest(bundle=self._bundle(), use_llm=True,
                llm_client=IncompleteClient(), config={"read_screening": "deterministic"}))

    def test_restored_notes_validate_passage_ownership_not_just_existence(self) -> None:
        bundle = self._bundle()
        bundle.chunks.append(TextChunk(chunk_id="p2-c1", document_id="openalex-p2", text="Another paper."))
        handoff = read_documents(ReadRequest(bundle=bundle)).to_handoff_dict()
        handoff["paper_notes"] = [{"paper_id": "openalex-p1", "evidence_refs": ["p2-c1", "missing"]}]
        restored = ReadResult.from_handoff_dict(handoff, bundle=bundle)
        self.assertEqual(restored.status, "partial")
        self.assertTrue(any("cross-document" in message for message in restored.diagnostics))
        self.assertEqual(restored.paper_notes[0]["evidence_refs"], ["p2-c1", "missing"])
        handoff["paper_notes"] = [{"paper_id": "openalex-p1", "evidence_refs": ["openalex-p1", "openalex-p1#chunk-001"]}]
        self.assertEqual(ReadResult.from_handoff_dict(handoff, bundle=bundle).status, "completed")
        handoff["paper_notes"] = [{"paper_id": "missing-paper", "evidence_refs": []}]
        self.assertEqual(ReadResult.from_handoff_dict(handoff, bundle=bundle).status, "partial")

    def test_document_query_filters_other_documents(self) -> None:
        bundle = self._bundle()
        bundle.chunks.append(TextChunk(chunk_id="p2-c1", document_id="openalex-p2", text="Other paper"))
        refs = query_evidence(bundle, document_id="openalex-p1", adjacent_chunks=1)
        self.assertEqual([ref.document_id for ref in refs], ["openalex-p1"])
        self.assertNotIn("Other paper", refs[0].context_text)
        handoff = read_documents(ReadRequest(bundle=bundle)).to_handoff_dict()
        self.assertEqual(handoff["source_spans"][0]["evidence_id"], refs[0].evidence_id)
        self.assertIn("extraction_status", handoff["source_spans"][0])
        self.assertNotIn("text", handoff["source_spans"][0])
        with self.assertRaisesRegex(ValueError, "does not belong"):
            query_evidence(bundle, document_id="openalex-p1", chunk_ids=("p2-c1",))

    def _bundle(self, *, with_chunks: bool = True) -> DocumentBundle:
        records = [
            DocumentRecord(
                document_id="openalex-p1",
                source_id="p1",
                title="Paper one",
                source="openalex",
                abstract="A method improves accuracy on a benchmark.",
            ),
            DocumentRecord(
                document_id="openalex-p2",
                source_id="p2",
                title="Paper two",
                source="openalex",
                abstract="A second method studies a task.",
            ),
        ]
        chunks = []
        if with_chunks:
            chunks.append(
                TextChunk(
                    chunk_id="openalex-p1#chunk-001",
                    document_id="openalex-p1",
                    text="A method improves accuracy on a benchmark.",
                )
            )
        return DocumentBundle(
            records=records,
            fulltext_manifest={},
            fulltext_extraction={},
            sections=[],
            chunks=chunks,
        )

    def test_paper_id_selection_matches_source_id(self) -> None:
        result = read_documents(
            ReadRequest(bundle=self._bundle(), paper_ids=("p1",))
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual([record.source_id for record in result.bundle.records], ["p1"])
        self.assertEqual([chunk.document_id for chunk in result.bundle.chunks], ["openalex-p1"])
        self.assertEqual(result.to_dict()["paper_card_count"], 1)

    def test_empty_selection_does_not_fall_back_to_all_documents(self) -> None:
        result = read_documents(ReadRequest(bundle=self._bundle(), paper_ids=()))

        self.assertEqual(result.status, "empty")
        self.assertEqual(result.bundle.records, [])
        self.assertEqual(result.bundle.chunks, [])
        self.assertTrue(result.diagnostics)

    def test_model_dropping_every_paper_does_not_restore_the_input(self) -> None:
        class DropAllClient:
            model = "scripted-drop-all"

            def ask_json(self, *args, **kwargs):
                return {"ranked_papers": []}

            def ask_json_many(self, requests, *, max_workers):
                return [{"decisions": [
                    {"paper_id": paper_id, "decision": "drop",
                     "coarse_relevance_score": 0, "reason": "Outside the topic."}
                    for paper_id in ("openalex-p1", "openalex-p2")
                ]} for _ in requests]

        result = read_documents(ReadRequest(
            bundle=self._bundle(), topic="unrelated topic",
            use_llm=True, llm_client=DropAllClient(),
        ))
        self.assertEqual(result.bundle.records, [])
        self.assertEqual(result.paper_notes, ())
        self.assertTrue(result.screening_decisions)
        self.assertTrue(all(row["decision"] == "drop" for row in result.screening_decisions))

    def test_metadata_only_read_is_partial(self) -> None:
        result = read_documents(ReadRequest(bundle=self._bundle(with_chunks=False)))

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.bundle.records[0].document_id, "openalex-p1")
        self.assertEqual(result.bundle.chunks, [])

    def test_query_evidence_preserves_source_identity_and_real_adjacent_context(self) -> None:
        record = DocumentRecord(
            document_id="doc-1",
            source_id="paper-1",
            title="Paper one",
            source="fixture",
            content_hash="sha256:abc",
            extraction_status="parsed",
        )
        bundle = DocumentBundle(
            records=[record],
            fulltext_manifest={},
            fulltext_extraction={},
            sections=[],
            chunks=[
                TextChunk(
                    chunk_id="doc-1#chunk-001",
                    document_id="doc-1",
                    text="Introduction evidence.",
                    source_path="paper.md",
                    line_start=3,
                    line_end=4,
                ),
                TextChunk(
                    chunk_id="doc-1#chunk-002",
                    document_id="doc-1",
                    text="The measured result is 0.75.",
                    source_path="paper.md",
                    line_start=6,
                    line_end=7,
                ),
                TextChunk(
                    chunk_id="doc-1#chunk-003",
                    document_id="doc-1",
                    text="The limitation is a small fixture.",
                    source_path="paper.md",
                    line_start=9,
                    line_end=10,
                ),
            ],
        )

        refs = query_evidence(
            bundle,
            document_id="doc-1",
            chunk_ids=("doc-1#chunk-002",),
            adjacent_chunks=1,
        )

        self.assertEqual(len(refs), 1)
        ref = refs[0]
        self.assertEqual(ref.evidence_id, "doc-1#chunk-002")
        self.assertEqual(ref.source_id, "paper-1")
        self.assertEqual(ref.document_revision, "sha256:abc")
        self.assertEqual(ref.source_path, "paper.md")
        self.assertEqual((ref.line_start, ref.line_end), (6, 7))
        self.assertEqual(ref.extraction_status, "parsed")
        self.assertEqual(ref.text, "The measured result is 0.75.")
        self.assertEqual(
            ref.adjacent_chunk_ids,
            ("doc-1#chunk-001", "doc-1#chunk-003"),
        )
        self.assertIn("Introduction evidence.", ref.context_text)
        self.assertIn("The limitation is a small fixture.", ref.context_text)

        with self.assertRaisesRegex(ValueError, "Unknown evidence chunk ID"):
            query_evidence(bundle, chunk_ids=("missing",))

    def test_model_read_records_screening_and_notes_in_the_handoff(self) -> None:
        class FakeClient:
            model = "fake-read-model"

            def ask_json(self, system: str, user: str, *, label: str = "") -> dict[str, object]:
                self.rerank_label = label
                return {
                    "ranked_papers": [
                        {
                            "paper_id": "openalex-p1",
                            "decision": "keep",
                            "reading_priority": 1,
                            "relevance_score": 5,
                            "quality_score": 4,
                            "evidence_role": "benchmark",
                            "reason": "Matches the evaluation topic.",
                            "synthesis_hint": "Use the benchmark evidence.",
                            "confidence": "medium",
                        }
                    ]
                }

            def ask_json_many(self, requests: list[object], *, max_workers: int) -> list[dict[str, object]]:
                labels = [str(getattr(request, "label", "")) for request in requests]
                if labels and all(label.startswith("read-coarse-") for label in labels):
                    return [
                        {
                            "decisions": [
                                {
                                    "paper_id": "openalex-p1",
                                    "decision": "keep",
                                    "coarse_relevance_score": 5,
                                    "likely_facet": "benchmark",
                                    "reason": "Relevant benchmark metadata.",
                                    "confidence": "medium",
                                },
                                {
                                    "paper_id": "openalex-p2",
                                    "decision": "drop",
                                    "coarse_relevance_score": 0,
                                    "likely_facet": "other",
                                    "reason": "Outside the topic.",
                                    "confidence": "medium",
                                },
                            ]
                        }
                    ]
                self.note_users = [str(getattr(request, "user", "")) for request in requests]
                return [
                    {
                        "paper_id": "openalex-p1",
                        "title": "Paper one",
                        "problem": "Study reliable agents.",
                        "method": "A validation method.",
                        "limitation": "Small fixture.",
                        "relation_to_topic": "Directly relevant.",
                        "synthesis_hint": "Use as the benchmark anchor.",
                        "confidence": "medium",
                    }
                    for _ in requests
                ]

        client = FakeClient()
        result = read_documents(
            ReadRequest(
                bundle=self._bundle(),
                topic="reliable coding agents",
                problem_markdown="# Problem\n\nStudy reliable coding agents.",
                research_plan_json='{"research_questions": []}',
                use_llm=True,
                llm_client=client,
                config={"read_screening_max_shortlist": 1},
            )
        )

        self.assertEqual([record.document_id for record in result.bundle.records], ["openalex-p1"])
        self.assertEqual(result.screening_decisions[1]["decision"], "drop")
        self.assertEqual(result.paper_notes[0]["paper_id"], "openalex-p1")
        self.assertIn("Paper one", result.notes_markdown)
        self.assertIn("A method improves accuracy on a benchmark.", client.note_users[0])
        restored = ReadResult.from_handoff_dict(
            result.to_handoff_dict(),
            bundle=result.bundle,
        )
        self.assertEqual(restored.paper_notes, result.paper_notes)
        self.assertEqual(restored.screening_decisions, result.screening_decisions)


if __name__ == "__main__":
    unittest.main()

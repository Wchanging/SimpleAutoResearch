from __future__ import annotations

import unittest

from simple_ar.research.contracts import DocumentRecord, TextChunk
from simple_ar.research.documents.ingest import DocumentBundle
from simple_ar.research.evidence.reader import (
    ReadRequest,
    ReadResult,
    query_evidence,
    read_documents,
)


class ReadBoundaryTests(unittest.TestCase):
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

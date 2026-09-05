from __future__ import annotations

import unittest

from simple_ar.research.contracts import DocumentRecord, TextChunk
from simple_ar.research.documents.ingest import DocumentBundle
from simple_ar.research.evidence.reader import ReadRequest, ReadResult, read_documents


class ReadBoundaryTests(unittest.TestCase):
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

    def test_metadata_only_read_is_partial(self) -> None:
        result = read_documents(ReadRequest(bundle=self._bundle(with_chunks=False)))

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.bundle.records[0].document_id, "openalex-p1")
        self.assertEqual(result.bundle.chunks, [])

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

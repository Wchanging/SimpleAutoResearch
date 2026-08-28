from __future__ import annotations

import unittest

from simple_ar.research.contracts import DocumentRecord, TextChunk
from simple_ar.research.documents.ingest import DocumentBundle
from simple_ar.research.evidence.reader import ReadRequest, read_documents


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


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from simple_ar.core import (
    ArtifactStore,
    AttemptManifest,
)
from simple_ar.core.capabilities import CapabilityContext
from simple_ar.research.brief import (
    ResearchBriefRequest,
    ResearchBriefResult,
    build_research_brief,
)
from simple_ar.research.contracts import DocumentRecord, PaperCard, TextChunk
from simple_ar.research.documents.ingest import DocumentBundle
from simple_ar.research.evidence.reader import (
    ReadRequest,
    ReadResult,
    run_read_capability,
    validate_read_evidence,
)


class ResearchBriefCapabilityTests(unittest.TestCase):
    def _bundle(self, *, with_chunks: bool = True) -> DocumentBundle:
        record = DocumentRecord(
            document_id="paper-1",
            source_id="paper-1",
            title="Reliable agents",
            source="fixture",
            abstract="A method improves benchmark success.",
        )
        chunks = (
            [
                TextChunk(
                    chunk_id="paper-1#chunk-1",
                    document_id="paper-1",
                    text=(
                        "Method: a validation method improves benchmark success. "
                        "Dataset: the benchmark reports success."
                    ),
                    metadata={"section": "method"},
                )
            ]
            if with_chunks
            else []
        )
        return DocumentBundle(
            records=[record],
            fulltext_manifest={},
            fulltext_extraction={},
            sections=[],
            chunks=chunks,
        )

    def test_brief_composes_read_and_synthesis_without_persistence(self) -> None:
        result = build_research_brief(
            ResearchBriefRequest(topic="reliable agents", bundle=self._bundle())
        )

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.read.status, "completed")
        self.assertIsNotNone(result.synthesis)
        self.assertTrue(result.synthesis.ideas)
        self.assertIsNotNone(result.synthesis.experiment_contract)
        self.assertEqual(result.to_dict()["schema_version"], "research_brief_result.v1")

    def test_brief_preserves_partial_read_status_and_requests_review(self) -> None:
        result = build_research_brief(
            ResearchBriefRequest(
                topic="metadata-only topic",
                bundle=self._bundle(with_chunks=False),
            )
        )

        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.read.status, "partial")
        self.assertIsNotNone(result.synthesis)
        self.assertTrue(result.diagnostics)

    def test_brief_empty_selection_does_not_synthesize_fake_evidence(self) -> None:
        result = build_research_brief(
            ResearchBriefRequest(
                topic="empty selection",
                bundle=self._bundle(),
                paper_ids=(),
            )
        )

        self.assertEqual(result.status, "empty")
        self.assertIsNone(result.synthesis)
        self.assertEqual(result.read.bundle.records, [])

    def test_request_validates_topic_and_limit(self) -> None:
        with self.assertRaises(ValueError):
            ResearchBriefRequest(topic=" ", bundle=self._bundle())
        with self.assertRaises(ValueError):
            ResearchBriefRequest(topic="topic", bundle=self._bundle(), idea_limit=0)

    def test_handoff_contains_cards_without_source_chunk_text(self) -> None:
        result = build_research_brief(
            ResearchBriefRequest(topic="reliable agents", bundle=self._bundle())
        )

        payload = result.to_handoff_dict(topic="reliable agents")

        self.assertEqual(payload["schema_version"], "research_brief.v1")
        self.assertEqual(payload["read"]["paper_cards"][0]["paper_id"], "paper-1")
        self.assertEqual(payload["read"]["source_spans"][0]["chunk_id"], "paper-1#chunk-1")
        self.assertNotIn("Method: a validation method", str(payload["read"]["source_spans"]))
        self.assertEqual(payload["synthesis"]["ideas"][0]["idea_id"], "idea-001")

    def test_read_capability_persists_selected_evidence_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = CapabilityContext(
                store=ArtifactStore(Path(tmp)),
                attempt=AttemptManifest(
                    attempt_id="attempt-001",
                    capability="read",
                ),
            )
            result = run_read_capability(
                context=context,
                request=ReadRequest(bundle=self._bundle()),
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.artifacts[0].path, "read_result.json")
            payload = context.store.read_json(result.artifacts[0])
            self.assertEqual(payload["schema_version"], "read_result.v1")
            self.assertEqual(payload["paper_cards"][0]["paper_id"], "paper-1")
            self.assertNotIn("Method: a validation method", str(payload["source_spans"]))

    def test_read_evidence_validation_accepts_bundle_references(self) -> None:
        result = build_research_brief(
            ResearchBriefRequest(topic="reliable agents", bundle=self._bundle())
        ).read

        self.assertEqual(validate_read_evidence(result), ())

    def test_corrupt_read_handoff_becomes_partial_instead_of_claiming_complete(self) -> None:
        restored = ReadResult.from_handoff_dict(
            {
                "schema_version": "read_result.v1",
                "status": "completed",
                "paper_cards": [
                    PaperCard(
                        paper_id="paper-1",
                        title="Reliable agents",
                        evidence_refs=["paper-1#missing-chunk"],
                    ).to_row()
                ],
                "claim_cards": [],
                "method_cards": [],
                "dataset_cards": [],
                "code_links": [],
                "source_spans": [],
            },
            bundle=self._bundle(),
        )

        self.assertEqual(restored.status, "partial")
        self.assertEqual(len(restored.diagnostics), 1)
        self.assertIn("paper-1#missing-chunk", restored.diagnostics[0])


if __name__ == "__main__":
    unittest.main()

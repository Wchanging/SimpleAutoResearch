from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from simple_ar.core.artifacts import write_jsonl
from simple_ar.core.pipeline import Context
from simple_ar.core.stages import Stage
from simple_ar.literature.models import Paper
from simple_ar.pipeline_stages.research import _write_read_cards
from simple_ar.research.contracts import DocumentRecord, TextChunk
from simple_ar.research.documents.ingest import build_document_bundle
from simple_ar.research.sources.base import build_source_plan
from simple_ar.research.service import load_search_document_bundle


class DocumentIngestTests(unittest.TestCase):
    def test_bundle_preserves_existing_local_document_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper_path = root / "paper.md"
            paper_path.write_text(
                "# Method\n\nA bounded method.\n\n# Results\n\nRMSE improves.\n",
                encoding="utf-8",
            )
            source_plan = build_source_plan(
                topic="agent coding",
                problem_markdown="",
                config={
                    "research_sources": ["local_files"],
                    "research_local_documents": [str(paper_path)],
                    "research_use_fulltext": True,
                    "research_max_chunks": 1,
                },
                default_query="agent coding",
                default_max_results=3,
            )
            bundle = build_document_bundle(
                papers=[
                    Paper(
                        id="p1",
                        title="Metadata Paper",
                        authors=[],
                        abstract="A metadata abstract.",
                        url="https://example.test/p1",
                        source="fixture",
                    )
                ],
                source_plan=source_plan,
                cache_dir=root / "cache",
                extraction_dir=root / "extracted",
                max_chunks=source_plan.budget["max_chunks"],
            )

            self.assertEqual(len(bundle.records), 2)
            self.assertEqual(bundle.records[1].extraction_status, "parsed")
            self.assertEqual(len(bundle.sections), 3)
            self.assertEqual(len(bundle.chunks), 1)
            self.assertEqual(bundle.fulltext_extraction["parsed_count"], 1)

    def test_search_document_bundle_loads_legacy_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            search_dir = run_dir / "02-search"
            write_jsonl(
                search_dir / "documents" / "documents.jsonl",
                [
                    DocumentRecord(
                        document_id="openalex-p1",
                        source_id="p1",
                        title="A paper",
                        source="openalex",
                    ).to_row()
                ],
            )
            write_jsonl(
                search_dir / "research_index" / "chunks.jsonl",
                [
                    TextChunk(
                        chunk_id="openalex-p1#chunk-001",
                        document_id="openalex-p1",
                        text="A source passage.",
                    ).to_row()
                ],
            )

            bundle = load_search_document_bundle(
                Context(run_dir=run_dir, topic="topic", current_stage=Stage.READ)
            )

            self.assertEqual([record.document_id for record in bundle.records], ["openalex-p1"])
            self.assertEqual([chunk.chunk_id for chunk in bundle.chunks], ["openalex-p1#chunk-001"])
            self.assertEqual(bundle.sections, [])
            self.assertEqual(bundle.fulltext_manifest, {})

    def test_read_cards_match_shortlist_paper_ids_to_document_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            write_jsonl(
                run_dir / "02-search" / "documents" / "documents.jsonl",
                [
                    DocumentRecord(
                        document_id="openalex-p1",
                        source_id="p1",
                        title="A paper",
                        source="openalex",
                        abstract="A method improves accuracy on a benchmark.",
                    ).to_row()
                ],
            )
            write_jsonl(
                run_dir / "02-search" / "research_index" / "chunks.jsonl",
                [
                    TextChunk(
                        chunk_id="openalex-p1#chunk-001",
                        document_id="openalex-p1",
                        text="A method improves accuracy on a benchmark.",
                    ).to_row()
                ],
            )
            write_jsonl(
                run_dir / "03-read" / "review" / "shortlist.jsonl",
                [{"paper_id": "p1"}],
            )

            _write_read_cards(Context(run_dir=run_dir, topic="topic", current_stage=Stage.READ))

            cards = (run_dir / "03-read" / "cards" / "paper_cards.jsonl").read_text(encoding="utf-8")
            self.assertIn('"title": "A paper"', cards)


if __name__ == "__main__":
    unittest.main()

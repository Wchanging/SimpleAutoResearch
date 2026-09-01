from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simple_ar.core.artifacts import write_jsonl
from simple_ar.core.pipeline import Context
from simple_ar.core.stages import Stage
from simple_ar.literature.models import Paper
from simple_ar.pipeline_stages.research import _write_read_cards
from simple_ar.research.contracts import DocumentRecord, SourcePlan, TextChunk
from simple_ar.research.documents.fulltext import build_fulltext_manifest
from simple_ar.research.documents.ingest import build_document_bundle
from simple_ar.research.sources.base import build_source_plan
from simple_ar.research.service import load_search_document_bundle


class DocumentIngestTests(unittest.TestCase):
    def test_remote_fulltext_reuses_a_valid_cache_entry(self) -> None:
        class Response:
            headers = {"Content-Type": "application/pdf"}

            def __init__(self) -> None:
                self._read = False

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self, _size: int = -1) -> bytes:
                if self._read:
                    return b""
                self._read = True
                return b"%PDF-1.7\nfixture"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = DocumentRecord(
                document_id="paper-1",
                title="A paper",
                source="fixture",
                metadata={
                    "fulltext_hints": [
                        {
                            "kind": "pdf",
                            "source": "fixture",
                            "url": "https://example.test/paper.pdf",
                        }
                    ]
                },
            )
            source_plan = SourcePlan(
                queries=["fixture"],
                require_fulltext=True,
                allow_pdf_download=True,
                budget={
                    "max_fulltext_documents": 1,
                    "max_fulltext_fetch_attempts": 1,
                    "keep_raw_pdf": True,
                },
            )
            with patch(
                "simple_ar.research.documents.fulltext.urllib.request.urlopen",
                return_value=Response(),
            ) as urlopen:
                first = build_fulltext_manifest(
                    records=[record],
                    source_plan=source_plan,
                    cache_dir=root / "cache",
                )
                second = build_fulltext_manifest(
                    records=[record],
                    source_plan=source_plan,
                    cache_dir=root / "cache",
                )

            first_hint = first["documents"][0]["hints"][0]
            second_hint = second["documents"][0]["hints"][0]
            self.assertEqual(first_hint["status"], "cached")
            self.assertEqual(second_hint["reason"], "cache_hit")
            self.assertEqual(first_hint["local_path"], second_hint["local_path"])
            urlopen.assert_called_once()

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

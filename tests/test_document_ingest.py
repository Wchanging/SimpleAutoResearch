from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from simple_ar.literature.models import Paper
from simple_ar.research.documents.ingest import build_document_bundle
from simple_ar.research.sources.base import build_source_plan


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


if __name__ == "__main__":
    unittest.main()

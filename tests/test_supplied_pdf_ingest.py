"""Supplied PDFs must not vanish when remote full-text fetching is off."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import redirect_stdout
import io
import tempfile
import unittest

from simple_ar.app.research_application import ResearchApplication, load_session
from simple_ar.literature.models import Paper
from simple_ar.research.documents.fulltext import build_fulltext_manifest
from simple_ar.research.documents.ingest import build_document_bundle
from simple_ar.research.documents.records import build_document_records
from simple_ar.research.sources.base import build_source_plan


class SuppliedPdfIngestTest(unittest.TestCase):
    def test_configured_pdf_reaches_normal_session_reading(self) -> None:
        from simple_ar.cli.main import main

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "method.pdf").write_bytes(b"%PDF-1.7\nfixture")
            config = root / "research.toml"
            config.write_text(
                '[task]\ngoal="Summarize the supplied TabM method"\noutputs=["summary"]\noutput_root="out"\n'
                '[model]\nname=""\n[research]\nproviders=["local_files"]\n'
                'use_fulltext=false\nallow_pdf_download=false\n'
                '[assets]\npapers=["method.pdf"]\n', encoding="utf-8",
            )
            console = io.StringIO()
            with patch(
                "simple_ar.research.documents.extractors._read_pdf",
                return_value="# Method\n\nTabM uses parameter-efficient ensembling.",
            ), redirect_stdout(console):
                try:
                    main(["research-session", "--config", str(config)])
                except SystemExit as exc:
                    self.fail(f"Session stopped: {exc}; console: {console.getvalue()}")
            session = next((root / "out").iterdir())
            app = load_session(session)
            self.assertEqual(len(app._local_documents()), 1)
            self.assertEqual(app.view().status, "completed")
            self.assertTrue(any(
                "parameter-efficient ensembling" in section.text
                for section in app._load_documents().sections
            ))

    def test_application_passes_local_pdf_to_source_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "method.pdf"
            paper.write_bytes(b"%PDF-1.7\nfixture")
            application = SimpleNamespace(assets=(SimpleNamespace(
                locator=str(paper), role="paper", availability="available",
            ),))
            self.assertEqual(ResearchApplication._local_documents(application), (paper,))

    def test_local_pdf_is_read_without_remote_fulltext_permission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / "method.pdf"
            paper.write_bytes(b"%PDF-1.7\nfixture")
            plan = build_source_plan(
                topic="TabM method",
                problem_markdown="",
                config={
                    "research_sources": ["local_files"],
                    "research_local_documents": [str(paper)],
                    "research_use_fulltext": False,
                    "research_allow_pdf_download": False,
                },
                default_query="TabM method",
                default_max_results=3,
            )
            with patch(
                "simple_ar.research.documents.extractors._read_pdf",
                return_value="# Method\n\nThe paper uses parameter-efficient ensembling.",
            ) as parse_pdf:
                bundle = build_document_bundle(
                    papers=[], source_plan=plan, cache_dir=None,
                    extraction_dir=root / "extracted",
                )
            parse_pdf.assert_called_once()
            self.assertTrue(bundle.fulltext_manifest["enabled"])
            self.assertFalse(bundle.fulltext_manifest["allow_pdf_download"])
            self.assertEqual(bundle.records[0].extraction_status, "parsed")
            self.assertIn("parameter-efficient ensembling", bundle.sections[0].text)
            self.assertTrue(bundle.chunks)

    def test_supplied_pdf_does_not_spend_remote_fetch_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "method.pdf"
            paper.write_bytes(b"%PDF-1.7\nfixture")
            plan = build_source_plan(
                topic="TabM method", problem_markdown="",
                config={
                    "research_sources": ["fixture"],
                    "research_local_documents": [str(paper)],
                    "research_use_fulltext": True,
                    "research_max_fulltext_documents": 1,
                    "research_max_fulltext_fetch_attempts": 1,
                },
                default_query="TabM method", default_max_results=3,
            )
            remote = Paper(
                id="remote-1", title="Related method", authors=[], abstract="Related.",
                url="https://example.test/method.html", source="fixture",
            )
            records = build_document_records(papers=[remote], source_plan=plan)
            manifest = build_fulltext_manifest(records=records, source_plan=plan)
            self.assertEqual(manifest["documents"][0]["hints"][0]["status"], "cached")
            self.assertEqual(manifest["documents"][1]["hints"][0]["status"], "selected")
            self.assertEqual(manifest["fetch_attempt_count"], 1)


if __name__ == "__main__":
    unittest.main()

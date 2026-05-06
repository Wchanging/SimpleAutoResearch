from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.literature.arxiv_client import is_rate_limit_message
from simple_ar.literature.bibtex import papers_to_bibtex
from simple_ar.literature.cache import get_cached, put_cache
from simple_ar.literature.models import Paper, normalize_paper_id
from simple_ar.literature.verify import CitationError, validate_citations


class LiteratureTests(unittest.TestCase):
    def test_normalize_paper_id_keeps_citation_safe_text(self) -> None:
        self.assertEqual(normalize_paper_id("https://arxiv.org/abs/cs/9901001v1"), "cs_9901001v1")
        self.assertEqual(normalize_paper_id("2401.12345v2"), "2401.12345v2")

    def test_bibtex_uses_paper_id_as_key(self) -> None:
        paper = Paper(
            id="2401.12345v1",
            title="A Test Paper",
            authors=["Ada Lovelace", "Grace Hopper"],
            abstract="Testing.",
            url="https://arxiv.org/abs/2401.12345v1",
            published="2024-01-01",
            source_id="2401.12345v1",
        )

        bibtex = papers_to_bibtex([paper])

        self.assertIn("@misc{2401.12345v1,", bibtex)
        self.assertIn("archivePrefix = {arXiv}", bibtex)

    def test_validate_citations_rejects_unknown_ids(self) -> None:
        with self.assertRaises(CitationError):
            validate_citations("Known [@paper-1], unknown [@paper-2].", {"paper-1"})

    def test_arxiv_rate_limit_message_detection(self) -> None:
        self.assertTrue(is_rate_limit_message("Page request resulted in HTTP 429"))
        self.assertTrue(is_rate_limit_message("Too many requests"))
        self.assertFalse(is_rate_limit_message("Connection reset"))

    def test_literature_cache_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            rows = [{"id": "paper-1", "title": "Cached Paper"}]

            put_cache("agent simulation", "arxiv", 5, rows, cache_dir=cache_dir)
            cached = get_cached("agent simulation", "arxiv", 5, cache_dir=cache_dir)

            self.assertEqual(cached, rows)


if __name__ == "__main__":
    unittest.main()

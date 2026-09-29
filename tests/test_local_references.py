from __future__ import annotations

import unittest

from simple_ar.literature.bibtex import papers_to_bibtex
from simple_ar.literature.models import Paper
from simple_ar.report.citations import references_markdown


class LocalReferenceTests(unittest.TestCase):
    def test_supplied_path_stays_in_provenance_not_exported_references(self) -> None:
        path = "/private/workspace/notes.md"
        paper = Paper(
            id="local-opaque-id", title="Notes", authors=[], abstract="",
            url=path, source="local_files", source_id=path,
        )

        rendered = references_markdown([paper], {paper.id: 1})
        bibtex = papers_to_bibtex([paper])

        self.assertIn("supplied local document", rendered)
        self.assertNotIn(path, rendered)
        self.assertNotIn(path, bibtex)
        self.assertIn("@misc{local-opaque-id", bibtex)

    def test_remote_reference_retains_real_url(self) -> None:
        paper = Paper(
            id="remote-paper", title="Remote Paper", authors=[], abstract="",
            url="https://example.org/paper", source="arxiv",
        )
        self.assertIn(paper.url, references_markdown([paper]))
        self.assertIn(paper.url, papers_to_bibtex([paper]))


if __name__ == "__main__":
    unittest.main()

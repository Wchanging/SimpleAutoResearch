from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.run_config import load_pipeline_run_config


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


class RunConfigTests(unittest.TestCase):
    def test_research_section_is_flattened_for_pipeline_config(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            config = root / "pipeline.toml"
            notes = root / "notes" / "local.md"
            notes.parent.mkdir()
            notes.write_text("# Local Evidence\n", encoding="utf-8")
            config.write_text(
                """
[run]
topic = "agent simulation"

[research]
mode = "strong"
sources = ["local_files", "openalex"]
queries = ["agent simulation benchmark"]
use_fulltext = true
allow_pdf_download = false
cache = true
index_backend = "sqlite_fts"
local_documents = ["notes/local.md"]

[research.budget]
max_documents = 12
max_chunks = 80
max_context_tokens = 6000
max_llm_calls = 8
""".strip(),
                encoding="utf-8",
            )

            parsed = load_pipeline_run_config(str(config))

            self.assertEqual(parsed["topic"], "agent simulation")
            self.assertEqual(parsed["research_mode"], "strong")
            self.assertEqual(parsed["research_sources"], ["local_files", "openalex"])
            self.assertEqual(parsed["research_queries"], ["agent simulation benchmark"])
            self.assertEqual(parsed["research_use_fulltext"], True)
            self.assertEqual(parsed["research_allow_pdf_download"], False)
            self.assertEqual(parsed["research_cache"], True)
            self.assertEqual(parsed["research_index_backend"], "sqlite_fts")
            self.assertEqual(parsed["research_local_documents"], [str(notes.resolve())])
            self.assertEqual(parsed["research_max_documents"], 12)
            self.assertEqual(parsed["research_max_chunks"], 80)
            self.assertEqual(parsed["research_max_context_tokens"], 6000)
            self.assertEqual(parsed["research_max_llm_calls"], 8)


if __name__ == "__main__":
    unittest.main()

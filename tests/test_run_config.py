from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.run_config import RunConfigError, load_pipeline_run_config


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
debug_artifacts = true

[research]
mode = "strong"
planner = "llm"
sources = ["local_files", "openalex"]
queries = ["agent simulation benchmark"]
auto_query_expansion = true
max_retrieval_rounds = 3
max_queries = 6
required_facets = ["method", "benchmark", "code_link"]
use_fulltext = true
allow_pdf_download = false
max_fulltext_documents = 5
max_pdf_mb = 12
keep_raw_pdf = true
parser_backend = "basic"
cache = true
index_backend = "sqlite_fts"
index_root = ".simple_ar_cache/research_index"
local_documents = ["notes/local.md"]

[research.budget]
max_documents = 12
max_chunks = 80
max_context_tokens = 6000
max_llm_calls = 8
max_follow_up_queries = 4
""".strip(),
                encoding="utf-8",
            )

            parsed = load_pipeline_run_config(str(config))

            self.assertEqual(parsed["topic"], "agent simulation")
            self.assertEqual(parsed["debug_artifacts"], True)
            self.assertEqual(parsed["research_mode"], "strong")
            self.assertEqual(parsed["research_planner"], "llm")
            self.assertEqual(parsed["research_sources"], ["local_files", "openalex"])
            self.assertEqual(parsed["research_queries"], ["agent simulation benchmark"])
            self.assertEqual(parsed["research_auto_query_expansion"], True)
            self.assertEqual(parsed["research_max_retrieval_rounds"], 3)
            self.assertEqual(parsed["research_max_queries"], 6)
            self.assertEqual(parsed["research_required_facets"], ["method", "benchmark", "code_link"])
            self.assertEqual(parsed["research_use_fulltext"], True)
            self.assertEqual(parsed["research_allow_pdf_download"], False)
            self.assertEqual(parsed["research_max_fulltext_documents"], 5)
            self.assertEqual(parsed["research_max_pdf_mb"], 12)
            self.assertEqual(parsed["research_keep_raw_pdf"], True)
            self.assertEqual(parsed["research_parser_backend"], "basic")
            self.assertEqual(parsed["research_cache"], True)
            self.assertEqual(parsed["research_index_backend"], "sqlite_fts")
            self.assertEqual(parsed["research_index_root"], ".simple_ar_cache/research_index")
            self.assertEqual(parsed["research_local_documents"], [str(notes.resolve())])
            self.assertEqual(parsed["research_max_documents"], 12)
            self.assertEqual(parsed["research_max_chunks"], 80)
            self.assertEqual(parsed["research_max_context_tokens"], 6000)
            self.assertEqual(parsed["research_max_llm_calls"], 8)
            self.assertEqual(parsed["research_max_follow_up_queries"], 4)

    def test_run_config_rejects_wrong_section_types(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            config = Path(tmp) / "pipeline.toml"
            config.write_text(
                """
[run]
topic = "agent simulation"

[research]
sources = "openalex"
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaises(RunConfigError) as raised:
                load_pipeline_run_config(str(config))

            self.assertIn("Invalid run config", str(raised.exception))
            self.assertIn("sources", str(raised.exception))


if __name__ == "__main__":
    unittest.main()

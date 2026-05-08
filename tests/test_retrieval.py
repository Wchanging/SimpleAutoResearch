from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.artifacts import read_json, read_jsonl, write_json, write_jsonl, write_text
from simple_ar.retrieval.chunking import build_artifact_chunks
from simple_ar.retrieval.index import build_artifact_index
from simple_ar.retrieval.search import search_artifacts


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


class RetrievalTests(unittest.TestCase):
    def test_artifact_index_records_kind_hash_and_stage(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            write_text(run_dir / "topic.txt", "toy topic\n")
            write_text(run_dir / "01-plan" / "goal.md", "# Goal\n\nImprove accuracy.\n")
            write_json(run_dir / "07-run" / "results.json", {"accuracy": 0.9})
            write_text(run_dir / "__pycache__" / "ignored.pyc", "ignored")
            write_text(run_dir / ".hidden" / "secret.txt", "ignored")

            index = build_artifact_index(run_dir)

            paths = {item["path"] for item in index["artifacts"]}
            self.assertIn("topic.txt", paths)
            self.assertIn("01-plan/goal.md", paths)
            self.assertIn("07-run/results.json", paths)
            self.assertNotIn("__pycache__/ignored.pyc", paths)
            self.assertNotIn(".hidden/secret.txt", paths)

            goal = _artifact(index, "01-plan/goal.md")
            self.assertEqual(goal["kind"], "markdown")
            self.assertEqual(goal["stage"], "plan")
            self.assertEqual(len(goal["sha256"]), 64)
            self.assertTrue((run_dir / "artifact_index.json").is_file())

    def test_chunking_writes_line_addressable_chunks(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            write_text(
                run_dir / "01-plan" / "goal.md",
                "# Goal\n\nImprove accuracy.\n\n## Details\n\nUse local evidence.\n",
            )
            write_text(
                run_dir / "06-code" / "model.py",
                "import math\n\n\ndef score(value):\n    return math.sqrt(value)\n",
            )
            write_jsonl(
                run_dir / "02-search" / "papers.jsonl",
                [{"id": "fixture-001", "title": "Toy paper"}],
            )

            index = build_artifact_index(run_dir)
            chunks = build_artifact_chunks(run_dir, index=index)

            self.assertTrue((run_dir / "artifact_chunks.jsonl").is_file())
            rows = read_jsonl(run_dir / "artifact_chunks.jsonl")
            self.assertEqual(len(chunks), len(rows))
            self.assertTrue(any(row["chunk_kind"] == "markdown-section" for row in rows))
            self.assertTrue(any(row["chunk_kind"] == "python-function" for row in rows))
            self.assertTrue(any(row["chunk_kind"] == "jsonl-row" for row in rows))
            self.assertTrue(all(row["line_start"] <= row["line_end"] for row in rows))

    def test_artifact_search_returns_source_snippets(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            write_text(run_dir / "01-plan" / "goal.md", "# Goal\n\nMeasure accuracy.\n")
            write_json(run_dir / "07-run" / "results.json", {"accuracy": 0.92, "f1": 0.88})

            results = search_artifacts(run_dir, "accuracy", top_k=3)

            self.assertTrue((run_dir / "artifact_search_results.json").is_file())
            saved = read_json(run_dir / "artifact_search_results.json")
            self.assertEqual(saved["query"], "accuracy")
            self.assertGreaterEqual(results["match_count"], 1)
            first = results["matches"][0]
            self.assertIn("path", first)
            self.assertIn("line_start", first)
            self.assertIn("line_end", first)
            self.assertIn("snippet", first)
            self.assertIn("accuracy", first["snippet"].lower())


def _artifact(index: dict[str, object], path: str) -> dict[str, object]:
    for item in index["artifacts"]:  # type: ignore[index]
        if isinstance(item, dict) and item.get("path") == path:
            return item
    raise AssertionError(f"Missing artifact: {path}")


if __name__ == "__main__":
    unittest.main()

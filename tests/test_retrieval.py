from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.core.artifacts import read_json, read_jsonl, write_json, write_jsonl, write_text
from simple_ar.retrieval.chunking import build_artifact_chunks
from simple_ar.retrieval.index import build_artifact_index
from simple_ar.retrieval.search import search_artifacts


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


class RetrievalTests(unittest.TestCase):
    def test_evidence_files_are_not_indexed_as_sources(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            write_text(run_dir / "01-plan" / "goal.md", "# Goal\n\nMeasure accuracy.\n")
            write_json(run_dir / "02-search" / "planning" / "research_plan.json", {"query_plan": {}})
            write_jsonl(run_dir / "02-search" / "documents" / "documents.jsonl", [{"document_id": "doc-1"}])
            write_json(run_dir / "02-search" / "documents" / "cache_manifest.json", {"document_count": 1})
            write_jsonl(run_dir / "02-search" / "research_index" / "chunks.jsonl", [{"chunk_id": "chunk-1"}])
            write_json(run_dir / "02-search" / "research_index" / "index_meta.json", {"chunk_count": 1})
            write_jsonl(run_dir / "03-read" / "cards" / "paper_cards.jsonl", [{"paper_id": "paper-1"}])
            write_jsonl(run_dir / "03-read" / "cards" / "claim_cards.jsonl", [{"claim_id": "claim-1"}])
            write_jsonl(run_dir / "03-read" / "cards" / "method_cards.jsonl", [{"method_id": "method-1"}])
            write_jsonl(run_dir / "03-read" / "cards" / "dataset_cards.jsonl", [{"dataset_id": "dataset-1"}])
            write_jsonl(run_dir / "03-read" / "cards" / "code_links.jsonl", [{"url": "https://example.test/repo"}])
            write_json(run_dir / "04-synthesize" / "evidence" / "evidence_pack.json", {"schema_version": "test"})
            write_text(run_dir / "04-synthesize" / "evidence" / "evidence_pack.md", "# Evidence\n")
            write_text(run_dir / "04-synthesize" / "evidence" / "gap_summary.md", "# Gap\n")
            write_jsonl(run_dir / "04-synthesize" / "evidence" / "idea_candidates.jsonl", [{"idea_id": "idea-1"}])
            write_jsonl(run_dir / "04-synthesize" / "evidence" / "novelty_checks.jsonl", [{"idea_id": "idea-1"}])
            write_json(run_dir / "05-design" / "evidence" / "experiment_contract.json", {"schema_version": "test"})
            write_text(run_dir / "05-design" / "evidence" / "experiment_contract.md", "# Contract\n")
            write_jsonl(run_dir / "02-search" / "traces" / "retrieval_rounds.jsonl", [{"status": "ok"}])
            write_jsonl(run_dir / "02-search" / "traces" / "retrieval_selection.jsonl", [{"decision": "keep"}])
            write_jsonl(run_dir / "03-read" / "review" / "screening_decisions.jsonl", [{"decision": "keep"}])
            write_json(run_dir / "02-search" / "review" / "coverage_report.json", {"status": "partial"})
            write_json(run_dir / "source_plan.json", {"stages": {}})
            write_jsonl(run_dir / "activity_log.jsonl", [{"event": "archived"}])
            write_jsonl(run_dir / "evidence_ledger.jsonl", [{"evidence_id": "archived"}])

            index = build_artifact_index(run_dir, write=False)
            paths = {item["path"] for item in index["artifacts"]}

            self.assertIn("01-plan/goal.md", paths)
            self.assertNotIn("02-search/planning/research_plan.json", paths)
            self.assertNotIn("02-search/documents/documents.jsonl", paths)
            self.assertNotIn("02-search/documents/cache_manifest.json", paths)
            self.assertNotIn("02-search/research_index/chunks.jsonl", paths)
            self.assertNotIn("02-search/research_index/index_meta.json", paths)
            self.assertNotIn("03-read/cards/paper_cards.jsonl", paths)
            self.assertNotIn("03-read/cards/claim_cards.jsonl", paths)
            self.assertNotIn("03-read/cards/method_cards.jsonl", paths)
            self.assertNotIn("03-read/cards/dataset_cards.jsonl", paths)
            self.assertNotIn("03-read/cards/code_links.jsonl", paths)
            self.assertNotIn("04-synthesize/evidence/evidence_pack.json", paths)
            self.assertNotIn("04-synthesize/evidence/evidence_pack.md", paths)
            self.assertNotIn("04-synthesize/evidence/gap_summary.md", paths)
            self.assertNotIn("04-synthesize/evidence/idea_candidates.jsonl", paths)
            self.assertNotIn("04-synthesize/evidence/novelty_checks.jsonl", paths)
            self.assertNotIn("05-design/evidence/experiment_contract.json", paths)
            self.assertNotIn("05-design/evidence/experiment_contract.md", paths)
            self.assertNotIn("02-search/traces/retrieval_rounds.jsonl", paths)
            self.assertNotIn("02-search/traces/retrieval_selection.jsonl", paths)
            self.assertNotIn("03-read/review/screening_decisions.jsonl", paths)
            self.assertNotIn("02-search/review/coverage_report.json", paths)
            self.assertNotIn("source_plan.json", paths)
            self.assertNotIn("activity_log.jsonl", paths)
            self.assertNotIn("evidence_ledger.jsonl", paths)

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
            write_json(run_dir / "01-plan" / "stage_meta.json", {"status": "done"})
            write_json(run_dir / "manifest.json", {"stages": ["plan"]})

            index = build_artifact_index(run_dir)
            chunks = build_artifact_chunks(run_dir, index=index)

            self.assertTrue((run_dir / "artifact_chunks.jsonl").is_file())
            rows = read_jsonl(run_dir / "artifact_chunks.jsonl")
            self.assertEqual(len(chunks), len(rows))
            self.assertTrue(any(row["chunk_kind"] == "markdown-section" for row in rows))
            self.assertTrue(any(row["chunk_kind"] == "python-function" for row in rows))
            self.assertTrue(any(row["chunk_kind"] == "jsonl-row" for row in rows))
            self.assertFalse(any(row["path"].endswith("stage_meta.json") for row in rows))
            self.assertFalse(any(row["path"] == "manifest.json" for row in rows))
            self.assertTrue(all(row["line_start"] <= row["line_end"] for row in rows))

            operational = build_artifact_chunks(run_dir, index=index, write=False, include_operational=True)
            self.assertTrue(any(chunk.path.endswith("stage_meta.json") for chunk in operational))

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
            self.assertFalse(saved["include_operational"])
            self.assertGreaterEqual(results["match_count"], 1)
            first = results["matches"][0]
            self.assertIn("path", first)
            self.assertIn("line_start", first)
            self.assertIn("line_end", first)
            self.assertIn("snippet", first)
            self.assertIn("accuracy", first["snippet"].lower())

    def test_artifact_search_can_include_operational_metadata_when_requested(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            write_text(run_dir / "01-plan" / "goal.md", "# Goal\n\nMeasure accuracy.\n")
            write_json(run_dir / "01-plan" / "stage_meta.json", {"status": "done"})

            default = search_artifacts(run_dir, "status", top_k=5, write=False)
            with_operational = search_artifacts(
                run_dir,
                "status",
                top_k=5,
                write=False,
                include_operational=True,
            )

            self.assertFalse(any(match["path"].endswith("stage_meta.json") for match in default["matches"]))
            self.assertTrue(
                any(match["path"].endswith("stage_meta.json") for match in with_operational["matches"])
            )


def _artifact(index: dict[str, object], path: str) -> dict[str, object]:
    for item in index["artifacts"]:  # type: ignore[index]
        if isinstance(item, dict) and item.get("path") == path:
            return item
    raise AssertionError(f"Missing artifact: {path}")


if __name__ == "__main__":
    unittest.main()

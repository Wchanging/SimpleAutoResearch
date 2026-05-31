from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.core.artifacts import read_json, read_jsonl, write_json, write_jsonl, write_text
from simple_ar.retrieval.evidence import collect_stage_evidence, format_evidence_snippets
from simple_ar.retrieval.index import build_artifact_index


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


class EvidenceTests(unittest.TestCase):
    def test_collect_stage_evidence_writes_plan_activity_and_ledger(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            write_jsonl(
                run_dir / "02-search" / "papers.jsonl",
                [{"id": "paper-1", "title": "Accuracy for toy classification"}],
            )
            write_json(run_dir / "02-search" / "search_meta.json", {"source": "fixture"})
            write_json(run_dir / "manifest.json", {"description": "accuracy should not win here"})

            rows = collect_stage_evidence(
                run_dir,
                "toy classification",
                "read",
                queries=["accuracy classification"],
                top_k=2,
            )

            self.assertTrue((run_dir / "source_plan.json").is_file())
            self.assertTrue((run_dir / "activity_log.jsonl").is_file())
            self.assertTrue((run_dir / "evidence_ledger.jsonl").is_file())
            self.assertGreaterEqual(len(rows), 1)

            plan = read_json(run_dir / "source_plan.json")
            self.assertEqual(plan["retrieval_mode"], "lexical")

            ledger = read_jsonl(run_dir / "evidence_ledger.jsonl")
            first = ledger[0]
            self.assertEqual(first["used_by_stage"], "read")
            self.assertIn("source_path", first)
            self.assertIn("line_start", first)
            self.assertIn("line_end", first)
            self.assertIn("quote_or_summary", first)

            activity_events = {row["event"] for row in read_jsonl(run_dir / "activity_log.jsonl")}
            self.assertIn("source_plan_created", activity_events)
            self.assertIn("retrieval_search_started", activity_events)
            self.assertIn("evidence_recorded", activity_events)
            self.assertTrue(
                all(row["source_path"].startswith("02-search/") for row in ledger),
                ledger,
            )

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
            write_jsonl(run_dir / "02-search" / "cards" / "paper_cards.jsonl", [{"paper_id": "paper-1"}])
            write_jsonl(run_dir / "02-search" / "cards" / "claim_cards.jsonl", [{"claim_id": "claim-1"}])
            write_jsonl(run_dir / "02-search" / "traces" / "retrieval_rounds.jsonl", [{"status": "ok"}])
            write_jsonl(run_dir / "02-search" / "traces" / "screening_decisions.jsonl", [{"decision": "keep"}])
            write_json(run_dir / "02-search" / "review" / "coverage_report.json", {"status": "partial"})
            collect_stage_evidence(run_dir, "toy topic", "read", queries=["accuracy"], top_k=1)

            index = build_artifact_index(run_dir, write=False)
            paths = {item["path"] for item in index["artifacts"]}

            self.assertIn("01-plan/goal.md", paths)
            self.assertNotIn("02-search/planning/research_plan.json", paths)
            self.assertNotIn("02-search/documents/documents.jsonl", paths)
            self.assertNotIn("02-search/documents/cache_manifest.json", paths)
            self.assertNotIn("02-search/research_index/chunks.jsonl", paths)
            self.assertNotIn("02-search/research_index/index_meta.json", paths)
            self.assertNotIn("02-search/cards/paper_cards.jsonl", paths)
            self.assertNotIn("02-search/cards/claim_cards.jsonl", paths)
            self.assertNotIn("02-search/traces/retrieval_rounds.jsonl", paths)
            self.assertNotIn("02-search/traces/screening_decisions.jsonl", paths)
            self.assertNotIn("02-search/review/coverage_report.json", paths)
            self.assertNotIn("source_plan.json", paths)
            self.assertNotIn("activity_log.jsonl", paths)
            self.assertNotIn("evidence_ledger.jsonl", paths)

    def test_format_evidence_snippets_keeps_provenance(self) -> None:
        rows = [
            {
                "evidence_id": "ev-test",
                "source_path": "07-run/results.json",
                "line_start": 4,
                "line_end": 8,
                "query": "accuracy",
                "quote_or_summary": '{"accuracy": 0.9}',
            }
        ]

        rendered = format_evidence_snippets(rows)

        self.assertIn("ev-test", rendered)
        self.assertIn("07-run/results.json:4-8", rendered)
        self.assertIn("accuracy", rendered)


if __name__ == "__main__":
    unittest.main()

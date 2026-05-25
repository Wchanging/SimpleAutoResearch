from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simple_ar.artifacts import read_json, read_jsonl, write_text
from simple_ar.literature.arxiv_client import ArxivRateLimitError, LiteratureSearchError
from simple_ar.literature.models import Paper
from simple_ar.literature.openalex_client import OpenAlexSearchError
from simple_ar.literature.semantic_scholar_client import SemanticScholarSearchError
from simple_ar.pipeline import Context
from simple_ar.stage_handlers import execute_search
from simple_ar.stages import Stage


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


class SearchStageTests(unittest.TestCase):
    def test_live_search_failure_does_not_use_fixture_by_default(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            ctx = _search_context(Path(tmp), allow_fixture_fallback=False)
            with _failed_live_search_patches():
                with self.assertRaises(LiteratureSearchError):
                    execute_search(ctx)

            self.assertFalse((ctx.run_dir / "02-search" / "papers.jsonl").exists())

    def test_fixture_fallback_requires_explicit_flag(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            ctx = _search_context(Path(tmp), allow_fixture_fallback=True)
            with _failed_live_search_patches():
                execute_search(ctx)

            papers = read_jsonl(ctx.run_dir / "02-search" / "papers.jsonl")
            meta = read_json(ctx.run_dir / "02-search" / "search_meta.json")
            source_plan = read_json(ctx.run_dir / "02-search" / "source_plan.json")

            self.assertEqual(papers[0]["source"], "fixture")
            self.assertEqual(meta["status"], "fixture_fallback")
            self.assertTrue(meta["allow_fixture_fallback"])
            self.assertEqual(source_plan["schema_version"], "source_plan.v1")
            self.assertEqual(source_plan["sources"], ["openalex", "semantic_scholar", "arxiv"])
            self.assertTrue((ctx.run_dir / "02-search" / "research_questions.json").exists())
            self.assertTrue((ctx.run_dir / "02-search" / "query_plan.json").exists())

    def test_openalex_success_is_used_before_arxiv(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            ctx = _search_context(Path(tmp), allow_fixture_fallback=False)
            with patch("simple_ar.stage_handlers.OpenAlexSearchClient", _OpenAlexSuccessClient), patch(
                "simple_ar.stage_handlers.ArxivSearchClient", _ArxivShouldNotRunClient
            ), patch(
                "simple_ar.stage_handlers.put_cache", return_value=None
            ):
                execute_search(ctx)

            papers = read_jsonl(ctx.run_dir / "02-search" / "papers.jsonl")
            meta = read_json(ctx.run_dir / "02-search" / "search_meta.json")
            source_plan = read_json(ctx.run_dir / "02-search" / "source_plan.json")

            self.assertEqual(papers[0]["source"], "openalex")
            self.assertEqual(meta["source"], "openalex")
            self.assertEqual(meta["status"], "ok")
            self.assertEqual(meta["source_plan"], "source_plan.json")
            self.assertEqual(meta["query_plan"], "query_plan.json")
            self.assertEqual(source_plan["sources"], ["openalex", "semantic_scholar", "arxiv"])
            self.assertGreaterEqual(len(source_plan["queries"]), 1)

    def test_semantic_scholar_can_compensate_after_openalex_failure(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            ctx = _search_context(Path(tmp), allow_fixture_fallback=False)
            with patch("simple_ar.stage_handlers.OpenAlexSearchClient", _OpenAlexFailingClient), patch(
                "simple_ar.stage_handlers.SemanticScholarSearchClient", _SemanticScholarSuccessClient
            ), patch(
                "simple_ar.stage_handlers.ArxivSearchClient", _ArxivShouldNotRunClient
            ), patch(
                "simple_ar.stage_handlers.get_cached", return_value=None
            ), patch(
                "simple_ar.stage_handlers.put_cache", return_value=None
            ):
                execute_search(ctx)

            papers = read_jsonl(ctx.run_dir / "02-search" / "papers.jsonl")
            meta = read_json(ctx.run_dir / "02-search" / "search_meta.json")
            retrieval_rounds = read_jsonl(ctx.run_dir / "02-search" / "retrieval_rounds.jsonl")

            self.assertEqual(papers[0]["source"], "semantic_scholar")
            self.assertEqual(meta["source"], "semantic_scholar")
            self.assertTrue(any(row["source"] == "openalex" and row["status"] == "error" for row in retrieval_rounds))
            self.assertTrue(any(row["source"] == "semantic_scholar" and row["status"] == "ok" for row in retrieval_rounds))

    def test_local_file_source_can_drive_search_stage(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            local_note = root / "agent_notes.md"
            write_text(
                local_note,
                "# Agent Simulation Notes\n\nAgent simulation benchmarks need grounded evaluation.",
            )
            ctx = _search_context(root, allow_fixture_fallback=False)
            ctx.config.update(
                {
                    "use_arxiv": False,
                    "research_sources": ["local_files"],
                    "research_queries": ["agent simulation"],
                    "research_local_documents": [str(local_note)],
                }
            )

            execute_search(ctx)

            papers = read_jsonl(ctx.run_dir / "02-search" / "papers.jsonl")
            meta = read_json(ctx.run_dir / "02-search" / "search_meta.json")
            source_plan = read_json(ctx.run_dir / "02-search" / "source_plan.json")
            retrieval_rounds = read_jsonl(ctx.run_dir / "02-search" / "retrieval_rounds.jsonl")
            screening = read_jsonl(ctx.run_dir / "02-search" / "screening_decisions.jsonl")

            self.assertEqual(papers[0]["source"], "local_files")
            self.assertEqual(meta["source"], "local_files")
            self.assertEqual(meta["status"], "ok")
            self.assertEqual(meta["retrieval_rounds"], "retrieval_rounds.jsonl")
            self.assertEqual(meta["screening_decisions"], "screening_decisions.jsonl")
            self.assertEqual(source_plan["sources"], ["local_files"])
            self.assertEqual(source_plan["local_documents"], [str(local_note)])
            self.assertIn("research_questions", meta)
            self.assertGreaterEqual(len(retrieval_rounds), 1)
            self.assertIn("title_keywords", retrieval_rounds[0])
            self.assertTrue(any(row["decision"] == "keep" for row in screening))

    def test_llm_research_planner_can_expand_queries_before_source_plan(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            local_note = root / "agent_notes.md"
            write_text(local_note, "# Notes\n\nMulti-agent coding benchmarks use repository tasks.")
            ctx = _search_context(root, allow_fixture_fallback=False)
            ctx.config.update(
                {
                    "use_arxiv": False,
                    "use_llm": True,
                    "research_planner": "llm",
                    "research_sources": ["local_files"],
                    "research_queries": ["multi-agent coding"],
                    "research_local_documents": [str(local_note)],
                    "research_max_queries": 4,
                    "research_required_facets": ["overview", "benchmark", "limitation"],
                }
            )

            with patch("simple_ar.stage_handlers._llm_client", return_value=_FakeResearchPlanner()):
                execute_search(ctx)

            questions = read_json(ctx.run_dir / "02-search" / "research_questions.json")
            query_plan = read_json(ctx.run_dir / "02-search" / "query_plan.json")
            source_plan = read_json(ctx.run_dir / "02-search" / "source_plan.json")
            meta = read_json(ctx.run_dir / "02-search" / "search_meta.json")

            self.assertEqual(questions["planner"], "llm")
            self.assertIn("limitation", [row["facet"] for row in questions["questions"]])
            self.assertEqual(query_plan["planner"], "llm")
            self.assertEqual(len(query_plan["query_specs"]), len(query_plan["queries"]))
            self.assertEqual(query_plan["query_specs"][0]["query"], query_plan["queries"][0])
            self.assertTrue(any(query.startswith("multi-agent code generation") for query in source_plan["queries"]))
            self.assertTrue(query_plan["query_specs"])
            self.assertIn("title_keywords", query_plan["query_specs"][0])
            self.assertEqual(meta["research_planner"], "llm")
            self.assertTrue((ctx.run_dir / "02-search" / "retrieval_rounds.jsonl").exists())
            self.assertTrue((ctx.run_dir / "02-search" / "screening_decisions.jsonl").exists())


class _RateLimitedClient:
    def __init__(self, *, page_size: int) -> None:
        self.page_size = page_size

    def search(self, query: str, *, max_results: int) -> list[object]:
        raise ArxivRateLimitError("Page request resulted in HTTP 429")


class _OpenAlexFailingClient:
    def search(self, query: str, *, max_results: int) -> list[Paper]:
        raise OpenAlexSearchError("OpenAlex unavailable")


class _SemanticScholarFailingClient:
    def search(self, query: str, *, max_results: int) -> list[Paper]:
        raise SemanticScholarSearchError("Semantic Scholar unavailable")


class _SemanticScholarSuccessClient:
    def search(self, query: str, *, max_results: int) -> list[Paper]:
        return [
            Paper(
                id="s2-1",
                title="A Semantic Scholar Metadata Paper",
                authors=["Grace Hopper"],
                abstract="Semantic Scholar metadata.",
                url="https://www.semanticscholar.org/paper/1",
                source="semantic_scholar",
                source_id="1",
            )
        ]


class _OpenAlexSuccessClient:
    def search(self, query: str, *, max_results: int) -> list[Paper]:
        return [
            Paper(
                id="openalex-W1",
                title="A Real Metadata Paper",
                authors=["Ada Lovelace"],
                abstract="OpenAlex metadata.",
                url="https://openalex.org/W1",
                source="openalex",
                source_id="W1",
            )
        ]


class _ArxivShouldNotRunClient:
    def __init__(self, *, page_size: int) -> None:
        self.page_size = page_size

    def search(self, query: str, *, max_results: int) -> list[object]:
        raise AssertionError("arXiv should not run after OpenAlex succeeds")


class _FakeResearchPlanner:
    def ask_json(self, system: str, user: str, *, label: str = "") -> dict[str, object]:
        self.system = system
        self.user = user
        self.label = label
        return {
            "questions": [
                {
                    "question": "What is the landscape of multi-agent coding systems?",
                    "facet": "overview",
                    "rationale": "Scope the area before retrieving papers.",
                    "required": True,
                    "success_criteria": ["Find survey or benchmark-oriented metadata."],
                },
                {
                    "question": "Which benchmarks evaluate multi-agent coding repair?",
                    "facet": "benchmark",
                    "rationale": "Benchmark coverage drives later screening.",
                    "required": True,
                    "success_criteria": ["Find benchmark or metric names."],
                },
            ],
            "query_specs": [
                {
                    "facet": "method",
                    "title_keywords": ["multi-agent", "code generation"],
                    "abstract_keywords": ["LLM", "software engineering", "agent"],
                    "rationale": "Find method papers.",
                    "source_hint": ["arxiv", "openalex"],
                },
                {
                    "facet": "benchmark",
                    "title_keywords": ["software engineering", "agents"],
                    "abstract_keywords": ["benchmark", "repository", "unit tests"],
                    "rationale": "Find benchmark papers.",
                    "source_hint": ["openalex"],
                },
            ],
            "required_facets": ["overview", "benchmark"],
            "negative_terms": ["deployment claims"],
            "rationale": "LLM-expanded retrieval terminology.",
        }


@contextlib.contextmanager
def _failed_live_search_patches():
    with patch("simple_ar.stage_handlers.OpenAlexSearchClient", _OpenAlexFailingClient), patch(
        "simple_ar.stage_handlers.SemanticScholarSearchClient", _SemanticScholarFailingClient
    ), patch(
        "simple_ar.stage_handlers.ArxivSearchClient", _RateLimitedClient
    ), patch("simple_ar.stage_handlers.get_cached", return_value=None):
        yield


def _search_context(tmp: Path, *, allow_fixture_fallback: bool) -> Context:
    run_dir = tmp / "run"
    write_text(run_dir / "01-plan" / "problem.md", "# Problem\nStudy retrieval.\n")
    ctx = Context(
        run_dir,
        "toy topic",
        config={
            "use_arxiv": True,
            "max_papers": 1,
            "allow_fixture_fallback": allow_fixture_fallback,
        },
        current_stage=Stage.SEARCH,
    )
    return ctx


if __name__ == "__main__":
    unittest.main()

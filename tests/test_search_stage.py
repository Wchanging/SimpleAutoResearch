from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simple_ar.core.artifacts import read_json, read_jsonl, write_text
from simple_ar.literature.arxiv_client import ArxivRateLimitError, LiteratureSearchError
from simple_ar.literature.models import Paper
from simple_ar.literature.openalex_client import OpenAlexSearchError
from simple_ar.literature.semantic_scholar_client import SemanticScholarSearchError
from simple_ar.core.pipeline import Context
from simple_ar.pipeline_stages.research import execute_search
from simple_ar.core.stages import Stage


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
            research_plan = read_json(ctx.run_dir / "02-search" / "planning" / "research_plan.json")
            source_plan = research_plan["source_plan"]
            documents = read_jsonl(ctx.run_dir / "02-search" / "documents" / "documents.jsonl")
            cache_manifest = read_json(ctx.run_dir / "02-search" / "documents" / "cache_manifest.json")
            fulltext_manifest = read_json(ctx.run_dir / "02-search" / "documents" / "fulltext_manifest.json")
            chunks = read_jsonl(ctx.run_dir / "02-search" / "research_index" / "chunks.jsonl")
            index_meta = read_json(ctx.run_dir / "02-search" / "research_index" / "index_meta.json")

            self.assertEqual(papers[0]["source"], "fixture")
            self.assertEqual(meta["status"], "fixture_fallback")
            self.assertTrue(meta["allow_fixture_fallback"])
            self.assertEqual(research_plan["schema_version"], "research_plan.v1")
            self.assertEqual(source_plan["schema_version"], "source_plan.v1")
            self.assertEqual(source_plan["sources"], ["openalex", "semantic_scholar", "arxiv"])
            self.assertEqual(meta["documents"], "documents/documents.jsonl")
            self.assertEqual(documents[0]["extraction_status"], "metadata_only")
            self.assertEqual(cache_manifest["document_count"], len(documents))
            self.assertEqual(meta["fulltext_manifest"], "documents/fulltext_manifest.json")
            self.assertEqual(fulltext_manifest["document_count"], len(documents))
            self.assertEqual(meta["chunks"], "research_index/chunks.jsonl")
            self.assertEqual(index_meta["chunk_count"], len(chunks))
            self.assertNotIn("paper_cards", meta)
            self.assertFalse((ctx.run_dir / "02-search" / "cards" / "paper_cards.jsonl").exists())

    def test_openalex_success_is_used_before_arxiv(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            ctx = _search_context(Path(tmp), allow_fixture_fallback=False)
            with patch("simple_ar.pipeline_stages.research.OpenAlexSearchClient", _OpenAlexSuccessClient), patch(
                "simple_ar.pipeline_stages.research.ArxivSearchClient", _ArxivShouldNotRunClient
            ), patch(
                "simple_ar.pipeline_stages.research.put_cache", return_value=None
            ):
                execute_search(ctx)

            papers = read_jsonl(ctx.run_dir / "02-search" / "papers.jsonl")
            meta = read_json(ctx.run_dir / "02-search" / "search_meta.json")
            research_plan = read_json(ctx.run_dir / "02-search" / "planning" / "research_plan.json")
            source_plan = research_plan["source_plan"]

            self.assertEqual(papers[0]["source"], "openalex")
            self.assertEqual(meta["source"], "openalex")
            self.assertEqual(meta["status"], "ok")
            self.assertEqual(meta["research_plan"], "planning/research_plan.json")
            self.assertEqual(source_plan["sources"], ["openalex", "semantic_scholar", "arxiv"])
            self.assertGreaterEqual(len(source_plan["queries"]), 1)

    def test_semantic_scholar_can_compensate_after_openalex_failure(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            ctx = _search_context(Path(tmp), allow_fixture_fallback=False)
            with patch("simple_ar.pipeline_stages.research.OpenAlexSearchClient", _OpenAlexFailingClient), patch(
                "simple_ar.pipeline_stages.research.SemanticScholarSearchClient", _SemanticScholarSuccessClient
            ), patch(
                "simple_ar.pipeline_stages.research.ArxivSearchClient", _ArxivShouldNotRunClient
            ), patch(
                "simple_ar.pipeline_stages.research.get_cached", return_value=None
            ), patch(
                "simple_ar.pipeline_stages.research.put_cache", return_value=None
            ):
                execute_search(ctx)

            papers = read_jsonl(ctx.run_dir / "02-search" / "papers.jsonl")
            meta = read_json(ctx.run_dir / "02-search" / "search_meta.json")
            retrieval_rounds = read_jsonl(ctx.run_dir / "02-search" / "traces" / "retrieval_rounds.jsonl")

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
            research_plan = read_json(ctx.run_dir / "02-search" / "planning" / "research_plan.json")
            source_plan = research_plan["source_plan"]
            retrieval_rounds = read_jsonl(ctx.run_dir / "02-search" / "traces" / "retrieval_rounds.jsonl")
            selection = read_jsonl(ctx.run_dir / "02-search" / "traces" / "retrieval_selection.jsonl")
            documents = read_jsonl(ctx.run_dir / "02-search" / "documents" / "documents.jsonl")
            chunks = read_jsonl(ctx.run_dir / "02-search" / "research_index" / "chunks.jsonl")
            index_meta = read_json(ctx.run_dir / "02-search" / "research_index" / "index_meta.json")
            fulltext_manifest = read_json(ctx.run_dir / "02-search" / "documents" / "fulltext_manifest.json")

            self.assertEqual(papers[0]["source"], "local_files")
            self.assertEqual(meta["source"], "local_files")
            self.assertEqual(meta["status"], "ok")
            self.assertEqual(meta["retrieval_rounds"], "traces/retrieval_rounds.jsonl")
            self.assertEqual(meta["retrieval_selection"], "traces/retrieval_selection.jsonl")
            self.assertEqual(source_plan["sources"], ["local_files"])
            self.assertEqual(source_plan["local_documents"], [str(local_note)])
            self.assertIn("research_plan", meta)
            self.assertGreaterEqual(len(retrieval_rounds), 1)
            self.assertIn("title_keywords", retrieval_rounds[0])
            self.assertTrue(any(row["decision"] == "keep" for row in selection))
            self.assertEqual(documents[0]["extraction_status"], "parsed")
            self.assertEqual(documents[0]["parser"], "plain_text")
            self.assertGreaterEqual(len(chunks), 1)
            self.assertEqual(index_meta["backend"], "sqlite_fts")
            self.assertFalse((ctx.run_dir / "02-search" / "cards" / "paper_cards.jsonl").exists())
            self.assertEqual(fulltext_manifest["documents"][0]["hints"][0]["status"], "hint_only")

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

            with patch("simple_ar.pipeline_stages.research._llm_client", return_value=_FakeResearchPlanner()):
                execute_search(ctx)

            research_plan = read_json(ctx.run_dir / "02-search" / "planning" / "research_plan.json")
            questions = research_plan["research_questions"]
            query_plan = research_plan["query_plan"]
            source_plan = research_plan["source_plan"]
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
            self.assertTrue((ctx.run_dir / "02-search" / "traces" / "retrieval_rounds.jsonl").exists())
            self.assertTrue((ctx.run_dir / "02-search" / "traces" / "retrieval_selection.jsonl").exists())

    def test_llm_research_planner_still_runs_when_query_expansion_is_disabled(self) -> None:
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
                    "research_auto_query_expansion": False,
                    "research_max_queries": 1,
                    "research_required_facets": ["overview", "benchmark"],
                }
            )

            fake = _FakeResearchPlanner()
            with patch("simple_ar.pipeline_stages.research._llm_client", return_value=fake):
                execute_search(ctx)

            research_plan = read_json(ctx.run_dir / "02-search" / "planning" / "research_plan.json")
            query_plan = research_plan["query_plan"]
            source_plan = research_plan["source_plan"]
            meta = read_json(ctx.run_dir / "02-search" / "search_meta.json")

            self.assertEqual(fake.label, "research-planner")
            self.assertEqual(query_plan["planner"], "llm")
            self.assertEqual(query_plan["auto_expansion"], False)
            self.assertEqual(len(query_plan["queries"]), 1)
            self.assertEqual(len(source_plan["queries"]), 1)
            self.assertEqual(meta["research_planner"], "llm")

    def test_coverage_gap_can_trigger_follow_up_retrieval_round(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            ctx = _search_context(Path(tmp), allow_fixture_fallback=False)
            ctx.config.update(
                {
                    "research_sources": ["openalex"],
                    "research_queries": ["agent overview"],
                    "research_required_facets": ["overview", "method"],
                    "research_auto_query_expansion": False,
                    "research_max_retrieval_rounds": 2,
                    "research_max_documents": 2,
                    "research_max_queries": 1,
                }
            )

            with patch("simple_ar.pipeline_stages.research.OpenAlexSearchClient", _CoverageOpenAlexClient), patch(
                "simple_ar.pipeline_stages.research.put_cache", return_value=None
            ):
                execute_search(ctx)

            retrieval_rounds = read_jsonl(ctx.run_dir / "02-search" / "traces" / "retrieval_rounds.jsonl")
            coverage = read_json(ctx.run_dir / "02-search" / "review" / "coverage_report.json")
            meta = read_json(ctx.run_dir / "02-search" / "search_meta.json")

            self.assertTrue(any(row["round"] == 2 for row in retrieval_rounds))
            self.assertIn("coverage_report", meta)
            self.assertEqual(coverage["retrieval"]["executed_rounds"], 2)


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


class _CoverageOpenAlexClient:
    def search(self, query: str, *, max_results: int) -> list[Paper]:
        if "method" in query or "architecture" in query:
            return [
                Paper(
                    id="method-paper",
                    title="Agent Method Architecture",
                    authors=[],
                    abstract="A method architecture for agent systems.",
                    url="https://example.test/method",
                    source="openalex",
                    source_id="W2",
                )
            ]
        return [
            Paper(
                id="overview-paper",
                title="Agent Overview",
                authors=[],
                abstract="An overview of agent systems.",
                url="https://example.test/overview",
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
    with patch("simple_ar.pipeline_stages.research.OpenAlexSearchClient", _OpenAlexFailingClient), patch(
        "simple_ar.pipeline_stages.research.SemanticScholarSearchClient", _SemanticScholarFailingClient
    ), patch(
        "simple_ar.pipeline_stages.research.ArxivSearchClient", _RateLimitedClient
    ), patch("simple_ar.pipeline_stages.research.get_cached", return_value=None):
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

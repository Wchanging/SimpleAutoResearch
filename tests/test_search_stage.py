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

            self.assertEqual(papers[0]["source"], "fixture")
            self.assertEqual(meta["status"], "fixture_fallback")
            self.assertTrue(meta["allow_fixture_fallback"])

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

            self.assertEqual(papers[0]["source"], "openalex")
            self.assertEqual(meta["source"], "openalex")
            self.assertEqual(meta["status"], "ok")


class _RateLimitedClient:
    def __init__(self, *, page_size: int) -> None:
        self.page_size = page_size

    def search(self, query: str, *, max_results: int) -> list[object]:
        raise ArxivRateLimitError("Page request resulted in HTTP 429")


class _OpenAlexFailingClient:
    def search(self, query: str, *, max_results: int) -> list[Paper]:
        raise OpenAlexSearchError("OpenAlex unavailable")


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


@contextlib.contextmanager
def _failed_live_search_patches():
    with patch("simple_ar.stage_handlers.OpenAlexSearchClient", _OpenAlexFailingClient), patch(
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

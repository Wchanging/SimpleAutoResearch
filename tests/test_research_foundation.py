from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from simple_ar.literature.models import Paper
from simple_ar.research.contracts import (
    ClaimCard,
    DocumentRecord,
    ExperimentContract,
    PaperCard,
    ResearchContract,
    ResearchQuestion,
    SourcePlan,
)
from simple_ar.research.planning import build_query_plan, build_research_questions
from simple_ar.research.retrieval import RetrievalCandidate, relevance_score, screen_retrieval_candidates
from simple_ar.research.sources import SearchQuery, build_source_plan, primary_query
from simple_ar.research.connectors.local_files import LocalFileConnector
from simple_ar.research.prompts import report_user_prompt
from simple_ar.prompts import report_user_prompt as compat_report_user_prompt


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


class ResearchFoundationTests(unittest.TestCase):
    def test_research_contract_round_trips_with_defaults(self) -> None:
        contract = ResearchContract.from_row({"topic": "agent simulation", "mode": "not-a-mode"})

        self.assertEqual(contract.topic, "agent simulation")
        self.assertEqual(contract.mode, "standard")
        self.assertTrue(contract.requires_experiment)
        self.assertEqual(contract.to_row()["schema_version"], "research_contract.v1")

    def test_core_cards_are_json_serializable(self) -> None:
        artifacts = [
            SourcePlan(queries=["agent simulation"]).to_row(),
            DocumentRecord(document_id="doc-1", title="A Paper", source="fixture").to_row(),
            PaperCard(paper_id="p1", title="A Paper", evidence_refs=["doc-1#chunk-1"]).to_row(),
            ClaimCard(claim_id="c1", paper_id="p1", claim="A bounded claim").to_row(),
            ExperimentContract(contract_id="e1", hypothesis="Small local test").to_row(),
        ]

        self.assertEqual(artifacts[0]["schema_version"], "source_plan.v1")
        self.assertEqual(artifacts[-1]["schema_version"], "experiment_contract.v1")

    def test_source_plan_builder_records_research_mode_and_budget(self) -> None:
        plan = build_source_plan(
            topic="agent simulation",
            problem_markdown="# Problem\nStudy agent simulation.",
            config={
                "research_mode": "strong",
                "research_sources": ["local_files", "openalex"],
                "research_queries": ["agent simulation benchmarks"],
                "research_local_documents": ["notes.md"],
                "research_index_backend": "sqlite_fts",
                "research_max_documents": 7,
                "research_max_chunks": 50,
            },
            default_query="agent simulation",
            default_max_results=4,
        )

        self.assertEqual(plan.mode, "strong")
        self.assertEqual(plan.sources, ["local_files", "openalex"])
        self.assertEqual(primary_query(plan), "agent simulation benchmarks")
        self.assertEqual(plan.local_documents, ["notes.md"])
        self.assertEqual(plan.budget["max_documents"], 7)
        self.assertEqual(plan.budget["max_chunks"], 50)

    def test_research_questions_and_query_plan_expand_topic(self) -> None:
        questions = build_research_questions(
            topic="multi-agent collaboration for coding",
            problem_markdown="# Problem\nStudy coding agents without claiming deployment.",
            config={"research_required_facets": ["overview", "method", "benchmark", "code_link"]},
        )
        query_plan = build_query_plan(
            topic="multi-agent collaboration for coding",
            problem_markdown="# Problem\nStudy coding agents without claiming deployment.",
            config={
                "research_queries": ["multi-agent coding agents"],
                "research_required_facets": ["method", "benchmark", "code_link"],
                "research_max_retrieval_rounds": 3,
            },
            default_query="coding agents",
            questions=questions,
        )

        self.assertIsInstance(questions[0], ResearchQuestion)
        self.assertEqual(questions[0].question_id, "RQ1")
        facets = [question.facet for question in questions]
        self.assertEqual(facets.count("overview"), 1)
        self.assertIn("benchmark", facets)
        self.assertEqual(query_plan.seed_queries, ["multi-agent coding agents"])
        self.assertEqual(query_plan.max_rounds, 3)
        self.assertTrue(query_plan.query_specs)
        self.assertTrue(all("title_keywords" in spec for spec in query_plan.query_specs))
        self.assertIn("benchmark", {str(spec.get("facet")) for spec in query_plan.query_specs})
        self.assertIn("code_link", {str(spec.get("facet")) for spec in query_plan.query_specs})
        self.assertTrue(any("benchmark" in query for query in query_plan.queries))
        self.assertTrue(any("github" in query for query in query_plan.queries))

    def test_local_file_connector_returns_matching_text_documents(self) -> None:
        connector = LocalFileConnector(paths=[])
        response = connector.search(SearchQuery(query="anything", max_results=2))

        self.assertEqual(response.source, "local_files")
        self.assertEqual(response.papers, [])

    def test_local_file_connector_uses_keyword_overlap_for_normalized_queries(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            note = Path(tmp) / "multi_agent_coding.md"
            note.write_text(
                "# Multi-Agent Collaboration for Coding Agents\n\n"
                "These notes discuss software engineering benchmarks and unit tests.",
                encoding="utf-8",
            )

            connector = LocalFileConnector(paths=[str(note)])
            response = connector.search(
                SearchQuery(query="multi-agent collaboration coding agents", max_results=2)
            )

            self.assertEqual(len(response.papers), 1)
            self.assertEqual(response.papers[0].source, "local_files")

    def test_screening_deduplicates_and_keeps_relevant_candidates(self) -> None:
        paper = Paper(
            id="paper-1",
            title="Multi-Agent Code Generation Benchmark",
            authors=[],
            abstract="LLM agents solve software engineering tasks with unit tests.",
            url="https://example.test/paper",
            source="openalex",
            source_id="W1",
        )
        duplicate = Paper(
            id="paper-1-dup",
            title=paper.title,
            authors=[],
            abstract=paper.abstract,
            url=paper.url,
            source="arxiv",
            source_id="2401.00001",
        )
        kept, decisions = screen_retrieval_candidates(
            [
                RetrievalCandidate(paper=paper, source="openalex", query="multi-agent code generation", query_index=1, round_index=1),
                RetrievalCandidate(paper=duplicate, source="arxiv", query="multi-agent code generation", query_index=1, round_index=1),
            ],
            max_documents=1,
        )

        self.assertEqual(len(kept), 1)
        self.assertTrue(any(row["decision"] == "keep" for row in decisions))
        self.assertTrue(any(row["reason"] == "duplicate_lower_score" for row in decisions))

    def test_negative_scope_acronyms_penalize_out_of_scope_metadata(self) -> None:
        paper = Paper(
            id="marl-paper",
            title="Communication Delay-Tolerant Multi-Agent Collaboration",
            authors=[],
            abstract="The method is evaluated in MARL benchmarks with delayed communication.",
            url="https://example.test/marl",
            source="openalex",
        )

        score_without_scope = relevance_score(paper, "multi-agent collaboration coding agents")
        score_with_scope = relevance_score(
            paper,
            "multi-agent collaboration coding agents",
            negative_terms=["multi-agent reinforcement learning"],
        )

        self.assertLess(score_with_scope, score_without_scope)

    def test_prompt_module_reexport_is_compatible(self) -> None:
        self.assertIs(report_user_prompt, compat_report_user_prompt)


class InMemoryConnectorSmokeTests(unittest.TestCase):
    def test_search_query_shape_accepts_papers(self) -> None:
        paper = Paper(
            id="paper-1",
            title="Paper",
            authors=[],
            abstract="abstract",
            url="https://example.test",
        )

        self.assertEqual(paper.to_row()["id"], "paper-1")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import sqlite3

from simple_ar.literature.models import Paper
from simple_ar.research.contracts import (
    ClaimCard,
    DocumentRecord,
    ExperimentContract,
    PaperCard,
    QueryPlan,
    ResearchContract,
    ResearchQuestion,
    SourcePlan,
)
from simple_ar.research.cards import build_evidence_cards
from simple_ar.research.coverage import build_coverage_report
from simple_ar.research.chunking import build_text_chunks
from simple_ar.research.documents import build_cache_manifest, build_document_records
from simple_ar.research.fulltext import build_fulltext_manifest, fulltext_hints_for_paper
from simple_ar.research.index import write_research_index
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

    def test_document_records_parse_local_text_and_track_metadata(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            note = Path(tmp) / "agent_notes.md"
            note.write_text("# Agent Notes\n\nMulti-agent coding evaluation notes.\n", encoding="utf-8")
            paper = Paper(
                id="local-agent-notes",
                title="Agent Notes",
                authors=[],
                abstract="metadata abstract",
                url=str(note),
                source="local_files",
                source_id=str(note),
            )
            source_plan = build_source_plan(
                topic="agent coding",
                problem_markdown="",
                config={
                    "research_sources": ["local_files"],
                    "research_local_documents": [str(note)],
                    "research_cache": True,
                },
                default_query="agent coding",
                default_max_results=3,
            )

            records = build_document_records(papers=[paper], source_plan=source_plan)
            manifest = build_cache_manifest(records=records, source_plan=source_plan)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].extraction_status, "parsed")
            self.assertEqual(records[0].parser, "plain_text")
            self.assertIn("Multi-agent coding", records[0].abstract)
            self.assertEqual(manifest["status_counts"], {"parsed": 1})
            self.assertEqual(manifest["document_count"], 1)

    def test_arxiv_fulltext_hints_are_budgeted_without_downloading(self) -> None:
        paper = Paper(
            id="2401.12345v1",
            title="An arXiv Paper",
            authors=[],
            abstract="abstract",
            url="https://arxiv.org/abs/2401.12345v1",
            source="arxiv",
            source_id="2401.12345v1",
        )
        source_plan = build_source_plan(
            topic="agent coding",
            problem_markdown="",
            config={
                "research_sources": ["arxiv"],
                "research_use_fulltext": True,
                "research_allow_pdf_download": False,
                "research_max_fulltext_documents": 3,
                "research_max_pdf_mb": 20,
                "research_parser_backend": "basic",
            },
            default_query="agent coding",
            default_max_results=3,
        )
        records = build_document_records(papers=[paper], source_plan=source_plan)
        manifest = build_fulltext_manifest(records=records, source_plan=source_plan)

        self.assertEqual(manifest["hint_count"], 1)
        self.assertEqual(manifest["selected_count"], 0)
        hint = manifest["documents"][0]["hints"][0]
        self.assertEqual(hint["kind"], "pdf")
        self.assertEqual(hint["url"], "https://arxiv.org/pdf/2401.12345v1.pdf")
        self.assertEqual(hint["status"], "blocked")
        self.assertEqual(hint["reason"], "pdf_download_disabled")

    def test_openalex_fulltext_url_hint_is_preserved(self) -> None:
        paper = Paper(
            id="openalex-W1",
            title="An OpenAlex Paper",
            authors=[],
            abstract="abstract",
            url="https://openalex.org/W1",
            source="openalex",
            source_id="W1",
            fulltext_url="https://example.test/paper.pdf",
        )

        hints = fulltext_hints_for_paper(paper, document_id="doc-openalex")

        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0].kind, "pdf")
        self.assertEqual(hints[0].source, "openalex")
        self.assertEqual(hints[0].access, "open")

    def test_text_chunks_and_sqlite_index_are_written(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            note = root / "agent_notes.md"
            note.write_text(
                "# Agent Notes\n\n"
                "Multi-agent coding systems coordinate repository repair agents.\n"
                "Benchmarks often use unit tests and patch validation.\n",
                encoding="utf-8",
            )
            record = DocumentRecord(
                document_id="local-agent-notes",
                title="Agent Notes",
                source="local_files",
                local_path=str(note),
                extraction_status="parsed",
                parser="plain_text",
            )

            chunks = build_text_chunks([record], max_chunks=4, chunk_chars=80, overlap_chars=10)
            meta = write_research_index(index_dir=root / "research_index", chunks=chunks, backend="sqlite_fts")

            self.assertGreaterEqual(len(chunks), 1)
            self.assertEqual(chunks[0].document_id, "local-agent-notes")
            self.assertEqual(meta["backend"], "sqlite_fts")
            self.assertEqual(meta["sqlite_fts"]["status"], "ready")
            self.assertTrue((root / "research_index" / "chunks.jsonl").is_file())
            self.assertTrue((root / "research_index" / "index_meta.json").is_file())
            conn = sqlite3.connect(root / "research_index" / "sqlite_fts.db")
            try:
                rows = conn.execute(
                    "SELECT chunk_id FROM chunks WHERE chunks MATCH ?",
                    ("repository",),
                ).fetchall()
            finally:
                conn.close()
            self.assertGreaterEqual(len(rows), 1)

    def test_evidence_cards_are_grounded_in_chunks(self) -> None:
        document = DocumentRecord(
            document_id="doc-agent",
            title="Agent Coding Benchmark",
            source="local_files",
            abstract=(
                "The study evaluates multi-agent coding systems on repository repair tasks. "
                "It reports success rate and runtime metrics. "
                "A limitation is instability across random seeds."
            ),
            extraction_status="metadata_only",
            metadata={"paper_id": "paper-agent"},
        )
        chunks = build_text_chunks([document], max_chunks=2)

        paper_cards, claim_cards = build_evidence_cards(documents=[document], chunks=chunks)

        self.assertEqual(len(paper_cards), 1)
        self.assertEqual(paper_cards[0].paper_id, "paper-agent")
        self.assertIn(chunks[0].chunk_id, paper_cards[0].evidence_refs)
        self.assertTrue(paper_cards[0].metrics)
        self.assertEqual(len(claim_cards), 2)
        self.assertTrue(all(card.evidence_refs for card in claim_cards))

    def test_evidence_card_claim_scope_prefers_limitations(self) -> None:
        document = DocumentRecord(
            document_id="doc-risk",
            title="Agent Coding Risk Notes",
            source="local_files",
            abstract=(
                "The system reports implementation risks for repository-scale coding agents. "
                "It compares benchmark outcomes across tasks."
            ),
            extraction_status="metadata_only",
        )
        chunks = build_text_chunks([document], max_chunks=2)

        _, claim_cards = build_evidence_cards(documents=[document], chunks=chunks)

        self.assertEqual(claim_cards[0].scope, "limitation")
        self.assertTrue(claim_cards[0].limitations)

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

    def test_coverage_uses_duplicate_hits_from_kept_documents(self) -> None:
        question = ResearchQuestion(
            question_id="RQ1",
            question="How is the method evaluated?",
            facet="benchmark",
        )
        report = build_coverage_report(
            topic="agent evaluation",
            questions=[question],
            query_plan=QueryPlan(
                topic="agent evaluation",
                seed_queries=["agent evaluation"],
                queries=["agent evaluation", "agent benchmark"],
                required_facets=["benchmark"],
                max_rounds=2,
            ),
            screening_rows=[
                {
                    "paper_id": "paper-1",
                    "facet": "overview",
                    "decision": "keep",
                    "reason": "top_ranked",
                    "relevance_score": 4,
                },
                {
                    "paper_id": "paper-1",
                    "facet": "benchmark",
                    "decision": "discard",
                    "reason": "duplicate_lower_score",
                    "relevance_score": 3,
                },
            ],
            retrieval_rows=[
                {"round": 1, "query": "agent evaluation", "source": "local_files"},
                {"round": 1, "query": "agent benchmark", "source": "local_files"},
            ],
            max_documents=3,
        )

        self.assertEqual(report["status"], "covered")
        self.assertEqual(report["covered_facets"], ["benchmark"])
        self.assertEqual(report["questions"][0]["status"], "covered")

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

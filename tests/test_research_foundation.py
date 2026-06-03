from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import sqlite3
from unittest.mock import patch

from simple_ar.literature.models import Paper
from simple_ar.literature.openalex_client import _fulltext_url_from_openalex
from simple_ar.research.contracts import (
    ClaimCard,
    DocumentRecord,
    ExperimentContract,
    PaperCard,
    QueryPlan,
    ResearchContract,
    ResearchQuestion,
    SourcePlan,
    TextChunk,
)
from simple_ar.research.evidence.cards import build_dataset_cards, build_evidence_cards, build_method_cards
from simple_ar.research.evidence.coverage import build_coverage_report
from simple_ar.research.evidence.derivation import (
    build_experiment_contract,
    build_idea_candidates,
    build_novelty_checks,
    build_tool_context,
)
from simple_ar.research.evidence.pack import build_evidence_pack
from simple_ar.research.store.chunking import build_text_chunks
from simple_ar.research.documents.records import build_cache_manifest, build_document_records
from simple_ar.research.documents.extractors import apply_fulltext_extraction
from simple_ar.research.documents.fulltext import build_fulltext_manifest, fulltext_hints_for_paper
from simple_ar.research.documents.sections import build_document_sections
from simple_ar.research.store.index import write_research_index
from simple_ar.research.planning.planner import build_query_plan, build_research_questions
from simple_ar.research.evidence.retrieval import RetrievalCandidate, relevance_score, screen_retrieval_candidates
from simple_ar.research.sources.base import SearchQuery, build_source_plan, primary_query
from simple_ar.research.connectors.local_files import LocalFileConnector
from simple_ar.research.prompts import report_user_prompt
from simple_ar.research.prompts import report_user_prompt as compat_report_user_prompt


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
                "research_index_root": ".simple_ar_cache/research_index",
                "research_max_documents": 7,
                "research_max_chunks": 50,
                "research_novelty_backend": "local",
            },
            default_query="agent simulation",
            default_max_results=4,
        )

        self.assertEqual(plan.mode, "strong")
        self.assertEqual(plan.sources, ["local_files", "openalex"])
        self.assertEqual(primary_query(plan), "agent simulation benchmarks")
        self.assertEqual(plan.local_documents, ["notes.md"])
        self.assertEqual(plan.index_root, ".simple_ar_cache/research_index")
        self.assertEqual(plan.budget["max_documents"], 7)
        self.assertEqual(plan.budget["max_chunks"], 50)
        self.assertEqual(plan.budget["novelty_backend"], "local")

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

    def test_local_fulltext_is_marked_cached_when_enabled(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            note = root / "agent_notes.md"
            note.write_text("# Agent Notes\n\nLocal full text.\n", encoding="utf-8")
            source_plan = build_source_plan(
                topic="agent coding",
                problem_markdown="",
                config={
                    "research_sources": ["local_files"],
                    "research_local_documents": [str(note)],
                    "research_use_fulltext": True,
                    "research_allow_pdf_download": False,
                },
                default_query="agent coding",
                default_max_results=3,
            )
            records = build_document_records(papers=[], source_plan=source_plan)
            manifest = build_fulltext_manifest(
                records=records,
                source_plan=source_plan,
                cache_dir=root / "fulltext_cache",
            )

            self.assertEqual(manifest["selected_count"], 1)
            hint = manifest["documents"][0]["hints"][0]
            self.assertEqual(hint["status"], "cached")
            self.assertEqual(hint["reason"], "local_fulltext_available")
            self.assertEqual(hint["local_path"], str(note))

    def test_cached_text_fulltext_is_parsed_into_records(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            note = root / "agent_notes.txt"
            note.write_text("This paper proposes a coding agent benchmark with runtime metrics.", encoding="utf-8")
            source_plan = build_source_plan(
                topic="agent coding",
                problem_markdown="",
                config={
                    "research_sources": ["local_files"],
                    "research_local_documents": [str(note)],
                    "research_use_fulltext": True,
                },
                default_query="agent coding",
                default_max_results=3,
            )
            records = build_document_records(papers=[], source_plan=source_plan)
            fulltext_manifest = build_fulltext_manifest(
                records=records,
                source_plan=source_plan,
                cache_dir=root / "fulltext_cache",
            )

            updated, extraction_manifest = apply_fulltext_extraction(
                records=records,
                fulltext_manifest=fulltext_manifest,
                source_plan=source_plan,
                extraction_dir=root / "extracted_text",
            )

            self.assertEqual(updated[0].extraction_status, "parsed")
            self.assertEqual(updated[0].parser, "plain_text")
            self.assertEqual(extraction_manifest["parsed_count"], 1)

    def test_unstructured_parser_backend_fails_manifest_only_when_missing(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            note = root / "paper.md"
            note.write_text("# Paper\n\nFull text.", encoding="utf-8")
            record = DocumentRecord(
                document_id="doc-md",
                title="Markdown Paper",
                source="local_files",
                local_path=str(note),
                extraction_status="metadata_only",
            )
            source_plan = build_source_plan(
                topic="agent coding",
                problem_markdown="",
                config={
                    "research_sources": ["local_files"],
                    "research_use_fulltext": True,
                    "research_parser_backend": "unstructured",
                },
                default_query="agent coding",
                default_max_results=3,
            )
            fulltext_manifest = {
                "enabled": True,
                "documents": [
                    {
                        "document_id": "doc-md",
                        "hints": [
                            {
                                "status": "cached",
                                "kind": "text",
                                "local_path": str(note),
                            }
                        ],
                    }
                ],
            }

            with patch.dict("sys.modules", {"unstructured": None}):
                updated, extraction_manifest = apply_fulltext_extraction(
                    records=[record],
                    fulltext_manifest=fulltext_manifest,
                    source_plan=source_plan,
                    extraction_dir=root / "extracted_text",
                )

            self.assertEqual(updated[0].extraction_status, "metadata_only")
            self.assertEqual(extraction_manifest["status_counts"], {"failed": 1})
            self.assertIn("unstructured is not installed", extraction_manifest["documents"][0]["reason"])

    def test_pdf_fulltext_parser_failure_is_manifest_only(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            pdf = root / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\nnot a real pdf")
            record = DocumentRecord(
                document_id="doc-pdf",
                title="PDF Paper",
                source="local_files",
                local_path=str(pdf),
                extraction_status="metadata_only",
            )
            source_plan = build_source_plan(
                topic="agent coding",
                problem_markdown="",
                config={"research_sources": ["local_files"], "research_use_fulltext": True},
                default_query="agent coding",
                default_max_results=3,
            )
            fulltext_manifest = {
                "enabled": True,
                "documents": [
                    {
                        "document_id": "doc-pdf",
                        "hints": [
                            {
                                "status": "cached",
                                "kind": "pdf",
                                "local_path": str(pdf),
                            }
                        ],
                    }
                ],
            }

            with patch("simple_ar.research.documents.extractors._read_pdf", side_effect=RuntimeError("parser failed")):
                updated, extraction_manifest = apply_fulltext_extraction(
                    records=[record],
                    fulltext_manifest=fulltext_manifest,
                    source_plan=source_plan,
                    extraction_dir=root / "extracted_text",
                )

            self.assertEqual(updated[0].extraction_status, "metadata_only")
            self.assertEqual(extraction_manifest["status_counts"], {"failed": 1})
            self.assertIn("parser failed", extraction_manifest["documents"][0]["reason"])

    def test_pdf_fulltext_extraction_repairs_common_mojibake(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            pdf = root / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\nmock")
            record = DocumentRecord(
                document_id="doc-pdf",
                title="PDF Paper",
                source="arxiv",
                local_path=str(pdf),
                extraction_status="metadata_only",
            )
            source_plan = build_source_plan(
                topic="agent coding",
                problem_markdown="",
                config={"research_sources": ["arxiv"], "research_use_fulltext": True},
                default_query="agent coding",
                default_max_results=3,
            )
            fulltext_manifest = {
                "enabled": True,
                "documents": [
                    {
                        "document_id": "doc-pdf",
                        "hints": [
                            {
                                "status": "cached",
                                "kind": "pdf",
                                "local_path": str(pdf),
                            }
                        ],
                    }
                ],
            }

            with patch(
                "simple_ar.research.documents.extractors._read_pdf",
                return_value="\u9225\u6de2orescient\u9225? GAI improves Achilles\u9225\u6a8beel cases.",
            ):
                updated, extraction_manifest = apply_fulltext_extraction(
                    records=[record],
                    fulltext_manifest=fulltext_manifest,
                    source_plan=source_plan,
                    extraction_dir=root / "extracted_text",
                )

            extracted = Path(updated[0].local_path).read_text(encoding="utf-8")
            self.assertIn("\u201cMorescient\u201d GAI", extracted)
            self.assertIn("Achilles\u2019heel", extracted)
            self.assertNotIn("鈥", extracted)
            self.assertEqual(extraction_manifest["parsed_count"], 1)

    def test_remote_fulltext_fetch_failure_is_recorded(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
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
        source_plan = build_source_plan(
            topic="agent coding",
            problem_markdown="",
            config={
                "research_sources": ["openalex"],
                "research_use_fulltext": True,
                "research_allow_pdf_download": True,
                "research_keep_raw_pdf": True,
                "research_max_fulltext_documents": 2,
                "research_max_pdf_mb": 10,
            },
            default_query="agent coding",
            default_max_results=3,
        )
        records = build_document_records(papers=[paper], source_plan=source_plan)

        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp, patch(
            "simple_ar.research.documents.fulltext.urllib.request.urlopen",
            side_effect=OSError("network unavailable"),
        ):
            manifest = build_fulltext_manifest(
                records=records,
                source_plan=source_plan,
                cache_dir=Path(tmp) / "fulltext_cache",
            )

        hint = manifest["documents"][0]["hints"][0]
        self.assertEqual(hint["status"], "fetch_failed")
        self.assertIn("network unavailable", hint["reason"])

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

    def test_scholarly_download_url_without_pdf_suffix_is_treated_as_pdf_hint(self) -> None:
        paper = Paper(
            id="openalex-W2",
            title="An OpenAlex Download Paper",
            authors=[],
            abstract="abstract",
            url="https://openalex.org/W2",
            source="openalex",
            source_id="W2",
            fulltext_url="https://ojs.aaai.org/index.php/AAAI/article/download/34497/36652",
        )

        hints = fulltext_hints_for_paper(paper, document_id="doc-openalex-download")

        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0].kind, "pdf")
        self.assertEqual(hints[0].access, "open")

    def test_openalex_fulltext_prefers_pdf_url_for_parsing(self) -> None:
        url = _fulltext_url_from_openalex(
            {
                "open_access": {"oa_url": "https://example.test/landing"},
                "best_oa_location": {
                    "pdf_url": "https://example.test/paper.pdf",
                    "landing_page_url": "https://example.test/landing-page",
                },
            }
        )

        self.assertEqual(url, "https://example.test/paper.pdf")

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
            shared_root = root / "shared_research_index"
            meta = write_research_index(
                index_dir=root / "research_index",
                chunks=chunks,
                backend="sqlite_fts",
                run_id="test-run",
                shared_root=shared_root,
            )

            self.assertGreaterEqual(len(chunks), 1)
            self.assertEqual(chunks[0].document_id, "local-agent-notes")
            self.assertEqual(meta["backend"], "sqlite_fts")
            self.assertEqual(meta["store"]["scope"], "shared")
            self.assertEqual(meta["store"]["root"], str(shared_root))
            self.assertEqual(meta["sqlite_fts"]["status"], "ready")
            self.assertTrue((root / "research_index" / "chunks.jsonl").is_file())
            self.assertTrue((root / "research_index" / "index_meta.json").is_file())
            self.assertFalse((root / "research_index" / "sqlite_fts.db").exists())
            conn = sqlite3.connect(Path(str(meta["sqlite_fts"]["path"])))
            try:
                rows = conn.execute(
                    "SELECT chunk_id FROM chunks WHERE run_id = ? AND chunks MATCH ?",
                    ("test-run", "repository"),
                ).fetchall()
                all_rows = conn.execute(
                    "SELECT run_id, chunk_id FROM chunks WHERE chunks MATCH ?",
                    ("repository",),
                ).fetchall()
            finally:
                conn.close()
            self.assertGreaterEqual(len(rows), 1)
            self.assertTrue(all(row[0] == "test-run" for row in all_rows))

    def test_section_aware_chunks_preserve_document_sections(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            note = root / "paper.md"
            note.write_text(
                "# Paper\n\n"
                "## Abstract\n"
                "The paper studies multi-agent coding systems.\n\n"
                "## Method\n"
                "The method coordinates planner, editor, and reviewer agents.\n\n"
                "## Experiments\n"
                "The benchmark reports success rate and runtime metrics.\n\n"
                "## Limitations\n"
                "A limitation is instability across random seeds.\n",
                encoding="utf-8",
            )
            record = DocumentRecord(
                document_id="doc-paper",
                title="Paper",
                source="local_files",
                local_path=str(note),
                extraction_status="parsed",
                parser="plain_text",
                metadata={"paper_id": "paper-1"},
            )

            sections = build_document_sections([record])
            chunks = build_text_chunks([record], sections=sections, max_chunks=10, chunk_chars=120)

            self.assertEqual([section.section for section in sections], ["abstract", "method", "experiments", "limitations"])
            self.assertTrue(any(chunk.metadata.get("section") == "method" for chunk in chunks))
            self.assertTrue(any("#section-" in chunk.chunk_id for chunk in chunks))

    def test_section_aware_cards_prefer_method_and_evaluation_sections(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            note = root / "paper.md"
            note.write_text(
                "## Abstract\n"
                "The paper introduces a coding-agent benchmark.\n\n"
                "## Method\n"
                "The method proposes a planner-editor-reviewer framework for repository repair.\n\n"
                "## Experiments\n"
                "The benchmark dataset reports success rate, F1, and runtime metrics.\n\n"
                "## Limitations\n"
                "A limitation is failure on long-horizon repository tasks.\n",
                encoding="utf-8",
            )
            document = DocumentRecord(
                document_id="doc-paper",
                title="Agent Coding Benchmark",
                source="local_files",
                local_path=str(note),
                extraction_status="parsed",
                parser="plain_text",
                metadata={"paper_id": "paper-agent"},
            )
            sections = build_document_sections([document])
            chunks = build_text_chunks([document], sections=sections, max_chunks=10, chunk_chars=160)

            paper_cards, claim_cards = build_evidence_cards(documents=[document], chunks=chunks)
            method_cards = build_method_cards(documents=[document], chunks=chunks)
            dataset_cards = build_dataset_cards(documents=[document], chunks=chunks)

            self.assertIn("planner-editor-reviewer", paper_cards[0].method_summary)
            self.assertTrue(paper_cards[0].metrics)
            self.assertTrue(any("long-horizon" in item for item in paper_cards[0].limitations))
            self.assertTrue(all("#section-" in ref for ref in paper_cards[0].evidence_refs))
            self.assertTrue(claim_cards)
            self.assertTrue(method_cards)
            self.assertTrue(dataset_cards)

    def test_lancedb_index_backend_is_optional(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            chunks = [
                TextChunk(
                    chunk_id="doc-1#chunk-001",
                    document_id="doc-1",
                    text="agent coding evidence",
                    metadata={"title": "Agent Coding", "source": "local_files"},
                )
            ]

            with patch.dict("sys.modules", {"lancedb": None}):
                meta = write_research_index(
                    index_dir=root / "research_index",
                    chunks=chunks,
                    backend="lancedb",
                    run_id="test-run",
                    shared_root=root / "shared_research_index",
                )

            self.assertEqual(meta["backend"], "lancedb")
            self.assertEqual(meta["lancedb"]["scope"], "shared")
            self.assertTrue(str(meta["lancedb"]["status"]).startswith("failed:"))
            self.assertTrue((root / "research_index" / "chunks.jsonl").is_file())

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

    def test_evidence_pack_and_derivations_are_compact_and_grounded(self) -> None:
        paper = Paper(
            id="paper-agent",
            title="Agent Coding Benchmark",
            authors=[],
            abstract="The method proposes a planner-editor-reviewer framework and reports success rate.",
            url="https://example.test/paper",
            source="openalex",
            source_id="W1",
        )
        document = DocumentRecord(
            document_id="doc-agent",
            title=paper.title,
            source="openalex",
            abstract=paper.abstract,
            extraction_status="parsed",
            metadata={"paper_id": paper.id},
        )
        chunks = build_text_chunks([document], max_chunks=2)
        paper_cards, claim_cards = build_evidence_cards(documents=[document], chunks=chunks)
        method_cards = build_method_cards(documents=[document], chunks=chunks)
        dataset_cards = build_dataset_cards(documents=[document], chunks=chunks)
        pack = build_evidence_pack(
            topic="multi-agent coding",
            source_plan=SourcePlan(queries=["multi-agent coding"], require_fulltext=True),
            papers=[paper],
            documents=[document],
            sections=[],
            chunks=chunks,
            index_meta={"backend": "keyword", "chunk_count": len(chunks)},
            paper_cards=paper_cards,
            claim_cards=claim_cards,
            method_cards=method_cards,
            dataset_cards=dataset_cards,
            code_links=[],
            coverage_report={"status": "covered", "covered_facets": ["method"], "missing_facets": []},
            fulltext_manifest={"enabled": True, "selected_count": 1},
            fulltext_extraction={"parsed_count": 1, "status_counts": {"parsed": 1}},
        )
        ideas = build_idea_candidates(pack)
        novelty_checks = build_novelty_checks(ideas, pack)
        contract = build_experiment_contract(ideas, pack)
        tool_context, _ = build_tool_context(pack=pack, contract=contract, novelty_checks=novelty_checks)

        self.assertEqual(pack["schema_version"], "evidence_pack.v1")
        self.assertNotIn("The method proposes a planner-editor-reviewer", str(pack["papers"]))
        self.assertTrue(ideas)
        self.assertTrue(ideas[0].motivation_refs)
        self.assertTrue(contract.motivation_refs)
        self.assertTrue(novelty_checks)
        self.assertTrue(tool_context["human_review_required"])
        self.assertIn("modify repository files without an approved code-task workspace", tool_context["forbidden_actions"])

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

    def test_screening_preserves_required_facet_diversity(self) -> None:
        method_a = Paper(
            id="method-a",
            title="LLM Agents Software Engineering Benchmark Method",
            authors=[],
            abstract="LLM agents solve software engineering tasks with unit tests and benchmarks.",
            url="https://example.test/method-a",
            source="openalex",
        )
        method_b = Paper(
            id="method-b",
            title="Software Engineering Agents Method",
            authors=[],
            abstract="Agents improve coding workflows with testing and benchmark feedback.",
            url="https://example.test/method-b",
            source="openalex",
        )
        overview = Paper(
            id="overview",
            title="Survey of LLM Coding Agents",
            authors=[],
            abstract="A survey summarizes multi-agent collaboration for software development.",
            url="https://example.test/overview",
            source="openalex",
        )

        kept, decisions = screen_retrieval_candidates(
            [
                RetrievalCandidate(
                    paper=method_a,
                    source="openalex",
                    query="llm agents software engineering benchmark",
                    query_index=1,
                    round_index=1,
                    facet="method",
                ),
                RetrievalCandidate(
                    paper=method_b,
                    source="openalex",
                    query="software engineering agents method",
                    query_index=2,
                    round_index=1,
                    facet="method",
                ),
                RetrievalCandidate(
                    paper=overview,
                    source="openalex",
                    query="multi-agent collaboration coding agents",
                    query_index=3,
                    round_index=1,
                    facet="overview",
                ),
            ],
            max_documents=2,
            priority_facets=["overview", "method"],
        )

        kept_ids = {paper.id for paper in kept}
        self.assertIn("overview", kept_ids)
        self.assertTrue(any(row["paper_id"] == "overview" and row["reason"] == "facet_coverage" for row in decisions))

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
            selection_rows=[
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

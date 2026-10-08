from __future__ import annotations

from io import BytesIO
from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from simple_ar.core.artifacts import write_jsonl
from simple_ar.literature.models import Paper
from simple_ar.literature.semantic_scholar_client import _paper_from_row as semantic_scholar_paper
from simple_ar.research.evidence.reader import ReadRequest, read_documents
from simple_ar.research.contracts import DocumentRecord, SourcePlan, TextChunk
from simple_ar.research.documents.fulltext import build_fulltext_manifest
from simple_ar.research.documents.ingest import build_document_bundle, build_supporting_material_bundle
from simple_ar.research.documents.records import build_document_records
from simple_ar.research.sources.base import build_source_plan
from simple_ar._legacy.documents import load_search_document_bundle


class DocumentIngestTests(unittest.TestCase):
    def test_plain_webpage_url_uses_existing_html_parser_and_cache(self) -> None:
        body = (b"<!doctype html><html><h1>Official guidance</h1><p>Documented conditions.</p>"
                b'<a href="https://example.test/code">Author code</a> '
                b'<a href="https://example.test/data">https://example.test/data</a>'
                b'<a href="https://github.com/author/research">Author implementation</a>'
                b"<script>evil()</script></html>")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = Paper(id="web", title="Official source", authors=[], abstract="", source="fixture",
                          url="https://example.test/official/guidance")
            plan = SourcePlan(queries=["guidance"], require_fulltext=True,
                budget={"max_fulltext_documents": 1, "max_fulltext_fetch_attempts": 1})
            response = BytesIO(body)
            response.headers = {"Content-Type": "text/html; charset=utf-8"}
            with patch("simple_ar.research.documents.fulltext.urllib.request.urlopen", return_value=response) as get:
                first = build_document_bundle(papers=[paper], source_plan=plan,
                    cache_dir=root / "cache", extraction_dir=root / "extracted")
                second = build_document_bundle(papers=[paper], source_plan=plan,
                    cache_dir=root / "cache", extraction_dir=root / "extracted")
            get.assert_called_once()
            hint = first.fulltext_manifest["documents"][0]["hints"][0]
            restored = second.fulltext_manifest["documents"][0]["hints"][0]
            self.assertEqual((hint["kind"], hint["status"]), ("html", "cached"))
            self.assertEqual(hint["original_hint"]["kind"], "landing")
            self.assertEqual(hint["original_hint"]["reason"], "metadata_url")
            self.assertEqual(hint["url"], paper.url)
            self.assertEqual(hint["content_scope"], "source_webpage_not_verified_paper_fulltext")
            self.assertEqual(restored["reason"], "cache_hit")
            self.assertEqual(hint["local_path"], restored["local_path"])
            self.assertEqual(first.records[0].parser, "basic_html")
            text = Path(first.records[0].local_path).read_text(encoding="utf-8")
            self.assertIn("Documented conditions.", text)
            self.assertNotIn("evil()", text)
            self.assertNotIn("method", [section.section for section in first.sections])
            self.assertIn("Author code (https://example.test/code)", text)
            self.assertEqual(text.count("https://example.test/data"), 1)
            from simple_ar.research.evidence.reader import linked_material_records
            read = read_documents(ReadRequest(bundle=first, topic="Implementation and data"))
            selected = linked_material_records(read, bundle=first, limit=1)
            self.assertEqual(selected[0].url, "https://github.com/author/research")
            self.assertIn(selected[0].url, selected[0].metadata["parent_quote"])

            material = DocumentRecord(document_id="official-page", title="Official guidance",
                source="web", url="https://example.test/official/guidance",
                metadata={"parent_document_id": first.records[0].document_id,
                          "link_quote": "Author implementation is available here."})
            plan.budget["max_pdf_mb"] = 1
            def acquire(current_plan):
                return build_supporting_material_bundle(records=[material], source_plan=current_plan,
                    cache_dir=root / "material-cache", extraction_dir=root / "material-text")
            transport = httpx.MockTransport(lambda request: httpx.Response(200,
                headers={"Content-Type": "text/html"}, stream=httpx.ByteStream(body)))
            client = httpx.Client(transport=transport, trust_env=False, follow_redirects=False)
            with patch("simple_ar.research.preparation_assets.socket.getaddrinfo",
                       return_value=[(0, 0, 0, "", ("93.184.216.34", 443))]), patch(
                       "simple_ar.research.preparation_assets.httpx.Client", return_value=client) as get:
                support = acquire(plan)
                restored_support = acquire(plan)
            get.assert_called_once()
            self.assertEqual(support.records[0].source, "supporting_material")
            self.assertEqual(support.records[0].metadata["kind"], "supporting_material")
            self.assertEqual(support.records[0].metadata["parent_document_id"], first.records[0].document_id)
            self.assertEqual(restored_support.fulltext_manifest["documents"][0]["hints"][0]["reason"], "cache_hit")
            self.assertTrue(support.chunks)
            for key in ("max_fulltext_documents", "max_fulltext_fetch_attempts", "max_pdf_mb"):
                saved = plan.budget[key]
                plan.budget[key] = 0
                with patch("simple_ar.research.preparation_assets.public_document_response") as get:
                    denied = acquire(plan)
                get.assert_not_called()
                self.assertEqual(denied.records[0].extraction_status, "metadata_only")
                plan.budget[key] = saved
            with patch("simple_ar.research.preparation_assets.public_document_response") as get:
                self.assertEqual(acquire(replace(plan, require_fulltext=False)).records[0].extraction_status, "metadata_only")
            get.assert_not_called()

            from simple_ar.research.preparation_assets import _document_transport
            with patch.dict("os.environ", {"HTTPS_PROXY": "http://127.0.0.1:1"}, clear=True):
                self.assertEqual(_document_transport(material.url), {})
            with patch.dict("os.environ", {"SIMPLE_AR_DOCUMENT_PROXY_ENV": "DOC_PROXY",
                    "DOC_PROXY": "http://127.0.0.1:8080",
                    "SIMPLE_AR_DOCUMENT_ROUTE_HOSTS": "example.org"}, clear=True):
                self.assertEqual(_document_transport("https://example.org/project"),
                                 {"proxy": "http://127.0.0.1:8080"})
                self.assertEqual(_document_transport("https://arxiv.org/pdf/1"), {})
                with patch.dict("os.environ", {"SIMPLE_AR_DOCUMENT_ROUTE_HOSTS": "*"}):
                    with self.assertRaisesRegex(ValueError, "exact_hosts"):
                        _document_transport(material.url)
            material = replace(material, document_id="failed-page")
            with patch("simple_ar.research.preparation_assets.public_document_response",
                       side_effect=ValueError("nonpublic_address")):
                failed = acquire(plan)
            self.assertEqual(failed.records[0].metadata["parent_document_id"], first.records[0].document_id)
            self.assertEqual(failed.fulltext_manifest["documents"][0]["hints"][0]["status"], "fetch_failed")
            from simple_ar.research.preparation_assets import public_document_response
            for status, headers, limit in (
                (302, {"Location": "https://127.0.0.1/private"}, 100),
                (200, {"Content-Type": "text/html"}, 2),
            ):
                transport = httpx.MockTransport(lambda request: httpx.Response(
                    status, headers=headers, stream=httpx.ByteStream(body)))
                client = httpx.Client(transport=transport, trust_env=False, follow_redirects=False)
                with patch("simple_ar.research.preparation_assets.socket.getaddrinfo",
                           return_value=[(0, 0, 0, "", ("93.184.216.34", 443))]), patch(
                           "simple_ar.research.preparation_assets.httpx.Client", return_value=client):
                    with self.assertRaises(ValueError):
                        public_document_response(material.url, max_bytes=limit)
            with patch("simple_ar.research.preparation_assets.httpx.Client") as get:
                with self.assertRaises(ValueError):
                    public_document_response("https://127.0.0.1/private", max_bytes=100)
                with self.assertRaises(ValueError):
                    public_document_response(material.url, max_bytes=0)
            get.assert_not_called()

    def test_landing_response_detection_and_unsupported_content(self) -> None:
        for mime, body, expected in (
            ("", b"<!doctype html><p>Source</p>", "html"),
            ("application/octet-stream", b"<html><p>Source</p></html>", "html"),
            ("text/plain", b"Plain source conditions", "text"),
            ("", b"Plain source conditions", "text"),
            ("application/pdf", b"%PDF-1.7\nfixture", "pdf"),
            ("image/png", b"\x89PNG\r\n\x1a\nfixture", "fetch_failed"),
            ("application/json", b'{"unhandled":true}', "fetch_failed"),
            ("", b"\x00\x01binary", "fetch_failed"),
            ("text/html", b"", "fetch_failed"),
        ):
            with self.subTest(mime=mime, expected=expected), tempfile.TemporaryDirectory() as tmp:
                response = BytesIO(body)
                response.headers = {"Content-Type": mime}
                record = DocumentRecord(document_id="web", title="Source", source="fixture",
                                        url="https://doi.org/10.0000/source")
                plan = SourcePlan(queries=["source"], require_fulltext=True, allow_pdf_download=True,
                                  budget={"keep_raw_pdf": True})
                with patch("simple_ar.research.documents.fulltext.urllib.request.urlopen", return_value=response):
                    manifest = build_fulltext_manifest(records=[record], source_plan=plan, cache_dir=Path(tmp))
                hint = manifest["documents"][0]["hints"][0]
                self.assertEqual(hint["status"], "fetch_failed" if expected == "fetch_failed" else "cached")
                if expected != "fetch_failed":
                    self.assertEqual(hint["kind"], expected)
                else:
                    self.assertTrue(hint["reason"])
                    self.assertFalse(list(Path(tmp).iterdir()))
                self.assertEqual(manifest["fetch_attempt_count"], 1)

    def test_pdf_redirect_and_cached_pdf_cannot_bypass_permissions(self) -> None:
        for hinted_kind in ("landing", "html"):
            for mime in ("application/pdf", "application/octet-stream"):
                for allow, keep, expected in ((False, True, "pdf_download_disabled"),
                                              (True, False, "raw_pdf_retention_disabled"),
                                              (True, True, "cached")):
                    with self.subTest(kind=hinted_kind, mime=mime, allow=allow, keep=keep), tempfile.TemporaryDirectory() as tmp:
                        root = Path(tmp)
                        record = DocumentRecord(document_id="web", title="Redirect", source="fixture",
                            metadata={"fulltext_hints": [{"kind": hinted_kind, "source": "fixture",
                                                          "url": "https://example.test/source"}]})
                        plan = SourcePlan(queries=["source"], require_fulltext=True, allow_pdf_download=allow,
                                          budget={"keep_raw_pdf": keep})
                        response = BytesIO(b"%PDF-1.7\nfixture")
                        response.headers = {"Content-Type": mime}
                        response.geturl = lambda: "https://example.test/resolved.pdf"
                        with patch("simple_ar.research.documents.fulltext.urllib.request.urlopen", return_value=response) as get:
                            manifest = build_fulltext_manifest(records=[record], source_plan=plan, cache_dir=root)
                        hint = manifest["documents"][0]["hints"][0]
                        self.assertEqual(hint["status"], "cached" if expected == "cached" else "fetch_failed")
                        get.assert_called_once()
                        if expected != "cached":
                            self.assertEqual(hint["reason"], expected)
                            self.assertFalse(list(root.iterdir()))
                        else:
                            self.assertEqual(hint["kind"], "pdf")
                            self.assertEqual(Path(hint["local_path"]).suffix, ".pdf")
                            for denied in (SourcePlan(queries=["source"], require_fulltext=True, allow_pdf_download=False,
                                                      budget={"keep_raw_pdf": True}),
                                           SourcePlan(queries=["source"], require_fulltext=True, allow_pdf_download=True,
                                                      budget={"keep_raw_pdf": False})):
                                with patch("simple_ar.research.documents.fulltext.urllib.request.urlopen") as cached_get:
                                    retry = build_fulltext_manifest(records=[record], source_plan=denied, cache_dir=root)
                                self.assertEqual(retry["documents"][0]["hints"][0]["status"], "fetch_failed")
                                cached_get.assert_not_called()
                                self.assertTrue(Path(hint["local_path"]).is_file())

    def test_landing_uses_shared_document_and_fetch_attempt_budgets(self) -> None:
        records = [DocumentRecord(document_id=f"web-{i}", title="Source", source="fixture",
                                  url=f"https://example.test/source/{i}") for i in range(2)]
        with patch("simple_ar.research.documents.fulltext.urllib.request.urlopen") as get:
            disabled = build_fulltext_manifest(records=records,
                source_plan=SourcePlan(queries=["source"], require_fulltext=False), cache_dir=None)
        self.assertEqual(disabled["documents"][0]["hints"][0]["reason"], "fulltext_disabled")
        get.assert_not_called()
        for first_body, mime, budget, reason in (
            (b"<p>Source</p>", "text/html", {"max_fulltext_documents": 1}, "max_fulltext_documents_reached"),
            (b"<p>Source</p>", "text/html", {"max_fulltext_documents": 2,
                "reserved_fulltext_documents": 1}, "max_fulltext_documents_reached"),
            (b"binary", "image/png", {"max_fulltext_fetch_attempts": 1}, "max_fulltext_fetch_attempts_reached"),
        ):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as tmp:
                response = BytesIO(first_body)
                response.headers = {"Content-Type": mime}
                with patch("simple_ar.research.documents.fulltext.urllib.request.urlopen", return_value=response) as get:
                    manifest = build_fulltext_manifest(records=records,
                        source_plan=SourcePlan(queries=["source"], require_fulltext=True, budget=budget), cache_dir=Path(tmp))
                self.assertEqual(manifest["documents"][1]["hints"][0]["reason"], reason)
                self.assertEqual(manifest["fetch_attempt_count"], 1)
                if "reserved_fulltext_documents" in budget:
                    self.assertEqual(manifest["budget"]["max_fulltext_documents"], 2)
                get.assert_called_once()

    def test_provider_open_pdf_flows_from_search_metadata_to_fetch_plan(self) -> None:
        paper = semantic_scholar_paper({
            "paperId": "s2-open", "title": "A study with an open PDF",
            "openAccessPdf": {"url": "https://example.org/study.pdf"},
        })
        plan = SourcePlan(
            queries=["study"], require_fulltext=True, allow_pdf_download=True,
            budget={"max_fulltext_documents": 4, "max_pdf_mb": 20, "keep_raw_pdf": True},
        )
        records = build_document_records(papers=[paper], source_plan=plan)
        manifest = build_fulltext_manifest(records=records, source_plan=plan, cache_dir=None)
        hint = manifest["documents"][0]["hints"][0]
        self.assertEqual(hint["kind"], "pdf")
        self.assertEqual(hint["status"], "selected")
        self.assertEqual(hint["url"], "https://example.org/study.pdf")
        self.assertEqual(manifest["budget"]["max_pdf_mb"], 20)

    def test_remote_fulltext_reuses_a_valid_cache_entry(self) -> None:
        class Response:
            headers = {"Content-Type": "application/pdf"}

            def __init__(self) -> None:
                self._read = False

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self, _size: int = -1) -> bytes:
                if self._read:
                    return b""
                self._read = True
                return b"%PDF-1.7\nfixture"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = DocumentRecord(
                document_id="paper-1",
                title="A paper",
                source="fixture",
                metadata={
                    "fulltext_hints": [
                        {
                            "kind": "pdf",
                            "source": "fixture",
                            "url": "https://example.test/paper.pdf",
                        }
                    ]
                },
            )
            source_plan = SourcePlan(
                queries=["fixture"],
                require_fulltext=True,
                allow_pdf_download=True,
                budget={
                    "max_fulltext_documents": 1,
                    "max_fulltext_fetch_attempts": 1,
                    "keep_raw_pdf": True,
                },
            )
            with patch(
                "simple_ar.research.documents.fulltext.urllib.request.urlopen",
                return_value=Response(),
            ) as urlopen:
                first = build_fulltext_manifest(
                    records=[record],
                    source_plan=source_plan,
                    cache_dir=root / "cache",
                )
                second = build_fulltext_manifest(
                    records=[record],
                    source_plan=source_plan,
                    cache_dir=root / "cache",
                )

            first_hint = first["documents"][0]["hints"][0]
            second_hint = second["documents"][0]["hints"][0]
            self.assertEqual(first_hint["status"], "cached")
            self.assertEqual(second_hint["reason"], "cache_hit")
            self.assertEqual(first_hint["local_path"], second_hint["local_path"])
            urlopen.assert_called_once()

    def test_remote_fulltext_byte_budget_applies_to_every_content_kind(self) -> None:
        limit = 1024 * 1024

        class Response(BytesIO):
            def __init__(self, body: bytes, length: str | None) -> None:
                super().__init__(body)
                self.headers = {} if length is None else {"Content-Length": length}

        for kind, suffix in (("pdf", ".pdf"), ("html", ".html"), ("text", ".txt"), ("landing", "")):
            for case, size, length, expected in (
                ("header", 20, str(limit + 1), "fetch_failed"),
                ("no_header", limit + 1, None, "fetch_failed"),
                ("short_header", limit + 1, "20", "fetch_failed"),
                ("invalid_header", limit + 1, "unknown", "fetch_failed"),
                ("exact_limit", limit, str(limit), "cached"),
            ):
                with self.subTest(kind=kind, case=case), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    body = b"%PDF-1.7\n" + b"x" * (size - 9) if kind == "pdf" else b"x" * size
                    record = DocumentRecord(document_id="bounded", title="Bounded resource", source="fixture",
                        metadata={"fulltext_hints": [{"kind": kind, "source": "fixture",
                                                     "url": "https://example.test/resource" + suffix}]})
                    plan = SourcePlan(queries=["fixture"], require_fulltext=True, allow_pdf_download=True,
                        budget={"max_pdf_mb": 1, "keep_raw_pdf": True})
                    with patch("simple_ar.research.documents.fulltext.urllib.request.urlopen",
                               return_value=Response(body, length)):
                        manifest = build_fulltext_manifest(records=[record], source_plan=plan, cache_dir=root)
                    hint = manifest["documents"][0]["hints"][0]
                    self.assertEqual(hint["status"], expected)
                    if expected == "fetch_failed":
                        self.assertEqual(hint["reason"], "remote_file_exceeds_max_pdf_mb")
                        self.assertFalse(list(root.iterdir()))  # Partial downloads are removed.
                    else:
                        self.assertEqual(hint["size_bytes"], limit)
                    self.assertTrue(any("PDF, HTML and text" in note for note in manifest["notes"]))

    def test_remote_cache_reuse_cannot_bypass_byte_budget(self) -> None:
        for kind, suffix in (("pdf", ".pdf"), ("html", ".html"), ("text", ".txt"), ("landing", ".html")):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                stem = "bounded-fixture"
                if kind == "landing":
                    stem += "-" + hashlib.sha256(("https://example.test/resource" + suffix).encode()).hexdigest()[:16]
                cached = root / (stem + suffix)
                body = b"%PDF-1.7\n" + b"x" * (1024 * 1024) if kind == "pdf" else b"x" * (1024 * 1024 + 1)
                cached.write_bytes(body)
                record = DocumentRecord(document_id="bounded", title="Bounded resource", source="fixture",
                    metadata={"fulltext_hints": [{"kind": kind, "source": "fixture",
                                                 "url": "https://example.test/resource" + suffix}]})
                plan = SourcePlan(queries=["fixture"], require_fulltext=True, allow_pdf_download=True,
                    budget={"max_pdf_mb": 1, "keep_raw_pdf": True})
                with patch("simple_ar.research.documents.fulltext.urllib.request.urlopen") as urlopen:
                    manifest = build_fulltext_manifest(records=[record], source_plan=plan, cache_dir=root)
                hint = manifest["documents"][0]["hints"][0]
                self.assertEqual(hint["status"], "fetch_failed")
                self.assertEqual(hint["reason"], "remote_file_exceeds_max_pdf_mb")
                self.assertEqual(cached.read_bytes(), body)
                urlopen.assert_not_called()

    def test_bundle_preserves_existing_local_document_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper_path = root / "paper.md"
            paper_path.write_text(
                "# Method\n\nA bounded method.\n\n# Results\n\nRMSE improves.\n",
                encoding="utf-8",
            )
            source_plan = build_source_plan(
                topic="agent coding",
                problem_markdown="",
                config={
                    "research_sources": ["local_files"],
                    "research_local_documents": [str(paper_path)],
                    "research_use_fulltext": True,
                    "research_max_chunks": 1,
                },
                default_query="agent coding",
                default_max_results=3,
            )
            bundle = build_document_bundle(
                papers=[
                    Paper(
                        id="p1",
                        title="Metadata Paper",
                        authors=[],
                        abstract="A metadata abstract.",
                        url="https://example.test/p1",
                        source="fixture",
                    )
                ],
                source_plan=source_plan,
                cache_dir=root / "cache",
                extraction_dir=root / "extracted",
                max_chunks=source_plan.budget["max_chunks"],
            )

            self.assertEqual(len(bundle.records), 2)
            self.assertEqual(bundle.records[0].extraction_status, "parsed")
            self.assertEqual(len(bundle.sections), 3)
            self.assertEqual(len(bundle.chunks), 1)
            self.assertEqual(bundle.fulltext_extraction["parsed_count"], 1)
            from types import SimpleNamespace
            from simple_ar.core.capabilities import ArtifactStore
            from simple_ar.core.artifacts import write_json
            from simple_ar.research.documents.ingest import (
                DocumentBundle, DocumentIngestRequest, retained_document_materials, run_document_ingest_capability)
            saved = root / "saved.json"
            write_json(saved, bundle.to_handoff_dict())
            before = saved.read_bytes()
            restored = retained_document_materials([saved])[saved.resolve()]
            self.assertEqual([row.to_row() for row in restored.chunks], [row.to_row() for row in bundle.chunks])
            store = ArtifactStore(root / "new-session")
            with patch("simple_ar.research.documents.fulltext.urllib.request.urlopen") as network:
                result = run_document_ingest_capability(context=SimpleNamespace(store=store),
                    request=DocumentIngestRequest(papers=(), source_plan=SourcePlan(queries=[], local_documents=[str(saved)]),
                        extraction_dir=root / "new-text", analysis_paths=(saved,)))
            network.assert_not_called()
            imported = DocumentBundle.from_handoff_dict(store.read_json(result.artifacts[0]))
            self.assertEqual([row.document_id for row in imported.records], [row.document_id for row in bundle.records])
            self.assertEqual([row.to_row() for row in imported.chunks], [row.to_row() for row in bundle.chunks])
            self.assertEqual(imported.records[1].metadata["retained_source_role"], "paper")
            self.assertEqual(saved.read_bytes(), before)
            from simple_ar.result_analysis.table import TableSpec, describe_table, parse_table
            package_dir = root / "analysis"
            package_dir.mkdir()
            (package_dir / "input.csv").write_text("value\n1\n3\n", encoding="utf-8")
            table = describe_table(parse_table("value\n1\n3\n", ".csv"), TableSpec(("value",), "one run"))
            table.update(source={"path": "input.csv", "kind": "user_data"}, source_name="input.csv")
            write_json(package_dir / "analysis.json", table)
            with_table = bundle.to_handoff_dict()
            with_table["documents"].append(DocumentRecord(document_id="saved-analysis", title="Measured data",
                source="local_analysis", metadata={"table_analysis": {"artifact": "analysis/analysis.json"}}).to_row())
            write_json(saved, with_table)
            copied_store = ArtifactStore(root / "with-analysis")
            copied_result = run_document_ingest_capability(context=SimpleNamespace(store=copied_store),
                request=DocumentIngestRequest(papers=(), source_plan=SourcePlan(queries=[], local_documents=[str(saved)]),
                    extraction_dir=root / "analysis-text", analysis_paths=(saved,)))
            copied = DocumentBundle.from_handoff_dict(copied_store.read_json(copied_result.artifacts[0]))
            analysis = next(row for row in copied.records if row.metadata.get("table_analysis"))
            self.assertEqual(analysis.metadata["table_analysis"]["records"], table["records"])
            self.assertTrue(copied_store.resolve(analysis.metadata["table_analysis"]["artifact"]).is_file())
            with_table["documents"][-1]["metadata"]["table_analysis"]["artifact"] = "../outside.json"
            write_json(saved, with_table)
            with self.assertRaisesRegex(ValueError, "inside the selected"):
                run_document_ingest_capability(context=SimpleNamespace(store=copied_store),
                    request=DocumentIngestRequest(papers=(), source_plan=SourcePlan(queries=[], local_documents=[str(saved)]),
                        extraction_dir=root / "bad-text", analysis_paths=(saved,)))
            invalid = bundle.to_handoff_dict()
            invalid["chunks"][0]["document_id"] = "absent"
            write_json(saved, invalid)
            with self.assertRaisesRegex(ValueError, "unknown document"):
                retained_document_materials([saved])

    def test_search_document_bundle_loads_legacy_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            search_dir = run_dir / "02-search"
            write_jsonl(
                search_dir / "documents" / "documents.jsonl",
                [
                    DocumentRecord(
                        document_id="openalex-p1",
                        source_id="p1",
                        title="A paper",
                        source="openalex",
                    ).to_row()
                ],
            )
            write_jsonl(
                search_dir / "research_index" / "chunks.jsonl",
                [
                    TextChunk(
                        chunk_id="openalex-p1#chunk-001",
                        document_id="openalex-p1",
                        text="A source passage.",
                    ).to_row()
                ],
            )

            bundle = load_search_document_bundle(
                run_dir / "02-search"
            )

            self.assertEqual([record.document_id for record in bundle.records], ["openalex-p1"])
            self.assertEqual([chunk.chunk_id for chunk in bundle.chunks], ["openalex-p1#chunk-001"])
            self.assertEqual(bundle.sections, [])
            self.assertEqual(bundle.fulltext_manifest, {})

    def test_read_cards_match_shortlist_paper_ids_to_document_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            write_jsonl(
                run_dir / "02-search" / "documents" / "documents.jsonl",
                [
                    DocumentRecord(
                        document_id="openalex-p1",
                        source_id="p1",
                        title="A paper",
                        source="openalex",
                        abstract="A method improves accuracy on a benchmark.",
                    ).to_row()
                ],
            )
            write_jsonl(
                run_dir / "02-search" / "research_index" / "chunks.jsonl",
                [
                    TextChunk(
                        chunk_id="openalex-p1#chunk-001",
                        document_id="openalex-p1",
                        text="A method improves accuracy on a benchmark.",
                    ).to_row()
                ],
            )
            bundle = load_search_document_bundle(
                run_dir / "02-search"
            )
            result = read_documents(ReadRequest(bundle=bundle, paper_ids=("p1",)))
            self.assertEqual(len(result.paper_cards), 1)
            self.assertEqual(result.paper_cards[0].title, "A paper")
            self.assertEqual(result.paper_cards[0].paper_id, "openalex-p1")


if __name__ == "__main__":
    unittest.main()

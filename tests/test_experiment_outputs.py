"""Result attachments cross the existing execution/writing boundary safely."""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from simple_ar.core.capabilities import ArtifactRef, ArtifactStore, AttemptManifest, CapabilityContext
from simple_ar.experiment.execution.backend import RunRequest
from simple_ar.experiment.execution.outputs import (
    MAX_OUTPUT_BYTES, capture_outputs, output_files, read_output_window,
)
from simple_ar.research.experiment import ExperimentRequest, run_experiment_capability
from simple_ar.report.execution_evidence import report_execution_evidence
from simple_ar.report.projection import _qualified_outputs, _output_handles, attach_experiment_history
from simple_ar.report.schema import ReportContext, ReportMemory, ReportRuntimeConfig, ReportToolCall
from simple_ar.report.templates import load_report_template_bundle
from simple_ar.report.tool_gateway import ReportToolGateway
from simple_ar.report.writing import ReportWritingRequest, run_report_writing_capability


class ExperimentOutputTests(unittest.TestCase):
    def test_contract_rejects_escape_and_unbounded_file_lists(self):
        for value in ({"x": "../x"}, {"x": "/etc/passwd"}, {"x": "C:/x"},
                      {"x": "a\\x"}, {"x": "./x"}, {"x": "a//b"},
                      {"x": 1}, {"": "x"}, {str(i): "x" for i in range(9)}, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                output_files({"output_files": value})
        self.assertEqual(output_files({}), {})
        self.assertEqual(output_files({"output_files": {"raw": "nested/raw.json"}}), {"raw": "nested/raw.json"})

    def test_live_process_two_layouts_registers_only_declared_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename in ("raw.json", "tables/seed values.csv"):
                with self.subTest(filename=filename):
                    store = ArtifactStore(root / filename.replace("/", "-"))
                    script = ("import os; from pathlib import Path; "
                              "p=Path(os.environ['SIMPLE_AR_OUTPUT_DIR']); "
                              f"f=p/{filename!r}; f.parent.mkdir(parents=True); "
                              "f.write_text('seed,value\\n0,0.4\\n', encoding='utf-8'); "
                              "(p/'unrelated.txt').write_text('not requested'); print('METRIC accuracy=0.4')")
                    request = ExperimentRequest(run=RunRequest([sys.executable, "-c", script], root, 5),
                        result_schema={"output_files": {"observations": filename, "optional": "absent.json"}})
                    result = run_experiment_capability(context=CapabilityContext(store, AttemptManifest("experiment-1")), request=request)
                    self.assertEqual(result.status, "completed", result.diagnostics)
                    canonical = store.read_json(next(ref for ref in result.artifacts if ref.kind == "experiment_result"))
                    rows = canonical["output_evidence"]
                    self.assertEqual([row["status"] for row in rows], ["available", "missing"])
                    refs = [ref for ref in result.artifacts if ref.kind == "experiment_output"]
                    self.assertEqual(len(refs), 1)
                    self.assertEqual(refs[0].path, rows[0]["artifact"])
                    self.assertNotIn("unrelated", json.dumps(rows))
                    self.assertIn("0,0.4", rows[0]["preview"]["text"])

    def test_invalid_contract_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            request = ExperimentRequest(run=RunRequest([sys.executable, "-c", "raise Exception('ran')"], Path(directory), 5),
                result_schema={"output_files": {"raw": "../outside"}})
            with patch("simple_ar.research.experiment.run_experiment") as run, self.assertRaises(ValueError):
                run_experiment_capability(context=CapabilityContext(ArtifactStore(Path(directory)), AttemptManifest("a")), request=request)
            run.assert_not_called()

    def test_preview_windows_unicode_and_move_do_not_invent_complete_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ArtifactStore(root / "source")
            file = store.root / "outputs" / "raw.txt"
            file.parent.mkdir(parents=True)
            text = "µ测量🙂" * 900
            file.write_text(text, encoding="utf-8")
            rows, refs = capture_outputs(store, file.parent, {"raw": "raw.txt"})
            self.assertTrue(rows[0]["preview"]["truncated"])
            self.assertEqual(rows[0]["preview"]["total_characters"], len(text))
            shutil.copytree(store.root, root / "moved")
            moved = ArtifactStore(root / "moved")
            view = read_output_window(moved.root, moved.resolve(refs[0]), offset=1200, limit=2400)
            self.assertEqual(view["text"], text[1200:3600])
            self.assertFalse(view["has_more"])
            self.assertTrue(view["truncated"])

    def test_unreadable_and_oversize_files_keep_distinct_attachment_status(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(Path(directory))
            (store.root / "big").write_bytes(b"x" * (MAX_OUTPUT_BYTES + 1))
            (store.root / "binary").write_bytes(b"\xff\x00")
            (store.root / "folder").mkdir()
            rows, refs = capture_outputs(store, store.root, {name: name for name in ("big", "binary", "folder", "absent")})
            self.assertEqual([row["status"] for row in rows], ["unreadable"] * 3 + ["missing"])
            self.assertEqual(refs, [])

    def test_literal_search_finds_late_evidence_without_guessing_offsets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "raw.json"
            text = "prefix\n" * 1800 + '\n{"seed": 9, "value": 0.42}\n' + "suffix\n" * 800
            file.write_text(text, encoding="utf-8")
            result = read_output_window(root, file, query='"seed": 9')
            self.assertTrue(result["query_matched"])
            self.assertIn('"value": 0.42', result["text"])
            self.assertGreater(result["offset"], 10000)
            self.assertEqual(result["text"], text[result["offset"]:result["next_offset"]])
            missing = read_output_window(root, file, query="no such phrase")
            self.assertFalse(missing["query_matched"])
            self.assertEqual(missing["text"], "")
            with self.assertRaises(ValueError):
                read_output_window(root, file, query="x" * 201)

    def test_producer_attribution_is_not_a_fabricated_paper_citation(self):
        from simple_ar.report.narrative import _prompt_handle_view
        handle = _output_handles([{"handle": "output:raw", "status": "available", "artifact": "raw.csv"}])[0]
        view = _prompt_handle_view(handle)
        self.assertIn("not a literature source", view["attribution_guidance"])
        self.assertNotIn("cite_as", view)

    def test_exact_record_selection_handles_json_csv_and_tsv_without_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [{"condition": i, "method": method, "value": i / 100}
                    for i in range(10) for method in ("A", "B")]
            for suffix in ("json", "csv", "tsv"):
                file = root / f"rows.{suffix}"
                if suffix == "json":
                    file.write_text(json.dumps(rows), encoding="utf-8")
                else:
                    import csv
                    with file.open("w", newline="", encoding="utf-8") as stream:
                        writer = csv.DictWriter(stream, fieldnames=rows[0], delimiter="\t" if suffix == "tsv" else ",")
                        writer.writeheader()
                        writer.writerows(rows)
                result = read_output_window(root, file, record_match={"condition": 9})
                selected = json.loads(result["text"])
                self.assertEqual(result["source_records"], 20)
                self.assertEqual(result["matched_records"], 2)
                self.assertEqual([row["method"] for row in selected], ["A", "B"])
                self.assertFalse(result["truncated"])
                self.assertEqual(read_output_window(root, file, record_match={"unknown": 9})["matched_records"], 0)
                with self.assertRaises(ValueError):
                    read_output_window(root, file, query="9", record_match={"condition": 9})

    def test_record_selection_retains_truncation_and_does_not_treat_boolean_as_number(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "raw.json"
            file.write_text(json.dumps([{"flag": True, "payload": "x" * 3000}, {"flag": 1}]))
            result = read_output_window(root, file, record_match={"flag": True})
            self.assertEqual(result["matched_records"], 1)
            self.assertTrue(result["truncated"])
            self.assertEqual(result["view_kind"], "selected_producer_records")
            file.write_text('{"summary": 1}')
            with self.assertRaises(ValueError):
                read_output_window(root, file, record_match={"summary": 1})

    def test_symlink_and_escape_do_not_read_external_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ArtifactStore(root / "store")
            store.root.mkdir()
            outside = root / "outside"
            outside.write_text("never expose")
            try:
                (store.root / "linked").symlink_to(outside)
                (store.root / "nested").symlink_to(root, target_is_directory=True)
            except OSError:
                self.skipTest("Symlinks unavailable")
            for file in (store.root / "linked", store.root / "nested" / "outside", store.root / ".." / "outside"):
                with self.subTest(file=file), self.assertRaises(ValueError):
                    read_output_window(store.root, file)
            if hasattr(os, "mkfifo"):
                os.mkfifo(store.root / "fifo")
                with self.assertRaises(ValueError):
                    read_output_window(store.root, store.root / "fifo")

    def test_projection_keeps_current_and_history_measurements_distinct(self):
        result = {"output_evidence": [{"name": "raw", "status": "available", "artifact": "outputs/raw.json",
                                       "preview": {"text": "producer", "truncated": False}}]}
        ref = ArtifactRef("attempts/experiment-2/results.json")
        qualified = _qualified_outputs(result, ref)
        context = ReportContext(topic="Measurements", report_mode="experiment", results={"output_evidence": qualified})
        memory = ReportMemory()
        before = json.dumps(result)
        context, memory = attach_experiment_history(context, memory,
            [("initial", ArtifactRef("attempts/experiment-1/results.json"), result), ("repair", ref, result)], current_ref=ref)
        view = report_execution_evidence(context)
        self.assertEqual(len(view["output_evidence"]), 2)
        self.assertEqual(view["output_evidence"][0]["artifact"], "attempts/experiment-2/outputs/raw.json")
        self.assertEqual(json.dumps(result), before)
        self.assertTrue(all(handle.kind == "experiment_output" for handle in _output_handles(qualified)))

    def test_gateway_does_not_guess_paths_or_read_without_registration(self):
        context = ReportContext(topic="Measurements", report_mode="experiment", source_handles=_output_handles([{"handle": "output:registered", "status": "available", "artifact": "raw.json"}]))
        gateway = ReportToolGateway(context)
        for handle in ("output:registered", "raw.json", "../../outside"):
            result = gateway.call(ReportToolCall(tool_name="get_code_task_result", arguments={"output_handle": handle}))
            self.assertEqual(result.status, "not_found")
        result = gateway.call(ReportToolCall(tool_name="get_code_task_result", arguments={"output_handle": "output:registered", "limit": 5000}))
        self.assertEqual(result.status, "error")

    def test_registered_gateway_selects_rows_and_reports_missing_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "raw.json"
            file.write_text(json.dumps([{"condition": 7, "value": 0.42}]))
            context = ReportContext(topic="Measurements", report_mode="experiment",
                source_handles=_output_handles([{"handle": "output:raw", "status": "available", "artifact": "raw.json"}]))
            def read(path, offset, limit, query, record_match):
                return read_output_window(root, root / path, offset=offset, limit=limit,
                                          query=query, record_match=record_match)
            gateway = ReportToolGateway(context, output_reader=read)
            for condition, expected in ((7, "ok"), (8, "not_found")):
                result = gateway.call(ReportToolCall(tool_name="get_code_task_result", arguments={
                    "output_handle": "output:raw", "record_match": {"condition": condition}}))
                self.assertEqual(result.status, expected)
                self.assertEqual(result.content["matched_records"], int(condition == 7))
                self.assertEqual(result.content["search_scope"], "registered_record_array")

    def test_writing_reader_uses_registered_inputs_and_recovers_after_move(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = ArtifactStore(root / "session")
            raw = source.write_text("attempts/experiment-1/outputs/raw.json", "measured facts" * 300,
                                    kind="experiment_output", schema="text.v1")
            context = ReportContext(topic="Measurements", report_mode="experiment", source_handles=_output_handles([{"handle": "output:raw", "status": "available", "artifact": raw.path}]))
            config = ReportRuntimeConfig()
            request = ReportWritingRequest(context, ReportMemory(), config,
                load_report_template_bundle(report_mode="experiment", config=config), object())
            shutil.copytree(source.root, root / "moved")
            for location in (source.root, root / "moved"):
                store = ArtifactStore(location)
                capability = CapabilityContext(ArtifactStore(location / "writer"), AttemptManifest("w"), inputs=(raw,), input_store=store)
                def inspect(**kwargs):
                    result = kwargs["gateway"].call(ReportToolCall(tool_name="get_code_task_result",
                        arguments={"output_handle": "output:raw", "offset": 1200, "limit": 1200}))
                    self.assertEqual(result.status, "ok", result.summary)
                    self.assertEqual(result.content["text"], ("measured facts" * 300)[1200:2400])
                    return None
                with patch("simple_ar.report.writing.run_report_agent", side_effect=inspect):
                    run_report_writing_capability(context=capability, request=request)
                with patch("simple_ar.report.writing.run_report_agent", return_value=None) as writer:
                    run_report_writing_capability(context=replace(capability, inputs=()), request=request)
                    result = writer.call_args.kwargs["gateway"].call(ReportToolCall(tool_name="get_code_task_result",
                        arguments={"output_handle": "output:raw"}))
                    self.assertEqual(result.status, "error")

    def test_application_grants_only_producer_registered_output_inputs(self):
        from simple_ar.app.research_application import create_session, load_session
        from simple_ar.research.workflow_contracts import ResearchBrief
        from simple_ar.report.schema import SourceHandle
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = create_session(ResearchBrief(request_text="Write recorded results"), root=root / "session")
            attempt_store = ArtifactStore(app.controller.store.root / "attempts" / "experiment-1")
            raw = attempt_store.write_text("outputs/raw.csv", "recorded", kind="experiment_output", schema="text.v1")
            attempt_store.write_attempt_manifest(AttemptManifest("experiment-1", capability="experiment", outputs=(raw,)))
            expected = app.controller.store.ref("attempts/experiment-1/outputs/raw.csv",
                kind=raw.kind, schema=raw.schema, producer=raw.producer)
            context = ReportContext(topic="Measurements", report_mode="experiment", source_handles=[
                SourceHandle(handle="output:raw", kind="experiment_output", artifact=expected.path),
                SourceHandle(handle="output:unregistered", kind="experiment_output", artifact="outside.csv")])
            config = ReportRuntimeConfig()
            parts = (context, ReportMemory(), config, load_report_template_bundle(report_mode="experiment", config=config), {})
            for candidate in (app, load_session(root / "session")):
                with patch.object(candidate, "_report_writing_parts", return_value=parts), patch.object(candidate, "_execute", return_value=True) as execute:
                    self.assertTrue(candidate._run_report_write_action())
                    granted = [ref for ref in execute.call_args.args[3] if ref.kind == "experiment_output"]
                    self.assertEqual(granted, [expected])


if __name__ == "__main__":
    unittest.main()

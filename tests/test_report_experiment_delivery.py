"""Reader prose and registered experiment records have separate owners."""
import copy
import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from simple_ar.core.capabilities import ArtifactStore, AttemptManifest, CapabilityContext
from simple_ar.report.audit import ReportAuditCapabilityRequest, run_report_audit_capability
from simple_ar.report.capability import ReportAssemblyRequest, run_report_capability, preview_report_document
from simple_ar.report.narrative import delivery_text_observation, narrative_context, evidence_outline_context
from simple_ar.report.schema import MetricSource, ReportContext, ReportMemory, ReportRuntimeConfig, ReportSectionDraft, ReportSectionPlan


class ExperimentDeliveryTests(unittest.TestCase):
    def test_native_document_review_receives_the_counted_experiment_block_and_resume_reuses_it(self):
        from simple_ar.report.agent import run_report_agent
        from simple_ar.report.templates import load_report_template_bundle
        from simple_ar.report.tool_gateway import ReportToolGateway

        for mode in ("linked", "full"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                context, base_config, request, runtime = self.objects(Path(temp), mode)
                config = base_config.model_copy(update={"document_review": True, "max_review_iterations": 0})
                scope = ReportSectionDraft(section_id="scope", heading="Scope", draft_markdown="This describes recorded results only.")
                request = replace(request, sections=(scope, *request.sections))
                plan = ReportSectionPlan(section_id="results", heading="Results", goal="Explain recorded results", draft_order=1, final_order=1)
                memory = ReportMemory(section_plan=[ReportSectionPlan(section_id="scope", heading="Scope", goal="State evidence limits"), plan])
                checkpoint = {"memory": memory.model_dump(mode="json"),
                    "sections": [row.model_dump(mode="json") for row in request.sections], "iterations": [],
                    "reviewer_findings": [], "tool_results": [], "pending_draft": None,
                    "document_review_done": False}
                before = copy.deepcopy(checkpoint)
                calls = []
                saved = []

                class Client:
                    def ask_json(self, system, prompt, *, label="", **kwargs):
                        calls.append((label, json.loads(prompt)))
                        return {"section_reviews": [{"section_id": sid, "verdict": "pass", "findings": []}
                            for sid in ("scope", "results")]}

                template = load_report_template_bundle(report_mode="experiment", config=config)
                result = run_report_agent(client=Client(), context=context, memory=memory, config=config,
                    template=template, gateway=ReportToolGateway(context), completed_checkpoint=checkpoint,
                    checkpoint_sink=lambda value: saved.append(copy.deepcopy(value)))
                self.assertIsNotNone(result)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][0], "report-document-reviewer")
                payload = calls[0][1]
                expected = evidence_outline_context(context, memory, config)["assembly_owned_content"]
                self.assertEqual(payload["assembly_owned_content"], expected)
                self.assertEqual(len(expected), 1)
                self.assertIn("experiment_evidence.", expected[0]["markdown"])
                observation = delivery_text_observation(context, memory, list(request.sections), config)
                self.assertEqual(payload["delivery_text_observation"], observation)
                self.assertEqual(checkpoint, before)
                run_report_agent(client=Client(), context=context, memory=memory, config=config,
                    template=template, gateway=ReportToolGateway(context), completed_checkpoint=saved[-1])
                self.assertEqual(len(calls), 1, "Completed document review must not run again on restoration.")

    def objects(self, root, mode="linked", body="Recorded accuracy was 0.8 under the declared protocol."):
        inputs = ArtifactStore(root / "inputs")
        source = inputs.write_json("measurements.json", {"rows": [{"accuracy": 0.8}, {"accuracy": 0.9}]})
        context = ReportContext(topic="Selected comparison", report_mode="experiment",
            results={"metrics": {"accuracy": 0.8}, "comparisons": [], "execution_record": {"returncode": 0}},
            metric_sources=[MetricSource(metric_id="accuracy", name="accuracy", value=0.8,
                artifact=source.path, label="candidate")])
        config = ReportRuntimeConfig(data_tables=mode, figures={"enabled": False})
        draft = ReportSectionDraft(section_id="results", heading="Results", draft_markdown=body)
        request = ReportAssemblyRequest(title=context.topic, sections=(draft,), config=config,
            experiment_context=context, experiment_inputs=(source,))
        runtime = CapabilityContext(store=ArtifactStore(root / "report"), attempt=AttemptManifest(attempt_id="report"),
            inputs=(source,), input_store=inputs)
        return context, config, request, runtime

    def audit(self, request, runtime, context):
        store = runtime.store
        refs = tuple(store.ref(path) for path in ("report.md", "report_body.md", "experiment_evidence.json", "experiment_evidence.md"))
        audit_context = CapabilityContext(store=ArtifactStore(store.root.parent / "audit"),
            attempt=AttemptManifest(attempt_id="audit"), inputs=refs, input_store=store)
        result = run_report_audit_capability(context=audit_context, request=ReportAuditCapabilityRequest(
            report_ref=refs[0], report_body_ref=refs[1], context=context, memory=ReportMemory(),
            experiment_evidence_ref=refs[2], experiment_records_ref=refs[3]))
        return result, audit_context.store.read_json("report_audit.json")

    def test_linked_full_preserve_same_records_and_do_not_change_writer_or_sources(self):
        packages = []
        for mode in ("linked", "full"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                context, config, request, runtime = self.objects(Path(temp), mode)
                before = copy.deepcopy(context.model_dump(mode="json"))
                source_bytes = runtime.require_input(request.experiment_inputs[0]).read_bytes()
                preview = preview_report_document(request)
                result = run_report_capability(context=runtime, request=request)
                text = runtime.store.read_text("report.md")
                self.assertEqual(text, preview.report_markdown)
                self.assertEqual(context.model_dump(mode="json"), before)
                self.assertEqual(runtime.require_input(request.experiment_inputs[0]).read_bytes(), source_bytes)
                self.assertEqual(runtime.store.read_text("experiment_sources/001.json").encode(), source_bytes)
                self.assertEqual(request.sections[0].draft_markdown, "Recorded accuracy was 0.8 under the declared protocol.")
                self.assertEqual("Metric Provenance" in text, mode == "full")
                self.assertIn("Metric Provenance", runtime.store.read_text("experiment_evidence.md"))
                self.assertTrue(any(ref.path == "experiment_evidence.json" for ref in result.artifacts))
                packages.append(runtime.store.read_json("experiment_evidence.json"))
                observation = delivery_text_observation(context, ReportMemory(), list(request.sections), config)
                self.assertEqual(observation["markdown_token_count"], len(text.split()))
                plan = ReportSectionPlan(section_id="results", heading="Results", goal="Explain recorded results")
                memory = ReportMemory(section_plan=[plan])
                view = narrative_context(memory, plan, list(request.sections), context=context, config=config)
                outline = evidence_outline_context(context, memory, config)
                self.assertEqual(view["assembly_owned_content"], outline["assembly_owned_content"])
                block = view["assembly_owned_content"][0]
                self.assertEqual(view["length_observation"]["assembly_owned_markdown_tokens"], len(block["markdown"].split()))
                self.assertEqual(block["heading"] in text, True)
                audit_result, audit = self.audit(request, runtime, context)
                self.assertEqual(audit_result.status, "completed")
                self.assertEqual(audit["metric_audit"]["status"], "passed")
        self.assertEqual(packages[0], packages[1])

    def test_attachment_cannot_rescue_a_missing_required_body_metric(self):
        with tempfile.TemporaryDirectory() as temp:
            context, _, request, runtime = self.objects(Path(temp), body="A completed run has recorded values in its attachment.")
            run_report_capability(context=runtime, request=request)
            result, audit = self.audit(request, runtime, context)
            self.assertEqual(result.status, "partial")
            self.assertIn("accuracy", audit["metric_audit"]["unmatched_metrics"])

    def test_native_link_without_registered_attachment_cannot_pass_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            recorded, _, request, runtime = self.objects(Path(temp))
            run_report_capability(context=runtime, request=request)
            refs = tuple(runtime.store.ref(path) for path in ("report.md", "report_body.md"))
            audit_store = ArtifactStore(Path(temp) / "audit")
            result = run_report_audit_capability(context=CapabilityContext(store=audit_store,
                attempt=AttemptManifest("missing-attachment"), inputs=refs, input_store=runtime.store),
                request=ReportAuditCapabilityRequest(report_ref=refs[0], report_body_ref=refs[1],
                    context=recorded, memory=ReportMemory()))
            self.assertEqual(result.status, "failed")
            self.assertTrue(any(row["finding_id"] == "experiment-attachment-consistency"
                                for row in audit_store.read_json("report_audit.json")["reviewer_findings"]))

    def test_changed_json_markdown_or_missing_copy_cannot_pass_attachment_audit(self):
        for changed in ("json", "markdown", "missing_copy"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temp:
                context, _, request, runtime = self.objects(Path(temp))
                run_report_capability(context=runtime, request=request)
                if changed == "json":
                    package = runtime.store.read_json("experiment_evidence.json")
                    package["records"]["metric_sources"][0]["value"] = 0.99
                    runtime.store.write_json("experiment_evidence.json", package)
                elif changed == "markdown":
                    runtime.store.write_text("experiment_evidence.md", "Altered records.")
                else:
                    runtime.store.resolve("experiment_sources/001.json").unlink()
                result, audit = self.audit(request, runtime, context)
                self.assertEqual(result.status, "failed")
                self.assertEqual(audit["metric_audit"]["status"], "failed")

    def test_unregistered_missing_directory_and_symlink_sources_are_not_copied(self):
        for invalid in ("unregistered", "missing", "directory", "symlink"):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as temp:
                context, _, request, runtime = self.objects(Path(temp))
                source = runtime.require_input(request.experiment_inputs[0])
                if invalid == "unregistered":
                    runtime = replace(runtime, inputs=())
                elif invalid == "missing":
                    source.unlink()
                elif invalid == "directory":
                    source.unlink()
                    source.mkdir()
                else:
                    outside = Path(temp) / "outside.json"
                    outside.write_text("{}")
                    source.unlink()
                    try:
                        source.symlink_to(outside)
                    except OSError:
                        self.skipTest("File symlinks unavailable")
                with self.assertRaises((ValueError, FileNotFoundError)):
                    run_report_capability(context=runtime, request=request)

    def test_standalone_move_retains_native_links_and_copied_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            context, _, request, runtime = self.objects(Path(temp))
            run_report_capability(context=runtime, request=request)
            moved = Path(temp) / "moved"
            shutil.copytree(runtime.store.root, moved)
            self.assertIn("(experiment_evidence.md)", (moved / "report.md").read_text())
            self.assertIn("(experiment_evidence.json)", (moved / "experiment_evidence.md").read_text())
            package = json.loads((moved / "experiment_evidence.json").read_text())
            for row in package["source_artifacts"]:
                self.assertTrue((moved / row["copied_path"]).is_file())
            from simple_ar.report.export import _copy_experiment_records
            export = Path(temp) / "export"
            export.mkdir()
            included = _copy_experiment_records(moved.resolve(), export)
            self.assertIn("experiment_sources/001.json", included)
            self.assertEqual((export / "experiment_sources/001.json").read_bytes(),
                             (moved / "experiment_sources/001.json").read_bytes())

    def test_export_does_not_follow_forged_record_source_paths(self):
        from simple_ar.report.export import _copy_experiment_records, ReportExportError
        with tempfile.TemporaryDirectory() as temp:
            context, _, request, runtime = self.objects(Path(temp))
            run_report_capability(context=runtime, request=request)
            package = runtime.store.read_json("experiment_evidence.json")
            package["source_artifacts"][0]["copied_path"] = "../inputs/measurements.json"
            runtime.store.write_json("experiment_evidence.json", package)
            export = Path(temp) / "export"
            export.mkdir()
            with self.assertRaises(ReportExportError):
                _copy_experiment_records(runtime.store.root.resolve(), export)
            self.assertFalse(list(export.iterdir()))

    def test_acm_export_preserves_native_links_without_copying_arbitrary_links(self):
        from unittest.mock import patch
        from simple_ar.report.export import export_acm_report
        with tempfile.TemporaryDirectory() as temp:
            _, _, request, runtime = self.objects(Path(temp))
            run_report_capability(context=runtime, request=request)
            links = ["experiment_evidence.md", "../inputs/measurements.json"]
            ast = {"pandoc-api-version": [1, 22], "meta": {}, "blocks": [
                {"t": "Para", "c": [{"t": "Link", "c": [["", [], []],
                    [{"t": "Str", "c": "Records"}], [target, ""]]}]} for target in links]}
            def convert(argv, **kwargs):
                if "--to=json" in argv:
                    return json.dumps(ast)
                document = json.loads(kwargs["text"])
                if "--to=markdown" in argv:
                    self.assertEqual(document["blocks"][0]["c"][0]["t"], "Link")
                    self.assertEqual(document["blocks"][1]["c"][0]["t"], "Span")
                    return "[Records](experiment_evidence.md)\n"
                return "Converted text"
            with patch("simple_ar.report.export.shutil.which", return_value="pandoc"), patch(
                    "simple_ar.report.export._run", side_effect=convert):
                manifest = export_acm_report(runtime.store.root, Path(temp) / "acm")
            self.assertIn("experiment_sources/001.json", manifest["experiment_record_files"])
            self.assertEqual(manifest["external_source_references"], [links[1]])
            self.assertFalse(manifest["quality_checked"])
            self.assertEqual((Path(temp) / "acm/experiment_sources/001.json").read_bytes(),
                             runtime.store.resolve("experiment_sources/001.json").read_bytes())

    def test_no_records_or_nonexperiment_reports_gain_no_evidence_block(self):
        with tempfile.TemporaryDirectory() as temp:
            context, _, request, runtime = self.objects(Path(temp))
            context.metric_sources = []
            result = run_report_capability(context=runtime, request=request)
            self.assertFalse(any(ref.path.startswith("experiment_evidence") for ref in result.artifacts))
            self.assertNotIn("Experiment Records", runtime.store.read_text("report.md"))


if __name__ == "__main__":
    unittest.main()

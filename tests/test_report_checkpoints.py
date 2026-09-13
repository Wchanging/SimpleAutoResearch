"""Writer checkpoint input identity at the persisted capability boundary."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simple_ar.core.capabilities import ArtifactStore, AttemptManifest, CapabilityContext
from simple_ar.report.schema import ReportContext, ReportMemory, ReportRuntimeConfig, ReportSectionPlan
from simple_ar.report.agent import run_report_agent
from simple_ar.report.tool_gateway import ReportToolGateway
from simple_ar.integrations.llm import LLMError
from simple_ar.report.templates import load_report_template_bundle
from simple_ar.report.writing import ReportWritingRequest, run_report_writing_capability


class ReportCheckpointTests(unittest.TestCase):
    def test_reviewer_failure_preserves_draft_without_repeating_writer(self):
        context = ReportContext(topic="Calibration", report_mode="experiment")
        memory = ReportMemory(section_plan=[ReportSectionPlan(section_id="method", heading="Method", goal="Describe evidence")])
        config = ReportRuntimeConfig(allow_llm_fallback=False, max_review_iterations=0)
        template = load_report_template_bundle(report_mode="experiment", config=config)
        saved = []

        class Client:
            fail_review = True
            writes = 0

            def ask_json(self, *args, label="", **kwargs):
                if "reviewer" in label:
                    if self.fail_review:
                        raise LLMError("review unavailable")
                    return {"section_id": "method", "verdict": "pass", "findings": []}
                self.writes += 1
                return {"section_id": "method", "heading": "Method", "draft_markdown": "No empirical validation was performed."}

        client = Client()
        kwargs = dict(client=client, context=context, memory=memory, config=config,
                      template=template, gateway=ReportToolGateway(context), checkpoint_sink=saved.append)
        with self.assertRaises(LLMError):
            run_report_agent(**kwargs)
        self.assertEqual(client.writes, 1)
        self.assertEqual(saved[-1]["sections"], [])
        self.assertEqual(saved[-1]["pending_draft"]["section_id"], "method")
        client.fail_review = False
        result = run_report_agent(**kwargs, completed_checkpoint=saved[-1])
        self.assertEqual(client.writes, 1)
        self.assertEqual(result.sections[0].draft_markdown, "No empirical validation was performed.")
        self.assertIsNone(saved[-1]["pending_draft"])

    def test_prompt_metrics_preserve_all_conditions_without_storage_metadata(self):
        from simple_ar.report.agent import _prompt_metrics
        from simple_ar.report.schema import MetricSource
        metrics = [MetricSource(metric_id=f"m{i}", name="accuracy", value=i / 20,
                                label="baseline" if i < 10 else "candidate",
                                artifact="results.json", protocol_fingerprint="a" * 64)
                   for i in range(20)]
        rows = _prompt_metrics(ReportMemory(metric_sources=metrics))
        self.assertEqual(len(rows), 20)
        self.assertEqual([row["value"] for row in rows], [metric.value for metric in metrics])
        self.assertEqual(rows[-1]["label"], "candidate")
        self.assertTrue(all("protocol_fingerprint" not in row and "artifact" not in row for row in rows))
        self.assertEqual(metrics[0].artifact, "results.json")

    def test_reviewer_failure_obeys_explicit_fallback_setting(self):
        context = ReportContext(topic="Calibration", report_mode="experiment")
        memory = ReportMemory(section_plan=[ReportSectionPlan(section_id="method", heading="Method", goal="Describe evidence")])

        class Client:
            def ask_json(self, *args, label="", **kwargs):
                if "reviewer" in label:
                    raise LLMError("review provider unavailable")
                return {"section_id": "method", "heading": "Method", "draft_markdown": "No empirical validation was performed."}

        for allow_fallback in (False, True):
            with self.subTest(allow_fallback=allow_fallback):
                config = ReportRuntimeConfig(allow_llm_fallback=allow_fallback, max_review_iterations=0)
                kwargs = dict(client=Client(), context=context, memory=memory, config=config,
                              template=load_report_template_bundle(report_mode="experiment", config=config),
                              gateway=ReportToolGateway(context))
                if not allow_fallback:
                    with self.assertRaisesRegex(LLMError, "review provider unavailable"):
                        run_report_agent(**kwargs)
                else:
                    result = run_report_agent(**kwargs)
                    self.assertEqual(result.reviewer_findings[0].type, "review_agent_fallback")
                    self.assertIn("fallback", result.reviewer_findings[0].message)

    def test_only_matching_inputs_reuse_completed_sections(self):
        config = ReportRuntimeConfig()
        request = ReportWritingRequest(
            ReportContext(topic="Calibration", report_mode="experiment"), ReportMemory(), config,
            load_report_template_bundle(report_mode="experiment", config=config), object(),
        )
        saved = {"sections": [{"section_id": "method", "draft_markdown": "Saved method."}]}
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(Path(tmp))
            first = CapabilityContext(store=store, attempt=AttemptManifest("writer-1"))

            def interrupted(**kwargs):
                kwargs["checkpoint_sink"](saved)
                return None

            with patch("simple_ar.report.writing.run_report_agent", side_effect=interrupted):
                result = run_report_writing_capability(context=first, request=request)
            self.assertEqual(result.status, "failed")
            checkpoint = next(ref for ref in result.artifacts if ref.kind == "report_checkpoint")
            for change in ("none", "location", "topic", "template", "criteria"):
                changed = change in {"topic", "template", "criteria"}
                with self.subTest(change=change):
                    output = ArtifactStore(Path(tmp) / change)
                    retry = CapabilityContext(store=output, attempt=AttemptManifest("writer-2"),
                                              inputs=(checkpoint,), input_store=store)
                    context = request.report_context.model_copy(update={"topic": "Changed"}) if change == "topic" else request.report_context
                    updates = {"template_path": "another/template.md", "criteria_path": "another/criteria.md"} if change == "location" else (
                        {f"{change}_markdown": "Changed writing instructions"} if change in {"template", "criteria"} else {}
                    )
                    resumed = replace(request, report_context=context, template=request.template.model_copy(update=updates), resume_ref=checkpoint)
                    with patch("simple_ar.report.writing.run_report_agent", side_effect=interrupted) as writer:
                        run_report_writing_capability(context=retry, request=resumed)
                    self.assertEqual(writer.call_args.kwargs["completed_checkpoint"], None if changed else saved)
                    lineage = output.read_json("sections.json")["resume_ref"]
                    self.assertEqual(lineage, None if changed else checkpoint.to_dict())

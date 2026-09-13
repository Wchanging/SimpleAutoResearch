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
    def test_minor_factual_finding_is_revised_within_existing_limit(self):
        context = ReportContext(topic="Calibration", report_mode="experiment")
        memory = ReportMemory(section_plan=[ReportSectionPlan(section_id="method", heading="Method", goal="Describe evidence")])
        for kind in ("metric_mismatch", "unsupported_claim", "missing_limitation"):
            with self.subTest(kind=kind):
                calls = []

                class Client:
                    def ask_json(self, *args, label="", **kwargs):
                        calls.append(label)
                        if "reviewer" in label:
                            return {"section_id": "method", "verdict": "pass", "findings": [
                                {"finding_id": "wording", "type": kind, "severity": "minor",
                                 "message": "Accuracy is not a likelihood-based metric."}]}
                        return {"section_id": "method", "heading": "Method",
                                "draft_markdown": "Accuracy measures classification correctness."
                                if "reviser" in label else "Accuracy is likelihood-based."}

                config = ReportRuntimeConfig(allow_llm_fallback=False, max_review_iterations=1)
                result = run_report_agent(client=Client(), context=context, memory=memory, config=config,
                    template=load_report_template_bundle(report_mode="experiment", config=config),
                    gateway=ReportToolGateway(context))
                revise = kind != "missing_limitation"
                self.assertEqual(sum("reviser" in label for label in calls), int(revise))
                self.assertEqual("classification correctness" in result.report_body, revise)

    def test_final_audit_uses_latest_review_without_erasing_history_or_failed_review(self):
        from simple_ar.report.audit import build_report_audit
        context = ReportContext(topic="Calibration", report_mode="experiment")
        memory = ReportMemory(section_plan=[ReportSectionPlan(section_id="method", heading="Method", goal="Describe evidence")])
        config = ReportRuntimeConfig(allow_llm_fallback=True, max_review_iterations=1)
        finding = {"finding_id": "unsupported", "type": "unsupported_claim", "severity": "critical",
                   "section_id": "method", "message": "Unsupported performance claim"}
        for final_review in ("pass", "fail", "unavailable"):
            with self.subTest(final_review=final_review):
                class Client:
                    reviews = 0

                    def ask_json(self, *args, label="", **kwargs):
                        if "reviewer" not in label:
                            return {"section_id": "method", "heading": "Method", "draft_markdown": "No empirical validation is claimed."}
                        self.reviews += 1
                        if self.reviews > 1 and final_review == "unavailable":
                            raise LLMError("review unavailable")
                        passed = self.reviews > 1 and final_review == "pass"
                        return {"section_id": "method", "verdict": "pass" if passed else "revise_required",
                                "findings": [] if passed else [finding]}

                result = run_report_agent(client=Client(), context=context, memory=memory, config=config,
                                          template=load_report_template_bundle(report_mode="experiment", config=config),
                                          gateway=ReportToolGateway(context))
                self.assertTrue(any(f.severity == "critical" for f in result.reviewer_findings))
                self.assertTrue(any(f.severity == "critical" for f in result.iterations[1].findings))
                audit = build_report_audit(report=result.report_body, report_body=result.report_body,
                                           context=context, memory=result.memory)
                self.assertEqual(audit.status, "passed" if final_review == "pass" else "failed")
                self.assertEqual(audit.semantic_review_status, "semantic_unchecked")

    def test_format_retry_does_not_repeat_transport_or_budget_failure(self):
        from simple_ar.integrations.llm import LLMResponseError
        context = ReportContext(topic="Calibration", report_mode="experiment")
        memory = ReportMemory(section_plan=[ReportSectionPlan(section_id="method", heading="Method", goal="Describe evidence")])
        config = ReportRuntimeConfig(allow_llm_fallback=False, max_review_iterations=0)
        template = load_report_template_bundle(report_mode="experiment", config=config)
        for phase in ("writer", "reviewer"):
            for error in (LLMError("provider unavailable"), LLMError("budget exhausted"), LLMResponseError("invalid JSON")):
                with self.subTest(phase=phase, error=str(error)):
                    calls = []

                    class Client:
                        def ask_json(self, *args, label="", **kwargs):
                            if f"report-{phase}" in label:
                                calls.append(label)
                                if len(calls) == 1:
                                    raise error
                            if "reviewer" in label:
                                return {"section_id": "method", "verdict": "pass", "findings": []}
                            return {"section_id": "method", "heading": "Method", "draft_markdown": "No measurements were taken."}

                    kwargs = dict(client=Client(), context=context, memory=memory, config=config,
                                  template=template, gateway=ReportToolGateway(context))
                    if isinstance(error, LLMResponseError):
                        result = run_report_agent(**kwargs)
                        self.assertEqual(len(result.sections), 1)
                        self.assertEqual(len(calls), 2)
                    else:
                        with self.assertRaisesRegex(LLMError, str(error)):
                            run_report_agent(**kwargs)
                        self.assertEqual(len(calls), 1)

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
        table = _prompt_metrics(ReportMemory(metric_sources=metrics))
        rows = [dict(zip(table["columns"], row)) for row in table["rows"]]
        self.assertEqual(len(rows), 20)
        self.assertEqual([row["value"] for row in rows], [metric.value for metric in metrics])
        self.assertEqual(rows[-1]["label"], "candidate")
        self.assertTrue(all("protocol_fingerprint" not in row and "artifact" not in row for row in rows))
        self.assertEqual(metrics[0].artifact, "results.json")
        for actual, metric in zip(rows, metrics):
            self.assertEqual(actual, metric.model_dump(include=set(table["columns"])))

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

"""Final report tables must preserve measurement conditions, not just numbers."""

import unittest

from simple_ar.report.projection import (
    _metric_ledger,
    _verified_experiment_evidence,
    attach_implementation_evidence,
)
from simple_ar.core import ArtifactStore
from pathlib import Path
import tempfile
from simple_ar.report.audit import build_report_audit
from simple_ar.report.schema import MetricSource, ReportContext, ReportMemory


class ReportMeasurementAuditTests(unittest.TestCase):
    def test_paired_report_keeps_detailed_measurements_out_of_paper_body(self):
        summary = {
            "metric": "accuracy",
            "n": 3,
            "baseline_mean": 0.60,
            "candidate_mean": 0.65,
            "delta_mean": 0.05,
            "delta_sample_std": 0.01,
        }
        metrics = [
            MetricSource(
                metric_id=f"metric:summary:{field}",
                name=f"accuracy.{field}",
                value=value,
                artifact="outputs/experiment_set.json",
                label="paired_summary:0",
            )
            for field, value in (
                ("n", 3),
                ("baseline_mean", 0.60),
                ("candidate_mean", 0.65),
                ("delta_mean", 0.05),
                ("delta_sample_std", 0.01),
            )
        ]
        context = ReportContext(
            topic="Continual learning",
            report_mode="experiment",
            metric_sources=metrics,
            results={
                "paired_summary": [summary],
                "collection_ref": {"path": "outputs/experiment_set.json"},
            },
        )
        body = _verified_experiment_evidence(context)
        self.assertIn("Aggregate Paired Metrics", body)
        self.assertIn("0.65", body)
        self.assertNotIn("accuracy_after_task_", body)
        self.assertLess(len(body.splitlines()), 20)
        audit = build_report_audit(
            report=body,
            report_body=body,
            context=context,
            memory=ReportMemory(),
        )
        self.assertEqual(audit.metric_audit.status, "passed")
        self.assertEqual(audit.metric_audit.unmatched_metrics, [])

    def test_numeric_prose_is_not_a_measurement_or_a_proof_of_support(self):
        from simple_ar.report.schema import ReportAudit
        metrics = [MetricSource(metric_id="accuracy", name="accuracy", value=0.8, artifact="candidate.json",
                                label="candidate", condition_id="eval", unit="fraction", source_kind="measured")]
        context = ReportContext(topic="Classifier", report_mode="experiment", metric_sources=metrics)
        for prose in ("Execution was limited to 20 seconds.", "A survey discusses 37 papers.",
                      "An unsupported sentence claims a sample size of 999."):
            body = _metric_ledger(metrics) + "\n" + prose
            audit = build_report_audit(report=body, report_body=body, context=context, memory=ReportMemory())
            self.assertEqual(audit.metric_audit.status, "passed")
            self.assertEqual(audit.semantic_review_status, "semantic_unchecked")
        # Historical warnings remain readable; they are not rewritten as passing.
        old = ReportAudit.model_validate({"status": "warning", "metric_audit": {
            "status": "warning", "unmatched_numbers": ["20"],
        }})
        self.assertEqual(old.metric_audit.unmatched_numbers, ["20"])
        self.assertEqual(old.status, "warning")

    def test_experiment_sections_keep_design_sources_beyond_search_prefix(self):
        from simple_ar.report.memory import initialize_report_memory
        from simple_ar.report.schema import SourceHandle, ReportRuntimeConfig
        from simple_ar.report.templates import load_report_template_bundle

        papers = [SourceHandle(handle=f"paper:p{i}", kind="paper", paper_id=f"p{i}",
                               title="General research survey") for i in range(12)]
        papers[-1].title = "Original replay method"
        context = ReportContext(
            topic="Replay", report_mode="experiment", max_section_sources=3,
            experiment_plan={"motivation_refs": ["p11#claim-001"]},
            source_handles=[SourceHandle(handle="execution", kind="experiment"), *papers],
        )
        template = load_report_template_bundle(report_mode="experiment", config=ReportRuntimeConfig())
        memory = initialize_report_memory(context=context, template=template)
        for section in memory.section_plan:
            self.assertIn("paper:p11", section.evidence_handles)
            self.assertIn("execution", section.evidence_handles)
            self.assertLessEqual(len(section.evidence_handles), 3)

    def test_writer_tool_reads_frozen_patch_with_truncation_and_provenance(self):
        from simple_ar.report.tool_gateway import ReportToolGateway
        from simple_ar.report.schema import ReportToolCall

        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(Path(tmp))
            patch = "--- method.py\n+++ method.py\n+use_phrase_features = True\n" + "x" * 12000
            store.write_text("attempts/implement-1/code_task/patch.diff", patch)
            ref = store.write_json("attempts/implement-1/implementation.json", {
                "status": "validated", "asset_integrity": {"status": "observed_unchanged"},
                "artifact_refs": {"patch": {"path": "code_task/patch.diff"}},
            })
            context = ReportContext(
                topic="Classifier",
                report_mode="experiment",
                experiment_plan={"contract_id": "fixture-v1", "metric": "accuracy"},
                results={"metrics": {"accuracy": 0.8}},
            )
            attach_implementation_evidence(context, store, ref)
            result = ReportToolGateway(context).call(ReportToolCall(tool_name="get_code_task_result", arguments={}))
            implementation = result.content["results"]["implementation"]
            self.assertEqual(result.content["results"]["metrics"], {"accuracy": 0.8})
            evidence = implementation["evidence"]["patch"]
            self.assertEqual(evidence["text"], patch[:12000])
            self.assertTrue(evidence["truncated"])
            self.assertEqual(evidence["artifact"], "attempts/implement-1/code_task/patch.diff")
            self.assertNotIn("review", implementation["evidence"])
            # Tool accessibility alone does not prove the agents received it.
            import json
            from simple_ar.report.agent import run_report_agent
            from simple_ar.report.schema import ReportRuntimeConfig, ReportSectionPlan
            from simple_ar.report.templates import load_report_template_bundle
            received = {}

            class Client:
                fail_first_draft = False

                def ask_json(self, system, user, *, label="", **kwargs):
                    payload = json.loads(user[user.index("{"):])
                    if "reviewer" in label:
                        received["reviewer"] = payload["verified_execution_results"]["implementation"]
                        received["reviewer_plan"] = payload["experiment_plan"]
                        return {"section_id": "method", "verdict": "pass", "findings": []}
                    if self.fail_first_draft and not label.endswith("-retry"):
                        from simple_ar.integrations.llm import LLMResponseError
                        raise LLMResponseError("invalid section JSON")
                    evidence_context = payload if label.endswith("-retry") else payload["global_research_context"]
                    received["writer"] = evidence_context["verified_execution_results"]["implementation"]
                    received["writer_plan"] = evidence_context["experiment_plan"]
                    return {"section_id": "method", "heading": "Method", "draft_markdown": "The recorded patch enables phrase features; the remainder is truncated."}

            config = ReportRuntimeConfig(max_review_iterations=0)
            memory = ReportMemory(section_plan=[ReportSectionPlan(section_id="method", heading="Method", goal="Describe actual changes")])
            run_report_agent(client=Client(), context=context, memory=memory, config=config,
                             template=load_report_template_bundle(report_mode="experiment", config=config),
                             gateway=ReportToolGateway(context))
            self.assertEqual(
                set(received),
                {"writer", "reviewer", "writer_plan", "reviewer_plan"},
            )
            self.assertEqual(received["writer_plan"], context.experiment_plan)
            self.assertEqual(received["reviewer_plan"], context.experiment_plan)
            for role in ("writer", "reviewer"):
                view = received[role]
                self.assertEqual(view["evidence"]["patch"], evidence)
                self.assertEqual(view["asset_integrity"], implementation["asset_integrity"])
            received.clear()
            retry_client = Client()
            retry_client.fail_first_draft = True
            run_report_agent(client=retry_client, context=context, memory=memory, config=config,
                             template=load_report_template_bundle(report_mode="experiment", config=config),
                             gateway=ReportToolGateway(context))
            self.assertEqual(
                set(received),
                {"writer", "reviewer", "writer_plan", "reviewer_plan"},
            )
            self.assertEqual(received["writer_plan"], context.experiment_plan)
            self.assertEqual(received["reviewer_plan"], context.experiment_plan)
            for role in ("writer", "reviewer"):
                self.assertEqual(received[role]["evidence"]["patch"], evidence)

    def test_swapped_values_or_conditions_fail_even_when_all_numbers_are_present(self):
        metrics = [MetricSource(metric_id=f"metric:{label}:accuracy", name="accuracy", value=value,
                               artifact=f"{label}.json", label=label, condition_id=label, unit="fraction", source_kind="measured")
                   for label, value in (("baseline", 0.6), ("candidate", 0.8))]
        context = ReportContext(topic="Calibration", report_mode="experiment", metric_sources=metrics)
        valid = _metric_ledger(metrics)
        variants = {
            "valid": valid,
            "swapped_values": valid.replace("0.6", "TEMP").replace("0.8", "0.6").replace("TEMP", "0.8"),
            "wrong_unit": valid.replace("fraction", "percent"),
            "wrong_condition": valid.replace("fraction | baseline", "fraction | candidate"),
            "unknown_metric": valid.replace("`accuracy`", "`f1`"),
        }
        for name, body in variants.items():
            with self.subTest(name=name):
                audit = build_report_audit(report=body, report_body=body, context=context, memory=ReportMemory())
                self.assertEqual(audit.metric_audit.status, "passed" if name == "valid" else "failed")
                self.assertEqual(audit.semantic_review_status, "semantic_unchecked")
        # Merely mentioning both numbers in prose proves neither attribution nor improvement.
        body = "Baseline accuracy 0.8 exceeds candidate accuracy 0.6."
        audit = build_report_audit(report=body, report_body=body, context=context, memory=ReportMemory())
        self.assertEqual(audit.semantic_review_status, "semantic_unchecked")

    def test_literature_report_cannot_invent_a_measurement_ledger(self):
        body = "| Source | Metric | Value | Unit | Condition | Origin |\n| --- | --- | --- | --- | --- | --- |\n| candidate | `accuracy` | 0.8 | fraction | candidate | measured |"
        audit = build_report_audit(report=body, report_body=body,
                                  context=ReportContext(topic="Review", report_mode="research_only"), memory=ReportMemory())
        self.assertEqual(audit.metric_audit.status, "failed")

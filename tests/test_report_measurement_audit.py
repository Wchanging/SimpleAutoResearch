"""Final report tables must preserve measurement conditions, not just numbers."""

import unittest

from simple_ar.report.projection import _metric_ledger, attach_implementation_evidence
from simple_ar.core import ArtifactStore
from pathlib import Path
import tempfile
from simple_ar.report.audit import build_report_audit
from simple_ar.report.schema import MetricSource, ReportContext, ReportMemory


class ReportMeasurementAuditTests(unittest.TestCase):
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
            context = ReportContext(topic="Classifier", report_mode="experiment", results={"metrics": {"accuracy": 0.8}})
            attach_implementation_evidence(context, store, ref)
            result = ReportToolGateway(context).call(ReportToolCall(tool_name="get_code_task_result", arguments={}))
            implementation = result.content["results"]["implementation"]
            self.assertEqual(result.content["results"]["metrics"], {"accuracy": 0.8})
            evidence = implementation["evidence"]["patch"]
            self.assertEqual(evidence["text"], patch[:12000])
            self.assertTrue(evidence["truncated"])
            self.assertEqual(evidence["artifact"], "attempts/implement-1/code_task/patch.diff")
            self.assertNotIn("review", implementation["evidence"])

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

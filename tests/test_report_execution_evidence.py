"""Factual provenance is shared by writing/review, without running code."""
import json
import unittest

from simple_ar.report.agent import (
    _writer_prompt, _writer_recovery_prompt, _reviewer_prompt, run_report_agent,
)
from simple_ar.report.execution_evidence import execution_record, report_execution_evidence
from simple_ar.report.schema import (
    ReportContext, ReportMemory, ReportRuntimeConfig, ReportSectionDraft, ReportSectionPlan,
)
from simple_ar.report.templates import load_report_template_bundle
from simple_ar.report.tool_gateway import ReportToolGateway
from simple_ar.integrations.llm import LLMError


class ReportExecutionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.context = ReportContext(
            topic="A fixed reproduction", report_mode="experiment",
            execution_context="Requested CPU.\n## Prepared execution specification\n120 second limit.",
            experiment_plan={"dataset": "Declared synthetic data", "comparison_conditions": {"seed": 7}},
            results={"status": "passed", "command": ["not", "an", "observation"],
                     "execution": {"process": {
                         "invocation_id": "run-1", "status": "finished",
                         "argv": ["python", "run.py", "--seed", "7"], "cwd": "/work",
                         "timeout_sec": 120, "duration_sec": 0.8, "returncode": 0,
                         "streams": {"stdout": {"log_path": "/work/stdout.log", "bytes_seen": 20,
                                                   "log_bytes_discarded": 0}},
                     }}},
        )

    def test_only_executor_record_is_an_observation(self):
        evidence = report_execution_evidence(self.context)
        self.assertEqual(evidence["declared_protocol"]["comparison_conditions"]["seed"], 7)
        self.assertIn("Prepared execution specification", evidence["declared_execution_context"]["text"])
        observed = evidence["execution_records"][0]
        self.assertEqual(observed["argv"], ["python", "run.py", "--seed", "7"])
        self.assertEqual(observed["duration_sec"], 0.8)
        self.assertEqual(observed["timeout_sec"], 120)
        self.assertNotIn("hardware", observed)
        self.assertEqual(evidence["implementation_verification"]["status"], "not_checked")
        self.assertEqual(execution_record({"command": ["python"], "status": "passed"}),
                         {"observation_status": "not_recorded"})

    def test_history_preserves_failed_and_passed_observations_without_duplicate(self):
        record = execution_record(self.context.results)
        self.context.results["measurement_history"] = [
            {"action": "candidate", "execution_record": record},
            {"action": "repair", "execution_record": {**record, "invocation_id": "run-2", "returncode": 1}},
        ]
        evidence = report_execution_evidence(self.context)
        self.assertEqual(evidence["execution_records_total"], 2)
        self.assertEqual(evidence["execution_records"][1]["returncode"], 1)

    def test_long_argv_and_declarations_mark_truncation(self):
        self.context.execution_context = "x" * 6000
        process = self.context.results["execution"]["process"]
        process["argv"] = ["x" * 1100] * 70
        evidence = report_execution_evidence(self.context)
        self.assertTrue(evidence["declared_execution_context"]["truncated"])
        self.assertTrue(evidence["execution_records"][0]["argv_truncated"])
        self.assertEqual(len(evidence["execution_records"][0]["argv"]), 64)

    def test_writer_recovery_and_reviewer_use_the_same_roles(self):
        section = ReportSectionPlan(section_id="setup", heading="Local Setup", goal="Explain conditions")
        memory = ReportMemory(section_plan=[section])
        config = ReportRuntimeConfig(template="reproduction")
        template = load_report_template_bundle(report_mode="experiment", config=config)
        draft = ReportSectionDraft(section_id="setup", heading="Local Setup", draft_markdown="Declared setup.")
        writer = json.loads(_writer_prompt(context=self.context, template=template, memory=memory,
            section=section, config=config, extra_context=[], previous_draft=None, review=None,
            source_batch_index=1, source_batch_count=1, include_previous_draft=True, draft_mode="section").split("\n\n", 1)[1])
        recovery = json.loads(_writer_recovery_prompt(context=self.context, memory=memory,
            section=section, config=config, previous_draft=draft, review=None, draft_mode="section_revision").split("\n\n", 1)[1])
        reviewer = json.loads(_reviewer_prompt(context=self.context, template=template, memory=memory,
            section=section, draft=draft).split("\n\n", 1)[1])
        expected = report_execution_evidence(self.context)
        self.assertEqual(writer["global_research_context"]["execution_evidence"], expected)
        self.assertEqual(recovery["execution_evidence"], expected)
        self.assertEqual(reviewer["execution_evidence"], expected)

    def test_rejected_document_candidate_survives_verifier_failure_and_checkpoint(self):
        memory = ReportMemory(section_plan=[
            ReportSectionPlan(section_id="setup", heading="Local Setup", goal="Explain"),
            ReportSectionPlan(section_id="results", heading="Results", goal="Explain"),
        ])
        config = ReportRuntimeConfig(document_review=True, max_review_iterations=1)
        saved = []

        class Client:
            def ask_json(self, system, prompt, *, label="", **kwargs):
                if label == "report-document-reviewer":
                    return {"section_reviews": [{"section_id": "setup", "verdict": "revise_required",
                        "findings": [{"finding_id": "f1", "type": "unsupported_claim", "severity": "major",
                                      "message": "Setup was declared, not validated.", "section_id": "setup"}]}]}
                if label == "report-document-reviser-setup":
                    return {"section_id": "setup", "heading": "Local Setup", "draft_markdown": "Candidate not adopted."}
                if label == "report-document-verifier-setup":
                    raise LLMError("Provider unavailable after candidate generation")
                if "reviewer" in label:
                    return {"verdict": "pass", "findings": []}
                section_id = "setup" if "setup" in label else "results"
                return {"section_id": section_id, "heading": section_id, "draft_markdown": "Original content."}

        result = run_report_agent(client=Client(), context=self.context, memory=memory, config=config,
            template=load_report_template_bundle(report_mode="experiment", config=config),
            gateway=ReportToolGateway(self.context), checkpoint_sink=saved.append)
        candidate = next(row for row in result.iterations if row.action == "document_revise")
        self.assertEqual(candidate.draft.draft_markdown, "Candidate not adopted.")
        self.assertFalse(candidate.adopted)
        self.assertNotIn("Candidate not adopted.", result.report_body)
        self.assertTrue(any(row.get("draft", {}).get("draft_markdown") == "Candidate not adopted."
                            for snapshot in saved for row in snapshot["iterations"] if row.get("draft")))
        self.assertTrue(any(row.type == "document_revision_unavailable" for row in result.memory.reviewer_findings))


if __name__ == "__main__":
    unittest.main()

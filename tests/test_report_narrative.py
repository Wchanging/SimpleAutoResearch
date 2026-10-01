"""Narrative context follows adopted prose without additional model calls or state."""
import json
import unittest

from simple_ar.integrations.llm import LLMResponseError
from simple_ar.report.agent import run_report_agent
from simple_ar.report.narrative import adopted_claims, narrative_context
from simple_ar.report.memory import initialize_report_memory
from simple_ar.report.schema import (
    ClaimEvidenceRecord, ReportContext, ReportMemory, ReportRuntimeConfig,
    ReportSectionDraft, ReportSectionPlan,
    SourceHandle,
)
from simple_ar.report.templates import load_report_template_bundle
from simple_ar.report.tool_gateway import ReportToolGateway


class NarrativeTests(unittest.TestCase):
    def test_material_writing_does_not_starve_results_behind_many_papers(self):
        handles = [SourceHandle(handle=f"paper:{i}", kind="paper", citation_key=f"P{i}") for i in range(10)]
        handles.extend([SourceHandle(handle="material:results", kind="material"),
                        SourceHandle(handle="chunk:results", kind="chunk")])
        template = load_report_template_bundle(report_mode="supplied_materials",
                                               config=ReportRuntimeConfig(template="experiment"))
        for budget in (1, 2, 8, 0):
            with self.subTest(budget=budget):
                context = ReportContext(topic="Use existing results", report_mode="supplied_materials",
                                        source_handles=handles, max_section_sources=budget)
                memory = initialize_report_memory(context=context, template=template)
                for section in memory.section_plan:
                    self.assertIn("material:results", section.evidence_handles)
                    if budget != 1:
                        self.assertIn("paper:0", section.evidence_handles)
                    self.assertNotIn("chunk:results", section.evidence_handles)
                    if budget > 0:
                        self.assertLessEqual(len(section.evidence_handles), budget)

    def test_template_section_purposes_survive_planning_without_cross_section_leakage(self):
        context = ReportContext(topic="Existing observations", report_mode="supplied_materials")
        config = ReportRuntimeConfig(template="analysis_report")
        template = load_report_template_bundle(report_mode=context.report_mode, config=config).model_copy(update={
            "template_markdown": "# Report\n## Writing Workflow\nDraft order: Results -> Conclusion\n"
                "## Results\nReport all observed values and limitations.\n"
                "## Conclusion\nDo not repeat the full numerical table; identify the next useful step.\n"
                "## References\nGenerated separately.\n"})
        memory = initialize_report_memory(context=context, template=template)
        self.assertEqual(len(memory.section_plan), 2)
        results, conclusion = memory.section_plan
        self.assertIn("Report all observed", results.goal)
        self.assertNotIn("Do not repeat", results.goal)
        self.assertIn("Do not repeat the full numerical table", conclusion.goal)
        view = narrative_context(memory, conclusion, [])
        self.assertIn("identify the next useful step", view["section_purpose"])
        self.assertNotIn("Generated separately", json.dumps(view))

    def test_long_template_guidance_reports_clipping_without_new_state(self):
        context = ReportContext(topic="Bounded", report_mode="supplied_materials")
        template = load_report_template_bundle(report_mode=context.report_mode,
            config=ReportRuntimeConfig(template="analysis_report")).model_copy(update={
                "template_markdown": "## 方法\n" + "界" * 1000})
        memory = initialize_report_memory(context=context, template=template)
        self.assertIn("200 guidance characters omitted", memory.section_plan[0].goal)

    def test_actual_prose_without_optional_claims_is_available_and_not_certified(self):
        plan = ReportSectionPlan(section_id="discussion", heading="Discussion", goal="Interpret uncertainty")
        draft = ReportSectionDraft(section_id="results", heading="Results",
                                   draft_markdown="Observed loss was 0.2; one run only.")
        context = narrative_context(ReportMemory(section_plan=[plan]), plan, [draft])
        row = context["adopted_sections"][0]
        self.assertEqual(row["prose_windows"][0]["text"], draft.draft_markdown)
        self.assertEqual(row["prose_characters_omitted"], 0)
        self.assertEqual(row["declared_claims"], [])
        self.assertIn("not_independently_verified", row["support_status"])

    def test_long_unicode_prose_keeps_qualification_and_explicit_coverage(self):
        text = "观测" * 1000 + "NOT independently reproduced."
        plan = ReportSectionPlan(section_id="end", heading="End", goal="Summarize")
        draft = ReportSectionDraft(section_id="method", heading="Method", draft_markdown=text)
        row = narrative_context(ReportMemory(), plan, [draft])["adopted_sections"][0]
        self.assertTrue(row["prose_windows"][-1]["text"].endswith("NOT independently reproduced."))
        self.assertEqual(row["prose_characters_omitted"], len(text) - 1000)
        for window in row["prose_windows"]:
            self.assertEqual(window["text"], text[window["start"]:window["end"]])

    def test_current_section_is_not_its_own_evidence_and_large_plans_show_omissions(self):
        plans = [ReportSectionPlan(section_id=str(i), heading=str(i), goal="Explain") for i in range(30)]
        drafts = [ReportSectionDraft(section_id=str(i), heading=str(i), draft_markdown=str(i)) for i in range(30)]
        view = narrative_context(ReportMemory(section_plan=plans), plans[-1], drafts)
        self.assertEqual(view["responsibilities_omitted"], 6)
        self.assertEqual(view["adopted_sections_omitted"], 17)
        self.assertNotIn("29", [row["section_id"] for row in view["adopted_sections"]])

    def test_claim_projection_preserves_duplicate_ids_and_removes_superseded_records(self):
        guard = ClaimEvidenceRecord(claim_id="guard", claim="Input guard")
        original = ReportSectionDraft(section_id="a", heading="A", claims=[
            ClaimEvidenceRecord(claim_id="same", claim="Old unsupported assertion", status="unsupported")])
        other = ReportSectionDraft(section_id="b", heading="B", claims=[
            ClaimEvidenceRecord(claim_id="same", claim="Different section assertion")])
        replacement = original.model_copy(update={"claims": []})
        rows = adopted_claims([guard], [replacement, other])
        self.assertEqual([row.claim for row in rows], ["Input guard", "Different section assertion"])
        self.assertEqual(len(adopted_claims([], [original, other])), 2)

    def test_writer_review_format_retry_and_resume_consume_same_adopted_prose(self):
        context = ReportContext(topic="Observed results", report_mode="supplied_materials")
        memory = ReportMemory(section_plan=[
            ReportSectionPlan(section_id="body", heading="Results", goal="State observations", final_order=1),
            ReportSectionPlan(section_id="summary", heading="Conclusion", goal="Bound conclusions", final_order=2),
        ])
        prompts = {}
        labels = []
        checkpoints = []

        class Client:
            def ask_json(self, system, user, *, label="", **kwargs):
                labels.append(label)
                payload = json.loads(user[user.index("{"):])
                prompts[label] = payload
                sid = payload["section"]["section_id"]
                if "reviewer" in label:
                    return {"section_id": sid, "verdict": "pass"}
                if label == "report-writer-summary":
                    raise LLMResponseError("Invalid outer response")
                return {"section_id": sid, "heading": sid,
                        "draft_markdown": "Only one measured run; improvement is unconfirmed."}

        config = ReportRuntimeConfig(max_review_iterations=0)
        kwargs = dict(client=Client(), context=context, memory=memory, config=config,
                      template=load_report_template_bundle(report_mode="supplied_materials", config=config),
                      gateway=ReportToolGateway(context))
        result = run_report_agent(**kwargs, checkpoint_sink=checkpoints.append)
        for label in ("report-writer-summary", "report-writer-summary-retry", "report-reviewer-summary"):
            rows = prompts[label]["narrative_context"]["adopted_sections"]
            self.assertEqual(rows[0]["section_id"], "body")
            self.assertIn("improvement is unconfirmed", rows[0]["prose_windows"][0]["text"])
        self.assertNotIn("section_digests", checkpoints[-1]["memory"])
        count = len(labels)
        resumed = run_report_agent(**kwargs, completed_checkpoint=checkpoints[-1])
        self.assertEqual(resumed.report_body, result.report_body)
        self.assertEqual(len(labels), count)
        partial = next(row for row in checkpoints if len(row["sections"]) == 1 and row["pending_draft"] is None)
        partial["memory"]["claims_evidence_matrix"] = [
            ClaimEvidenceRecord(claim_id="obsolete", claim="Obsolete superseded assertion").model_dump(mode="json")]
        run_report_agent(**kwargs, completed_checkpoint=partial)
        self.assertNotIn("Obsolete superseded assertion", " ".join(prompts["report-writer-summary"]["prior_claim_notes"]))
        self.assertEqual(labels.count("report-writer-body"), 1)


if __name__ == "__main__":
    unittest.main()

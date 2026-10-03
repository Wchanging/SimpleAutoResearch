"""Task requirements survive compact memory, revisions and old checkpoints."""
import json
import unittest

from simple_ar.report.agent import (
    _writer_prompt, _writer_recovery_prompt, _reviewer_prompt, run_report_agent,
)
from simple_ar.report.narrative import report_objective, evidence_outline_context
from simple_ar.report.memory import initialize_report_memory
from simple_ar.report.schema import (
    ReportContext, ReportMemory, ReportRuntimeConfig, ReportSectionDraft,
    ReportSectionPlan, ReportSectionReview, ReviewerFinding,
)
from simple_ar.report.templates import load_report_template_bundle
from simple_ar.report.tool_gateway import ReportToolGateway


def payload(prompt):
    return json.JSONDecoder().raw_decode(prompt[prompt.index("{"):])[0]


class ReportObjectiveTests(unittest.TestCase):
    def objects(self):
        # Requirements in the middle and tail must not depend on an excerpt.
        request = ("材料与问题。" * 300 + " Keep all conditions distinct. "
                   + "约束和解释。" * 500 + " Deliver editable sources; do not claim significance.")
        context = ReportContext(topic="A bounded report", report_mode="supplied_materials",
                                problem_markdown=request, goal_markdown=request)
        config = ReportRuntimeConfig(template="analysis_report")
        template = load_report_template_bundle(report_mode=context.report_mode, config=config)
        memory = initialize_report_memory(context=context, template=template)
        section = memory.section_plan[0]
        draft = ReportSectionDraft(section_id=section.section_id, heading=section.heading,
                                   draft_markdown="Recorded observations only.")
        return context, config, template, memory, section, draft

    def test_original_request_not_summary_is_shared_by_planner_writer_retry_reviewer(self):
        context, config, template, memory, section, draft = self.objects()
        self.assertEqual(len(memory.objective), 1200)
        review = ReportSectionReview(section_id=section.section_id, verdict="revise_required",
            findings=[ReviewerFinding(finding_id="f1", type="missing_limitation", severity="minor",
                required_action="revise", message="Keep the declared limits.")],
            revision_instructions=["Do not omit the requested conditions."])
        common = dict(context=context, memory=memory, section=section)
        prompts = [
            _writer_prompt(**common, template=template, config=config, extra_context=[],
                previous_draft=draft, review=review, source_batch_index=1, source_batch_count=1,
                include_previous_draft=True, draft_mode="section_revision"),
            _writer_recovery_prompt(**common, config=config, previous_draft=draft,
                review=review, draft_mode="section_revision"),
            _reviewer_prompt(**common, template=template, draft=draft,
                revision_review=review, previous_draft=draft),
        ]
        for prompt in prompts:
            self.assertEqual(payload(prompt)["objective"], context.problem_markdown)
            self.assertEqual(prompt.count("Deliver editable sources"), 1)
        global_context = payload(prompts[0])["global_research_context"]
        self.assertEqual(global_context["derived_context_status"]["independent_verification"], "not_performed")
        outline = evidence_outline_context(context, memory, config)
        self.assertEqual(outline["objective"]["text"], context.problem_markdown)
        self.assertFalse(outline["objective"]["truncated"])
        self.assertEqual(outline["synthesis_status"]["evidence_role"], "recorded_derived_context")
        self.assertNotIn("problem", outline)  # No second truncated request owner.

    def test_distinct_problem_and_goal_are_preserved_without_source_confusion(self):
        context, _, _, memory, _, _ = self.objects()
        context.goal_markdown = "Goal-only requirement: report the missing input instead of inventing it."
        objective = report_objective(context, memory)
        self.assertIn(context.goal_markdown, objective)
        self.assertIn(context.problem_markdown, objective)
        context.problem_markdown = ""
        self.assertEqual(report_objective(context, memory), context.goal_markdown)

    def test_legacy_context_uses_recorded_objective_without_mutation(self):
        context = ReportContext(topic="Legacy title", report_mode="supplied_materials")
        memory = ReportMemory(objective="Historical request." * 300)
        before = memory.model_dump()
        self.assertEqual(report_objective(context, memory), memory.objective)
        self.assertEqual(memory.model_dump(), before)
        memory.objective = ""
        self.assertEqual(report_objective(context, memory), context.topic)

    def test_whole_document_review_and_completed_restore_keep_full_request(self):
        context, config, template, memory, section, _ = self.objects()
        memory.section_plan = [section, ReportSectionPlan(section_id="end", heading="Limitations",
                                                         goal="Bound the observations.")]
        config = config.model_copy(update={"document_review": True, "max_review_iterations": 0})
        seen, calls, checkpoints = {}, [], []

        class Client:
            def ask_json(self, system, prompt, *, label="", **kwargs):
                calls.append(label)
                seen[label] = payload(prompt)
                if label == "report-document-reviewer":
                    return {"section_reviews": []}
                planned = seen[label]["section"]
                if "reviewer" in label:
                    return {"section_id": planned["section_id"], "verdict": "pass"}
                return {"section_id": planned["section_id"], "heading": planned["heading"],
                        "draft_markdown": "Recorded observations only."}

        kwargs = dict(client=Client(), context=context, config=config, template=template,
                      memory=memory, gateway=ReportToolGateway(context))
        result = run_report_agent(**kwargs, checkpoint_sink=checkpoints.append)
        self.assertEqual(seen["report-document-reviewer"]["objective"], context.problem_markdown)
        self.assertEqual(seen["report-document-reviewer"]["length_observation"]["total_markdown_tokens"], 6)
        count = len(calls)
        restored = run_report_agent(**kwargs, completed_checkpoint=checkpoints[-1])
        self.assertEqual(restored.report_body, result.report_body)
        self.assertEqual(len(calls), count)


if __name__ == "__main__":
    unittest.main()

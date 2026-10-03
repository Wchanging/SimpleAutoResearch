"""A frozen adaptive plan owns topology; factual/custom review requirements stay."""
import json
import unittest

from simple_ar.report.agent import _maybe_adapt_outline, _reviewer_prompt
from simple_ar.report.editor import review_document
from simple_ar.report.schema import (
    ReportContext, ReportDocumentPlan, ReportMemory, ReportRuntimeConfig,
    ReportSectionDraft, ReportSectionPlan,
)
from simple_ar.report.templates import drafting_template_guidance, load_report_template_bundle


class TemplateOwnershipTests(unittest.TestCase):
    def test_document_roles_omit_drafting_goals_without_changing_saved_plan(self):
        from simple_ar.report.schema import ReviewerFinding
        config, template, memory, plan, draft = self.objects()
        plan.goal = "Include an interpretation named only in the plan, subject to evidence."
        before = memory.model_dump(mode="json")
        views = []
        class Capture:
            def ask_json(self, system, prompt, **unused):
                views.append(json.loads(prompt))
                return {"section_reviews": []}
        prior = ReviewerFinding(finding_id="old", type="unsupported_claim", severity="major",
            section_id=plan.section_id, message="A fallible allegation about current prose.")
        for label, findings, correction in (("report-document-reviewer", [], {}),
                                           ("report-document-verifier", [], {"error": "Saved format error"}),
                                           ("report-document-finding-checker", [prior], {})):
            review_document(client=Capture(), template=template, memory=memory, sections=[draft], config=config,
                execution_summary={}, metric_summary={}, historical_findings=findings,
                format_correction=correction, label=label)
        for index, view in enumerate(views):
            self.assertEqual(view["sections"][0]["markdown"], draft.draft_markdown)
            self.assertNotIn("section_responsibilities", view)
            self.assertNotIn("section_responsibilities_status", view)
            self.assertNotIn(plan.goal, json.dumps(view))
            if index == 2:
                self.assertEqual(view["historical_findings_role"], "prior_model_opinions_not_source_facts")
            else:
                self.assertIn("criteria", view)
        self.assertEqual(memory.model_dump(mode="json"), before)

    def objects(self, name="survey", strategy="topic_specific_outline"):
        config = ReportRuntimeConfig(template=name)
        template = load_report_template_bundle(report_mode="research_only", config=config)
        plan = ReportSectionPlan(section_id="conditions", heading="Conditional evidence", goal="Explain what the observations establish")
        memory = ReportMemory(section_plan=[plan], document_plan=ReportDocumentPlan(sections=[plan]),
            outline_planning={"strategy": strategy, "status": "adapted"})
        draft = ReportSectionDraft(section_id=plan.section_id, heading=plan.heading, draft_markdown="Bounded source claims.")
        return config, template, memory, plan, draft

    def views(self, config, template, memory, plan, draft):
        prompt = _reviewer_prompt(context=ReportContext(topic="Evidence", report_mode="research_only"),
            template=template, memory=memory, section=plan, draft=draft, config=config)
        section = json.JSONDecoder().raw_decode(prompt[prompt.index("{"):])[0]
        captured = []
        class Capture:
            def ask_json(self, system, prompt, **unused):
                captured.append(json.loads(prompt))
                return {"section_reviews": []}
        review_document(client=Capture(), template=template, memory=memory, sections=[draft],
            config=config, execution_summary={}, metric_summary={})
        return section["criteria_markdown"], captured[0]["criteria"]

    def test_adapted_survey_writer_does_not_receive_another_draft_order(self):
        for name in ("survey", "survey_long"):
            for strategy in ("topic_specific_outline", "evidence_organized_outline"):
                with self.subTest(name=name, strategy=strategy):
                    _, template, memory, _, _ = self.objects(name, strategy)
                    guidance = drafting_template_guidance(template, memory)
                    self.assertIn("## Intended Use", guidance)
                    self.assertNotIn("Draft order:", guidance)
                    self.assertNotIn("## Abstract", guidance)

    def test_adapted_reviews_keep_facts_and_not_default_topology(self):
        for name, fact in (("survey", "Every paper-specific claim"),
                           ("survey_long", "Every paper-specific claim"),
                           ("experiment", "Reported numbers must match recorded metrics")):
            with self.subTest(name=name):
                config, template, memory, plan, draft = self.objects(name)
                before = template.model_dump(mode="json")
                for view in self.views(config, template, memory, plan, draft):
                    self.assertIn(fact, view)
                    self.assertNotIn("## Default Structure", view)
                    self.assertNotIn("The body should use survey-style sections", view)
                    self.assertNotIn("The section structure should cover", view)
                    self.assertNotIn("recognizable Abstract, Introduction, Method", view)
                    self.assertIn("## Output Expectations", view)
                self.assertEqual(template.model_dump(mode="json"), before)

    def test_unadapted_and_explicit_custom_criteria_remain_complete(self):
        config, template, memory, plan, draft = self.objects()
        custom = template.model_copy(update={"criteria_markdown":
            "## Required Checks\nMy fact checks.\n## Default Structure\nMy required custom arrangement.\n"})
        for view in self.views(config.model_copy(update={"criteria": "my-review.md"}), custom, memory, plan, draft):
            self.assertEqual(view, custom.criteria_markdown)
        for state in ({}, {"strategy": "topic_specific_outline", "status": "failed"},
                      {"strategy": "deterministic_outline_with_full_evidence_budget", "status": "adapted"}):
            unadapted = memory.model_copy(update={"outline_planning": state})
            self.assertEqual(drafting_template_guidance(template, unadapted), template.template_markdown)
            self.assertTrue(all(view == template.criteria_markdown
                for view in self.views(config, template, unadapted, plan, draft)))

    def test_legacy_unsplit_criteria_are_not_rewritten_or_inferred(self):
        config, template, memory, plan, draft = self.objects()
        template = template.model_copy(update={"criteria_markdown": "Saved legacy instructions, including a fixed heading."})
        self.assertTrue(all(view == template.criteria_markdown
            for view in self.views(config, template, memory, plan, draft)))

    def test_custom_template_with_builtin_filename_is_not_a_builtin(self):
        config, template, memory, plan, draft = self.objects()
        config = config.model_copy(update={"template": "my-project/survey.md"})
        self.assertEqual(drafting_template_guidance(template, memory, config), template.template_markdown)
        self.assertTrue(all(view == template.criteria_markdown
            for view in self.views(config, template, memory, plan, draft)))
        unplanned = memory.model_copy(update={"document_plan": None,
            "survey_contract": {"enabled": True},
            "section_plan": [plan.model_copy(update={"section_id": f"section-{i}"}) for i in range(3)]})
        class NoCalls:
            def ask_json(self, *args, **kwargs):
                raise AssertionError("Custom topology must not be replaced by the built-in planner")
        result = _maybe_adapt_outline(client=NoCalls(), context=ReportContext(topic="Custom survey", report_mode="research_only"),
            template=template, memory=unplanned, config=config, emit=None)
        self.assertIs(result, unplanned)


if __name__ == "__main__":
    unittest.main()

"""Evidence-organized documents reuse the existing report plan and lifecycle."""
from __future__ import annotations

import unittest
import json
from unittest.mock import Mock

from simple_ar.integrations.llm import LLMError
from simple_ar.report.agent import _evidence_outline_sections, _maybe_adapt_outline, run_report_agent
from simple_ar.report.narrative import evidence_outline_context
from simple_ar.report.schema import (
    MetricSource, ReportContext, ReportMemory, ReportRuntimeConfig, ReportSectionPlan, SourceHandle,
)
from simple_ar.report.templates import load_report_template_bundle
from simple_ar.report.tool_gateway import ReportToolGateway


class EvidenceOutlineTests(unittest.TestCase):
    def setUp(self):
        self.context = ReportContext(topic="Observed trade-off", report_mode="experiment",
            results={"metrics": {"coverage": 0.9}},
            experiment_plan={"resource_budget": {"device": "requested_gpu"}})
        self.memory = ReportMemory(template="reproduction", report_mode="experiment",
            objective="Explain this finite reproduction, not a new algorithm.",
            source_handles=[SourceHandle(handle="run:1", kind="experiment", title="Measured run")],
            metric_sources=[MetricSource(metric_id="m:1", name="coverage", value=0.9,
                artifact="results.json", source_kind="measurement")],
            section_plan=[ReportSectionPlan(section_id="findings", heading="Findings",
                goal="Explain observed measurements.", evidence_handles=["run:1"], target_words=500)],
            limitations=["Only one selected condition was measured."])
        self.response = {"sections": [
            {"heading": "Scope", "goal": "Name the limited claim.", "evidence_handles": []},
            {"heading": "Coverage and cost", "goal": "Explain the measured trade-off; no significance claim.",
             "evidence_handles": ["run:1"], "target_words": 300},
        ]}

    def plan(self, response=None, config=None):
        return _evidence_outline_sections(response or self.response, memory=self.memory,
            config=config or ReportRuntimeConfig())

    def adapt(self, client, config):
        template = load_report_template_bundle(report_mode="experiment", config=config)
        return _maybe_adapt_outline(client=client, context=self.context, memory=self.memory,
            template=template, config=config, emit=None)

    def test_non_survey_auto_and_template_make_no_extra_calls(self):
        for strategy in ("auto", "template"):
            client = Mock()
            result = self.adapt(client, ReportRuntimeConfig(template="reproduction", outline_strategy=strategy))
            self.assertIs(result, self.memory)
            client.ask_json.assert_not_called()

    def test_frozen_evidence_plan_does_not_reintroduce_template_topology(self):
        from simple_ar.report.agent import _resolve_document_plan
        from simple_ar.report.templates import drafting_template_guidance
        config = ReportRuntimeConfig(template='reproduction', outline_strategy='adaptive')
        template = load_report_template_bundle(report_mode='experiment', config=config)
        client = Mock()
        client.ask_json.return_value = self.response
        frozen = _resolve_document_plan(self.adapt(client, config), config=config)
        guidance = drafting_template_guidance(template, frozen)
        self.assertIn('Describe the declared reproduction scope', guidance)
        self.assertNotIn('## Ablation Matrix', guidance)
        self.assertNotIn('Draft order:', guidance)
        self.assertEqual(frozen.document_plan.sections[1].heading, 'Coverage and cost')
        from simple_ar.report.agent import _writer_prompt
        prompt = _writer_prompt(context=self.context, template=template, memory=frozen,
            section=frozen.section_plan[1], config=config, extra_context=[], previous_draft=None,
            review=None, source_batch_index=1, source_batch_count=1, include_previous_draft=False,
            draft_mode='initial')
        payload = json.loads(prompt[prompt.find('{'):])
        self.assertEqual(payload['template_markdown'], guidance)
        self.assertEqual(payload['document_plan']['sections'][1]['heading'], 'Coverage and cost')
        restored = ReportMemory.model_validate(frozen.model_dump(mode='json'))
        self.assertEqual(drafting_template_guidance(template, restored), guidance)
        self.assertEqual(drafting_template_guidance(template, self.memory), template.template_markdown)
        custom = template.model_copy(update={'name': 'custom', 'template_markdown': '## Custom responsibilities'})
        self.assertEqual(drafting_template_guidance(custom, frozen), custom.template_markdown)
        for strategy in ('topic_specific_outline', 'deterministic_outline_with_full_evidence_budget'):
            kept = frozen.model_copy(update={'outline_planning': {'strategy': strategy, 'status': 'adapted'}})
            self.assertEqual(drafting_template_guidance(template, kept), template.template_markdown)

    def test_explicit_adaptive_reuses_existing_plan(self):
        client = Mock()
        client.ask_json.return_value = self.response
        result = self.adapt(client, ReportRuntimeConfig(template="reproduction", outline_strategy="adaptive"))
        self.assertEqual(len(result.section_plan), 2)
        self.assertEqual(result.outline_planning["strategy"], "evidence_organized_outline")
        self.assertEqual(result.outline_planning["attempts"], 1)
        self.assertEqual(result.metric_sources, self.memory.metric_sources)
        self.assertEqual(result.limitations, self.memory.limitations)

    def test_title_is_resolved_with_outline_without_an_extra_call(self):
        from simple_ar.report.agent import _resolve_document_plan
        self.response["title"] = "Observed coverage and region-size trade-offs"
        client = Mock()
        client.ask_json.return_value = self.response
        config = ReportRuntimeConfig(template="reproduction", outline_strategy="adaptive")
        result = _resolve_document_plan(self.adapt(client, config), config=config)
        self.assertEqual(result.document_plan.title, self.response["title"])
        self.assertEqual(client.ask_json.call_count, 1)

    def test_supplied_figure_ownership_is_validated_and_frozen_in_same_plan(self):
        from simple_ar.report.agent import _resolve_document_plan
        from simple_ar.report.document_plan import visual_plan_for_renderer, visual_requirements
        self.memory.source_handles[0].metadata = {"document_id": "data-1"}
        self.context.source_handles = self.memory.source_handles
        self.context.results["supplied_analyses"] = [{"document_id": "data-1", "evidence_role": "recomputed_from_user_supplied_data",
            "spec": {}, "records": [], "figures": [{"path": "figures/chart.svg"}]}]
        self.response["sections"][0]["evidence_handles"] = ["run:1"]
        self.response["visual_intents"] = [{"kind": "figure", "view": "supplied-data",
            "section_heading": "Coverage and cost", "evidence_handles": ["run:1"],
            "title": "Observed comparison", "purpose": "Present the supplied coordinates"}]
        config = ReportRuntimeConfig(template="reproduction", outline_strategy="adaptive")
        client = Mock()
        client.ask_json.return_value = self.response
        frozen = _resolve_document_plan(self.adapt(client, config), config=config, context=self.context)
        intent = frozen.document_plan.visual_intents[0]
        self.assertEqual(intent.section_id, frozen.document_plan.sections[1].section_id)
        self.assertEqual(visual_plan_for_renderer(frozen.document_plan), [])
        self.assertTrue(visual_requirements(frozen.document_plan, frozen.document_plan.sections[1])["figures"][0]["assembly_owned"])
        self.assertEqual(client.ask_json.call_count, 1)
        self.assertIs(_resolve_document_plan(frozen, config=config, context=ReportContext(topic="Moved", report_mode="supplied_materials")), frozen)
        self.memory = frozen
        self.assertIs(self.adapt(client, config), frozen)
        self.assertEqual(client.ask_json.call_count, 1)

    def test_invalid_supplied_visual_owner_uses_only_existing_one_correction(self):
        for visual in (
            {"kind": "figure", "view": "supplied-data", "section_heading": "Coverage and cost",
             "title": "Unregistered", "purpose": "No actual figures", "evidence_handles": ["run:1"]},
            "not an intent",
        ):
            client = Mock()
            client.ask_json.return_value = {**self.response, "visual_intents": [visual]}
            with self.assertRaises(LLMError):
                self.adapt(client, ReportRuntimeConfig(template="reproduction", outline_strategy="adaptive"))
            self.assertEqual(client.ask_json.call_count, 2)

    def test_invalid_title_uses_the_existing_bounded_retry_contract(self):
        for title in (["not text"], "# heading", "one\ntwo", "x" * 241):
            with self.subTest(title=title):
                client = Mock()
                client.ask_json.return_value = {**self.response, "title": title}
                with self.assertRaises(LLMError):
                    self.adapt(client, ReportRuntimeConfig(template="reproduction", outline_strategy="adaptive"))
                self.assertEqual(client.ask_json.call_count, 2)

    def test_no_survey_citation_quota_or_subsection_filler(self):
        sections = self.plan()
        self.assertEqual(sections[1].evidence_handles, ["run:1"])
        self.assertEqual(sections[1].target_words, 300)
        self.assertEqual(sections[1].min_citations, 0)
        self.assertEqual(sections[1].subsections, [])

    def test_unknown_handle_is_not_silently_removed(self):
        self.response["sections"][1]["evidence_handles"] = ["invented:trial"]
        with self.assertRaisesRegex(ValueError, "unknown"):
            self.plan()

    def test_invalid_handles_get_only_one_correction(self):
        client = Mock()
        client.ask_json.return_value = {"sections": [{"heading": "Bad", "goal": "Bad"}]}
        with self.assertRaises(LLMError):
            self.adapt(client, ReportRuntimeConfig(template="analysis_report", outline_strategy="adaptive"))
        self.assertEqual(client.ask_json.call_count, 2)

    def test_explicit_fallback_preserves_template_evidence(self):
        client = Mock()
        client.ask_json.return_value = {}
        result = self.adapt(client, ReportRuntimeConfig(template="reproduction", outline_strategy="adaptive",
            allow_llm_fallback=True))
        self.assertEqual(result.section_plan, self.memory.section_plan)
        self.assertEqual(result.outline_planning["status"], "fallback")
        self.assertEqual(client.ask_json.call_count, 2)

    def test_invalid_structure_and_reference_section_rejected(self):
        for raw in ([{}], [self.response["sections"][0], {}],
                    [self.response["sections"][0], {"heading": "References", "goal": "Bibliography"}]):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.plan({"sections": raw})

    def test_explicit_source_budget_not_silently_truncated(self):
        self.memory.source_handles.append(SourceHandle(handle="paper:2", kind="paper"))
        self.response["sections"][1]["evidence_handles"] = ["run:1", "paper:2"]
        with self.assertRaisesRegex(ValueError, "source limit"):
            self.plan(config=ReportRuntimeConfig(max_section_sources=1))

    def test_no_metrics_does_not_create_result_or_novelty(self):
        self.memory.metric_sources = []
        self.context.results = {}
        payload = evidence_outline_context(self.context, self.memory, ReportRuntimeConfig())
        self.assertEqual(payload["recorded_metrics"]["rows"], [])
        self.assertEqual(payload["results"], {})
        self.assertIn("Only one selected condition", payload["limits"][0])
        self.assertIn("do not invent novelty", " ".join(payload["planning_rules"]))

    def test_projection_keeps_declared_conditions_distinct(self):
        payload = evidence_outline_context(self.context, self.memory, ReportRuntimeConfig())
        self.assertEqual(payload["execution_evidence"]["execution_records"], [])
        self.assertEqual(payload["execution_evidence"]["declared_protocol"]["resource_budget"]["device"], "requested_gpu")

    def test_projection_reports_shortened_inputs(self):
        self.memory.objective = "x" * 4000
        self.memory.source_handles.extend(SourceHandle(handle=f"paper:{i}", kind="paper") for i in range(50))
        payload = evidence_outline_context(self.context, self.memory, ReportRuntimeConfig())
        self.assertTrue(payload["objective"]["truncated"])
        self.assertEqual(payload["objective"]["total_characters"], 4000)
        self.assertEqual(payload["sources_omitted"], 11)

    def test_custom_template_remains_authoritative(self):
        client = Mock()
        config = ReportRuntimeConfig(outline_strategy="adaptive")
        template = load_report_template_bundle(report_mode="experiment", config=config).model_copy(update={"name": "user_article"})
        result = _maybe_adapt_outline(client=client, context=self.context, memory=self.memory,
            template=template, config=config, emit=None)
        self.assertIs(result, self.memory)
        client.ask_json.assert_not_called()

    def test_custom_path_with_builtin_basename_is_not_adapted(self):
        client = Mock()
        template = load_report_template_bundle(report_mode="experiment", config=ReportRuntimeConfig())
        config = ReportRuntimeConfig(template="my-templates/experiment.md", outline_strategy="adaptive")
        result = _maybe_adapt_outline(client=client, context=self.context, memory=self.memory,
            template=template, config=config, emit=None)
        self.assertIs(result, self.memory)
        client.ask_json.assert_not_called()

    def test_completed_adaptive_checkpoint_does_not_replan_or_rewrite(self):
        self.response["title"] = "Finite observations"
        config = ReportRuntimeConfig(template="reproduction", outline_strategy="adaptive", reviewer="disabled")
        calls, checkpoints = [], []
        response = self.response

        class Client:
            def ask_json(self, system, prompt, *, label="", **kwargs):
                import json
                calls.append(label)
                if label == "report-outline-planner":
                    return response
                payload = json.loads(prompt.partition("\n\n")[2])
                section = payload["section"]
                return {"section_id": section["section_id"], "heading": section["heading"],
                        "draft_markdown": "A bounded description of the supplied observations."}

        kwargs = dict(client=Client(), context=self.context, memory=self.memory, config=config,
            template=load_report_template_bundle(report_mode="experiment", config=config),
            gateway=ReportToolGateway(self.context))
        result = run_report_agent(**kwargs, checkpoint_sink=checkpoints.append)
        before = len(calls)
        resumed = run_report_agent(**kwargs, completed_checkpoint=checkpoints[-1])
        self.assertEqual(len(calls), before)
        self.assertEqual(resumed.memory.document_plan, result.memory.document_plan)
        self.assertEqual(resumed.report_body, result.report_body)
        self.assertTrue(result.report_body.startswith("# Finite observations\n"))
        self.assertEqual(resumed.memory.document_plan.title, "Finite observations")

    def test_older_plan_without_title_still_loads(self):
        from simple_ar.report.schema import ReportDocumentPlan
        self.assertEqual(ReportDocumentPlan.model_validate({"sections": []}).title, "")


if __name__ == "__main__":
    unittest.main()

"""Assembly-owned prose stays visible without changing drafts or saved data."""
import copy
import json
import unittest
from unittest.mock import Mock

from simple_ar.report.agent import _reviewer_prompt, _writer_prompt, _writer_recovery_prompt
from simple_ar.report.editor import review_document
from simple_ar.report.narrative import evidence_outline_context
from simple_ar.report.schema import (
    ReportContext, ReportDocumentPlan, ReportMemory, ReportRuntimeConfig,
    ReportSectionDraft, ReportSectionPlan, SourceHandle,
)
from simple_ar.report.templates import load_report_template_bundle


def prompt_view(prompt):
    return json.JSONDecoder().raw_decode(prompt[prompt.index("{"):])[0]


class DataDeliveryTests(unittest.TestCase):
    def objects(self, *, full=False, enabled=True):
        section = ReportSectionPlan(section_id="observations", heading="Observations", goal="Compare supplied values",
            evidence_handles=["material:data"], target_words=300)
        memory = ReportMemory(section_plan=[section], document_plan=ReportDocumentPlan(sections=[section]))
        result = {"document_id": "data", "row_count": 2, "evidence_role": "recomputed_from_user_supplied_data",
            "records": [{"group": "A", "column": "score", "count": 2, "missing": 0,
                         "mean": 4.0, "sample_std": 1.0, "min": 3.0, "max": 5.0}],
            "spec": {"mode": "observations", "observation_unit": "one supplied row", "value_unit": "points",
                     "missing": "reject", "plot": "bar"},
            "figures": [{"path": "figures/bar.svg", "caption": "Supplied values in points; not a new experiment."}]}
        context = ReportContext(topic="Write a bounded report", report_mode="supplied_materials",
            results={"supplied_analyses": [result]}, source_handles=[SourceHandle(handle="material:data",
                kind="material", title="Data", metadata={"document_id": "data"})])
        config = ReportRuntimeConfig(data_tables="full" if full else "linked", figures={"enabled": enabled})
        return context, memory, section, config

    def test_all_writing_roles_receive_exact_additions_and_do_not_change_input(self):
        context, memory, section, config = self.objects(full=True)
        original = copy.deepcopy((context.model_dump(), memory.model_dump()))
        template = load_report_template_bundle(report_mode=context.report_mode, config=config)
        draft = ReportSectionDraft(section_id=section.section_id, heading=section.heading, draft_markdown="Observed values only.")
        common = dict(context=context, memory=memory, section=section, template=template, config=config)
        outline = evidence_outline_context(context, memory, config)
        expected = outline["assembly_owned_content"]
        views = [outline,
            prompt_view(_writer_prompt(**common, previous_draft=None, review=None, extra_context=[],
                source_batch_index=1, source_batch_count=1, include_previous_draft=True, draft_mode="section")),
            prompt_view(_writer_recovery_prompt(context=context, memory=memory, section=section, config=config, previous_draft=None, review=None,
                extra_context=[], draft_mode="section")),
            prompt_view(_reviewer_prompt(**common, draft=draft))]
        self.assertEqual(expected[0]["section_id"], "observations")
        self.assertIn("| A | score | 2 | 0 | 4 |", expected[0]["markdown"])
        self.assertIn("not a new experiment", expected[0]["markdown"])
        for view in views[1:]:
            self.assertEqual(view["narrative_context"]["assembly_owned_content"], expected)
        self.assertEqual((context.model_dump(), memory.model_dump()), original)

    def test_document_review_counts_additions_separately_and_preserves_draft(self):
        context, memory, section, config = self.objects()
        from simple_ar.report.data_delivery import supplied_data_delivery
        additions = supplied_data_delivery(context, config=config, plan=memory.document_plan, section_ids=[section.section_id])
        draft = ReportSectionDraft(section_id=section.section_id, heading=section.heading, draft_markdown="Two observed values.")
        client = Mock()
        client.ask_json.return_value = {"section_reviews": []}
        review_document(client=client, template=load_report_template_bundle(report_mode=context.report_mode, config=config), memory=memory,
            sections=[draft], config=config, execution_summary={}, metric_summary={}, assembly_owned_content=additions)
        view = json.loads(client.ask_json.call_args.args[1])
        length = view["length_observation"]
        self.assertEqual(length["total_markdown_tokens"], 3)
        self.assertEqual(length["known_delivery_markdown_tokens"], 3 + len(additions[0]["markdown"].split()))
        self.assertEqual(view["assembly_owned_content"], additions)
        self.assertEqual(draft.draft_markdown, "Two observed values.")

    def test_planning_reserve_matches_real_assembly_overhead_not_only_model_body(self):
        from simple_ar.report.narrative import budget_document_plan, delivery_text_observation
        from simple_ar.report.document_plan import resolve_document_plan
        context, memory, section, config = self.objects(full=True)
        context.problem_markdown = "Write 350-500 words."
        second = section.model_copy(update={"section_id": "discussion", "heading": "Discussion", "target_words": 100})
        plan = resolve_document_plan(sections=[section, second], contract={}, config=config, title="Recorded values")
        original = copy.deepcopy((context.model_dump(), memory.model_dump(), plan.model_dump()))
        request = {"unit": "words", "scope": "whole_document", "request_quote": context.problem_markdown,
            "min_words": 350, "max_words": 500, "target_words": 430}
        budgeted = budget_document_plan(context, memory, config, plan, request)
        words = ["Alpha beta gamma.", "Delta epsilon."]
        drafts = [ReportSectionDraft(section_id=row.section_id, heading=row.heading, draft_markdown=text)
                  for row, text in zip(plan.sections, words)]
        observed = delivery_text_observation(context, memory.model_copy(update={"document_plan": budgeted}), drafts, config)
        fixed = observed["markdown_token_count"] - sum(len(text.split()) for text in words)
        self.assertEqual(budgeted.length_budget["known_fixed_markdown_tokens"], fixed)
        self.assertEqual(sum(row.target_words for row in budgeted.sections), 430 - fixed)
        self.assertNotIn("SARPlanningPlaceholder", json.dumps(budgeted.model_dump()))
        self.assertEqual((context.model_dump(), memory.model_dump(), plan.model_dump()), original)
        context.results["supplied_analyses"][0].pop("row_count")
        with self.assertRaisesRegex(ValueError, "known assembly forecast"):
            budget_document_plan(context, memory, config, plan, request)

    def test_planning_unknown_future_references_are_not_reported_as_complete_zero_cost(self):
        from simple_ar.report.narrative import budget_document_plan, delivery_text_observation
        context, memory, section, config = self.objects()
        context.problem_markdown = "Write 350-500 words."
        context.papers = [{"id": "paper-1", "title": "A reference whose selection has not been made"}]
        plan = memory.document_plan
        request = {"unit": "words", "scope": "whole_document", "request_quote": context.problem_markdown,
            "min_words": 350, "max_words": 500, "target_words": 450}
        budgeted = budget_document_plan(context, memory, config, plan, request)
        self.assertIn("cited_reference_selection_and_display", budgeted.length_budget["unresolved_future_components"])
        self.assertFalse(budgeted.length_budget["rendering_performed"])
        self.assertEqual(context.papers[0]["id"], "paper-1")
        observed = delivery_text_observation(context, memory.model_copy(update={"document_plan": budgeted}), [], config)
        self.assertEqual(observed["preview_status"], "unavailable")
        self.assertIsNone(observed["markdown_token_count"])

    def test_ambiguous_placement_and_figure_off_keep_provenance_without_fake_owner(self):
        context, memory, section, config = self.objects(enabled=False)
        from simple_ar.report.data_delivery import supplied_data_delivery
        second = section.model_copy(update={"section_id": "discussion", "heading": "Discussion"})
        memory.document_plan.sections.append(second)
        additions = supplied_data_delivery(context, config=config, plan=memory.document_plan,
            section_ids=[section.section_id, second.section_id])
        self.assertEqual(additions[0]["section_id"], "supplied_analysis_1")
        self.assertNotIn("![", additions[0]["markdown"])
        self.assertIn("scientific validity were not verified", additions[0]["markdown"])
        self.assertIn("analysis.json", additions[0]["markdown"])

    def test_addition_text_is_not_silently_truncated_for_full_review(self):
        context, memory, section, config = self.objects()
        client = Mock()
        with self.assertRaisesRegex(ValueError, "bounded source window"):
            review_document(client=client, template=load_report_template_bundle(report_mode=context.report_mode, config=config), memory=memory,
                sections=[ReportSectionDraft(section_id=section.section_id, heading=section.heading, draft_markdown="Short.")],
                config=config, execution_summary={}, metric_summary={},
                assembly_owned_content=[{"markdown": "caption " * 9000, "markdown_token_count": 9000}])
        client.ask_json.assert_not_called()

    def test_partial_saved_package_does_not_claim_zero_delivery_length(self):
        context, memory, section, config = self.objects()
        context.results["supplied_analyses"][0].pop("row_count")
        from simple_ar.report.data_delivery import supplied_data_delivery
        additions = supplied_data_delivery(context, config=config, plan=memory.document_plan, section_ids=[section.section_id])
        self.assertEqual(additions[0]["preview_status"], "unavailable")
        self.assertEqual(additions[0]["missing_field"], "row_count")

    def test_text_preview_matches_canonical_delivery_including_headings_and_references(self):
        from pathlib import Path
        from simple_ar.report.capability import ReportAssemblyRequest, assemble_report_document, preview_report_document
        from simple_ar.report.data_delivery import attach_delivery_block, supplied_data_delivery
        from simple_ar.report.narrative import delivery_text_observation
        from simple_ar.report.figures import ReportFigureResult
        context, memory, section, config = self.objects()
        context.papers = [{"id": "paper-1", "title": "A deliberately long reference title", "authors": ["Example Author"]},
                          {"id": "unused", "title": "Not cited in the prose"}]
        context.citation_key_map = {"P1": "paper-1"}
        memory.document_plan.title = "Frozen Reader Facing Title"
        draft = ReportSectionDraft(section_id=section.section_id, heading=section.heading,
            draft_markdown="## Observations\n\nTwo observed values [@P1].\n\n## References\n\nIgnore this draft reference.")
        before = copy.deepcopy((context.model_dump(), memory.model_dump(), draft.model_dump()))
        blocks = supplied_data_delivery(context, config=config, plan=memory.document_plan, section_ids=[section.section_id])
        sections = attach_delivery_block([draft], blocks[0])
        request = ReportAssemblyRequest(title=context.topic, sections=sections, config=config,
            document_plan=memory.document_plan, papers=tuple(context.papers), citation_key_map=context.citation_key_map)
        preview = preview_report_document(request)
        renderer = Mock()
        renderer.render.side_effect = lambda **kwargs: ReportFigureResult(report_markdown=kwargs["report_markdown"], figures=[])
        actual = assemble_report_document(request, report_dir=Path("unused-no-files"), figure_renderer=renderer)
        self.assertEqual(preview.report_markdown, actual.report_markdown)
        self.assertIn("Frozen Reader Facing Title", preview.report_markdown)
        self.assertIn("A deliberately long reference title", preview.report_markdown)
        self.assertNotIn("Not cited in the prose", preview.report_markdown)
        self.assertNotIn("Ignore this draft reference", preview.report_markdown)
        self.assertIn(blocks[0]["markdown"], preview.report_body_markdown)
        observed = delivery_text_observation(context, memory, [draft], config)
        self.assertEqual(observed["markdown_token_count"], len(actual.report_markdown.split()))
        self.assertFalse(observed["rendering_performed"])
        self.assertEqual((context.model_dump(), memory.model_dump(), draft.model_dump()), before)

    def test_missing_attachment_or_uncited_registered_sources_leave_count_unknown(self):
        from simple_ar.report.narrative import delivery_text_observation
        context, memory, section, config = self.objects()
        draft = ReportSectionDraft(section_id=section.section_id, heading=section.heading, draft_markdown="Supplied values.")
        empty = draft.model_copy(update={"draft_markdown": ""})
        self.assertIsNone(delivery_text_observation(context, memory, [empty], config)["markdown_token_count"])
        context.papers = [{"id": "paper-1", "title": "Registered but uncited"}]
        preview = delivery_text_observation(context, memory, [draft], config)
        self.assertEqual(preview["preview_status"], "unavailable")
        self.assertIsNone(preview["markdown_token_count"])
        context.papers = []
        context.results["supplied_analyses"][0].pop("row_count")
        self.assertIsNone(delivery_text_observation(context, memory, [draft], config)["markdown_token_count"])

    def test_experiment_appendix_is_counted_without_rendering_or_altering_draft(self):
        from simple_ar.report.narrative import delivery_text_observation
        from simple_ar.report.capability import ReportAssemblyRequest, preview_report_document
        from simple_ar.report.schema import MetricSource
        context, memory, section, config = self.objects()
        context.results = {}
        context.report_mode = "experiment"
        context.metric_sources = [MetricSource(metric_id="observed-loss", name="loss", value=2.0, artifact="results.json")]
        draft = ReportSectionDraft(section_id=section.section_id, heading=section.heading, draft_markdown="One recorded result.")
        canonical = preview_report_document(ReportAssemblyRequest(title=context.topic,
            sections=(draft,), config=config, document_plan=memory.document_plan, experiment_context=context))
        preview = delivery_text_observation(context, memory, [draft], config)
        self.assertEqual(preview["markdown_token_count"], len(canonical.report_markdown.split()))
        self.assertIn("Experiment Records", canonical.report_markdown)
        self.assertNotIn("Metric Provenance", canonical.report_markdown)
        self.assertEqual(draft.draft_markdown, "One recorded result.")

    def test_section_review_counts_candidate_not_superseded_adopted_version(self):
        from simple_ar.report.narrative import delivery_text_observation
        context, memory, section, config = self.objects()
        other = section.model_copy(update={"section_id": "discussion", "heading": "Discussion", "evidence_handles": []})
        memory.section_plan.append(other)
        memory.document_plan.sections.append(other)
        adopted = [ReportSectionDraft(section_id=section.section_id, heading=section.heading, draft_markdown="Existing supplied data."),
                   ReportSectionDraft(section_id=other.section_id, heading=other.heading, draft_markdown="superseded " * 80)]
        candidate = adopted[-1].model_copy(update={"draft_markdown": "Current short interpretation."})
        before = copy.deepcopy([row.model_dump() for row in adopted])
        view = prompt_view(_reviewer_prompt(context=context, memory=memory, section=other, config=config,
            template=load_report_template_bundle(report_mode=context.report_mode, config=config),
            draft=candidate, adopted_sections=adopted))
        current = delivery_text_observation(context, memory, [adopted[0], candidate], config)
        self.assertEqual(view["narrative_context"]["delivery_text_observation"], current)
        self.assertNotEqual(current["markdown_token_count"], delivery_text_observation(context, memory, adopted, config)["markdown_token_count"])
        self.assertEqual([row.model_dump() for row in adopted], before)

    def test_pending_frozen_attachment_owner_is_visible_not_silently_omitted(self):
        from simple_ar.report.narrative import delivery_text_observation, narrative_context
        from simple_ar.report.capability import ReportAssemblyRequest, preview_report_document
        from simple_ar.report.data_delivery import attach_delivery_block, supplied_data_delivery
        context, memory, owner, config = self.objects()
        first = owner.model_copy(update={"section_id": "purpose", "heading": "Purpose", "evidence_handles": []})
        memory.section_plan.insert(0, first)
        memory.document_plan.sections.insert(0, first)
        draft = ReportSectionDraft(section_id=first.section_id, heading=first.heading, draft_markdown="Scope of existing data.")
        pending = ReportSectionDraft(section_id=owner.section_id, heading=owner.heading, draft_markdown="")
        blocks = supplied_data_delivery(context, config=config, plan=memory.document_plan,
            section_ids=[first.section_id, owner.section_id])
        with self.assertRaisesRegex(ValueError, "absent section"):
            attach_delivery_block([draft], blocks[0])
        expected = preview_report_document(ReportAssemblyRequest(title=context.topic,
            sections=attach_delivery_block([draft, pending], blocks[0]), config=config, document_plan=memory.document_plan))
        observed = narrative_context(memory, owner, [draft], context=context, config=config)["delivery_text_observation"]
        self.assertEqual(observed["pending_owner_sections"], [owner.section_id])
        self.assertEqual(observed["markdown_token_count"], len(expected.report_markdown.split()))
        self.assertIn(blocks[0]["markdown"], expected.report_body_markdown)
        # The section reviewer includes its initial candidate even when no
        # adopted draft has that id yet; the data-only placeholder then ends.
        candidate = pending.model_copy(update={"draft_markdown": "Describe the supplied records."})
        view = prompt_view(_reviewer_prompt(context=context, memory=memory, section=owner, config=config,
            template=load_report_template_bundle(report_mode=context.report_mode, config=config),
            draft=candidate, adopted_sections=[draft]))
        self.assertEqual(view["narrative_context"]["delivery_text_observation"]["pending_owner_sections"], [])
        self.assertEqual(view["narrative_context"]["delivery_text_observation"]["markdown_token_count"],
                         delivery_text_observation(context, memory, [draft, candidate], config)["markdown_token_count"])

    def test_full_review_uses_canonical_count_or_unknown_not_competing_partial_sum(self):
        context, memory, section, config = self.objects()
        draft = ReportSectionDraft(section_id=section.section_id, heading=section.heading, draft_markdown="Two supplied values.")
        client = Mock()
        client.ask_json.return_value = {"section_reviews": []}
        for observation in ({"preview_status": "pre_render_text_preview", "markdown_token_count": 125, "counting_rule": "Canonical text."},
                            {"preview_status": "unavailable", "markdown_token_count": None, "counting_rule": "Unknown text."}):
            review_document(client=client, template=load_report_template_bundle(report_mode=context.report_mode, config=config),
                memory=memory, sections=[draft], config=config, execution_summary={}, metric_summary={},
                delivery_text_observation=observation)
            view = json.loads(client.ask_json.call_args.args[1])
            self.assertEqual(view["length_observation"]["known_delivery_markdown_tokens"], observation["markdown_token_count"])
            self.assertEqual(view["length_observation"]["delivery_count_scope"], observation["preview_status"])

from __future__ import annotations

import unittest
import json
import re
import shutil
from pathlib import Path

from simple_ar.literature.models import Paper
from simple_ar.literature.verify import validate_citations
from simple_ar.core.pipeline import Context
from simple_ar.report.agent import (
    _merge_completion_draft,
    _merge_revision_draft,
    _normalize_draft_response,
    _outline_is_overly_template_like,
    run_report_agent,
)
from simple_ar.report.assembler import apply_section_numbering
from simple_ar.report.audit import build_report_audit
from simple_ar.report.citations import (
    append_references_section as _append_references_section,
    body_citation_ids as _body_citation_ids,
    citation_display_map as _citation_display_map,
    citation_map_artifact as _citation_map_artifact,
    cited_papers as _cited_papers,
    display_citation_numbers as _display_citation_numbers,
    expand_short_citation_keys as _expand_short_citation_keys,
    sanitize_report_citations as _sanitize_report_citations,
    strip_references_section as _strip_references_section,
)
from simple_ar.report.context import build_report_context
from simple_ar.report.memory import initialize_report_memory
from simple_ar.report.quality import build_report_quality
from simple_ar.report.schema import ReportRuntimeConfig, ReportSectionDraft, ReportSectionPlan, ReportToolCall
from simple_ar.report.schema import ReportSectionReview
from simple_ar.report.service import (
    _build_research_report,
    _build_report,
    _report_bound_errors,
    _validated_agent_report,
)
from simple_ar.report.templates import load_report_template_bundle
from simple_ar.report.tool_gateway import ReportToolGateway
from simple_ar.report.survey import _build_taxonomy, enrich_survey_sections


class _FakeReportLLM:
    def ask_json(
        self,
        system: str,
        user: str,
        *,
        label: str = "",
        max_output_tokens: int | None = None,
    ) -> dict[str, object]:
        section_id = _extract_prompt_value(user, "section_id") or "section"
        heading = _extract_prompt_value(user, "heading") or "Section"
        if "reviewer" in label:
            return {
                "section_id": section_id,
                "verdict": "pass",
                "findings": [],
                "context_requests": [],
                "revision_instructions": [],
                "notes": "Looks evidence-bound.",
            }
        return {
            "section_id": section_id,
            "heading": heading,
            "status": "drafted",
            "draft_markdown": f"This section summarizes current evidence [@P1] for {heading}.",
            "used_sources": ["paper:paper-1"],
            "metric_ids": [],
            "citations": ["P1"],
            "claims": [
                {
                    "claim_id": f"claim:{section_id}",
                    "claim": f"{heading} is grounded in current-run evidence.",
                    "status": "partially_supported",
                    "evidence_handles": ["paper:paper-1"],
                    "metric_ids": [],
                    "citation_ids": ["P1"],
                    "notes": "Fake LLM test claim.",
                }
            ],
            "open_questions": [],
            "limitations": [],
        }


class _TrackingReportLLM(_FakeReportLLM):
    def __init__(self) -> None:
        self.labels: list[str] = []

    def ask_json(
        self,
        system: str,
        user: str,
        *,
        label: str = "",
        max_output_tokens: int | None = None,
    ) -> dict[str, object]:
        self.labels.append(label)
        return super().ask_json(system, user, label=label, max_output_tokens=max_output_tokens)


def _extract_prompt_value(prompt: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]+)"', prompt)
    return match.group(1) if match else ""


class ReportSafetyTests(unittest.TestCase):
    def test_outline_template_copy_is_detected_without_rejecting_partial_overlap(self) -> None:
        copied = [
            {"heading": heading}
            for heading in (
                "Abstract",
                "Introduction and Scope",
                "Conceptual Foundations and Taxonomy",
                "Methods and System Construction",
                "Applications and Use Cases",
                "Evaluation, Benchmarks, and Evidence Quality",
                "Related Surveys and Positioning",
                "Challenges and Future Directions",
                "Conclusion",
            )
        ]
        evidence_derived = [
            {"heading": heading}
            for heading in (
                "Abstract",
                "Introduction",
                "Gaussian Representation and Optimization",
                "Densification, Compression, and Rendering Variants",
                "Novel-View Synthesis and Scene Reconstruction",
                "Benchmark Protocols and Evaluation Trade-offs",
                "Challenges and Future Directions",
                "Conclusion",
            )
        ]

        self.assertTrue(_outline_is_overly_template_like(copied))
        self.assertFalse(_outline_is_overly_template_like(evidence_derived))

    def test_writer_claim_status_does_not_invalidate_section_draft(self) -> None:
        section = ReportSectionPlan(section_id="methods", heading="Methods", goal="Compare methods.")
        normalized = _normalize_draft_response(
            {
                "section_id": "methods",
                "heading": "Methods",
                "status": "supported",
                "draft_markdown": "Evidence-backed prose.",
                "claims": [{"status": "supported", "claim": "A supported claim."}],
            },
            section,
        )

        self.assertEqual(normalized["status"], "drafted")
        self.assertEqual(normalized["claims"][0]["status"], "supported")

    def test_writer_normalizes_common_body_aliases_and_nested_draft(self) -> None:
        section = ReportSectionPlan(section_id="methods", heading="Methods", goal="Compare methods.")
        normalized = _normalize_draft_response(
            {
                "section": {
                    "status": "supported",
                    "content": "Evidence-backed prose [@P1].",
                }
            },
            section,
        )

        self.assertEqual(normalized["section_id"], "methods")
        self.assertEqual(normalized["status"], "drafted")
        self.assertEqual(normalized["draft_markdown"], "Evidence-backed prose [@P1].")

    def test_writer_normalizes_structured_open_questions_and_limitations(self) -> None:
        section = ReportSectionPlan(section_id="methods", heading="Methods", goal="Compare methods.")
        normalized = _normalize_draft_response(
            {
                "draft_markdown": "Evidence-backed prose [@P1].",
                "open_questions": [
                    {"question": "How stable is the conclusion?", "notes": "Across domains."},
                ],
                "limitations": [
                    {"limitation": "Evidence is incomplete.", "notes": "No experiments."},
                ],
            },
            section,
        )

        self.assertEqual(normalized["open_questions"], ["How stable is the conclusion?"])
        self.assertEqual(normalized["limitations"], ["Evidence is incomplete."])

    def test_completion_appends_new_prose_without_erasing_prior_draft(self) -> None:
        previous = ReportSectionDraft(
            section_id="methods",
            heading="Methods",
            draft_markdown="Existing evidence synthesis [@P1].",
            citations=["P1"],
        )
        completion = ReportSectionDraft(
            section_id="methods",
            heading="Methods",
            draft_markdown="Additional comparison and boundary condition [@P2].",
            citations=["P2"],
        )

        merged = _merge_completion_draft(previous, completion)

        self.assertIn("Existing evidence synthesis", merged.draft_markdown)
        self.assertIn("Additional comparison", merged.draft_markdown)
        self.assertEqual(merged.citations, ["P1", "P2"])

    def test_completion_does_not_duplicate_a_full_replacement(self) -> None:
        previous = ReportSectionDraft(
            section_id="methods",
            heading="Methods",
            draft_markdown="Existing evidence synthesis [@P1].",
        )
        replacement = ReportSectionDraft(
            section_id="methods",
            heading="Methods",
            draft_markdown="Existing evidence synthesis [@P1]. Expanded comparison [@P2].",
        )

        merged = _merge_completion_draft(previous, replacement)

        self.assertEqual(merged.draft_markdown, replacement.draft_markdown)

    def test_completion_strips_headings_but_preserves_added_evidence(self) -> None:
        previous = ReportSectionDraft(
            section_id="methods",
            heading="Methods",
            draft_markdown="### Existing taxonomy\n\nExisting synthesis [@P1].",
            citations=["P1"],
        )
        completion = ReportSectionDraft(
            section_id="methods",
            heading="Methods",
            draft_markdown="### Existing taxonomy\n\nAdditional comparison [@P2].",
            citations=["P2"],
        )

        merged = _merge_completion_draft(previous, completion)

        self.assertEqual(merged.draft_markdown.count("### Existing taxonomy"), 1)
        self.assertIn("Additional comparison [@P2].", merged.draft_markdown)

    def test_short_reviewer_revision_cannot_erase_substantive_draft(self) -> None:
        previous = ReportSectionDraft(
            section_id="methods",
            heading="Methods",
            draft_markdown=" ".join(["Existing evidence."] * 100),
            citations=["P1"],
        )
        revised = ReportSectionDraft(
            section_id="methods",
            heading="Methods",
            draft_markdown="Brief rewrite.",
            citations=["P2"],
        )

        merged = _merge_revision_draft(previous, revised)

        self.assertEqual(merged.draft_markdown, previous.draft_markdown)
        self.assertEqual(merged.citations, ["P1", "P2"])

    def test_substantive_reviewer_revision_replaces_prior_draft(self) -> None:
        previous = ReportSectionDraft(
            section_id="methods",
            heading="Methods",
            draft_markdown=" ".join(["Existing evidence."] * 100),
        )
        revised = ReportSectionDraft(
            section_id="methods",
            heading="Methods",
            draft_markdown=" ".join(["Revised evidence."] * 95),
        )

        merged = _merge_revision_draft(previous, revised)

        self.assertEqual(merged.draft_markdown, revised.draft_markdown)

    def test_report_context_includes_nested_code_task_comparison(self) -> None:
        run_dir = Path(".tmp_tests") / "report-code-task-context"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        comparison_dir = run_dir / "06-code" / "code_task_run" / "code_task" / "run"
        comparison_dir.mkdir(parents=True)
        comparison = {
            "verdict": "improved",
            "reasons": ["improved `accuracy` by +0.10"],
            "baseline": {"metrics": {"accuracy": 0.70, "macro_f1": 0.68}},
            "patched": {"metrics": {"accuracy": 0.80, "macro_f1": 0.79}},
            "metrics": [
                {
                    "name": "accuracy",
                    "delta": 0.10,
                    "direction": "higher_is_better",
                }
            ],
        }
        (comparison_dir / "comparison.json").write_text(
            json.dumps(comparison),
            encoding="utf-8",
        )
        meta_dir = run_dir / "06-code"
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / "code_task_experiment.json").write_text(
            json.dumps({"code_task_run_dir": str(meta_dir / "code_task_run")}),
            encoding="utf-8",
        )
        ctx = Context(run_dir, "code task report")
        paper = Paper(
            id="paper-1",
            title="Known Paper",
            authors=[],
            abstract="Known abstract.",
            url="https://example.com/1",
        )

        context = build_report_context(
            ctx,
            report_mode="experiment",
            goal="",
            problem="",
            search_meta={},
            synthesis="",
            hypothesis="",
            plan={"template": "code_task_project"},
            results={"metrics": {"accuracy": 0.80}},
            paper_rows=[paper.to_row()],
            papers=[paper],
            research_evidence_summary="",
        )
        template = load_report_template_bundle(
            report_mode="experiment",
            config=ReportRuntimeConfig(template="experiment"),
            project_root=Path.cwd(),
        )
        memory = initialize_report_memory(context=context, template=template)

        metric_ids = {metric.metric_id for metric in memory.metric_sources}
        self.assertIn("metric:code_task_baseline_accuracy", metric_ids)
        self.assertIn("metric:code_task_patched_accuracy", metric_ids)
        self.assertIn("metric:code_task_delta_accuracy", metric_ids)
        self.assertIn("artifact:code_task_comparison", memory.section_plan[0].evidence_handles)
        shutil.rmtree(run_dir)

    def test_report_review_accepts_object_revision_instructions(self) -> None:
        review = ReportSectionReview.model_validate(
            {
                "section_id": "related_work",
                "verdict": "revise_required",
                "findings": [],
                "context_requests": [],
                "revision_instructions": [
                    {
                        "finding_id": "rw-001",
                        "suggested_action": "Replace broad benchmark claims with source-bound wording.",
                    }
                ],
                "notes": "",
            }
        )

        self.assertEqual(
            review.revision_instructions,
            ["rw-001: Replace broad benchmark claims with source-bound wording."],
        )

    def test_sanitize_report_citations_removes_unknown_placeholders(self) -> None:
        body = (
            "# Draft\n\n"
            "Known evidence [@paper-1; @1] and an unsupported placeholder [@missing].\n"
        )

        sanitized, removed = _sanitize_report_citations(body, {"paper-1"})

        self.assertEqual(removed, ["1", "missing"])
        self.assertIn("[@paper-1]", sanitized)
        self.assertNotIn("@1", sanitized)
        self.assertNotIn("@missing", sanitized)
        validate_citations(sanitized, {"paper-1"})

    def test_validated_agent_report_repairs_unknown_citations(self) -> None:
        paper = Paper(
            id="paper-1",
            title="Known Paper",
            authors=[],
            abstract="",
            url="https://example.com/paper-1",
        )
        draft = (
            "# Draft\n\n"
            "Known evidence is still cited [@paper-1], while a malformed "
            "placeholder should be removed [@paper-typo].\n"
        )

        result = _validated_agent_report(
            Context(Path("run"), "Agent Simulation", config={}),
            draft,
            search_meta={"source": "openalex", "status": "ok"},
            plan={},
            papers=[paper],
            citation_key_map={},
            report_mode="research_only",
            results_present=False,
        )

        self.assertIsNotNone(result)
        assert result is not None
        report_body, removed = result
        self.assertEqual(removed, ["paper-typo"])
        self.assertIn("[@paper-1]", report_body)
        self.assertNotIn("paper-typo", report_body)

    def test_short_citation_keys_expand_before_validation(self) -> None:
        body = "# Draft\n\nKnown evidence [@P1; @p2]. Bare fallback [P1, P2].\n"

        expanded = _expand_short_citation_keys(body, {"P1": "paper-1", "P2": "paper-2"})

        self.assertIn("[@paper-1; @paper-2]", expanded)
        self.assertIn("Bare fallback [@paper-1; @paper-2]", expanded)
        validate_citations(expanded, {"paper-1", "paper-2"})

    def test_validated_agent_report_accepts_short_citation_keys(self) -> None:
        paper = Paper(
            id="paper-1",
            title="Known Paper",
            authors=[],
            abstract="",
            url="https://example.com/paper-1",
        )

        result = _validated_agent_report(
            Context(Path("run"), "Agent Simulation", config={}),
            "# Draft\n\nKnown evidence is cited with a short model key [@P1].\n",
            search_meta={"source": "openalex", "status": "ok"},
            plan={},
            papers=[paper],
            citation_key_map={"P1": "paper-1"},
            report_mode="research_only",
            results_present=False,
        )

        self.assertIsNotNone(result)
        assert result is not None
        report_body, removed = result
        self.assertEqual(removed, [])
        self.assertIn("[@paper-1]", report_body)
        self.assertNotIn("[@P1]", report_body)

    def test_numeric_citation_display_uses_map_without_losing_source_ids(self) -> None:
        paper = Paper(
            id="paper-1",
            title="Known Paper",
            authors=[],
            abstract="",
            url="https://example.com/paper-1",
        )
        citation_map = _citation_display_map([paper])

        display = _display_citation_numbers("Known evidence [@paper-1]. Extra note [paper-1].", citation_map)
        report = _append_references_section(display, [paper], citation_map)

        self.assertIn("Known evidence [1].", report)
        self.assertIn("Extra note [1].", report)
        self.assertIn("- [1] Known Paper.", report)
        self.assertNotIn("[@paper-1]", report)

    def test_citation_map_records_model_keys(self) -> None:
        paper = Paper(
            id="paper-1",
            title="Known Paper",
            authors=[],
            abstract="",
            url="https://example.com/paper-1",
        )

        artifact = _citation_map_artifact({"paper-1": 1}, [paper], {"P1": "paper-1"})

        self.assertEqual(artifact["model_key_style"], "short_keys")
        self.assertEqual(artifact["entries"][0]["model_key"], "P1")
        self.assertEqual(artifact["entries"][0]["paper_id"], "paper-1")

    def test_report_template_bundle_and_memory_are_structured(self) -> None:
        ctx = Context(Path("run"), "Agent Simulation", config={})
        paper = Paper(
            id="paper-1",
            title="Known Paper",
            authors=["Ada"],
            abstract="A paper about agent systems.",
            url="https://example.com/paper-1",
        )
        context = build_report_context(
            ctx,
            report_mode="research_only",
            goal="# Goal\nStudy agents.",
            problem="# Problem\nWhat evidence exists?",
            search_meta={"source": "openalex", "status": "ok"},
            synthesis="# Synthesis\nAgent workflows have roles.",
            hypothesis="# Hypothesis\nRole separation may help.",
            plan={},
            results={},
            paper_rows=[paper.to_row()],
            papers=[paper],
            research_evidence_summary="- Known evidence [@paper-1].",
        )
        template = load_report_template_bundle(
            report_mode="research_only",
            config=ReportRuntimeConfig(template="survey"),
            project_root=Path.cwd(),
        )
        memory = initialize_report_memory(context=context, template=template)

        self.assertEqual(template.name, "survey")
        self.assertTrue(memory.section_plan)
        self.assertNotIn("Intended Use", {section.heading for section in memory.section_plan})
        self.assertNotIn("References", {section.heading for section in memory.section_plan})
        self.assertIn("paper:paper-1", {handle.handle for handle in memory.source_handles})
        self.assertIn("P1", {handle.citation_key for handle in memory.source_handles})

    def test_report_memory_can_expose_all_selected_papers(self) -> None:
        papers = [
            Paper(
                id=f"paper-{index}",
                title=f"Known Paper {index}",
                authors=[],
                abstract="Known metadata.",
                url=f"https://example.com/paper-{index}",
            )
            for index in range(1, 13)
        ]
        context = build_report_context(
            Context(Path("run"), "Agent Simulation", config={}),
            report_mode="research_only",
            goal="# Goal\nStudy agents.",
            problem="# Problem\nWhat evidence exists?",
            search_meta={"source": "openalex", "status": "ok"},
            synthesis="# Synthesis\nAgent workflows have roles.",
            hypothesis="# Hypothesis\nRole separation may help.",
            plan={},
            results={},
            paper_rows=[paper.to_row() for paper in papers],
            papers=papers,
            research_evidence_summary="- Known evidence.",
            max_section_sources=0,
        )
        template = load_report_template_bundle(
            report_mode="research_only",
            config=ReportRuntimeConfig(template="survey"),
            project_root=Path.cwd(),
        )
        memory = initialize_report_memory(context=context, template=template)

        first_section = memory.section_plan[0]
        paper_handles = [handle for handle in first_section.evidence_handles if handle.startswith("paper:")]

        self.assertEqual(len(paper_handles), 12)

    def test_report_agent_drafts_and_reviews_template_sections(self) -> None:
        paper = Paper(
            id="paper-1",
            title="Known Paper",
            authors=["Ada"],
            abstract="A paper about agent systems.",
            url="https://example.com/paper-1",
        )
        context = build_report_context(
            Context(Path("run"), "Agent Simulation", config={}),
            report_mode="research_only",
            goal="# Goal\nStudy agents.",
            problem="# Problem\nWhat evidence exists?",
            search_meta={"source": "openalex", "status": "ok"},
            synthesis="# Synthesis\nAgent workflows have roles.",
            hypothesis="# Hypothesis\nRole separation may help.",
            plan={},
            results={},
            paper_rows=[paper.to_row()],
            papers=[paper],
            research_evidence_summary="- Known evidence [@paper-1].",
        )
        template = load_report_template_bundle(
            report_mode="research_only",
            config=ReportRuntimeConfig(template="survey"),
            project_root=Path.cwd(),
        )
        memory = initialize_report_memory(context=context, template=template)
        gateway = ReportToolGateway(context)

        result = run_report_agent(
            client=_FakeReportLLM(),
            context=context,
            template=template,
            memory=memory,
            config=ReportRuntimeConfig(template="survey", max_review_iterations=0),
            gateway=gateway,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.used_agent)
        self.assertGreaterEqual(len(result.sections), 3)
        self.assertIn("[@P1]", result.report_body)
        self.assertNotIn("## Intended Use", result.report_body)
        self.assertNotIn("## References", result.report_body)
        self.assertTrue(result.iterations[0].section_id.startswith("method"))
        self.assertLess(
            result.report_body.index("## Abstract / Executive Summary"),
            result.report_body.index("## Method Families"),
        )

    def test_disabled_report_reviewer_skips_review_and_revision_calls(self) -> None:
        paper = Paper(
            id="paper-1",
            title="Known Paper",
            authors=["Ada"],
            abstract="A paper about agent systems.",
            url="https://example.com/1",
        )
        context = build_report_context(
            Context(Path("run"), "Agent Simulation", config={}),
            report_mode="research_only",
            goal="# Goal\nStudy agents.",
            problem="# Problem\nWhat evidence exists?",
            search_meta={"source": "openalex", "status": "ok"},
            synthesis="# Synthesis\nAgent workflows have roles.",
            hypothesis="# Hypothesis\nRole separation may help.",
            plan={},
            results={},
            paper_rows=[paper.to_row()],
            papers=[paper],
            research_evidence_summary="- Known evidence [@paper-1].",
        )
        template = load_report_template_bundle(
            report_mode="research_only",
            config=ReportRuntimeConfig(template="survey"),
            project_root=Path.cwd(),
        )
        client = _TrackingReportLLM()
        result = run_report_agent(
            client=client,
            context=context,
            template=template,
            memory=initialize_report_memory(context=context, template=template),
            config=ReportRuntimeConfig(template="survey", reviewer="disabled"),
            gateway=ReportToolGateway(context),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.sections)
        self.assertFalse(any("reviewer" in label or "reviser" in label for label in client.labels))
        self.assertFalse(any(item.action.startswith("review") or item.action.startswith("revise") for item in result.iterations))

    def test_report_agent_can_refine_sections_over_source_batches(self) -> None:
        papers = [
            Paper(
                id=f"paper-{index}",
                title=f"Known Paper {index}",
                authors=[],
                abstract="Known metadata.",
                url=f"https://example.com/paper-{index}",
            )
            for index in range(1, 13)
        ]
        context = build_report_context(
            Context(Path("run"), "Agent Simulation", config={}),
            report_mode="research_only",
            goal="# Goal\nStudy agents.",
            problem="# Problem\nWhat evidence exists?",
            search_meta={"source": "openalex", "status": "ok"},
            synthesis="# Synthesis\nAgent workflows have roles.",
            hypothesis="# Hypothesis\nRole separation may help.",
            plan={},
            results={},
            paper_rows=[paper.to_row() for paper in papers],
            papers=papers,
            research_evidence_summary="- Known evidence.",
            max_section_sources=0,
        )
        template = load_report_template_bundle(
            report_mode="research_only",
            config=ReportRuntimeConfig(template="survey"),
            project_root=Path.cwd(),
        )
        memory = initialize_report_memory(context=context, template=template)

        result = run_report_agent(
            client=_FakeReportLLM(),
            context=context,
            template=template,
            memory=memory,
            config=ReportRuntimeConfig(
                template="survey",
                max_review_iterations=0,
                source_strategy="batch_refine",
                source_batch_size=5,
                review_source_batches=True,
            ),
            gateway=ReportToolGateway(context),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(any(item.action == "integrate_sources" for item in result.iterations))
        self.assertTrue(any(item.action == "review_source_batch" for item in result.iterations))

    def test_report_tool_gateway_exports_and_resolves_sources(self) -> None:
        paper = Paper(
            id="paper-1",
            title="Known Paper",
            authors=[],
            abstract="Known metadata.",
            url="https://example.com/paper-1",
        )
        context = build_report_context(
            Context(Path("run"), "Agent Simulation", config={}),
            report_mode="experiment",
            goal="",
            problem="",
            search_meta={},
            synthesis="",
            hypothesis="",
            plan={},
            results={"metrics": {"accuracy": 0.75}},
            paper_rows=[paper.to_row()],
            papers=[paper],
            research_evidence_summary="",
        )
        gateway = ReportToolGateway(context)

        tool_names = {tool["function"]["name"] for tool in gateway.openai_tools()}
        self.assertIn("get_paper_brief", tool_names)
        result = gateway.call(
            ReportToolCall(
                tool_name="get_metric_source",
                arguments={"metric_id": "metric:accuracy"},
            )
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.content["name"], "accuracy")
        paper_result = gateway.call(
            ReportToolCall(
                tool_name="get_paper_brief",
                arguments={"citation_key": "P1"},
            )
        )
        self.assertEqual(paper_result.status, "ok")
        self.assertEqual(paper_result.content["handles"][0]["cite_as"], "[@P1]")
        self.assertNotIn("paper_id", paper_result.content["handles"][0])
        handle_result = gateway.call(
            ReportToolCall(
                tool_name="get_paper_brief",
                arguments={"handle": "paper:paper-1"},
            )
        )
        self.assertEqual(handle_result.status, "ok")

    def test_report_audit_records_unknown_citation_and_metric_sources(self) -> None:
        paper = Paper(
            id="paper-1",
            title="Known Paper",
            authors=[],
            abstract="",
            url="https://example.com/paper-1",
        )
        context = build_report_context(
            Context(Path("run"), "Agent Simulation", config={}),
            report_mode="experiment",
            goal="",
            problem="",
            search_meta={},
            synthesis="",
            hypothesis="",
            plan={},
            results={"metrics": {"accuracy": 0.75}},
            paper_rows=[paper.to_row()],
            papers=[paper],
            research_evidence_summary="",
        )
        template = load_report_template_bundle(
            report_mode="experiment",
            config=ReportRuntimeConfig(template="experiment"),
            project_root=Path.cwd(),
        )
        memory = initialize_report_memory(context=context, template=template)
        audit = build_report_audit(
            report="# Draft\n\nKnown claim [@missing]. accuracy is 0.75.\n",
            report_body="# Draft\n\nKnown claim [@missing]. accuracy is 0.75.\n",
            context=context,
            memory=memory,
        )

        self.assertEqual(audit.status, "failed")
        self.assertIn("missing", audit.citation_audit.unknown_citations)
        self.assertIn("metric:accuracy", audit.metric_audit.matched_metrics)

    def test_model_written_references_are_replaced_with_known_papers(self) -> None:
        draft = (
            "# A Draft\n\n"
            "## Abstract\n\n"
            "Known citation [@paper-1].\n\n"
            "## References\n\n"
            "- fabricated reference [@fake-paper]\n"
        )
        paper = Paper(
            id="paper-1",
            title="Known Paper",
            authors=["Ada Lovelace"],
            abstract="Known metadata.",
            url="https://example.com/paper-1",
        )

        report = _append_references_section(_strip_references_section(draft), [paper])

        self.assertIn("[@paper-1]", report)
        self.assertNotIn("@fake-paper", report)
        validate_citations(report, {"paper-1"})

    def test_fallback_report_states_fixture_limitations(self) -> None:
        ctx = Context(
            Path("run"),
            "Agent Simulation",
            config={"max_papers": 1, "experiment_timeout_sec": 30},
        )
        paper = Paper(
            id="fixture-001",
            title="Placeholder Paper for Pipeline Testing",
            authors=["SimpleAutoResearch"],
            abstract="Fixture metadata.",
            url="https://example.com/fixture-001",
            source="fixture",
        )
        report = _append_references_section(
            _build_report(
                ctx,
                goal="# Goal\nStudy agent simulation.",
                problem="# Problem\nHow can agent simulation be studied?",
                search_meta={
                    "query": "Agent Simulation",
                    "source": "fixture",
                    "status": "fallback",
                    "returned": 1,
                },
                synthesis="# Synthesis\nStage outputs can become later inputs.",
                hypothesis="# Hypothesis\nA file-first pipeline is inspectable.",
                plan={
                    "template": "toy_text_classification",
                    "dataset": "built_in_toy_spam",
                    "baseline": "keyword_rules",
                    "method": "bag_of_words_logistic_regression",
                    "metrics": ["accuracy"],
                },
                results={
                    "returncode": 0,
                    "timed_out": False,
                    "metrics": {"accuracy": 0.75},
                    "command": ["python", "experiment.py"],
                },
                papers=[paper],
            ),
            [paper],
        )

        self.assertIn("## Literature Search", report)
        self.assertIn("fixture metadata", report)
        self.assertIn("| `accuracy` | 0.75 |", report)
        self.assertIn("[@fixture-001]", report.split("## References", maxsplit=1)[0])
        self.assertNotIn("Raw result metadata", report)
        validate_citations(report, {"fixture-001"})

    def test_code_task_fixture_fallback_discussion_uses_operational_evidence(self) -> None:
        ctx = Context(Path("run"), "LLM-guided improvement", config={"max_papers": 1})
        paper = Paper(
            id="fixture-001",
            title="Placeholder Paper for Pipeline Testing",
            authors=["SimpleAutoResearch"],
            abstract="Fixture metadata.",
            url="https://example.com/fixture-001",
            source="fixture",
        )

        report = _build_report(
            ctx,
            goal="# Goal\nImprove toy code.",
            problem="# Problem\nCan the workflow patch a toy project?",
            search_meta={"source": "fixture", "status": "offline_fixture", "returned": 1},
            synthesis="# Synthesis\nA placeholder hypothesis about output accuracy.",
            hypothesis="# Hypothesis\nMeasure placeholder effectiveness.",
            plan={
                "template": "llm_code_task_toy_spam",
                "mode": "embedded_code_task",
                "metrics": ["benchmark_passed", "changed_files"],
            },
            results={
                "returncode": 0,
                "timed_out": False,
                "metrics": {"benchmark_passed": 1.0, "changed_files": 1.0},
                "command": ["python", "experiment.py"],
            },
            papers=[paper],
        )

        self.assertIn("operational rather than literature-backed", report)
        self.assertIn("changed 1 file(s)", report)
        self.assertNotIn("placeholder effectiveness", report)

    def test_body_citation_ids_ignore_reference_list_only_citations(self) -> None:
        markdown = (
            "# Draft\n\n"
            "## Abstract\n\n"
            "No body citation.\n\n"
            "## References\n\n"
            "- [@paper-1] Known Paper.\n"
        )

        self.assertEqual(_body_citation_ids(markdown, {"paper-1"}), set())

    def test_cited_papers_prunes_uncited_references(self) -> None:
        papers = [
            Paper(
                id="paper-1",
                title="Cited Paper",
                authors=[],
                abstract="",
                url="https://example.com/1",
            ),
            Paper(
                id="paper-2",
                title="Uncited Paper",
                authors=[],
                abstract="",
                url="https://example.com/2",
            ),
        ]

        cited = _cited_papers("# Draft\n\nKnown prior work [@paper-1].", papers)
        report = _append_references_section("# Draft\n\nKnown prior work [@paper-1].", cited)

        self.assertEqual([paper.id for paper in cited], ["paper-1"])
        self.assertIn("[@paper-1]", report)
        self.assertNotIn("[@paper-2]", report)

    def test_report_quality_records_metrics_and_runtime_limits(self) -> None:
        paper = Paper(
            id="paper-1",
            title="Known Paper",
            authors=[],
            abstract="",
            url="https://example.com/1",
            source="fixture",
        )
        report_body = (
            "# Draft\n\n"
            "The run uses fixture metadata [@paper-1].\n\n"
            "## Results\n\n"
            "| Metric | Value |\n"
            "|---|---:|\n"
            "| `accuracy` | 0.75 |\n\n"
            "## Limitations\n\n"
            "The literature stage used fixture metadata and the experiment timed out."
        )
        report = _append_references_section(report_body, [paper])

        quality = build_report_quality(
            report,
            report_body,
            search_meta={"source": "fixture", "status": "fallback"},
            results={"metrics": {"accuracy": 0.75}, "returncode": None, "timed_out": True},
            papers=[paper],
            cited_papers=[paper],
        )

        self.assertEqual(quality["status"], "passed")
        self.assertEqual(quality["summary"]["metric_count"], 1)
        self.assertEqual(quality["body_citation_ids"], ["paper-1"])

    def test_report_bounds_reject_fixture_overclaims(self) -> None:
        report = (
            "# Draft\n\n"
            "## Related Work\n\n"
            "Prior research has established groundwork for practical solutions "
            "in spam filtering [@fixture-001].\n\n"
            "## Limitations\n\n"
            "The run used fixture metadata."
        )

        errors = _report_bound_errors(
            report,
            search_meta={"source": "fixture", "status": "offline_fixture"},
            plan={"template": "llm_code_task_toy_spam"},
            report_mode="experiment",
            results_present=True,
        )

        self.assertTrue(any("overclaims" in error for error in errors))

    def test_report_bounds_reject_toy_code_task_overclaims(self) -> None:
        report = (
            "# Draft\n\n"
            "## Results\n\n"
            "The patch demonstrates performance improvements and the potential of "
            "LLMs for enhancing spam detection beyond this benchmark. This is a "
            "promising direction."
        )

        errors = _report_bound_errors(
            report,
            search_meta={"source": "openalex", "status": "ok"},
            plan={"template": "llm_code_task_toy_spam"},
            report_mode="experiment",
            results_present=True,
        )

        self.assertTrue(any("code-task benchmark" in error for error in errors))

    def test_report_bounds_accept_conservative_fixture_disclosure(self) -> None:
        report = (
            "# Draft\n\n"
            "## Related Work\n\n"
            "The only available citation is fixture metadata used to keep the "
            "pipeline deterministic [@fixture-001].\n\n"
            "## Results\n\n"
            "The benchmark passed after one source-file patch."
        )

        self.assertEqual(
            _report_bound_errors(
                report,
                search_meta={"source": "fixture", "status": "offline_fixture"},
                plan={"template": "llm_code_task_toy_spam"},
                report_mode="experiment",
                results_present=True,
            ),
            [],
        )

    def test_research_only_fallback_does_not_imply_experiment_execution(self) -> None:
        paper = Paper(
            id="fixture-001",
            title="Placeholder Paper for Pipeline Testing",
            authors=["SimpleAutoResearch"],
            abstract="Fixture metadata.",
            url="https://example.com/fixture-001",
            source="fixture",
        )
        ctx = Context(Path("run"), "Agent Simulation", config={"max_papers": 1})

        report = _build_research_report(
            ctx,
            goal="# Goal\nStudy agent simulation.",
            problem="# Problem\nWhat themes appear in agent simulation metadata?",
            search_meta={"source": "fixture", "status": "offline_fixture", "returned": 1},
            synthesis="# Synthesis\nThe retrieved metadata is a placeholder.",
            hypothesis="# Hypothesis\nA later benchmark could test a concrete implementation.",
            papers=[paper],
        )

        self.assertIn("## Draft Status", report)
        self.assertIn("## Research Question", report)
        self.assertIn("## Available Sources", report)
        self.assertIn("## Evidence Handoff", report)
        self.assertIn("## Boundaries And Next Steps", report)
        self.assertIn("conservative fallback", report)
        self.assertNotRegex(report, r"(?m)^## Method\s*$")
        self.assertNotRegex(report, r"(?m)^## Experiments\s*$")
        self.assertNotRegex(report, r"(?m)^## Results\s*$")
        self.assertNotIn("experiment design, code generation, execution", report)
        self.assertIn("No experiment was executed", report)
        self.assertIn("should not be treated as a complete literature-backed review", report)
        self.assertNotIn("Hint:", report)
        self.assertNotIn("Use this paper as", report)
        self.assertNotIn("Paper Brief", report)
        self.assertNotIn("Additional synthesis detail", report)
        self.assertNotIn("## Search Scope", report)
        self.assertNotIn("## Evidence Summary", report)

    def test_research_only_bounds_reject_prompt_residue(self) -> None:
        report = (
            "# Draft\n\n"
            "## Method Families\n\n"
            "Paper Brief [@paper-1]: Hint: Use this paper as an example.\n\n"
            "## Evidence Summary\n\n"
            "Additional synthesis detail is available in the stage artifacts."
        )

        errors = _report_bound_errors(
            report,
            search_meta={"source": "openalex", "status": "ok"},
            plan={},
            report_mode="research_only",
            results_present=False,
        )

        self.assertTrue(any("pipeline residue" in error for error in errors))

    def test_research_only_bounds_allow_academic_evidence_terms_in_prose(self) -> None:
        report = (
            "# Draft\n\n"
            "## Method Families\n\n"
            "Corrective methods can broaden search scope when initial retrieval "
            "does not support an answer. The evidence summary should distinguish "
            "source limitations from confirmed findings [@paper-1]."
        )

        errors = _report_bound_errors(
            report,
            search_meta={"source": "openalex", "status": "ok"},
            plan={},
            report_mode="research_only",
            results_present=False,
        )

        self.assertFalse(any("pipeline residue" in error for error in errors))

    def test_academic_section_numbering_preserves_unnumbered_front_and_back_matter(self) -> None:
        rendered = apply_section_numbering(
            """# Example Survey

## Abstract

Summary.

## Introduction

### Scope

## Method Families

### Retrieval Methods

#### Dense Retrieval

## References
""",
            mode="academic",
        )

        self.assertIn("## Abstract", rendered)
        self.assertIn("## 1 Introduction", rendered)
        self.assertIn("### 1.1 Scope", rendered)
        self.assertIn("## 2 Method Families", rendered)
        self.assertIn("### 2.1 Retrieval Methods", rendered)
        self.assertIn("#### 2.1.1 Dense Retrieval", rendered)
        self.assertIn("## References", rendered)

    def test_taxonomy_keeps_coverage_checklist_out_of_organization_axes(self) -> None:
        taxonomy = _build_taxonomy(
            topic="Example Topic",
            coverage_facets=["method_taxonomy", "datasets_benchmarks_and_evaluation"],
            selected_papers=[
                {
                    "citation_key": "P1",
                    "title": "A Benchmark for Example Topic",
                    "abstract": "Evaluation metrics and datasets.",
                    "role": "evaluation",
                },
                {
                    "citation_key": "P2",
                    "title": "A Survey of Example Topic",
                    "abstract": "A survey and taxonomy.",
                    "role": "related_survey",
                },
            ],
        )

        self.assertEqual(
            [row["label"] for row in taxonomy["coverage_facets"]],
            ["Method Taxonomy", "Datasets Benchmarks And Evaluation"],
        )
        self.assertNotIn("Method Taxonomy", [row["label"] for row in taxonomy["facets"]])
        self.assertNotIn("Datasets Benchmarks And Evaluation", [row["label"] for row in taxonomy["facets"]])

    def test_survey_outline_fallback_restores_configured_source_budget(self) -> None:
        papers = [
            Paper(
                id=f"paper-{index}",
                title=f"Example Evidence {index}",
                authors=[],
                abstract="Example methods, evaluation, and applications evidence.",
                url=f"https://example.com/paper-{index}",
            )
            for index in range(1, 13)
        ]
        context = build_report_context(
            Context(Path("run"), "Example Topic", config={}),
            report_mode="research_only",
            goal="# Goal\nSynthesize the field.",
            problem="# Problem\nWhat evidence is available?",
            search_meta={},
            synthesis="# Synthesis\nMethods and evaluation are both relevant.",
            hypothesis="",
            plan={},
            results={},
            paper_rows=[paper.to_row() for paper in papers],
            papers=papers,
            research_evidence_summary="",
            max_section_sources=0,
        ).model_copy(
            update={
                "survey_contract": {
                    "enabled": True,
                    "outline_strategy": "adaptive",
                    "section_source_budget": 8,
                    "topic_terms": ["example", "methods", "evaluation"],
                    "outline_plan": {
                        "sections": [
                            {
                                "section_id": "methods",
                                "heading": "Methods",
                                "goal": "Compare method evidence.",
                                "citation_keys": ["P1", "P2"],
                                "target_words": 800,
                                "min_citations": 3,
                                "required": True,
                            },
                            {
                                "section_id": "evaluation",
                                "heading": "Evaluation",
                                "goal": "Compare evaluation evidence.",
                                "citation_keys": ["P3", "P4"],
                                "target_words": 800,
                                "min_citations": 3,
                                "required": True,
                            },
                            {
                                "section_id": "applications",
                                "heading": "Applications",
                                "goal": "Summarize applications.",
                                "citation_keys": ["P5", "P6"],
                                "target_words": 800,
                                "min_citations": 3,
                                "required": True,
                            },
                            {
                                "section_id": "challenges",
                                "heading": "Challenges",
                                "goal": "Describe limitations and open problems.",
                                "citation_keys": ["P7", "P8"],
                                "target_words": 800,
                                "min_citations": 3,
                                "required": True,
                            },
                            {
                                "section_id": "conclusion",
                                "heading": "Conclusion",
                                "goal": "Conclude the synthesis.",
                                "citation_keys": ["P9", "P10"],
                                "target_words": 400,
                                "min_citations": 0,
                                "required": True,
                            },
                        ]
                    },
                }
            }
        )

        sections = enrich_survey_sections([], context=context)

        self.assertEqual(len(sections), 5)
        self.assertTrue(all(len(section.evidence_handles) == 8 for section in sections))


if __name__ == "__main__":
    unittest.main()

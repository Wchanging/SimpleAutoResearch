from __future__ import annotations

import unittest
import json
import re
import shutil
import tempfile
from pathlib import Path

from simple_ar.experiment.code_task_bridge import CODE_TASK_PROJECT_TEMPLATE
from simple_ar.literature.models import Paper
from simple_ar.literature.verify import validate_citations
from simple_ar.core.pipeline import Context
from simple_ar.report.agent import (
    _is_claim_record_response,
    _merge_revision_draft,
    _normalize_draft_response,
    _outline_is_overly_template_like,
    run_report_agent,
)
from simple_ar.report.document_plan import resolve_document_plan, visual_requirements
from simple_ar.report.assembler import apply_section_numbering
from simple_ar.report.audit import ReportAuditRequest, audit_report, build_report_audit
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
from simple_ar.report.schema import (
    ReportMemory,
    ReportRuntimeConfig,
    ReportSectionDraft,
    ReportSectionPlan,
    ReportToolCall,
)
from simple_ar.report.schema import ReportSectionReview
from simple_ar.report.service import (
    _build_research_report,
    _build_report,
    _ensure_code_task_evidence_section,
    _report_runtime_config,
    _report_bound_errors,
    _validated_agent_report,
)
from simple_ar.report.templates import load_report_template_bundle
from simple_ar.report.tool_gateway import ReportToolGateway
from simple_ar.report.survey import _build_taxonomy, _build_visual_coverage_audit, enrich_survey_sections


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
        self.max_output_tokens_values: list[int | None] = []
        self.prompts: dict[str, str] = {}

    def ask_json(
        self,
        system: str,
        user: str,
        *,
        label: str = "",
        max_output_tokens: int | None = None,
    ) -> dict[str, object]:
        self.labels.append(label)
        self.max_output_tokens_values.append(max_output_tokens)
        self.prompts[label] = user
        return super().ask_json(system, user, label=label, max_output_tokens=max_output_tokens)


class _TwoRevisionReportLLM(_FakeReportLLM):
    """Require two targeted revisions for every section before accepting it."""

    def __init__(self) -> None:
        self.review_counts: dict[str, int] = {}

    def ask_json(
        self,
        system: str,
        user: str,
        *,
        label: str = "",
        max_output_tokens: int | None = None,
    ) -> dict[str, object]:
        section_id = _extract_prompt_value(user, "section_id") or "section"
        if "reviewer" in label:
            count = self.review_counts.get(section_id, 0) + 1
            self.review_counts[section_id] = count
            if count <= 2:
                return {
                    "section_id": section_id,
                    "verdict": "revise_required",
                    "findings": [
                        {
                            "finding_id": f"{section_id}-finding-{count}",
                            "type": "style",
                            "severity": "minor",
                            "message": "Add the requested evidence-qualified comparison.",
                            "section_id": section_id,
                            "suggested_action": "Add one concise comparison and retain prior evidence.",
                        }
                    ],
                    "revision_instructions": ["Add one concise comparison and retain prior evidence."],
                }
            return {
                "section_id": section_id,
                "verdict": "pass",
                "findings": [],
                "revision_instructions": [],
            }
        response = super().ask_json(system, user, label=label, max_output_tokens=max_output_tokens)
        if "reviser" in label:
            response["status"] = "revised"
            response["draft_markdown"] = str(response["draft_markdown"]) + "\n\nA retained comparison clarifies the boundary conditions."
        return response


class _ClaimRecordThenDraftLLM(_TrackingReportLLM):
    def __init__(self) -> None:
        super().__init__()
        self.returned_claim_record = False

    def ask_json(
        self,
        system: str,
        user: str,
        *,
        label: str = "",
        max_output_tokens: int | None = None,
    ) -> dict[str, object]:
        self.labels.append(label)
        self.max_output_tokens_values.append(max_output_tokens)
        if not self.returned_claim_record and label.startswith("report-writer-"):
            self.returned_claim_record = True
            return {
                "claim_id": "claim:misplaced",
                "claim": "A nested claim record is not a section draft.",
                "status": "supported",
                "evidence_handles": ["paper:paper-1"],
                "metric_ids": [],
                "citation_ids": ["P1"],
                "notes": "Incorrect response level.",
            }
        return _FakeReportLLM.ask_json(
            self,
            system,
            user,
            label=label,
            max_output_tokens=max_output_tokens,
        )


def _extract_prompt_value(prompt: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]+)"', prompt)
    return match.group(1) if match else ""


class ReportSafetyTests(unittest.TestCase):
    def test_report_runtime_config_accepts_zero_revision_cycles(self) -> None:
        config = _report_runtime_config(
            Context(Path("run"), "Agent Simulation", config={"report_max_review_iterations": 0})
        )

        self.assertEqual(config.max_review_iterations, 0)

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

    def test_claim_record_is_not_accepted_as_a_section_draft(self) -> None:
        self.assertTrue(
            _is_claim_record_response(
                {
                    "claim_id": "claim:1",
                    "claim": "A claim.",
                    "citation_ids": ["P1"],
                }
            )
        )
        self.assertFalse(
            _is_claim_record_response(
                {
                    "claim_id": "claim:1",
                    "claim": "A claim.",
                    "draft_markdown": "Section prose.",
                }
            )
        )

    def test_document_plan_rebalances_section_plan(self) -> None:
        plan = resolve_document_plan(
            sections=[
                ReportSectionPlan(section_id="abstract", heading="Abstract", goal="Summarize.", target_words=250),
                ReportSectionPlan(section_id="intro", heading="Introduction", goal="Frame.", target_words=2200),
                ReportSectionPlan(section_id="body", heading="Methods", goal="Explain.", target_words=2800),
                ReportSectionPlan(section_id="end", heading="Conclusion", goal="Close.", target_words=1200),
            ],
            contract={"expected_coverage": {"target_words": 5000}},
            config=ReportRuntimeConfig(),
        )

        self.assertEqual(sum(section.target_words for section in plan.sections), 5000)

    def test_document_plan_leaves_reports_without_a_global_target_unchanged(self) -> None:
        plan = resolve_document_plan(
            sections=[
                ReportSectionPlan(
                    section_id="methods",
                    heading="Methods",
                    goal="Explain.",
                    target_words=900,
                )
            ],
            contract={},
            config=ReportRuntimeConfig(),
        )

        self.assertEqual(plan.sections[0].target_words, 900)

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

        client = _TrackingReportLLM()
        result = run_report_agent(
            client=client,
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

    def test_report_agent_applies_multiple_review_revision_cycles(self) -> None:
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
        config = ReportRuntimeConfig(template="survey", max_review_iterations=2)
        template = load_report_template_bundle(
            report_mode="research_only",
            config=config,
            project_root=Path.cwd(),
        )

        result = run_report_agent(
            client=_TwoRevisionReportLLM(),
            context=context,
            template=template,
            memory=initialize_report_memory(context=context, template=template),
            config=config,
            gateway=ReportToolGateway(context),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(sum(item.action == "revise" for item in result.iterations), len(result.sections) * 2)
        self.assertEqual(
            sum(item.action == "review_revision" for item in result.iterations),
            len(result.sections) * 2,
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

    def test_writer_recovers_from_claim_record_with_configured_token_cap(self) -> None:
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
        config = ReportRuntimeConfig(
            template="survey",
            reviewer="disabled",
            max_section_tokens=777,
        )
        template = load_report_template_bundle(
            report_mode="research_only",
            config=config,
            project_root=Path.cwd(),
        )
        client = _ClaimRecordThenDraftLLM()

        result = run_report_agent(
            client=client,
            context=context,
            template=template,
            memory=initialize_report_memory(context=context, template=template),
            config=config,
            gateway=ReportToolGateway(context),
        )

        self.assertIsNotNone(result)
        self.assertTrue(client.returned_claim_record)
        self.assertTrue(any(label.endswith("-retry") for label in client.labels))
        self.assertTrue(client.max_output_tokens_values)
        self.assertTrue(all(value == 777 for value in client.max_output_tokens_values))

    def test_report_agent_uses_plan_controlled_evidence_batches(self) -> None:
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

        client = _TrackingReportLLM()
        result = run_report_agent(
            client=client,
            context=context,
            template=template,
            memory=memory,
            config=ReportRuntimeConfig(
                template="survey",
                max_review_iterations=0,
                source_strategy="batch_refine",
                source_batch_size=5,
            ),
            gateway=ReportToolGateway(context),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(any(item.action == "integrate_sources" for item in result.iterations))
        self.assertFalse(any(item.action == "review_source_batch" for item in result.iterations))
        self.assertTrue(any(item.action == "draft" for item in result.iterations))
        integration_prompts = [
            prompt for label, prompt in client.prompts.items() if label.startswith("report-integrator-")
        ]
        self.assertTrue(integration_prompts)
        self.assertTrue(all('"previous_draft": {' in prompt for prompt in integration_prompts))

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
        wrapped = audit_report(
            ReportAuditRequest(
                report="# Draft",
                report_body="# Draft\n\nKnown claim [@missing]. accuracy is 0.75.\n",
                context=context,
                memory=memory,
            )
        )
        self.assertEqual(wrapped.status, audit.status)

    def test_report_audit_does_not_treat_pass_at_k_as_citation(self) -> None:
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
            results={"metrics": {"pass@1": 0.75}},
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
        body = "# Results\n\nThe run recorded pass@1 = 0.75 [@paper-1].\n"

        audit = build_report_audit(
            report=body,
            report_body=body,
            context=context,
            memory=memory,
        )

        self.assertEqual(audit.citation_audit.status, "passed")
        self.assertEqual(audit.citation_audit.unknown_citations, [])
        sanitized, removed = _sanitize_report_citations(body, {"paper-1"})
        self.assertEqual(removed, [])
        self.assertIn("pass@1", sanitized)

    def test_report_audit_ignores_numbers_inside_citation_ids(self) -> None:
        paper = Paper(
            id="arxiv-2603.01327v2",
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
        body = "# Results\n\nAccuracy is 0.75 [@arxiv-2603.01327v2].\n"

        audit = build_report_audit(
            report=body,
            report_body=body,
            context=context,
            memory=memory,
        )

        self.assertEqual(audit.metric_audit.unmatched_numbers, [])
        self.assertEqual(audit.citation_audit.unknown_citations, [])

    def test_report_audit_allows_numbers_grounded_in_selected_source_metadata(self) -> None:
        paper = Paper(
            id="paper-1",
            title="Known Paper",
            authors=[],
            abstract="We audited twelve benchmark papers.",
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
        body = "# Results\n\nWe reviewed 12 benchmark papers [@paper-1]. Accuracy was 0.75.\n"

        audit = build_report_audit(
            report=body,
            report_body=body,
            context=context,
            memory=memory,
        )

        self.assertEqual(audit.metric_audit.status, "passed")
        self.assertEqual(audit.metric_audit.unmatched_numbers, [])

    def test_report_audit_accepts_readable_metric_names_and_scientific_values(self) -> None:
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
            results={
                "metrics": {
                    "accuracy": 0.75,
                    "train_time_sec": 4e-05,
                    "model_size": 24.0,
                    "eval_examples": 14.0,
                }
            },
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
        body = (
            "# Results\n\n"
            "Accuracy was 0.75. Training time was 4.0e-05 seconds, "
            "model size was 24.0, and the evaluation set contained 14 examples.\n"
        )

        audit = build_report_audit(
            report=body,
            report_body=body,
            context=context,
            memory=memory,
        )

        self.assertEqual(audit.metric_audit.status, "passed")
        self.assertEqual(audit.metric_audit.unmatched_metrics, [])
        self.assertEqual(audit.metric_audit.unmatched_numbers, [])

    def test_code_task_evidence_completes_an_existing_partial_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            report_dir = run_dir / "08-report"
            comparison_path = (
                report_dir / "code_task_run" / "code_task" / "run" / "comparison.json"
            )
            comparison_path.parent.mkdir(parents=True)
            (report_dir / "code_task_experiment.json").write_text(
                json.dumps({"code_task_run_dir": "code_task_run"}),
                encoding="utf-8",
            )
            comparison_path.write_text(
                json.dumps(
                    {
                        "verdict": "improved",
                        "reasons": ["improved `accuracy`"],
                        "baseline": {
                            "metrics": {"accuracy": 0.7, "feature_family_count": 1.0}
                        },
                        "patched": {
                            "metrics": {"accuracy": 0.8, "feature_family_count": 2.0}
                        },
                        "metrics": [
                            {
                                "name": "accuracy",
                                "baseline": 0.7,
                                "patched": 0.8,
                                "delta": 0.1,
                                "interpretation": "improved",
                            },
                            {
                                "name": "feature_family_count",
                                "baseline": 1.0,
                                "patched": 2.0,
                                "delta": 1.0,
                                "interpretation": "increased",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            context = Context(run_dir, "reliable agents")
            partial = "## Code Task Evidence\n\nThe writer covered accuracy only.\n"
            report = _ensure_code_task_evidence_section(
                context,
                {"template": CODE_TASK_PROJECT_TEMPLATE},
                partial,
            )

        self.assertIn("### Verified Comparison Metrics", report)
        self.assertIn("`feature_family_count`", report)
        self.assertIn("| 2 | 1 | increased |", report)

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

    def test_document_plan_accepts_only_feasible_planner_selected_visuals(self) -> None:
        sections = [
            ReportSectionPlan(
                section_id="evaluation",
                heading="Evaluation",
                goal="Compare evidence.",
                evidence_handles=["paper:P1", "paper:P2", "paper:P3"],
            ),
            ReportSectionPlan(
                section_id="methods",
                heading="Methods",
                goal="Compare methods.",
                evidence_handles=["paper:P4", "paper:P5"],
            ),
        ]
        config = ReportRuntimeConfig(
            figures={"enabled": True, "max_figures": 2},
            longform={"target_tables": 2},
        )
        plan = resolve_document_plan(
            sections=sections,
            contract={},
            config=config,
            visual_candidates=[
                {
                    "kind": "table",
                    "title": "Evaluation Settings",
                    "purpose": "Compare protocols and limitations.",
                    "section_heading": "Evaluation",
                    "columns": ["Setting", "Metric", "Limitation"],
                },
                {
                    "kind": "figure",
                    "title": "Evaluation Landscape",
                    "purpose": "Show the relationship between evaluation components.",
                    "section_heading": "Evaluation",
                    "view": "evaluation-landscape",
                },
                {
                    "kind": "table",
                    "title": "Unsupported Table",
                    "purpose": "Should be rejected because its section is absent.",
                    "section_heading": "Absent",
                    "columns": ["A", "B"],
                },
            ],
        )

        requirements = visual_requirements(plan, sections[0])
        self.assertEqual(len(plan.visual_intents), 2)
        self.assertEqual(requirements["tables"][0]["title"], "Evaluation Settings")
        self.assertEqual(requirements["figures"][0]["view"], "evaluation-landscape")
        self.assertEqual(visual_requirements(plan, sections[1]), {"tables": [], "figures": []})

    def test_visual_coverage_audit_matches_captioned_table_and_figure(self) -> None:
        visual_plan = {
            "requested_table_count": 2,
            "requested_figure_count": 1,
            "tables": [
                {
                    "table_id": "taxonomy-comparison",
                    "title": "Taxonomy and Representative Evidence",
                    "section_id": "taxonomy",
                    "suggested_columns": ["Facet", "Core idea", "Representative papers", "Evidence boundary"],
                },
                {
                    "table_id": "evaluation-landscape",
                    "title": "Evaluation Settings and Metrics",
                    "section_id": "evaluation",
                    "suggested_columns": ["Setting", "Task or dataset", "Metric", "Observed limitation"],
                },
            ],
            "figures": [{"figure_id": "taxonomy-map", "title": "Survey Taxonomy Map"}],
        }
        report = """## Taxonomy

**Table: Taxonomy and Representative Evidence**

| Facet | Core idea | Representative papers | Evidence boundary |
| --- | --- | --- | --- |
| A | B | [@P1] | Limited scope |

![Conceptual taxonomy map](figures/taxonomy-map.svg)
"""
        audit = _build_visual_coverage_audit(final_report=report, visual_plan=visual_plan)

        self.assertEqual(audit["realized_table_count"], 1)
        self.assertEqual(audit["realized_figure_count"], 1)
        self.assertEqual(audit["missing_tables"][0]["table_id"], "evaluation-landscape")
        self.assertEqual(audit["status"], "warning")


if __name__ == "__main__":
    unittest.main()

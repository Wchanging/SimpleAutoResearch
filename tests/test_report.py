from __future__ import annotations

import unittest
import json
import re
import shutil
import tempfile
from unittest.mock import patch
from pathlib import Path

from simple_ar.literature.models import Paper
from simple_ar.literature.verify import validate_citations
from simple_ar.integrations.llm import LLMError
from simple_ar.report.agent import (
    _compact_execution_results,
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
from simple_ar.report.memory import initialize_report_memory
from simple_ar.report.schema import (
    MetricSource,
    ReportContext,
    SourceHandle,
    ReportMemory,
    ReportRuntimeConfig,
    ReportSectionDraft,
    ReportSectionPlan,
    ReportToolCall,
)
from simple_ar.report.schema import ReportSectionReview
from simple_ar.report.templates import load_report_template_bundle
from simple_ar.report.tool_gateway import ReportToolGateway
from simple_ar.report.survey import enrich_survey_sections


def _report_fixture(papers: list[Paper], **fields) -> ReportContext:
    """Writer/audit inputs, independent of legacy stage-directory discovery."""
    context = ReportContext(
        papers=[paper.to_row() for paper in papers],
        citation_key_map={f"P{i}": paper.id for i, paper in enumerate(papers, 1)},
        source_handles=[SourceHandle(
            handle=f"paper:{paper.id}", kind="paper", citation_key=f"P{i}",
            title=paper.title, paper_id=paper.id, summary=paper.abstract,
            metadata={"authors": paper.authors, "url": paper.url,
                      "source": paper.source, "source_id": paper.source_id,
                      "published": paper.published},
        ) for i, paper in enumerate(papers, 1)],
        **fields,
    )
    context.metric_sources = [MetricSource(
        metric_id=f"metric:{name}", name=name, value=value,
        artifact="results.json", label="experiment",
    ) for name, value in context.results.get("metrics", {}).items()]
    return context


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
    def test_report_agents_receive_bounded_verified_execution_comparison(self) -> None:
        compact = _compact_execution_results(
            {
                "status": "passed",
                "primary_metric": "accuracy",
                "metrics": {"accuracy": 0.89, "stdout": "should not be copied"},
                "baseline": {"status": "passed", "metrics": {"accuracy": 0.77}},
                "comparisons": [
                    {
                        "verdict": "improved",
                        "reasons": ["accuracy increased"],
                        "metrics": [
                            {
                                "name": "accuracy",
                                "baseline": 0.77,
                                "patched": 0.89,
                                "delta": 0.12,
                                "direction": "higher",
                            },
                            {
                                "name": "accuracy_after_task_1_on_task_1",
                                "baseline": 0.2,
                                "patched": 0.3,
                                "delta": 0.1,
                                "direction": "higher",
                            }
                        ],
                        "stdout": "should not be copied",
                    }
                ],
            }
        )

        self.assertEqual(compact["primary_metric"], "accuracy")
        self.assertEqual(compact["baseline"]["metrics"]["accuracy"], 0.77)
        self.assertEqual(compact["comparisons"][0]["verdict"], "improved")
        self.assertEqual(compact["comparisons"][0]["metrics"][0]["delta"], 0.12)
        self.assertEqual(len(compact["comparisons"][0]["metrics"]), 1)
        self.assertNotIn("stdout", compact["metrics"])
        self.assertNotIn("stdout", compact["comparisons"][0])

    def test_report_runtime_config_accepts_zero_revision_cycles(self) -> None:
        config = ReportRuntimeConfig(max_review_iterations=0)

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

    def test_short_reviewer_revision_replaces_prose_and_preserves_provenance(self) -> None:
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

        self.assertEqual(merged.draft_markdown, revised.draft_markdown)
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


    def test_short_citation_keys_expand_before_validation(self) -> None:
        body = "# Draft\n\nKnown evidence [@P1; @p2]. Bare fallback [P1, P2].\n"

        expanded = _expand_short_citation_keys(body, {"P1": "paper-1", "P2": "paper-2"})

        self.assertIn("[@paper-1; @paper-2]", expanded)
        self.assertIn("Bare fallback [@paper-1; @paper-2]", expanded)
        validate_citations(expanded, {"paper-1", "paper-2"})


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
        paper = Paper(
            id="paper-1",
            title="Known Paper",
            authors=["Ada"],
            abstract="A paper about agent systems.",
            url="https://example.com/paper-1",
        )
        context = _report_fixture(
            [paper],
            topic='Agent Simulation',
            report_mode="research_only",
            goal_markdown="# Goal\nStudy agents.",
            problem_markdown="# Problem\nWhat evidence exists?",
            search_meta={"source": "openalex", "status": "ok"},
            synthesis_markdown="# Synthesis\nAgent workflows have roles.",
            hypothesis_markdown="# Hypothesis\nRole separation may help.",
            evidence_summary="- Known evidence [@paper-1].",
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

    def test_report_templates_can_resolve_from_packaged_resources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packaged = root / "package"
            shutil.copytree(Path(__file__).resolve().parents[1] / "templates" / "report", packaged / "report_templates")
            project_root = root / "outside-checkout"
            project_root.mkdir()
            with patch(
                "simple_ar.report.templates.resources.files",
                return_value=packaged,
            ):
                template = load_report_template_bundle(
                    report_mode="research_only",
                    config=ReportRuntimeConfig(template="survey"),
                    project_root=project_root,
                )

            self.assertEqual(
                Path(template.template_path).parent,
                packaged / "report_templates",
            )
            self.assertIn("Abstract", template.template_markdown)

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
        context = _report_fixture(
            papers,
            topic='Agent Simulation',
            report_mode="research_only",
            goal_markdown="# Goal\nStudy agents.",
            problem_markdown="# Problem\nWhat evidence exists?",
            search_meta={"source": "openalex", "status": "ok"},
            synthesis_markdown="# Synthesis\nAgent workflows have roles.",
            hypothesis_markdown="# Hypothesis\nRole separation may help.",
            evidence_summary="- Known evidence.",
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
        context = _report_fixture(
            [paper],
            topic='Agent Simulation',
            report_mode="research_only",
            goal_markdown="# Goal\nStudy agents.",
            problem_markdown="# Problem\nWhat evidence exists?",
            search_meta={"source": "openalex", "status": "ok"},
            synthesis_markdown="# Synthesis\nAgent workflows have roles.",
            hypothesis_markdown="# Hypothesis\nRole separation may help.",
            evidence_summary="- Known evidence [@paper-1].",
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

        # A later provider failure must not discard completed, reviewed sections.
        checkpoints = []
        failing_client = _TrackingReportLLM()
        original_ask = failing_client.ask_json

        def fail_after_first_section(*args, **kwargs):
            if checkpoints and checkpoints[-1]["sections"]:
                raise LLMError("provider unavailable after first section")
            return original_ask(*args, **kwargs)

        with patch.object(failing_client, "ask_json", side_effect=fail_after_first_section), self.assertRaises(LLMError):
            run_report_agent(
                client=failing_client, context=context, template=template, memory=memory,
                config=ReportRuntimeConfig(template="survey", max_review_iterations=1),
                gateway=gateway, checkpoint_sink=checkpoints.append,
            )
        saved = checkpoints[-1]
        self.assertEqual(len(saved["sections"]), 1)
        self.assertTrue(saved["iterations"])

        resumed_client = _TrackingReportLLM()
        resumed = run_report_agent(
            client=resumed_client, context=context, template=template, memory=memory,
            config=ReportRuntimeConfig(template="survey", max_review_iterations=1),
            gateway=gateway, completed_checkpoint=saved,
        )
        self.assertIsNotNone(resumed)
        self.assertEqual(resumed.sections[0].model_dump(mode="json"), saved["sections"][0])
        self.assertEqual(len(resumed.sections), len(result.sections))
        first_id = saved["sections"][0]["section_id"]
        self.assertFalse(any(_extract_prompt_value(prompt, "section_id") == first_id
                             for prompt in resumed_client.prompts.values()))
        self.assertEqual(resumed.iterations[0].model_dump(mode="json"), saved["iterations"][0])

    def test_report_agent_applies_multiple_review_revision_cycles(self) -> None:
        paper = Paper(
            id="paper-1",
            title="Known Paper",
            authors=["Ada"],
            abstract="A paper about agent systems.",
            url="https://example.com/paper-1",
        )
        context = _report_fixture(
            [paper],
            topic='Agent Simulation',
            report_mode="research_only",
            goal_markdown="# Goal\nStudy agents.",
            problem_markdown="# Problem\nWhat evidence exists?",
            search_meta={"source": "openalex", "status": "ok"},
            synthesis_markdown="# Synthesis\nAgent workflows have roles.",
            hypothesis_markdown="# Hypothesis\nRole separation may help.",
            evidence_summary="- Known evidence [@paper-1].",
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
        context = _report_fixture(
            [paper],
            topic='Agent Simulation',
            report_mode="research_only",
            goal_markdown="# Goal\nStudy agents.",
            problem_markdown="# Problem\nWhat evidence exists?",
            search_meta={"source": "openalex", "status": "ok"},
            synthesis_markdown="# Synthesis\nAgent workflows have roles.",
            hypothesis_markdown="# Hypothesis\nRole separation may help.",
            evidence_summary="- Known evidence [@paper-1].",
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
        context = _report_fixture(
            [paper],
            topic='Agent Simulation',
            report_mode="research_only",
            goal_markdown="# Goal\nStudy agents.",
            problem_markdown="# Problem\nWhat evidence exists?",
            search_meta={"source": "openalex", "status": "ok"},
            synthesis_markdown="# Synthesis\nAgent workflows have roles.",
            hypothesis_markdown="# Hypothesis\nRole separation may help.",
            evidence_summary="- Known evidence [@paper-1].",
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
        context = _report_fixture(
            papers,
            topic='Agent Simulation',
            report_mode="research_only",
            goal_markdown="# Goal\nStudy agents.",
            problem_markdown="# Problem\nWhat evidence exists?",
            search_meta={"source": "openalex", "status": "ok"},
            synthesis_markdown="# Synthesis\nAgent workflows have roles.",
            hypothesis_markdown="# Hypothesis\nRole separation may help.",
            evidence_summary="- Known evidence.",
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
                max_section_tokens=777,
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
        self.assertTrue(all('"previous_draft":' in prompt for prompt in integration_prompts))
        self.assertTrue(client.max_output_tokens_values)
        self.assertTrue(all(value == 777 for value in client.max_output_tokens_values))

    def test_report_tool_gateway_exports_and_resolves_sources(self) -> None:
        paper = Paper(
            id="paper-1",
            title="Known Paper",
            authors=[],
            abstract="Known metadata.",
            url="https://example.com/paper-1",
        )
        context = _report_fixture(
            [paper],
            topic='Agent Simulation',
            report_mode="experiment",
            results={"metrics": {"accuracy": 0.75}},
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
        context = _report_fixture(
            [paper],
            topic='Agent Simulation',
            report_mode="experiment",
            results={"metrics": {"accuracy": 0.75}},
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
        context = _report_fixture(
            [paper],
            topic='Agent Simulation',
            report_mode="experiment",
            results={"metrics": {"pass@1": 0.75}},
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
        context = _report_fixture(
            [paper],
            topic='Agent Simulation',
            report_mode="experiment",
            results={"metrics": {"accuracy": 0.75}},
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
        context = _report_fixture(
            [paper],
            topic='Agent Simulation',
            report_mode="experiment",
            results={"metrics": {"accuracy": 0.75}},
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
        context = _report_fixture(
            [paper],
            topic='Agent Simulation',
            report_mode="experiment",
            results={
                "metrics": {
                    "accuracy": 0.75,
                    "train_time_sec": 4e-05,
                    "model_size": 24.0,
                    "eval_examples": 14.0,
                }
            },
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

    def test_report_audit_accepts_thousands_separators(self) -> None:
        paper = Paper(
            id="paper-1",
            title="Known Paper",
            authors=[],
            abstract="",
            url="https://example.com/paper-1",
        )
        context = _report_fixture(
            [paper],
            topic='Agent Simulation',
            report_mode="experiment",
        )
        context.metric_sources = [
            MetricSource(
                metric_id="metric:parameter_count",
                name="parameter_count",
                value=1810,
                artifact="results.json",
            )
        ]
        memory = ReportMemory()

        audit = build_report_audit(
            report="# Results\n\nThe parameter count is 1,810.\n",
            report_body="# Results\n\nThe parameter count is 1,810.\n",
            context=context,
            memory=memory,
        )

        self.assertEqual(audit.metric_audit.status, "passed")
        self.assertEqual(audit.metric_audit.unmatched_metrics, [])
        self.assertEqual(audit.metric_audit.unmatched_numbers, [])


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

    def test_report_audit_keeps_unused_source_pool_without_warning(self) -> None:
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
                title="Selected but Uncited Paper",
                authors=[],
                abstract="",
                url="https://example.com/2",
            ),
        ]
        context = _report_fixture(
            papers,
            topic='Agent Simulation',
            report_mode="experiment",
        )
        memory = ReportMemory()

        audit = build_report_audit(
            report="# Draft\n\nPrior work [@paper-1].\n",
            report_body="# Draft\n\nPrior work [@paper-1].\n",
            context=context,
            memory=memory,
        )

        self.assertEqual(audit.status, "passed")
        self.assertEqual(audit.citation_audit.status, "passed")
        self.assertEqual(audit.citation_audit.unused_references, ["paper-2"])
        self.assertEqual(audit.citation_audit.warnings, [])








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
        context = _report_fixture(
            papers,
            topic='Example Topic',
            report_mode="research_only",
            goal_markdown="# Goal\nSynthesize the field.",
            problem_markdown="# Problem\nWhat evidence is available?",
            synthesis_markdown="# Synthesis\nMethods and evaluation are both relevant.",
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



if __name__ == "__main__":
    unittest.main()

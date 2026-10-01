from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import ValidationError

from simple_ar.integrations.llm import LLMClient, LLMError, LLMResponseError
from simple_ar.research.contracts import CLAIM_SCOPE_RULES
from simple_ar.report.assembler import assemble_report_sections
from simple_ar.report.document_plan import resolve_document_plan, visual_requirements
from simple_ar.report.editor import MAX_DOCUMENT_REVIEW_SECTIONS, review_document
from simple_ar.report.execution_evidence import report_execution_evidence
from simple_ar.report.narrative import (
    adopted_claims, narrative_context, pending_document_revisions, pending_revision_review, revision_context,
)
from simple_ar.report.schema import (
    finding_requires_resolution,
    REVIEW_ACTION_RULES,
    AgentReportResult,
    ReportContext,
    ReportIterationRecord,
    ReportMemory,
    ReportRuntimeConfig,
    ReportSectionDraft,
    ReportSectionPlan,
    ReportSectionReview,
    ReportTemplateBundle,
    ReportToolResult,
    ReviewerFinding,
)
from simple_ar.report.survey import is_survey_report, route_section_sources
from simple_ar.report.tool_gateway import ReportToolGateway


_EXPERIMENT_EVIDENCE_RULES = """
Distinguish changes shown by the frozen patch from reused existing code and
unimplemented proposals. Do not describe an invoked utility as modified unless
its implementation appears in the patch.
Separate code that was written from behavior that was observed. A failed run
may not have reached or completed the intended operation: describe that code
as configured or attempted, not as a computation that actually occurred.
Treat claims that a failed run executed past its recorded error as factual
conflicts, even when the patch contains the intended code path.
An unchanged protocol isolates the implemented candidate as a whole, not a
unique causal mechanism. Without a relevant ablation or direct mechanism
measurement, do not assign all differences to one component. Do not invent
alternative implementation effects either: mention a plausible contributor
only if it is present in the verified implementation evidence, and distinguish
an untested explanation from an observed result. Review unsupported causal
attribution as a factual issue, not merely a style suggestion.
Means and sample standard deviations are descriptive: they do not establish
statistical significance or rule out seed variance. Such claims require an
actual appropriate statistical analysis in the supplied evidence.
With a small number of seeds and no significance test, do not call a result
conclusive, rule out seed noise, or imply stable general improvement. Use
bounded wording such as "under this protocol" and "descriptive across the
observed seeds".
Hardware, accelerator, operating-system, and runtime details stated only in the
task or prepared context are declared conditions, not observed execution
evidence. Unless verified_execution_results contains those details, describe
them as requested or configured conditions and say that hardware was not
recorded; do not write that the runs executed on that hardware.
Technical implementation status such as `validated`, a passing static check,
or an LLM review warning is not research-method evidence. If
implementation.method_validation.status is `未检查`, state that the candidate
mechanism was not independently checked; if it records execution evidence of
contradiction, do not present the same candidate method as validated.
Cite directly relevant original method papers when available in the supplied
sources; surveys are not a substitute for attribution of the adopted method.
"""

WRITER_SYSTEM = """You are the SimpleAutoResearch report Writer.
Write only evidence-bounded Markdown sections for the current run.
Do not invent citations, metrics, datasets, methods, or external references.
Treat the supplied verified execution results as authoritative for local
baseline, candidate, comparison, and resource claims.
Use short citation keys exactly as provided, in Pandoc-style form like [@P1].
For surveys, write synthesized prose, not a pipeline run log: omit stage names
and search/debug internals. For experiment/reproduction setup, include the
meaningful command and execution controls needed to repeat the work, using
execution_evidence rather than inferring them from protocol declarations.
Keep paragraphs short and focused. Use as many paragraphs as the requested
section target needs; only when no substantive length target is provided,
prefer concise paragraphs or a short comparison list instead of one dense
block. A short conclusion may need only one paragraph; do not pad it.
For multi-source survey reports, synthesize across papers: build taxonomies,
contrast assumptions, compare evaluation settings, and state boundary conditions.
For a single-source review, assess that source directly; do not manufacture a
method family, baseline comparison, or cross-paper consensus.
When many sources are available, use them to improve coverage and confidence;
do not make the report grow linearly by writing one paragraph per paper.
Never write prompt-planning language such as "Hint:", "Use this paper as", or
"Additional synthesis detail is available".
Return one JSON object matching the requested schema.""" + _EXPERIMENT_EVIDENCE_RULES


REVIEWER_SYSTEM = """You are the SimpleAutoResearch report Reviewer.
Review independently against the provided criteria, source handles, metrics, and current-run boundaries.
Recorded bibliographic metadata is not independent proof of source identity,
edition, publication date or a complete byline. Check important attribution
claims against the supplied original passages; retain unresolved metadata
conflicts or coverage limits instead of inventing replacements.
Optional draft source-use/citation arrays may be empty. Judge inline citations
and their support in the prose, not the presence of those optional arrays.
Do not rewrite prose unless asked. Return structured findings and optional bounded context requests.
Treat the supplied verified execution results as authoritative for local
baseline, candidate, comparison, and resource claims. Do not flag a claim as
unsupported merely because the deterministic execution appendix is assembled
after the Writer/Reviewer pass.
Flag operational/provenance sections in research-only reports when they make
the report read like a pipeline log instead of an academic survey.
Flag any section that is one huge paragraph or mixes many unrelated claims
without paragraph breaks or comparison bullets.
Flag paper-by-paper note dumps, prompt/planning residue, and performance claims
without boundary conditions. Require a taxonomy or cross-paper comparison only
when multiple independent sources support one; for a single-source review,
check that the report does not invent a comparison or consensus.
Flag conclusive language, claims that seed noise has been ruled out, or claims
of stable general improvement when the supplied evidence has only a small seed
set and descriptive statistics. Ask the Writer to bound those statements to
the observed protocol unless an appropriate statistical analysis is present.
Prefer revision instructions that improve synthesis density, evidence coverage,
and section structure over requests to add more paper-by-paper detail.
Return one JSON object matching the requested schema.""" + _EXPERIMENT_EVIDENCE_RULES


OUTLINE_PLANNER_SYSTEM = """You are the SimpleAutoResearch survey Outline Planner.
Create a topic-specific academic survey outline from the current run evidence.
Do not use external gold outlines, benchmark references, or hidden evaluator
expectations. Keep the outline broad, readable, and evidence-bounded.
Return one JSON object matching the requested schema."""


def run_report_agent(
    *,
    client: LLMClient | None,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    config: ReportRuntimeConfig,
    gateway: ReportToolGateway,
    emit: Callable[[str], None] | None = None,
    checkpoint_sink: Callable[[dict[str, Any]], None] | None = None,
    completed_checkpoint: Mapping[str, Any] | None = None,
) -> AgentReportResult | None:
    """Run the bounded Writer/Reviewer loop for the report stage.

    Args:
        client: Configured LLM client. ``None`` disables agent mode.
        context: Compact report input from earlier stages.
        template: Loaded writing template and reviewer criteria.
        memory: Recoverable report memory.
        config: Runtime report config.
        gateway: Read-only report tool gateway.
        emit: Optional progress callback.
        checkpoint_sink: Save completed sections, pending drafts and revision diagnostics.
        completed_checkpoint: Previously completed prefix; the caller verifies input identity.

    Returns:
        Agent-generated report body and updated memory, or ``None`` when agent
        mode is disabled or a validated agent pass cannot be produced.
    """
    if client is None or config.agent == "disabled":
        return None
    if not memory.section_plan:
        return None

    current = memory.model_copy(deep=True)
    current = _maybe_adapt_survey_outline(
        client=client,
        context=context,
        template=template,
        memory=current,
        config=config,
        emit=emit,
    ) if completed_checkpoint is None else current
    current = _resolve_document_plan(current, config=config)
    sections: list[ReportSectionDraft] = []
    iterations: list[ReportIterationRecord] = []
    all_findings: list[ReviewerFinding] = []
    all_tool_results: list[ReportToolResult] = []
    document_review_done = bool(completed_checkpoint.get("document_review_done")) if completed_checkpoint else False

    if completed_checkpoint is not None:
        current = ReportMemory.model_validate(completed_checkpoint["memory"])
        sections = [ReportSectionDraft.model_validate(row) for row in completed_checkpoint["sections"]]
        iterations = [ReportIterationRecord.model_validate(row) for row in completed_checkpoint["iterations"]]
        all_findings = [ReviewerFinding.model_validate(row) for row in completed_checkpoint["reviewer_findings"]]
        all_tool_results = [ReportToolResult.model_validate(row) for row in completed_checkpoint["tool_results"]]
        # Reuse the persisted results as the read-call ledger. Resuming must
        # not silently grant another full tool allowance.
        for name in gateway.call_counts:
            gateway.call_counts[name] = max(gateway.call_counts[name],
                sum(row.tool_name == name for row in all_tool_results))
        expected = [section.section_id for section in _draft_sequence(current.section_plan)]
        if [section.section_id for section in sections] != expected[:len(sections)]:
            raise ValueError("Report checkpoint sections do not match the planned draft sequence.")
    completed_count = len(sections)
    pending = completed_checkpoint.get("pending_draft") if completed_checkpoint else None
    pending_draft = ReportSectionDraft.model_validate(pending) if pending is not None else None
    if pending_draft is not None:
        planned = _draft_sequence(current.section_plan)
        if completed_count >= len(planned) or pending_draft.section_id != planned[completed_count].section_id:
            raise ValueError("Pending report draft does not match the next planned section.")

    # A checkpoint from the older append-only view may retain superseded claims.
    # Reproject before the first resumed prompt, not only at the next checkpoint.
    current.claims_evidence_matrix = adopted_claims(memory.claims_evidence_matrix, sections)

    def checkpoint() -> None:
        # Derive the claim view from the same adopted sections that are saved.
        # An old revision's metadata is not evidence for its replacement prose.
        current.claims_evidence_matrix = adopted_claims(memory.claims_evidence_matrix, sections)
        if checkpoint_sink is not None:
            checkpoint_sink({"memory": current.model_dump(mode="json"),
                             "sections": [row.model_dump(mode="json") for row in sections],
                             "iterations": [row.model_dump(mode="json") for row in iterations],
                             "reviewer_findings": [row.model_dump(mode="json") for row in all_findings],
                             "tool_results": [row.model_dump(mode="json") for row in all_tool_results],
                             "pending_draft": pending_draft.model_dump(mode="json") if pending_draft else None,
                             "document_review_done": document_review_done})

    try:
        for section_index, section in enumerate(_draft_sequence(current.section_plan), start=1):
            if section_index <= completed_count:
                continue
            if pending_draft is not None:
                draft = pending_draft
            else:
                # Long reports still need multiple evidence windows. Each window
                # revises the same complete section so the Writer can reconcile
                # new evidence without accumulating duplicate prose.
                source_batches = _source_batches(
                    section.evidence_handles,
                    config,
                )
                first_batch = source_batches[0] if source_batches else section.evidence_handles
                draft_section = _section_with_evidence(section, first_batch)
                _emit(emit, f"Writer drafting `{section.heading}`.")
                draft = _draft_section_with_recovery(
                    client=client,
                    context=context,
                    template=template,
                    memory=current,
                    section=draft_section,
                    config=config,
                    extra_context=[],
                    label=f"report-writer-{section.section_id}",
                    source_batch_index=1,
                    source_batch_count=len(source_batches),
                    adopted_sections=sections,
                    emit=emit,
                )
                iterations.append(_iteration(section_index, section, "draft", draft.status, draft.used_sources, draft=draft))

                if config.source_strategy == "batch_refine" and len(source_batches) > 1:
                    for batch_index, batch in enumerate(source_batches[1:], start=2):
                        _emit(
                            emit,
                            (
                                f"Writer integrating source batch {batch_index}/"
                                f"{len(source_batches)} for `{section.heading}`."
                            ),
                        )
                        batch_section = _section_with_evidence(section, batch)
                        revised = _draft_section_with_recovery(
                            client=client,
                            context=context,
                            template=template,
                            memory=current,
                            section=batch_section,
                            config=config,
                            extra_context=[],
                            previous_draft=draft,
                            label=f"report-integrator-{section.section_id}-{batch_index}",
                            source_batch_index=batch_index,
                            source_batch_count=len(source_batches),
                            adopted_sections=sections,
                            emit=emit,
                            draft_mode="section_revision",
                        )
                        draft = _merge_revision_draft(draft, revised)
                        iterations.append(
                            _iteration(section_index, section, "integrate_sources", draft.status, draft.used_sources, draft=draft)
                        )
            if config.reviewer == "disabled":
                sections.append(draft)
                _merge_draft_into_memory(current, draft, [])
                pending_draft = None
                checkpoint()
                continue

            section_findings: list[ReviewerFinding] = []
            pending_draft = draft
            checkpoint()
            review_context = next((row.tool_results for row in reversed(iterations)
                if row.section_id == section.section_id and row.action in {"revise", "review_context"}), [])
            revision_request, revision_baseline = pending_revision_review(iterations, section.section_id)
            revisions_used = sum(row.action == "revise" for row in iterations if row.section_id == section.section_id)
            remaining_revisions = max(0, config.max_review_iterations - revisions_used)
            # A review pass is always recorded. Each allowed correction then
            # receives another review, so max_review_iterations means actual
            # review -> revise cycles rather than extra reviews without edits.
            for remaining_round in range(remaining_revisions + 1):
                review_round = revisions_used + remaining_round
                action = "review" if review_round == 0 else "review_revision"
                label_suffix = "" if review_round == 0 else f"-round-{review_round + 1}"
                _emit(emit, f"Reviewer checking `{section.heading}` (round {review_round + 1}).")
                review = _review_section_with_recovery(
                    client=client,
                    context=context,
                    template=template,
                    memory=current,
                    section=section,
                    draft=draft,
                    config=config,
                    label=f"report-reviewer-{section.section_id}{label_suffix}",
                    emit=emit,
                    extra_context=review_context,
                    adopted_sections=sections,
                    revision_review=revision_request, previous_draft=revision_baseline,
                )
                tool_results = _run_context_requests(gateway, review, config)
                all_tool_results.extend(tool_results)
                if _needs_evidence_recheck(review):
                    # A pass that asks for missing evidence is provisional.
                    # Rejections already go through revise -> verify with the
                    # fetched evidence, so they do not need an extra review.
                    iterations.append(_iteration(section_index, section, "review_context", review.verdict,
                        draft.used_sources, findings=review.findings, tool_results=tool_results))
                    all_findings.extend(review.findings)
                    checkpoint()
                    if tool_results:
                        review = _review_section_with_recovery(client=client, context=context, template=template,
                            memory=current, section=section, draft=draft, config=config,
                            label=f"report-reviewer-{section.section_id}{label_suffix}-evidence", emit=emit,
                            extra_context=[*review_context, *tool_results], adopted_sections=sections,
                            revision_review=revision_request, previous_draft=revision_baseline)
                    review = _mark_pending_evidence(review)
                review_context = tool_results
                section_findings.extend(review.findings)
                all_findings.extend(review.findings)
                iterations.append(
                    _iteration(
                        section_index,
                        section,
                        action,
                        review.verdict,
                        draft.used_sources,
                        findings=review.findings,
                        tool_results=tool_results,
                        revision_instructions=review.revision_instructions,
                    )
                )
                if not _needs_revision(review) or remaining_round >= remaining_revisions:
                    break

                _emit(
                    emit,
                    f"Writer applying reviewer findings to `{section.heading}` (revision {review_round + 1}).",
                )
                revised = _draft_section_with_recovery(
                    client=client,
                    context=context,
                    template=template,
                    memory=current,
                    section=section,
                    config=config,
                    previous_draft=draft,
                    review=review,
                    extra_context=tool_results,
                    label=f"report-reviser-{section.section_id}-round-{review_round + 1}",
                    adopted_sections=sections,
                    emit=emit,
                )
                revision_request, revision_baseline = review, draft
                draft = _merge_revision_draft(draft, revised)
                iterations.append(
                    _iteration(
                        section_index,
                        section,
                        "revise",
                        draft.status,
                        draft.used_sources,
                        draft=draft,
                        tool_results=tool_results,
                    )
                )
                pending_draft = draft
                checkpoint()

            sections.append(draft)
            _merge_draft_into_memory(current, draft, section_findings)
            pending_draft = None
            checkpoint()

        # Audit the latest reviewed draft, not the union of issues from every
        # superseded draft. Iterations/all_findings retain the complete history.
        if not document_review_done:
            # A completed document review already resolved or retained the
            # section findings in its checkpoint. Replaying older per-section
            # reviews on resume would erase its unresolved cross-section issues.
            latest: dict[str, list[ReviewerFinding]] = {}
            for record in iterations:
                if record.action not in {"review", "review_revision"}:
                    continue
                if any(finding.type == "review_agent_fallback" for finding in record.findings):
                    latest.setdefault(record.section_id, [
                        finding for finding in current.reviewer_findings if finding.section_id == record.section_id
                    ]).extend(record.findings)
                else:
                    latest[record.section_id] = list(record.findings)
            current.reviewer_findings = _dedupe_findings(
                [finding for finding in current.reviewer_findings if finding.section_id not in latest]
                + [finding for findings in latest.values() for finding in findings]
            )
        if (config.document_review and config.reviewer != "disabled"
                and len(sections) > 1 and not document_review_done):
            _edit_whole_document(
                client=client, context=context, template=template, memory=current,
                config=config, sections=sections, iterations=iterations,
                all_findings=all_findings, emit=emit,
                checkpoint=checkpoint, gateway=gateway, all_tool_results=all_tool_results,
            )
            document_review_done = True
        current.claims_evidence_matrix = adopted_claims(memory.claims_evidence_matrix, sections)
        # Final section-review reconciliation also matters when whole-document
        # review is disabled or inapplicable. Persist the memory we return,
        # not the earlier append-only history from the last section checkpoint.
        checkpoint()
        body = assemble_report_sections(title=context.topic, sections=_final_sequence(current.section_plan, sections))
        return AgentReportResult(
            report_body=body,
            memory=current,
            sections=sections,
            iterations=iterations,
            reviewer_findings=all_findings,
            tool_results=all_tool_results,
            used_agent=True,
        )
    except (LLMError, ValidationError, ValueError) as exc:
        if not config.allow_llm_fallback:
            raise LLMError(
                f"Report agent failed validation and LLM fallback is disabled: {exc}"
            ) from exc
        _emit(emit, f"Report agent failed validation; using explicit offline fallback. {exc}")
        return None


def _edit_whole_document(
    *, client: LLMClient, context: ReportContext, template: ReportTemplateBundle,
    memory: ReportMemory, config: ReportRuntimeConfig, sections: list[ReportSectionDraft],
    iterations: list[ReportIterationRecord], all_findings: list[ReviewerFinding],
    emit: Callable[[str], None] | None,
    checkpoint: Callable[[], None], gateway: ReportToolGateway,
    all_tool_results: list[ReportToolResult],
) -> None:
    """Correct at most two sections within their configured, recoverable allowance."""
    plans = {plan.section_id: plan for plan in memory.section_plan}
    by_id = {draft.section_id: index for index, draft in enumerate(sections)}
    pending = pending_document_revisions(iterations)
    continuations = pending_document_revisions(iterations, include_rejected=True)

    def checked_document_reviews(label: str) -> list[ReportSectionReview]:
        def call(review_label: str) -> list[ReportSectionReview]:
            return review_document(client=client, template=template, memory=memory,
                sections=_final_sequence(memory.section_plan, sections), config=config,
                execution_summary=_compact_execution_results(context.results),
                execution_evidence=report_execution_evidence(context), supplementary_evidence=all_tool_results,
                metric_summary=_prompt_metrics(memory, detail="summary"),
                source_evidence=[_prompt_handle_view(row) for row in memory.source_handles if row.kind in {"paper", "material"}],
                label=review_label)

        reviews = call(label)
        provisional = [row for row in reviews if _needs_evidence_recheck(row)]
        if not provisional:
            return reviews
        fetched = []
        for review in provisional:
            results = _run_context_requests(gateway, review, config)
            fetched.extend(results)
            iterations.append(_iteration(len(iterations) + 1, plans[review.section_id], "document_context",
                review.verdict, sections[by_id[review.section_id]].used_sources,
                findings=review.findings, tool_results=results))
            all_findings.extend(review.findings)
        all_tool_results.extend(fetched)
        checkpoint()
        if fetched:
            return [_mark_pending_evidence(row) for row in call(label + "-evidence")]
        return [_mark_pending_evidence(row) if row in provisional else row for row in reviews]

    try:
        _emit(emit, "Reviewer checking whole-document coherence.")
        reviews = checked_document_reviews("report-document-reviewer")
    except (LLMError, ValidationError, ValueError) as exc:
        finding = ReviewerFinding(
            finding_id="document-review-unavailable", type="document_review_unavailable",
            severity="major", message=f"Whole-document review did not complete: {exc}",
            suggested_action="Inspect cross-section coherence before publication.",
        )
        all_findings.append(finding)
        memory.reviewer_findings = _dedupe_findings([*memory.reviewer_findings, finding])
        return
    adopted_revision = False
    # Finish persisted candidates first; a fresh review cannot change the
    # correction contract midway or cause the Writer to regenerate that draft.
    reviews = [*(request for _, request in continuations.values()),
               *(review for review in reviews if review.section_id not in continuations)]
    # This bounded queue also handles a new defect introduced by a correction.
    # A candidate is not adopted until it passes; subsequent corrections retain
    # the original contract and consume the same section allowance.
    while reviews:
        review = reviews.pop(0)
        plan = plans[review.section_id]
        index = by_id[review.section_id]
        original = sections[index]
        all_findings.extend(review.findings)
        iterations.append(_iteration(len(iterations) + 1, plan, "document_review",
            review.verdict, original.used_sources, findings=review.findings,
            revision_instructions=review.revision_instructions))
        if not _needs_revision(review) or config.max_review_iterations == 0:
            memory.reviewer_findings = _dedupe_findings([*memory.reviewer_findings, *review.findings])
            continue
        try:
            # An optional editor must never replace an already reviewed section
            # with the generic writer/reviewer fallback after a provider error.
            persisted = pending.get(plan.section_id)
            prior_candidates = [row for row in iterations if row.action == "document_revise"]
            targets = {row.section_id for row in prior_candidates}
            attempts_used = sum(row.section_id == plan.section_id for row in prior_candidates)
            if persisted is None and (attempts_used >= config.max_review_iterations
                    or (plan.section_id not in targets and len(targets) >= MAX_DOCUMENT_REVIEW_SECTIONS)):
                finding = ReviewerFinding(finding_id=f"{plan.section_id}-document-revision-budget",
                    type="document_revision_unresolved", severity="major", section_id=plan.section_id,
                    message="The editor correction allowance was already consumed; recovery does not grant another draft.",
                    suggested_action="Inspect the retained candidates and unresolved review before publication.")
                all_findings.append(finding)
                memory.reviewer_findings = _dedupe_findings([*memory.reviewer_findings, *review.findings, finding])
                continue
            previous = continuations.get(plan.section_id)
            tool_results = list(previous[0].tool_results) if previous else []
            if previous:
                candidate_index = iterations.index(previous[0])
                for event in iterations[candidate_index + 1:]:
                    if event.section_id == plan.section_id and event.action == "document_context":
                        tool_results.extend(result for result in event.tool_results if result not in tool_results)
            if persisted is None:
                fetched = _run_context_requests(gateway, review, config)
                tool_results.extend(fetched)
                all_tool_results.extend(fetched)
            strict = config.model_copy(update={"allow_llm_fallback": False})
            if persisted:
                candidate_record, revised = persisted[0], persisted[0].draft
            else:
                _emit(emit, f"Writer revising `{plan.heading}` for document coherence.")
                revised = _draft_section_with_recovery(
                    client=client, context=context, template=template, memory=memory,
                    section=plan, config=strict, extra_context=tool_results,
                    previous_draft=previous[0].draft if previous else original,
                    review=review, label=f"report-document-reviser-{plan.section_id}",
                    draft_mode="section_revision", emit=emit, adopted_sections=sections,
                )
                iterations.append(_iteration(len(iterations) + 1, plan, "document_revise",
                    revised.status, revised.used_sources, draft=revised, tool_results=tool_results))
                candidate_record = iterations[-1]
                candidate_record.adopted = False
                checkpoint()
            _emit(emit, f"Reviewer verifying revised `{plan.heading}`.")
            verification = _review_section_with_recovery(
                client=client, context=context, template=template, memory=memory,
                section=plan, draft=revised, config=strict,
                label=f"report-document-verifier-{plan.section_id}", emit=emit,
                extra_context=tool_results,
                adopted_sections=sections,
                revision_review=review, previous_draft=original,
            )
            if verification.context_requests:
                fetched = _run_context_requests(gateway, verification, config)
                tool_results.extend(fetched)
                all_tool_results.extend(fetched)
                if fetched:
                    iterations.append(_iteration(len(iterations) + 1, plan, "document_context",
                        verification.verdict, revised.used_sources, tool_results=fetched))
                    checkpoint()
                if fetched and _needs_evidence_recheck(verification):
                    verification = _review_section_with_recovery(client=client, context=context, template=template,
                        memory=memory, section=plan, draft=revised, config=strict,
                        label=f"report-document-verifier-{plan.section_id}-evidence", emit=emit,
                        extra_context=tool_results, adopted_sections=sections,
                        revision_review=review, previous_draft=original)
                verification = _mark_pending_evidence(verification)
            all_findings.extend(verification.findings)
            iterations.append(_iteration(len(iterations) + 1, plan, "document_verify",
                verification.verdict, revised.used_sources, findings=verification.findings,
                revision_instructions=verification.revision_instructions))
            pending.pop(plan.section_id, None)
            if _needs_revision(verification):
                unresolved = verification.findings or [ReviewerFinding(
                    finding_id=f"{plan.section_id}-document-verification-failed",
                    type="document_revision_unresolved", severity="major",
                    message="The whole-document revision did not pass section verification.",
                    section_id=plan.section_id,
                )]
                memory.reviewer_findings = _dedupe_findings([
                    *memory.reviewer_findings, *review.findings, *unresolved,
                ])
                # Persist the rejection before another API call. Recovery may
                # continue this draft, but does not regenerate it or reset quota.
                checkpoint()
                if attempts_used + (persisted is None) < config.max_review_iterations:
                    continuation = pending_document_revisions(iterations, include_rejected=True)[plan.section_id]
                    continuations[plan.section_id] = continuation
                    reviews.insert(0, continuation[1])
                continue
            sections[index] = revised
            candidate_record.adopted = True
            adopted_revision = True
            memory.reviewer_findings = [
                finding for finding in memory.reviewer_findings if finding.section_id != plan.section_id
            ]
            _merge_draft_into_memory(memory, revised, verification.findings)
        except (LLMError, ValidationError, ValueError) as exc:
            unresolved = ReviewerFinding(
                finding_id=f"{plan.section_id}-document-revision-unavailable",
                type="document_revision_unavailable", severity="major",
                message=f"Whole-document finding for {plan.heading} was not resolved: {exc}",
                section_id=plan.section_id,
                suggested_action="Inspect the original section and the document review finding.",
            )
            all_findings.append(unresolved)
            memory.reviewer_findings = _dedupe_findings([
                *memory.reviewer_findings, *review.findings, unresolved,
            ])
    if adopted_revision:
        try:
            _emit(emit, "Reviewer rechecking whole-document coherence after revision.")
            verification_reviews = checked_document_reviews("report-document-verifier")
            for review in verification_reviews:
                plan = plans[review.section_id]
                unresolved = review.findings or ([ReviewerFinding(
                    finding_id=f"{plan.section_id}-document-recheck-unresolved",
                    type="document_revision_unresolved", severity="major",
                    message="The final whole-document recheck still requested revision.",
                    section_id=plan.section_id,
                )] if _needs_revision(review) else [])
                all_findings.extend(unresolved)
                iterations.append(_iteration(len(iterations) + 1, plan, "document_recheck",
                    review.verdict, sections[by_id[review.section_id]].used_sources,
                    findings=unresolved))
                memory.reviewer_findings = _dedupe_findings([
                    *memory.reviewer_findings, *unresolved,
                ])
        except (LLMError, ValidationError, ValueError) as exc:
            unavailable = ReviewerFinding(
                finding_id="document-recheck-unavailable", type="document_review_unavailable",
                severity="major", message=f"Whole-document recheck did not complete: {exc}",
                suggested_action="Inspect the revised report before publication.",
            )
            all_findings.append(unavailable)
            memory.reviewer_findings = _dedupe_findings([*memory.reviewer_findings, unavailable])


def _maybe_adapt_survey_outline(
    *,
    client: LLMClient,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    config: ReportRuntimeConfig,
    emit: Callable[[str], None] | None,
) -> ReportMemory:
    contract = memory.survey_contract if isinstance(memory.survey_contract, dict) else {}
    if not contract.get("enabled"):
        return memory
    strategy = str(contract.get("outline_strategy") or config.outline_strategy or "auto").lower()
    if strategy == "template":
        return memory
    if not is_survey_report(template_name=template.name, style=config.style, report_mode=context.report_mode):
        return memory
    if len(memory.section_plan) < 3:
        return memory
    errors: list[str] = []
    planned: list[ReportSectionPlan] = []
    visual_candidates: list[dict[str, Any]] = []
    for attempt in (1, 2):
        try:
            planned, visual_candidates = _plan_topic_specific_outline(
                client=client,
                context=context,
                template=template,
                memory=memory,
                config=config,
                retry=attempt == 2,
            )
            break
        except (LLMError, ValidationError, ValueError, TypeError) as exc:
            errors.append(str(exc))
            if attempt == 1:
                _emit(emit, f"Survey outline planner did not yield a usable plan; retrying once. {exc}")
            else:
                if config.allow_llm_fallback:
                    _emit(emit, f"Survey outline planner fallback used; keeping template outline. {exc}")
                else:
                    _emit(emit, f"Survey outline planner failed after retry; fallback is disabled. {exc}")
    if not planned:
        if not config.allow_llm_fallback:
            raise LLMError(
                "Survey outline planner failed after bounded retries and LLM fallback is disabled"
            )
        return memory.model_copy(
            update={
                "outline_planning": {
                    "schema_version": "report_outline_planning.v1",
                    "status": "fallback",
                    "strategy": "deterministic_outline_with_full_evidence_budget",
                    "attempts": len(errors),
                    "errors": errors,
                    "section_source_budget": _outline_source_budget(memory.survey_contract, config),
                    "visual_candidates": [],
                },
                "key_decisions": memory.key_decisions
                + [
                    "Survey outline planner fallback retained the configured evidence budget for every section."
                ],
            }
        )
    _emit(emit, f"Survey outline planner produced {len(planned)} topic-specific section(s).")
    return memory.model_copy(
        update={
            "section_plan": planned,
            "outline_planning": {
                "schema_version": "report_outline_planning.v1",
                "status": "adapted",
                "strategy": "topic_specific_outline",
                "attempts": len(errors) + 1,
                "errors": errors,
                "section_source_budget": _outline_source_budget(memory.survey_contract, config),
                "visual_candidates": visual_candidates,
            },
            "key_decisions": memory.key_decisions
            + ["Survey section plan adapted to the topic and current-run evidence before drafting."],
        }
    )


def _resolve_document_plan(memory: ReportMemory, *, config: ReportRuntimeConfig) -> ReportMemory:
    """Freeze the only plan that Writer, Reviewer, renderer, and audit consume."""
    candidates = memory.outline_planning.get("visual_candidates", [])
    if not isinstance(candidates, list):
        candidates = []
    plan = resolve_document_plan(
        sections=memory.section_plan,
        contract=memory.survey_contract,
        config=config,
        visual_candidates=[row for row in candidates if isinstance(row, dict)],
        status=str(memory.outline_planning.get("status") or "resolved"),
    )
    planning = dict(memory.outline_planning)
    planning["document_plan"] = {
        "schema_version": plan.schema_version,
        "status": plan.status,
        "section_count": len(plan.sections),
        "visual_intent_count": len(plan.visual_intents),
        "target_words": plan.target_words,
    }
    return memory.model_copy(
        update={
            "section_plan": plan.sections,
            "document_plan": plan,
            "outline_planning": planning,
        }
    )


def _plan_topic_specific_outline(
    *,
    client: LLMClient,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    config: ReportRuntimeConfig,
    retry: bool = False,
) -> tuple[list[ReportSectionPlan], list[dict[str, Any]]]:
    response = client.ask_json(
        OUTLINE_PLANNER_SYSTEM,
        _outline_planner_prompt(
            context=context,
            template=template,
            memory=memory,
            config=config,
            retry=retry,
        ),
        label="report-outline-planner-retry" if retry else "report-outline-planner",
    )
    sections = _ensure_survey_outline_coverage(_normalize_outline_sections(response))
    if len(sections) < 5:
        raise ValueError("outline planner returned fewer than 5 usable sections")
    if _outline_is_overly_template_like(sections):
        raise ValueError(
            "outline reused the default structural headings instead of deriving "
            "topic-specific body axes from the selected evidence"
        )
    budget = _outline_source_budget(memory.survey_contract, config)
    planned: list[ReportSectionPlan] = []
    planned_sections = sections[:12]
    total_target_words = _outline_target_words(memory.survey_contract, default=12000)
    default_min_citations = _outline_min_citations(memory.survey_contract, default=3)
    for index, row in enumerate(planned_sections, start=1):
        heading = _clean_outline_heading(row["heading"])
        goal = row["goal"]
        keywords = " ".join(row.get("keywords", []))
        default_target_words = _planned_section_target_words(
            heading=heading,
            index=index,
            total=len(planned_sections),
            target_words=total_target_words,
        )
        target_words = _coerce_int(
            row.get("target_words") or default_target_words,
            default=default_target_words,
            lower=0,
            upper=8000,
        )
        min_citations = _coerce_int(
            row.get("min_citations") or default_min_citations,
            default=default_min_citations,
            lower=0,
            upper=20,
        )
        subsections = _string_items(row.get("subsections"))[:6] or _default_subsections_for_heading(
            heading,
            row.get("keywords", []),
        )
        if not _section_allows_subsections(heading):
            subsections = []
        evidence_handles = route_section_sources(
            context=context,
            heading=heading,
            goal=f"{goal} {keywords}",
            contract=memory.survey_contract,
            budget=budget,
        )
        planned.append(
            ReportSectionPlan(
                section_id=_section_slug(heading) or f"section_{index}",
                heading=heading,
                goal=goal,
                evidence_handles=evidence_handles or _fallback_section_handles(memory, limit=budget),
                target_words=target_words,
                min_citations=min_citations,
                subsections=subsections,
                required=True,
                final_order=index,
                draft_order=_survey_draft_order(heading, index, len(planned_sections)),
            )
        )
    candidates = response.get("visual_intents") if isinstance(response.get("visual_intents"), list) else []
    return _dedupe_section_ids(planned), [row for row in candidates if isinstance(row, dict)]


def _outline_planner_prompt(
    *,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    config: ReportRuntimeConfig,
    retry: bool = False,
) -> str:
    compact_contract = _compact_survey_contract(memory.survey_contract)
    # Role groups help the Writer balance sources, but exposing them as
    # ``taxonomy_facets`` anchors the outline agent to provenance labels.
    compact_contract["taxonomy_facets"] = []
    # The deterministic contract outline is a coverage fallback, not a gold
    # outline. Showing it to the planner made weaker models copy its generic
    # headings verbatim and still appear to have produced an "adapted" plan.
    compact_contract["outline_sections"] = []
    payload = {
        "task": "plan_topic_specific_survey_outline",
        "topic": context.topic,
        "report_mode": context.report_mode,
        "template": template.name,
        "style": config.style,
        "delivery_constraints": {
            "max_cited_sources": config.max_cited_sources or None,
            "source_scope": "Final document, not each section" if config.max_cited_sources else None,
        },
        "survey_contract": compact_contract,
        "required_structure": {
            "front_matter": ["Abstract", "Introduction"],
            "back_matter": ["Conclusion"],
            "body_requirement": "Derive 4-7 topic-specific body sections from current-run evidence.",
        },
        "available_source_brief": _outline_source_brief(context),
        "synthesis_excerpt": context.synthesis_markdown[:2500],
        "evidence_summary_excerpt": context.evidence_summary[:2500],
            "planning_rules": [
            "Create 7-10 display sections, including Abstract, Introduction, body sections, and Conclusion.",
            "For long-form surveys, each major body section should include 2-4 planned third-level subsection hints so the final report has a navigable internal structure.",
            "Do not add subsection hints for Abstract, Introduction, or Conclusion; those sections should remain single-section prose.",
            "Body sections must be topic-specific; do not blindly reuse generic headings if the topic suggests better axes.",
            "Do not use the default body headings `Conceptual Foundations and Taxonomy`, `Methods and System Construction`, `Applications and Use Cases`, or `Evaluation, Benchmarks, and Evidence Quality`. Replace them with concrete conceptual axes, method families, task settings, or evaluation regimes evident in the selected literature.",
            "At least three body headings must identify topic-specific concepts from the source brief. A reader should be able to distinguish this outline from an outline for a different research topic without reading the prose.",
            "Treat required facets as coverage checks, not as section titles or taxonomy axes. Derive reader-facing taxonomy and headings from the source brief and selected papers.",
            "Keep SurveyBench-compatible Markdown in mind: final report will use # Title and ## numbered sections.",
            "Use headings that a human survey reader would expect for this topic.",
            "Cover foundations/scope, a taxonomy or method families, system or method construction, evaluation practice, applications or domains when relevant, challenges, and future directions.",
            "For long surveys, keep coverage broad even when headings are topic-specific: include related surveys or adjacent fields when evidence permits, and include future directions separately when it improves reader utility.",
            "If a facet has weak evidence, include it only as a limitation or open problem instead of inventing coverage.",
            "Do not mention SimpleAutoResearch, pipeline stages, artifacts, prompts, or evaluation benchmark internals.",
            "Do not include a References section; references are appended separately.",
            "Return the requested JSON object with a non-empty `sections` list; do not return prose outside JSON.",
        ],
        "output_schema": {
            "sections": [
                {
                    "heading": "Short academic section heading without numbering",
                    "goal": "Reader-facing purpose and synthesis target for this section",
                    "keywords": ["routing keywords for evidence selection"],
                    "target_words": 1200,
                    "min_citations": 3,
                    "subsections": ["optional third-level subsection heading"],
                    "required": True,
                }
            ],
            "visual_intents": [
                {
                    "kind": "table|figure",
                    "title": "Reader-facing visual title",
                    "purpose": "What comparison or structure this visual clarifies",
                    "section_heading": "One heading from sections",
                    "evidence_handles": ["optional selected source handles"],
                    "columns": ["table column", "table column"],
                    "view": "taxonomy-map|system-construction-flow|evaluation-landscape|challenge-roadmap for figures only",
                }
            ],
            "notes": "optional planning notes",
        },
    }
    if retry:
        payload["retry_instruction"] = (
            "The previous outline copied the generic structural template instead of organizing the "
            "available evidence. Keep the required report functions, but name the body sections after "
            "concrete concepts, method families, task settings, or evaluation regimes from the source brief. "
            "Return 7-10 valid, reader-facing sections with the required JSON fields."
        )
    return _json_prompt(payload)


_DEFAULT_SURVEY_BODY_HEADINGS = frozenset(
    {
        "conceptual foundations and taxonomy",
        "methods and system construction",
        "applications and use cases",
        "evaluation benchmarks and evidence quality",
        "related surveys and positioning",
        "challenges and future directions",
    }
)


def _outline_is_overly_template_like(sections: list[dict[str, Any]]) -> bool:
    """Detect copied fallback structure without constraining valid survey organization.

    Generic front and back matter are appropriate.  The signal only fires when
    most body headings exactly match the deterministic fallback vocabulary,
    which is evidence that a model copied the contract scaffold rather than
    synthesizing an outline from the current literature.
    """

    body_headings = [
        _clean_outline_heading(str(row.get("heading") or "")).lower()
        for row in sections
        if _clean_outline_heading(str(row.get("heading") or "")).lower()
        not in {"abstract", "introduction", "introduction and scope", "conclusion"}
    ]
    if len(body_headings) < 4:
        return False
    copied = sum(heading in _DEFAULT_SURVEY_BODY_HEADINGS for heading in body_headings)
    return copied >= max(3, (len(body_headings) * 3 + 4) // 5)


def _outline_source_brief(context: ReportContext) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for handle in context.source_handles:
        if handle.kind not in {"paper", "paper_brief", "material"}:
            continue
        rows.append(
            {
                "handle": handle.handle,
                "cite_as": handle.citation_key,
                "title": handle.title[:180],
                "summary": handle.summary[:500],
                "section": handle.section[:120],
                "metadata": _compact_source_metadata(handle.metadata),
            }
        )
        if len(rows) >= 28:
            break
    return rows


def _compact_source_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    keys = ("method", "contribution", "evaluation", "relevance", "venue", "year")
    compact: dict[str, str] = {}
    for key in keys:
        value = metadata.get(key) if isinstance(metadata, dict) else None
        if value not in (None, ""):
            compact[key] = str(value)[:240]
    return compact


def _normalize_outline_sections(response: dict[str, Any]) -> list[dict[str, Any]]:
    raw = response.get("sections")
    if not isinstance(raw, list):
        raise ValueError("outline planner response missing `sections` list")
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        heading = _clean_outline_heading(str(item.get("heading") or ""))
        if not heading or heading.lower() == "references":
            continue
        goal = str(item.get("goal") or "").strip()
        keywords = item.get("keywords")
        if isinstance(keywords, str):
            keyword_rows = [keywords]
        elif isinstance(keywords, list):
            keyword_rows = [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
        else:
            keyword_rows = []
        rows.append(
            {
                "heading": heading,
                "goal": goal or f"Synthesize evidence relevant to {heading}.",
                "keywords": keyword_rows[:10],
                "target_words": item.get("target_words") or 0,
                "min_citations": item.get("min_citations") or 0,
                "subsections": _string_items(item.get("subsections"))[:6],
            }
        )
    return rows


def _ensure_survey_outline_coverage(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preserve broad survey coverage while allowing topic-specific headings."""
    if not sections:
        return sections
    rows = list(sections)
    text = " ".join(f"{row.get('heading', '')} {row.get('goal', '')}" for row in rows).lower()
    additions: list[dict[str, Any]] = []
    if not any(term in text for term in ("related survey", "prior survey", "positioning", "adjacent", "neighboring")):
        additions.append(
            {
                "heading": "Related Surveys and Positioning",
                "goal": "Position the topic against prior surveys and neighboring fields, explaining what this synthesis adds and where boundaries remain.",
                "keywords": ["related surveys", "positioning", "adjacent fields", "neighboring areas"],
                "subsections": [
                    "Prior surveys and their scope",
                    "Neighboring fields",
                    "What this synthesis adds",
                ],
            }
        )
    if not any(term in text for term in ("future direction", "future work", "research direction")):
        additions.append(
            {
                "heading": "Future Directions",
                "goal": "State concrete research directions, testable hypotheses, and evidence needed to validate or falsify them.",
                "keywords": ["future directions", "research directions", "open problems", "hypotheses"],
                "subsections": [
                    "Open technical problems",
                    "Evidence needed next",
                    "Research roadmap",
                ],
            }
        )
    if not additions:
        return rows
    insert_at = len(rows)
    for index, row in enumerate(rows):
        if "conclusion" in str(row.get("heading", "")).lower():
            insert_at = index
            break
    return rows[:insert_at] + additions + rows[insert_at:]


def _clean_outline_heading(text: str) -> str:
    heading = text.strip().strip("#").strip()
    heading = " ".join(heading.split())
    heading = re.sub(r"^\d+(?:\.\d+)*\s+", "", heading)
    return heading[:100]


def _outline_source_budget(contract: dict[str, Any], config: ReportRuntimeConfig) -> int:
    raw = contract.get("section_source_budget") if isinstance(contract, dict) else None
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        value = 24 if config.cost_profile == "thorough" else 12
    return max(4, min(40, value))


def _outline_target_words(contract: dict[str, Any], *, default: int) -> int:
    expected = contract.get("expected_coverage") if isinstance(contract, dict) else {}
    raw = expected.get("target_words") if isinstance(expected, dict) else None
    return _coerce_int(raw, default=default, lower=1200, upper=50000)


def _outline_min_citations(contract: dict[str, Any], *, default: int) -> int:
    expected = contract.get("expected_coverage") if isinstance(contract, dict) else {}
    raw = expected.get("min_citations_per_section") if isinstance(expected, dict) else None
    return _coerce_int(raw, default=default, lower=0, upper=20)


def _planned_section_target_words(
    *,
    heading: str,
    index: int,
    total: int,
    target_words: int,
) -> int:
    lowered = heading.lower()
    if "abstract" in lowered or "conclusion" in lowered:
        return max(250, min(900, target_words // max(total * 3, 1)))
    body_count = max(1, total - 2)
    body_budget = max(600, target_words - min(1800, target_words // 5))
    return max(600, min(3500, body_budget // body_count))


def _default_subsections_for_heading(heading: str, keywords: object) -> list[str]:
    lowered = heading.lower()
    if not _section_allows_subsections(heading):
        return []
    if "foundation" in lowered or "taxonomy" in lowered:
        return ["Core concepts", "Taxonomy axes", "Interactions between axes"]
    if "method" in lowered or "system" in lowered or "construction" in lowered:
        return ["Common design pattern", "Representative method families", "Trade-offs"]
    if "application" in lowered or "use case" in lowered:
        return ["Task families", "Deployment settings", "Evidence strength"]
    if "evaluation" in lowered or "benchmark" in lowered:
        return ["Evaluation protocols", "Metrics and datasets", "Evidence limitations"]
    if "survey" in lowered or "positioning" in lowered:
        return ["Prior surveys", "Adjacent fields", "Added synthesis"]
    if "challenge" in lowered or "future" in lowered:
        return ["Technical bottlenecks", "Evaluation gaps", "Future research directions"]
    rows = [str(item).replace("_", " ").title() for item in _string_items(keywords)]
    return rows[:3]


def _section_allows_subsections(heading: str) -> bool:
    lowered = heading.lower()
    return not any(term in lowered for term in ("abstract", "introduction", "conclusion"))


def _coerce_int(value: object, *, default: int, lower: int, upper: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(upper, parsed))


def _fallback_section_handles(memory: ReportMemory, *, limit: int) -> list[str]:
    return [
        handle.handle
        for handle in memory.source_handles
        if handle.kind in {"paper", "paper_brief", "material"}
    ][:limit]


def _survey_draft_order(heading: str, final_order: int, total: int) -> int:
    lowered = heading.lower()
    if "abstract" in lowered:
        return total + 20
    if "introduction" in lowered:
        return total + 10
    if "conclusion" in lowered:
        return total + 5
    return final_order


def _section_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")[:60]


def _dedupe_section_ids(sections: list[ReportSectionPlan]) -> list[ReportSectionPlan]:
    seen: dict[str, int] = {}
    result: list[ReportSectionPlan] = []
    for section in sections:
        base = section.section_id or "section"
        count = seen.get(base, 0) + 1
        seen[base] = count
        section_id = base if count == 1 else f"{base}_{count}"
        result.append(section.model_copy(update={"section_id": section_id}))
    return result


def _draft_section(
    *,
    client: LLMClient,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    section: ReportSectionPlan,
    config: ReportRuntimeConfig,
    extra_context: list[ReportToolResult],
    label: str,
    previous_draft: ReportSectionDraft | None = None,
    review: ReportSectionReview | None = None,
    source_batch_index: int = 1,
    source_batch_count: int = 1,
    prompt_suffix: str = "",
    include_previous_draft: bool = True,
    draft_mode: str = "section",
    recovery: bool = False,
    adopted_sections: list[ReportSectionDraft] | None = None,
) -> ReportSectionDraft:
    if recovery:
        prompt = _writer_recovery_prompt(
            context=context,
            memory=memory,
            section=section,
            config=config,
            previous_draft=previous_draft,
            review=review,
            draft_mode=draft_mode,
            extra_context=extra_context,
            adopted_sections=adopted_sections,
        )
    else:
        prompt = _writer_prompt(
            context=context,
            template=template,
            memory=memory,
            section=section,
            config=config,
            extra_context=extra_context,
            previous_draft=previous_draft,
            review=review,
            source_batch_index=source_batch_index,
            source_batch_count=source_batch_count,
            include_previous_draft=include_previous_draft,
            draft_mode=draft_mode,
            adopted_sections=adopted_sections,
        )
    if prompt_suffix:
        prompt = prompt + "\n\n" + prompt_suffix
    response = client.ask_json(
        WRITER_SYSTEM,
        prompt,
        label=label,
        max_output_tokens=config.max_section_tokens if config.max_section_tokens > 0 else None,
    )
    if _is_claim_record_response(response):
        raise LLMResponseError(
            "Writer returned a claim-level metadata record instead of the required section draft."
        )
    draft = ReportSectionDraft.model_validate(_normalize_draft_response(response, section))
    if not draft.draft_markdown.strip() and draft.status != "skipped":
        keys = ", ".join(sorted(str(key) for key in response)[:12])
        raise LLMResponseError(f"Writer returned empty draft for {section.section_id}; response keys: {keys or '(none)'}")
    return draft


def _review_section(
    *,
    client: LLMClient,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    section: ReportSectionPlan,
    draft: ReportSectionDraft,
    label: str,
    max_output_tokens: int | None = None,
    prompt_suffix: str = "",
    adopted_sections: list[ReportSectionDraft] | None = None,
    revision_review: ReportSectionReview | None = None,
    previous_draft: ReportSectionDraft | None = None,
) -> ReportSectionReview:
    prompt = _reviewer_prompt(
        context=context,
        template=template,
        memory=memory,
        section=section,
        draft=draft,
        adopted_sections=adopted_sections,
        revision_review=revision_review, previous_draft=previous_draft,
    )
    if prompt_suffix:
        prompt = prompt + "\n\n" + prompt_suffix
    response = client.ask_json(
        REVIEWER_SYSTEM,
        prompt,
        label=label,
        max_output_tokens=max_output_tokens,
    )
    return ReportSectionReview.model_validate(_normalize_review_response(response, section))


def _draft_section_with_recovery(
    *,
    client: LLMClient,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    section: ReportSectionPlan,
    config: ReportRuntimeConfig,
    extra_context: list[ReportToolResult],
    label: str,
    previous_draft: ReportSectionDraft | None = None,
    review: ReportSectionReview | None = None,
    source_batch_index: int = 1,
    source_batch_count: int = 1,
    emit: Callable[[str], None] | None = None,
    prompt_suffix: str = "",
    include_previous_draft: bool = True,
    draft_mode: str = "section",
    adopted_sections: list[ReportSectionDraft] | None = None,
) -> ReportSectionDraft:
    for attempt in range(2):
        try:
            return _draft_section(
                client=client, context=context, template=template, memory=memory,
                section=section, config=config, extra_context=extra_context,
                label=f"{label}-retry" if attempt else label,
                previous_draft=previous_draft, review=review,
                source_batch_index=source_batch_index, source_batch_count=source_batch_count,
                prompt_suffix=prompt_suffix,
                include_previous_draft=include_previous_draft, draft_mode=draft_mode,
                recovery=bool(attempt),
                adopted_sections=adopted_sections,
            )
        except (LLMError, ValueError) as exc:
            failure = exc
            # Transport retries belong to the provider. A format retry requires
            # an actual response; budget/transport failures have no draft to fix.
            if attempt or (isinstance(exc, LLMError) and not isinstance(exc, LLMResponseError)):
                break
            _emit(emit, f"Writer JSON validation failed for `{section.heading}`; retrying once. {exc}")
    if not config.allow_llm_fallback:
        raise LLMError(f"Writer failed for `{section.heading}` and LLM fallback is disabled: {failure}") from failure
    _emit(emit, f"Writer fallback used for `{section.heading}`. {failure}")
    return _fallback_section_draft(section)


def _review_section_with_recovery(
    *,
    client: LLMClient,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    section: ReportSectionPlan,
    draft: ReportSectionDraft,
    config: ReportRuntimeConfig,
    label: str,
    emit: Callable[[str], None] | None = None,
    extra_context: list[ReportToolResult] | None = None,
    adopted_sections: list[ReportSectionDraft] | None = None,
    revision_review: ReportSectionReview | None = None,
    previous_draft: ReportSectionDraft | None = None,
) -> ReportSectionReview:
    evidence_suffix = json.dumps({"extra_tool_context": [row.model_dump(mode="json")
                                for row in (extra_context or [])[-6:]],
                                "extra_tool_context_omitted": max(0, len(extra_context or []) - 6)},
                                ensure_ascii=False) if extra_context else ""
    for attempt in range(2):
        try:
            return _review_section(
                client=client, context=context, template=template, memory=memory,
                section=section, draft=draft,
                adopted_sections=adopted_sections,
                revision_review=revision_review, previous_draft=previous_draft,
                max_output_tokens=config.max_section_tokens if config.max_section_tokens > 0 else None,
                label=f"{label}-retry" if attempt else label,
                prompt_suffix=evidence_suffix + ((
                    "Return exactly one JSON object matching the reviewer schema, "
                    "with `revision_instructions` as a list of strings."
                ) if attempt else ""),
            )
        except (LLMError, ValueError) as exc:
            failure = exc
            if attempt or (isinstance(exc, LLMError) and not isinstance(exc, LLMResponseError)):
                break
            _emit(emit, f"Reviewer JSON validation failed for `{section.heading}`; retrying once. {exc}")
    if not config.allow_llm_fallback:
        raise LLMError(f"Reviewer failed for `{section.heading}` and LLM fallback is disabled: {failure}") from failure
    _emit(emit, f"Reviewer fallback used for `{section.heading}`. {failure}")
    return ReportSectionReview(
        section_id=section.section_id,
        verdict="warning",
        findings=[ReviewerFinding(
            finding_id=f"{section.section_id}-review-fallback",
            type="review_agent_fallback",
            severity="minor",
            message=f"Reviewer could not complete: {failure}. Section kept with fallback warning.",
            section_id=section.section_id,
            evidence_handles=section.evidence_handles[:5],
            suggested_action="Manually inspect this section before publishing.",
        )],
        revision_instructions=["Manually inspect this section before publishing."],
        notes="Reviewer fallback used; semantic review is incomplete.",
    )


def _fallback_section_draft(section: ReportSectionPlan) -> ReportSectionDraft:
    handles = [handle for handle in section.evidence_handles if handle][:5]
    citation_text = " ".join(f"[@{handle}]" for handle in handles[:3])
    body = (
        f"## {section.heading}\n\n"
        "This section could not be fully drafted by the report writer after a "
        "structured-output retry. The available evidence should still be "
        "reviewed before publication"
        + (f" ({citation_text})." if citation_text else ".")
        + "\n\n"
        f"Section goal: {section.goal.strip() or 'No section goal was recorded.'}"
    )
    return ReportSectionDraft(
        section_id=section.section_id,
        heading=section.heading,
        status="drafted",
        draft_markdown=body,
        used_sources=handles,
        citations=handles,
        open_questions=[
            "Report writer failed structured-output validation for this section; inspect evidence manually."
        ],
        limitations=[
            "This section is a conservative section-level fallback, not a polished model-written section."
        ],
    )


def _writer_prompt(
    *,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    section: ReportSectionPlan,
    config: ReportRuntimeConfig,
    extra_context: list[ReportToolResult],
    previous_draft: ReportSectionDraft | None,
    review: ReportSectionReview | None,
    source_batch_index: int,
    source_batch_count: int,
    include_previous_draft: bool,
    draft_mode: str,
    adopted_sections: list[ReportSectionDraft] | None = None,
) -> str:
    section_visuals = visual_requirements(memory.document_plan, section)
    payload = {
        "task": "draft_or_revise_one_report_section",
        "draft_mode": draft_mode,
        "report_mode": context.report_mode,
        "style": config.style,
        "max_section_tokens": config.max_section_tokens if config.max_section_tokens > 0 else "",
        "section": section.model_dump(mode="json"),
        "section_constraints": _section_constraints(section),
        "length_requirement": _section_length_requirement(section),
        "source_strategy": {
            "mode": config.source_strategy,
            "batch_index": source_batch_index,
            "batch_count": source_batch_count,
            "instruction": _source_strategy_instruction(
                config=config,
                batch_index=source_batch_index,
                batch_count=source_batch_count,
            ),
        },
        "template_markdown": template.template_markdown,
        "objective": memory.objective,
        "document_plan": _compact_document_plan(memory),
        "narrative_context": narrative_context(memory, section, adopted_sections or []),
        "visual_requirements": section_visuals,
        "global_research_context": {
            "evidence_summary": context.evidence_summary[:3000],
            "execution_context": _compact_execution_context(context.execution_context),
            "experiment_plan": _compact_experiment_plan(context.experiment_plan),
            "verified_execution_results": _compact_execution_results(context.results),
            "execution_evidence": report_execution_evidence(context),
            "synthesis": context.synthesis_markdown[:3000],
            "hypothesis": context.hypothesis_markdown[:1500],
        },
        "limitations": memory.limitations[:8],
        "source_handles": _handles_for_section(memory, section),
        "metric_sources": _prompt_metrics(
            memory, detail=_report_metric_detail(section.heading)
        ),
        "prior_claim_notes": _writer_prior_claim_notes(memory),
        "previous_draft": (
            previous_draft.model_dump(mode="json")
            if previous_draft is not None and include_previous_draft
            else {}
        ),
        "review_findings": [finding.model_dump(mode="json") for finding in (review.findings if review else [])],
        "review_instructions": review.revision_instructions if review else [],
        "revision_preservation_requirement": _revision_preservation_requirement(
            section=section,
            previous_draft=previous_draft if include_previous_draft else None,
            review=review,
        ),
        "extra_tool_context": [result.model_dump(mode="json") for result in extra_context[-6:]],
        "extra_tool_context_omitted": max(0, len(extra_context) - 6),
        "style_rules": [
            "Write reader-facing prose appropriate to the requested template and section purpose, not pipeline documentation. Material-based analysis need not be a long academic survey.",
            "Do not include pipeline/debug internals. In experiment/reproduction setup, preserve meaningful commands and declared limits needed to repeat the work, distinguishing them from verified method behavior. Survey prose need not include commands.",
            "Do not create sections named Search Scope, Evidence Summary, Pipeline, Artifacts, or Stage Outputs.",
            "Do not use prompt-planning phrases such as Hint:, Use this paper as, Paper Brief, or Additional synthesis detail.",
            "For multi-source surveys, synthesize rather than dump paper notes; group papers by supported comparison dimensions. Do not impose a taxonomy on analysis notes or a single-source review.",
            "For multi-source surveys, use the source set broadly but compress by grouping similar papers and citing representative evidence.",
            "For long survey templates, optimize for topic coverage and reader needs: explain foundations, construction patterns, applications, evaluation practice, related surveys, challenges, and future directions.",
            "Use the resolved document plan as the only local writing plan. Treat its section target and evidence set as planning guidance, not a license to pad prose.",
            "When section_constraints specify target_words, min_citations, or subsections, treat them as local writing constraints for this section.",
            "When length_requirement is present, cover the planned analytical scope and then stop; do not add generic background merely to hit a number.",
            "For reviewer-directed revision, preserve supported content needed for the section; removing redundancy, unsupported claims or irrelevant details may shorten it substantially. For source-batch integration, preserve valid prior coverage.",
            "If subsections are listed, use them as meaningful `###` subheadings unless the section is Abstract, Introduction, or Conclusion.",
            "Use meaningful subheadings inside large sections only when they improve navigation.",
            "Do not add Markdown image links unless a real generated image artifact exists; deterministic rendering handles planned figures separately.",
            "Draft front-matter as if it is written after the body: Abstract and Introduction should summarize the actual synthesis, not generic background.",
            "For each strong conclusion, add a boundary condition or uncertainty statement.",
            "When a prepared execution context is supplied, treat its project, dataset, benchmark, and runtime limits as authoritative; do not repeat a conflicting literature setting as if it were the executed experiment.",
            "Use verified_execution_results for local baseline/candidate metrics, comparison verdicts, deltas, and resource changes. Do not use literature citations to support local benchmark outcomes.",
            "If verified_execution_results shows that more than one implementation factor changed, describe that as a limitation or scope boundary rather than presenting a single-factor causal claim.",
            "Keep paragraphs under roughly 120 words; split dense synthesis into short paragraphs or concise bullets.",
            "Use only `cite_as` values such as [@P1] for body citations; never cite long source handles or raw paper ids.",
            "The final renderer will map short citation keys back to verified source ids and numeric citations.",
            "`draft_markdown` is the required primary payload. Put the complete Markdown prose there and never substitute `content`, `body`, or an explanation outside the JSON object.",
            "Return the outer section object, never a metadata record or an explanation.",
        ],
        "output_schema": _writer_response_contract(section),
    }
    if section_visuals["tables"]:
        payload["style_rules"].append(
            "For every required table in visual_requirements.tables, include one compact Markdown table in this section. "
            "Place `**Table: <planned title>**` immediately above it, preserve the planned comparison purpose, "
            "and use only evidence-supported cells with adjacent citations. Do not create placeholder rows."
        )
    return _json_prompt(payload)


def _writer_recovery_prompt(
    *,
    context: ReportContext,
    memory: ReportMemory,
    section: ReportSectionPlan,
    config: ReportRuntimeConfig,
    previous_draft: ReportSectionDraft | None,
    review: ReportSectionReview | None,
    draft_mode: str,
    extra_context: list[ReportToolResult] | None = None,
    adopted_sections: list[ReportSectionDraft] | None = None,
) -> str:
    """Build a small, schema-first retry after a Writer output mismatch.

    The ordinary Writer prompt deliberately carries rich planning context.  When
    a model returns a nested claim record or a truncated object, sending that
    same nested schema again is counterproductive.  This retry retains only
    the evidence needed to write the section and makes the outer response
    contract unambiguous.
    """
    section_visuals = visual_requirements(memory.document_plan, section)
    payload = {
        "task": "recover_one_report_section",
        "draft_mode": draft_mode,
        "instruction": (
            "The previous response was not a usable section draft. Return one outer JSON object "
            "whose required `draft_markdown` field contains the requested Markdown prose. "
            "Do not return a claim record, a nested object, an explanation, or Markdown fences."
        ),
        "topic": context.topic,
        "objective": memory.objective,
        "narrative_context": narrative_context(memory, section, adopted_sections or []),
        "execution_context": _compact_execution_context(context.execution_context),
        "experiment_plan": _compact_experiment_plan(context.experiment_plan),
        "verified_execution_results": _compact_execution_results(context.results),
        "execution_evidence": report_execution_evidence(context),
        "metric_sources": _prompt_metrics(
            memory, detail=_report_metric_detail(section.heading)
        ),
        "section": {
            "section_id": section.section_id,
            "heading": section.heading,
            "goal": section.goal,
        },
        "section_constraints": _section_constraints(section),
        "visual_requirements": section_visuals,
        "length_requirement": _section_length_requirement(section),
        "source_handles": _handles_for_section(memory, section),
        "extra_tool_context": [row.model_dump(mode="json") for row in (extra_context or [])[-6:]],
        "extra_tool_context_omitted": max(0, len(extra_context or []) - 6),
        "previous_draft": (
            {
                "draft_markdown": previous_draft.draft_markdown,
                "citations": previous_draft.citations,
            }
            if previous_draft is not None
            else {}
        ),
        "review_instructions": [
            *(review.revision_instructions if review else []),
            *(
                finding.suggested_action or finding.message
                for finding in (review.findings if review else [])
            ),
        ],
        "review_findings": [finding.model_dump(mode="json") for finding in (review.findings if review else [])],
        "revision_preservation_requirement": _revision_preservation_requirement(
            section=section, previous_draft=previous_draft, review=review),
        "style_rules": [
            "Write evidence-bounded prose appropriate to the requested section purpose using only supplied sources.",
            "When a prepared execution context is supplied, keep the executed project, dataset, benchmark, and runtime limits authoritative.",
            "Use only supplied short citation keys such as [@P1].",
            "Do not include a References section.",
            "`draft_markdown` is mandatory; optional metadata may be empty arrays.",
        ],
        "output_schema": _writer_response_contract(section),
    }
    if section_visuals["tables"]:
        payload["style_rules"].append(
            "Include every required visual_requirements table using a `**Table: <planned title>**` caption and a compact Markdown table."
        )
    return _json_prompt(payload)


def _writer_response_contract(section: ReportSectionPlan) -> dict[str, Any]:
    """Describe the Writer response without embedding a competing claim schema."""
    return {
        "required": {
            "section_id": section.section_id,
            "heading": section.heading,
            "status": "drafted|revised|skipped",
            "draft_markdown": "non-empty Markdown body for this section only; no References",
        },
        "optional": {
            "used_sources": "list of source handle ids, or []",
            "metric_ids": "list of metric ids, or []",
            "citations": "list of short citation keys such as P1, or []",
            "open_questions": "list of strings, or []",
            "limitations": "list of strings, or []",
        },
    }


def _writer_prior_claim_notes(memory: ReportMemory) -> list[str]:
    """Provide prior claim context as prose, not as another nested output shape."""
    notes: list[str] = []
    for claim in memory.claims_evidence_matrix[:12]:
        citations = ", ".join(claim.citation_ids[:4]) or "no citation key recorded"
        notes.append(f"{claim.status}: {claim.claim} (citations: {citations})")
    return notes


def _reviewer_prompt(
    *,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    section: ReportSectionPlan,
    draft: ReportSectionDraft,
    adopted_sections: list[ReportSectionDraft] | None = None,
    revision_review: ReportSectionReview | None = None,
    previous_draft: ReportSectionDraft | None = None,
) -> str:
    section_visuals = visual_requirements(memory.document_plan, section)
    payload = {
        "task": "review_one_report_section",
        "report_mode": context.report_mode,
        "section": section.model_dump(mode="json"),
        "section_constraints": _section_constraints(section),
        "criteria_markdown": template.criteria_markdown,
        "objective": memory.objective,
        "document_plan": _compact_document_plan(memory),
        "narrative_context": narrative_context(memory, section, adopted_sections or []),
        "revision_context": revision_context(revision_review, previous_draft),
        "visual_requirements": section_visuals,
        "known_limitations": memory.limitations[:8],
        "experiment_plan": _compact_experiment_plan(context.experiment_plan),
        "allowed_sources": _handles_for_section(memory, section),
        "metric_sources": _prompt_metrics(
            memory, detail=_report_metric_detail(section.heading)
        ),
        "verified_execution_results": _compact_execution_results(context.results),
        "execution_evidence": report_execution_evidence(context),
        "draft": draft.model_dump(mode="json"),
        "tool_policy": {
            "allowed_tools": [
                "get_paper_brief",
                "get_neighbor_chunks",
                "search_source_chunks",
                "get_metric_source",
                "get_synthesis_brief",
                "get_code_task_result",
            ],
            "only_request_tools_when_evidence_is_insufficient": True,
            "search_source_chunks_arguments": {"handle": "one allowed source handle", "query": "specific claim or condition"},
            "prefer_get_paper_brief_arguments": {"citation_key": "P1"},
        },
        "review_focus": [
            *CLAIM_SCOPE_RULES,
            *REVIEW_ACTION_RULES,
            "Does this section fulfill the requested template and its own purpose, rather than reading like a pipeline log? Analysis notes need not be a long academic survey.",
            "Are citations adjacent to paper-specific claims?",
            "If section_constraints include target_words, min_citations, or subsections, did the draft reasonably satisfy them without padding or unsupported citations?",
            "If planned subsections are present for a body section, does the draft use clear internal `###` headings or equivalent navigational structure? Do not require this for Abstract, Introduction, or Conclusion.",
            "Are long paragraphs split into readable units?",
            "Are operational details moved out of the body unless they are reader-facing limitations?",
            "For multi-source surveys, does the section synthesize instead of listing paper briefs?",
            "For multi-source surveys, are taxonomy/comparison dimensions supported by the sources? Do not require them for supplied-material analysis or a single-source review.",
            "For multi-source surveys, does it use the source set broadly without becoming a paper-by-paper dump?",
            "For long survey templates, does the section improve topic coverage for reader needs rather than merely restating a compact technical brief?",
            "If the long-form synthesis contract is enabled, does the section cover the relevant outline_sections, required facets, citation policy, and reader needs without drifting into an experiment report or pipeline log?",
            "For long survey templates, are construction, applications, evaluation, related surveys, challenges, and future directions covered across the report plan?",
            "When visual_requirements.tables is non-empty, does this section realize every required table with its planned caption, meaningful columns, and evidence-supported cells? Request revision for a missing or placeholder table.",
            "Does Evaluation include an evidence-quality map or equivalent compact comparison when useful?",
            "Are benchmark limitations and transfer boundaries stated near empirical claims?",
            "Use verified_execution_results as the authoritative local benchmark record. Do not request a missing comparison table or metric merely because the deterministic execution evidence is appended after this review.",
            "If verified_execution_results shows multiple implementation changes or resource changes, ensure the draft states that limitation; do not treat the presence of those changes alone as an unsupported causal claim.",
            "Do not require literature citations for local baseline/candidate metrics; those values are supported by verified execution results and metric sources.",
            "Is it free of prompt residue such as Hint, Use this paper as, Paper Brief, or Additional synthesis detail?",
        ],
        "output_schema": {
            "section_id": section.section_id,
            "verdict": "pass|warning|revise_required|fail",
            "findings": [
                {
                    "finding_id": "stable id",
                    "type": "unsupported_claim|citation_misuse|missing_limitation|missing_visual|metric_mismatch|style",
                    "severity": "info|minor|major|critical",
                    "required_action": "advisory|revise|verify",
                    "message": "specific issue",
                    "section_id": section.section_id,
                    "claim_id": "",
                    "evidence_handles": [],
                    "suggested_action": "",
                }
            ],
            "context_requests": [
                {
                    "tool_name": "get_paper_brief",
                    "arguments": {"handle": "paper:..."},
                    "caller": "reviewer",
                    "trace_id": "optional",
                }
            ],
            "revision_instructions": [],
            "notes": "",
        },
    }
    return _json_prompt(payload)


def _json_prompt(payload: dict[str, Any]) -> str:
    return (
        "Return exactly one JSON object. Do not wrap it in Markdown fences.\n\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def _compact_execution_results(results: Mapping[str, Any] | object) -> dict[str, Any]:
    """Expose bounded, authoritative experiment evidence to report agents.

    The deterministic report assembly already appends execution evidence after
    the agent pass.  Giving the Writer and Reviewer the same compact result
    projection prevents them from treating a real comparison as missing while
    keeping raw stdout, paths, and unrelated run metadata out of the prompt.
    """
    if not isinstance(results, Mapping):
        return {}

    def _mapping(value: object) -> Mapping[str, Any] | None:
        return value if isinstance(value, Mapping) else None

    def _metrics(value: object) -> dict[str, Any]:
        mapping = _mapping(value)
        if mapping is None:
            return {}
        blocked = {"stdout", "stderr", "command", "logs", "trace", "raw_output"}
        return {
            str(key): item
            for key, item in list(mapping.items())[:32]
            if str(key).lower() not in blocked
            and (item is None or isinstance(item, (bool, int, float, str)))
            and (not isinstance(item, str) or len(item) <= 200)
        }

    compact: dict[str, Any] = {}
    analyses = results.get("supplied_analyses")
    if isinstance(analyses, list):
        compact["supplied_analyses"] = [{"document_id": row["document_id"], "evidence_role": row["evidence_role"],
            "spec": row["spec"], "records": row["records"][:12], "records_truncated": len(row["records"]) > 12}
            for row in analyses[:6]]
        compact["supplied_analyses_truncated"] = len(analyses) > 6
    implementation = _mapping(results.get("implementation"))
    if implementation is not None:
        compact["implementation"] = {
            key: implementation[key] for key in (
                "artifact", "status", "asset_integrity", "method_validation", "interpretation",
            )
            if key in implementation
        }
        integrity = _mapping(implementation.get("asset_integrity"))
        if integrity is not None:
            compact["implementation"]["asset_integrity"] = {
                key: integrity[key]
                for key in ("status", "changed_assets", "errors", "content_fingerprint")
                if key in integrity
            }
        evidence = _mapping(implementation.get("evidence"))
        if evidence is not None and "patch" in evidence:
            # Patches are already bounded at the artifact boundary. Keep the
            # cumulative lineage when a repair attempt stores only a delta;
            # Keep bounded method-validation facts separately; omit bulky
            # validation/review logs from the report prompt.
            compact_evidence: dict[str, Any] = {"patch": evidence["patch"]}
            patches = evidence.get("patches")
            if isinstance(patches, list):
                compact_evidence["patches"] = [
                    item for item in patches[:6] if isinstance(item, Mapping)
                ]
            compact["implementation"]["evidence"] = compact_evidence
    for key in ("status", "primary_metric"):
        if key in results:
            compact[key] = results[key]
    if "metrics" in results:
        compact["metrics"] = _metrics(results.get("metrics"))
    diagnosis = _mapping(results.get("failure_diagnosis"))
    if diagnosis is not None:
        compact["failure_diagnosis"] = {
            key: diagnosis[key]
            for key in ("status", "summary", "deficiencies", "stderr_tail")
            if key in diagnosis
        }

    for label in ("baseline", "patched", "candidate"):
        run = _mapping(results.get(label))
        if run is None:
            continue
        compact[label] = {
            key: run[key]
            for key in ("status", "returncode", "timed_out")
            if key in run
        }
        compact[label]["metrics"] = _metrics(run.get("metrics"))

    history = results.get("measurement_history")
    if isinstance(history, list):
        rows = [item for item in history if isinstance(item, Mapping)]
        selected = rows if len(rows) <= 24 else [*rows[:8], *rows[-16:]]
        compact["measurement_history_total"] = len(rows)
        compact["measurement_history_omitted"] = len(rows) - len(selected)
        compact["passed_candidate_measurements"] = sum(
            1 for item in rows
            if str(item.get("status") or "").lower() == "passed"
            and not str(item.get("action") or "").startswith((
                "baseline", "matrix_baseline", "supplement_baseline",
            ))
        )
        compact["measurement_history"] = [
            {
                "action": str(item.get("action") or ""),
                "status": str(item.get("status") or "unknown"),
                "metrics": _metrics(item.get("metrics")),
                "artifact": str(item.get("artifact") or ""),
                "implementation_artifact": str(
                    item.get("implementation_ref", {}).get("path") or ""
                ) if isinstance(item.get("implementation_ref"), Mapping) else "",
            }
            for item in selected
        ]

    comparisons = results.get("comparisons")
    if isinstance(comparisons, list):
        compact["comparisons"] = []
        for item in comparisons[:4]:
            comparison = _mapping(item)
            if comparison is None:
                continue
            row: dict[str, Any] = {
                key: comparison[key]
                for key in ("status", "verdict", "name", "condition", "reasons")
                if key in comparison
            }
            if "metrics" in comparison:
                metric_rows = comparison.get("metrics")
                if isinstance(metric_rows, list):
                    row["metrics"] = [
                        {
                            key: metric[key]
                            for key in ("name", "baseline", "patched", "candidate", "delta", "direction", "status")
                            if key in metric
                        }
                        for metric in metric_rows
                        if isinstance(metric, Mapping)
                        and "_after_task_" not in str(metric.get("name") or "")
                    ]
                else:
                    row["metrics"] = _metrics(metric_rows)
            compact["comparisons"].append(row)
    return compact


def _compact_experiment_plan(plan: Mapping[str, Any] | object) -> dict[str, Any]:
    """Keep report prompts focused while preserving the executed protocol.

    The persisted report snapshot keeps the complete experiment contract.  A
    Writer or Reviewer only needs the user-facing plan plus one compact copy of
    each distinct paired protocol; the same protocol was previously repeated
    once per seed and could dominate a long provider request.
    """
    if not isinstance(plan, Mapping):
        return {}

    compact = {
        str(key): value
        for key, value in plan.items()
        if key != "paired_protocols"
    }
    paired = plan.get("paired_protocols")
    if not isinstance(paired, list):
        return compact

    runs: list[dict[str, Any]] = []
    protocols: list[dict[str, Any]] = []
    seen_protocols: set[str] = set()
    protocol_keys = (
        "contract_id",
        "schema_version",
        "protocol_revision",
        "dataset_refs",
        "split_spec",
        "metric_specs",
        "comparison_conditions",
        "protected_assets",
    )
    for item in paired:
        if not isinstance(item, Mapping):
            continue
        run = {
            key: item[key]
            for key in ("seed", "condition", "artifact")
            if key in item
        }
        if run:
            runs.append(run)
        protocol = item.get("protocol")
        if not isinstance(protocol, Mapping):
            continue
        projection = {
            key: protocol[key]
            for key in protocol_keys
            if key in protocol and protocol[key] not in (None, "", [], {})
        }
        identity = json.dumps(projection, sort_keys=True, ensure_ascii=False)
        if projection and identity not in seen_protocols:
            seen_protocols.add(identity)
            protocols.append(projection)

    if runs:
        compact["paired_runs"] = runs
    if protocols:
        compact["paired_protocols"] = protocols
    return compact


def _compact_execution_context(value: object) -> str:
    """Legacy narrative view; structured declared/observed evidence is separate."""
    if not isinstance(value, str):
        return ""
    narrative = value.split("## Prepared execution specification", 1)[0].strip()
    return narrative[:5000]


def _compact_survey_contract(contract: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(contract, dict) or not contract.get("enabled"):
        return {}
    expected = contract.get("expected_coverage") if isinstance(contract.get("expected_coverage"), dict) else {}
    selection = contract.get("paper_selection") if isinstance(contract.get("paper_selection"), dict) else {}
    selected_papers = selection.get("selected_papers") if isinstance(selection.get("selected_papers"), list) else []
    taxonomy = contract.get("taxonomy") if isinstance(contract.get("taxonomy"), dict) else {}
    outline = contract.get("outline_plan") if isinstance(contract.get("outline_plan"), dict) else {}
    return {
        "schema_version": contract.get("schema_version", "survey_contract.v1"),
        "topic": contract.get("topic", ""),
        "objective": contract.get("objective", ""),
        "reader_needs": _string_items(contract.get("reader_needs"))[:6],
        "required_facets": _string_items(contract.get("required_facets"))[:10],
        "expected_coverage": expected,
        "selected_paper_brief": [
            {
                "cite_as": item.get("citation_key") or item.get("paper_id") or "",
                "title": str(item.get("title") or "")[:160],
                "role": item.get("role") or "",
                "facets": _string_items(item.get("facets"))[:4],
            }
            for item in selected_papers[:20]
            if isinstance(item, dict)
        ],
        "taxonomy_facets": [
            {
                "label": facet.get("label") or "",
                "paper_keys": _string_items(facet.get("paper_keys"))[:8],
                "evidence_count": facet.get("evidence_count") or 0,
            }
            for facet in (taxonomy.get("facets") if isinstance(taxonomy.get("facets"), list) else [])[:10]
            if isinstance(facet, dict)
        ],
        "coverage_requirements": [
            {
                "label": facet.get("label") or "",
                "evidence_count": facet.get("evidence_count") or 0,
            }
            for facet in (taxonomy.get("coverage_facets") if isinstance(taxonomy.get("coverage_facets"), list) else [])[:10]
            if isinstance(facet, dict)
        ],
        "outline_sections": [
            {
                "heading": section.get("heading") or "",
                "goal": section.get("goal") or "",
                "target_words": section.get("target_words") or "",
                "min_citations": section.get("min_citations") or "",
                "subsections": _string_items(section.get("subsections"))[:6],
                "citation_keys": _string_items(section.get("citation_keys"))[:10],
            }
            for section in (outline.get("sections") if isinstance(outline.get("sections"), list) else [])[:12]
            if isinstance(section, dict)
        ],
        "citation_policy": contract.get("citation_policy") if isinstance(contract.get("citation_policy"), dict) else {},
        "boundaries": _string_items(contract.get("boundaries"))[:6],
    }


def _compact_document_plan(memory: ReportMemory) -> dict[str, Any]:
    """Expose the frozen plan without reintroducing parallel planning state."""
    plan = memory.document_plan
    if plan is None:
        return {}
    return {
        "schema_version": plan.schema_version,
        "status": plan.status,
        "target_words": plan.target_words,
        "sections": [
            {
                "section_id": section.section_id,
                "heading": section.heading,
                "goal": section.goal,
                "target_words": section.target_words,
                "min_citations": section.min_citations,
                "subsections": section.subsections[:6],
            }
            for section in plan.sections
        ],
        "visual_budget": plan.visual_budget,
        "visual_intents": [
            {
                "kind": intent.kind,
                "title": intent.title,
                "purpose": intent.purpose,
                "section_id": intent.section_id,
                "view": intent.view,
                "columns": intent.columns,
            }
            for intent in plan.visual_intents
        ],
    }


def _string_items(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        for key in (
            "text",
            "question",
            "limitation",
            "description",
            "message",
            "content",
            "body",
            "label",
            "title",
            "name",
            "notes",
        ):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return [candidate.strip()]
        return []
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            items.extend(_string_items(item))
        return items
    return []


def _normalize_draft_response(response: dict[str, Any], section: ReportSectionPlan) -> dict[str, Any]:
    normalized = dict(response)
    nested = normalized.get("draft")
    if isinstance(nested, dict):
        for key, value in nested.items():
            normalized.setdefault(key, value)
    nested_section = normalized.get("section")
    if isinstance(nested_section, dict):
        for key, value in nested_section.items():
            normalized.setdefault(key, value)
    if not str(normalized.get("draft_markdown") or "").strip():
        for key in ("markdown", "content", "body", "text", "section_markdown"):
            value = normalized.get(key)
            if isinstance(value, str) and value.strip():
                normalized["draft_markdown"] = value
                break
    normalized.setdefault("section_id", section.section_id)
    normalized.setdefault("heading", section.heading)
    normalized.setdefault("status", "drafted")
    normalized.setdefault("draft_markdown", "")
    normalized.setdefault("used_sources", [])
    normalized.setdefault("metric_ids", [])
    normalized.setdefault("citations", [])
    normalized.setdefault("claims", [])
    normalized.setdefault("open_questions", [])
    normalized.setdefault("limitations", [])
    normalized["open_questions"] = _string_items(normalized["open_questions"])
    normalized["limitations"] = _string_items(normalized["limitations"])
    # Some models copy a claim-level evidence label such as ``supported`` into
    # the section-level status field.  The two enums intentionally differ:
    # preserve the draft and its claim statuses while normalizing the section
    # lifecycle state to the writer contract.
    if str(normalized.get("status") or "").strip().lower() not in {"drafted", "revised", "skipped"}:
        normalized["status"] = "drafted"
    return normalized


def _is_claim_record_response(response: dict[str, Any]) -> bool:
    """Identify a valid inner claim object mistakenly returned as a section."""
    if any(key in response for key in ("draft_markdown", "markdown", "content", "body", "text")):
        return False
    return bool(
        isinstance(response.get("claim_id"), str)
        and isinstance(response.get("claim"), str)
        and any(
            key in response
            for key in ("evidence_handles", "citation_ids", "metric_ids")
        )
    )


def _normalize_review_response(response: dict[str, Any], section: ReportSectionPlan) -> dict[str, Any]:
    normalized = dict(response)
    normalized.setdefault("section_id", section.section_id)
    normalized.setdefault("verdict", "warning")
    normalized.setdefault("findings", [])
    normalized.setdefault("context_requests", [])
    normalized.setdefault("revision_instructions", [])
    normalized.setdefault("notes", "")
    normalized["findings"] = [
        _normalize_finding(finding, section, index)
        for index, finding in enumerate(normalized.get("findings") or [], start=1)
        if isinstance(finding, dict)
    ]
    normalized["context_requests"] = [
        _normalize_tool_call(call, index)
        for index, call in enumerate(normalized.get("context_requests") or [], start=1)
        if isinstance(call, dict)
    ]
    return normalized


def _normalize_finding(
    finding: dict[str, Any],
    section: ReportSectionPlan,
    index: int,
) -> dict[str, Any]:
    item = dict(finding)
    item.setdefault("finding_id", f"{section.section_id}-finding-{index:03d}")
    item.setdefault("type", "review")
    item.setdefault("severity", "minor")
    item.setdefault("message", item.get("suggested_action") or "Reviewer finding.")
    item.setdefault("section_id", section.section_id)
    item.setdefault("claim_id", "")
    item.setdefault("evidence_handles", [])
    item.setdefault("suggested_action", "")
    return item


def _normalize_tool_call(call: dict[str, Any], index: int) -> dict[str, Any]:
    item = dict(call)
    item.setdefault("tool_name", "")
    item.setdefault("arguments", {})
    item.setdefault("caller", "reviewer")
    item.setdefault("trace_id", f"review-tool-{index:03d}")
    return item


def _mark_pending_evidence(review: ReportSectionReview) -> ReportSectionReview:
    """Pending evidence is neither a verified pass nor proof of a false claim."""
    if not review.context_requests:
        return review
    finding = ReviewerFinding(finding_id=f"{review.section_id}-source-verification-incomplete",
        type="source_verification_incomplete", severity="major", section_id=review.section_id,
        message="Reviewer still requires source evidence after the bounded lookup, or source backtracking is unavailable. No complete source verification was performed.",
        suggested_action="Qualify/remove the unresolved claim or provide the missing source material.")
    return review.model_copy(update={"verdict": "revise_required", "findings": [*review.findings, finding]})


def _run_context_requests(
    gateway: ReportToolGateway,
    review: ReportSectionReview,
    config: ReportRuntimeConfig,
) -> list[ReportToolResult]:
    if not config.allow_source_backtracking:
        return []
    results: list[ReportToolResult] = []
    for call in review.context_requests[: config.max_backtracking_calls]:
        if not call.tool_name:
            continue
        results.append(gateway.call(call))
    return results


def _handles_for_section(memory: ReportMemory, section: ReportSectionPlan) -> list[dict[str, Any]]:
    handles = {handle.handle: handle for handle in memory.source_handles}
    selected: list[str] = []
    for handle in section.evidence_handles:
        if handle in handles:
            selected.append(handle)
    if not selected:
        selected = list(handles)[:8]
    return [_prompt_handle_view(handles[handle]) for handle in selected]


def _source_batches(evidence_handles: list[str], config: ReportRuntimeConfig) -> list[list[str]]:
    handles = [handle for handle in evidence_handles if handle]
    if not handles:
        return [[]]
    if config.source_strategy != "batch_refine":
        return [handles]
    batch_size = max(1, config.source_batch_size)
    batches = [handles[index : index + batch_size] for index in range(0, len(handles), batch_size)]
    if config.max_source_batches > 0:
        return batches[: config.max_source_batches]
    return batches


def _section_with_evidence(section: ReportSectionPlan, evidence_handles: list[str]) -> ReportSectionPlan:
    return section.model_copy(update={"evidence_handles": list(evidence_handles)})


def _merge_revision_draft(
    previous: ReportSectionDraft,
    revised: ReportSectionDraft,
) -> ReportSectionDraft:
    """Use the complete revised section; old metadata is not evidence for its prose."""
    if revised.section_id != previous.section_id:
        raise ValueError("Report revision must preserve the section identity.")
    return revised


def _source_strategy_instruction(
    *,
    config: ReportRuntimeConfig,
    batch_index: int,
    batch_count: int,
) -> str:
    if config.source_strategy != "batch_refine" or batch_count <= 1:
        return "Draft using the provided source handles as the section evidence set."
    if batch_index <= 1:
        return (
            "Draft an initial section from the first source batch. Leave the "
            "structure easy to revise when later source batches arrive."
        )
    return (
        "Revise the complete previous section to integrate the newly supplied "
        "source batch. Preserve valid prior coverage, update comparisons and "
        "evidence-quality judgments compactly, and return one coherent section."
    )


def _section_constraints(
    section: ReportSectionPlan,
    *,
    include_subsections: bool = True,
) -> dict[str, Any]:
    constraints: dict[str, Any] = {
        "target_words": section.target_words if section.target_words > 0 else "",
        "min_citations": section.min_citations if section.min_citations > 0 else "",
        "subsections": section.subsections[:6] if include_subsections else [],
    }
    guidance: list[str] = []
    if section.target_words > 0:
        guidance.append(
            "Aim for this approximate section length, but prefer evidence-bounded prose over padding."
        )
    if section.min_citations > 0:
        guidance.append(
            "Use at least this many distinct allowed citations when the evidence set supports it."
        )
    if section.subsections and include_subsections:
        guidance.append(
            "Use the subsection list as navigational `###` headings or close equivalents."
        )
    constraints["guidance"] = guidance
    return constraints


def _section_length_requirement(section: ReportSectionPlan) -> str:
    target_words = max(0, int(section.target_words or 0))
    if target_words <= 0:
        return ""
    return (
        f"Draft approximately {target_words} substantive words for this section. "
        "Treat this as a document budget rather than a minimum to exceed: cover the requested "
        "evidence and then stop instead of expanding into paper-by-paper notes. Use multiple "
        "focused paragraphs and subsections when useful."
    )


def _revision_preservation_requirement(
    *,
    section: ReportSectionPlan,
    previous_draft: ReportSectionDraft | None,
    review: ReportSectionReview | None,
) -> str:
    if previous_draft is None or review is None:
        return ""
    if not previous_draft.draft_markdown.strip():
        return ""
    return (
        "This is a reviewer-directed revision, not a fresh summary. Return the complete "
        "revised section, preserving supported analysis, necessary qualifications and valid citations "
        "while addressing each finding and explicit instruction. Remove unsupported or duplicated "
        "content when needed; do not retain or add prose merely to preserve the previous word count."
    )


def _draft_sequence(sections: list[ReportSectionPlan]) -> list[ReportSectionPlan]:
    return sorted(
        sections,
        key=lambda section: (
            section.draft_order if section.draft_order else section.final_order,
            section.final_order,
            section.section_id,
        ),
    )


def _final_sequence(
    plans: list[ReportSectionPlan],
    drafts: list[ReportSectionDraft],
) -> list[ReportSectionDraft]:
    order = {plan.section_id: plan.final_order or index for index, plan in enumerate(plans, start=1)}
    return sorted(drafts, key=lambda draft: (order.get(draft.section_id, 9999), draft.section_id))


def _prompt_metrics(memory: ReportMemory, *, detail: str = "full") -> dict[str, Any]:
    """Build a compact model-facing table while retaining raw evidence elsewhere.

    Paired experiments also keep task-by-task measurements in the session for
    audit and export.  Sending those rows to every section writer duplicates a
    large amount of context without helping ordinary paper prose; aggregate
    and non-task-level seed rows are sufficient for the Writer.
    """
    columns = ["metric_id", "name", "value", "label", "direction", "condition_id", "unit", "source_kind"]
    metrics = list(memory.metric_sources)
    paired_summary = [
        metric for metric in metrics
        if metric.label.startswith("paired_summary:")
        and "_after_task_" not in metric.name
    ]
    if paired_summary:
        seed_metrics = [
            metric for metric in metrics
            if not metric.label.startswith("paired_summary:")
            and "_after_task_" not in metric.name
        ]
        metrics = (
            paired_summary
            if detail == "summary"
            else [*paired_summary, *seed_metrics]
        )
    return {"columns": columns, "rows": [
        [getattr(metric, column) for column in columns] for metric in metrics
    ]}


def _report_metric_detail(heading: str) -> str:
    """Send per-seed rows only to sections that interpret measured results."""
    return (
        "full" if heading.strip().lower() in {"results", "result"} else "summary"
    )


def _prompt_handle_view(handle: Any) -> dict[str, Any]:
    """Return a compact model-facing handle with short citation guidance.

    The raw handle is retained for source provenance and chunk backtracking, but
    prose citations should use ``cite_as``. This keeps long provider ids out of
    normal body citation generation.
    """
    data = handle.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    if "title" in data:
        data["title"] = str(data["title"])[:240]
    if "summary" in data:
        data["summary"] = str(data["summary"])[:800]
    metadata = _compact_source_metadata(data.get("metadata", {}))
    data["metadata"] = {key: value[:160] for key, value in metadata.items()}
    # Writer and Reviewer must see the same evidence, not just an abstract.
    # Keep source excerpts distinct from model-derived reading notes.
    source_metadata = handle.metadata
    title_source = source_metadata.get("title_source")
    if isinstance(title_source, dict):
        data["metadata"]["title_source"] = {
            key: str(title_source.get(key) or "")[:240] for key in ("section_id", "quote", "scope")
        }
    bibliography = source_metadata.get("bibliography")
    if isinstance(bibliography, dict):
        # Bibliography is recorded metadata, not reading notes or source support.
        authors = bibliography.get("authors", [])
        data["metadata"]["bibliography"] = {
            **{key: str(bibliography.get(key) or "")[:480]
               for key in ("title", "published", "year", "doi", "url", "source", "verification_status")},
            "authors": [str(name)[:160] for name in authors[:6]],
            "recorded_authors_count": len(authors),
            "author_names_omitted_from_prompt": max(0, len(authors) - 6),
            "author_visibility": "Shown names are a bounded prompt view of the recorded list; omission here is not a provider completeness finding.",
            "missing_fields": list(bibliography.get("missing_fields", [])),
            "notes": [str(note)[:240] for note in bibliography.get("notes", [])[:4]],
            "notes_omitted": max(0, len(bibliography.get("notes", [])) - 4),
        }
    for key in ("document_id", "extraction_status", "reading_artifact", "reading_state", "reading_notes_kind", "evidence_role"):
        if source_metadata.get(key):
            data["metadata"][key] = str(source_metadata[key])[:240]
    notes = source_metadata.get("reading_notes")
    if isinstance(notes, dict):
        projected_notes = {}
        notes_truncated = False
        for key in ("problem", "method", "datasets", "metrics", "key_claims", "limitations", "open_questions", "confidence", "evidence_refs"):
            value = notes.get(key)
            if isinstance(value, list):
                projected_notes[key] = [str(item)[:400] for item in value[:6]]
                notes_truncated |= len(value) > 6 or any(len(str(item)) > 400 for item in value[:6])
            elif isinstance(value, str):
                projected_notes[key] = value[:600]
                notes_truncated |= len(value) > 600
        scopes = notes.get("claim_scopes", [])
        if isinstance(scopes, list):
            projected_notes["claim_scopes"] = []
            for row in scopes[:4]:
                if not isinstance(row, dict):
                    continue
                claim = {key: str(row.get(key) or "unknown")[:600]
                         for key in ("claim_id", "claim", "object", "property", "evidence_kind", "scope")}
                for key in ("conditions", "evidence_refs"):
                    values = row.get(key, [])
                    if not isinstance(values, list):
                        claim[key] = []
                        notes_truncated = True
                        continue
                    claim[key] = [str(value)[:240] for value in values[:6]]
                    notes_truncated |= len(values) > 6 or any(len(str(value)) > 240 for value in values[:6])
                notes_truncated |= any(len(str(row.get(key) or "")) > 600 for key in claim if key not in {"conditions", "evidence_refs"})
                projected_notes["claim_scopes"].append(claim)
            projected_notes["claim_scopes_omitted"] = max(0, len(scopes) - 4)
            notes_truncated |= len(scopes) > 4
        coverage = notes.get("reading_coverage")
        if isinstance(coverage, dict):
            bounded_coverage = {key: value if type(value) is int else str(value)[:160]
                                for key in ("available_chunks", "selection", "excerpt_chars", "semantic_verification")
                                if (value := coverage.get(key)) is not None}
            for key in ("shown_chunk_ids", "shortened_chunk_ids", "followup_shown_chunk_ids"):
                ids = coverage.get(key)
                if isinstance(ids, list):
                    bounded_coverage[key] = [str(item)[:240] for item in ids[:12]]
                    notes_truncated |= len(ids) > 12 or any(len(str(item)) > 240 for item in ids[:12])
            projected_notes["reading_coverage"] = bounded_coverage
        followup = notes.get("reading_followup")
        if isinstance(followup, dict):
            projected_notes["reading_followup"] = {
                "revision_performed": bool(followup.get("revision_performed")),
                "scope": str(followup.get("scope", ""))[:160],
                "pending_queries": [str(query)[:500] for query in followup.get("pending_queries", [])[:2]],
                "lookups": [
                    {key: row[key] for key in ("query", "status", "matched_chunk_ids", "new_window_count", "omitted_window_count", "semantic_verification") if key in row}
                    for row in followup.get("lookups", [])[:2] if isinstance(row, dict)
                ],
            }
        data["metadata"]["reading_notes"] = projected_notes
        data["metadata"]["reading_notes_truncated"] = notes_truncated
    passages = source_metadata.get("evidence_passages")
    if isinstance(passages, list):
        data["metadata"]["evidence_passages"] = [
            {"chunk_id": str(row.get("chunk_id", "")), "text": str(row.get("text", ""))[:1400],
             "character_start": row.get("character_start"), "character_end": row.get("character_end"),
             "truncated": bool(row.get("truncated")) or len(str(row.get("text", ""))) > 1400}
            for row in passages[:6] if isinstance(row, dict)
        ]
        data["metadata"]["evidence_passages_truncated"] = bool(source_metadata.get("evidence_passages_truncated")) or len(passages) > 6
    if "section" in data:
        data["section"] = str(data["section"])[:240]
    citation_key = data.get("citation_key") or ""
    if citation_key:
        data["cite_as"] = f"[@{citation_key}]"
        data["paper_id_for_display"] = citation_key
        data.pop("paper_id", None)
        data["tool_args"] = {"citation_key": citation_key}
    return data


def _needs_revision(review: ReportSectionReview) -> bool:
    if review.verdict in {"revise_required", "fail"}:
        return True
    return any(finding_requires_resolution(finding) for finding in review.findings)


def _needs_evidence_recheck(review: ReportSectionReview) -> bool:
    """Check a verification-only request before spending a writing correction.

    Mixed corrections already fetch context and revise, avoiding a redundant
    model call. Missing/blocked evidence cannot turn the provisional check into
    acceptance: the subsequent review still passes through pending-evidence guards.
    """
    return bool(review.context_requests) and (
        not _needs_revision(review)
        or (review.verdict not in {"revise_required", "fail"} and all(
            not finding_requires_resolution(finding) or finding.required_action == "verify"
            for finding in review.findings
        ))
    )


def _merge_draft_into_memory(
    memory: ReportMemory,
    draft: ReportSectionDraft,
    findings: list[ReviewerFinding],
) -> None:
    # Claims are projected from adopted sections at the checkpoint boundary.
    # This helper only accumulates diagnostic history, not another prose owner.
    memory.reviewer_findings = _dedupe_findings(memory.reviewer_findings + findings)
    for item in draft.open_questions:
        if item and item not in memory.open_questions:
            memory.open_questions.append(item)
    for item in draft.limitations:
        if item and item not in memory.limitations:
            memory.limitations.append(item)
    decision = f"Section `{draft.section_id}` drafted with {len(draft.used_sources)} source handle(s)."
    if decision not in memory.key_decisions:
        memory.key_decisions.append(decision)


def _dedupe_findings(findings: list[ReviewerFinding]) -> list[ReviewerFinding]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[ReviewerFinding] = []
    for finding in findings:
        key = (finding.type, finding.section_id, finding.message)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped


def _iteration(
    iteration: int,
    section: ReportSectionPlan,
    action: str,
    status: str,
    used_sources: list[str],
    *,
    findings: list[ReviewerFinding] | None = None,
    tool_results: list[ReportToolResult] | None = None,
    draft: ReportSectionDraft | None = None,
    revision_instructions: list[str] | None = None,
) -> ReportIterationRecord:
    return ReportIterationRecord(
        iteration=iteration,
        section_id=section.section_id,
        action=action,
        status=status,
        summary=f"{action} `{section.heading}` -> {status}",
        used_sources=used_sources[:8],
        findings=findings or [],
        tool_results=tool_results or [],
        draft=draft.model_copy(deep=True) if draft is not None else None,
        revision_instructions=revision_instructions or [],
    )


def _emit(callback: Callable[[str], None] | None, message: str) -> None:
    if callback is not None:
        callback(message)

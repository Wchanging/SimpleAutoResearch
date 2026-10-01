"""Bounded writing context projected from adopted drafts, not a second memory.

The controller already saves complete sections in its checkpoint. Rebuild this
view from those sections on every call, including recovery; a rejected candidate
or stale claim record must not become evidence for the next section.
"""

from collections.abc import Sequence

from simple_ar.report.schema import (
    ClaimEvidenceRecord, ReportIterationRecord, ReportMemory, ReportSectionDraft,
    ReportSectionPlan, ReportSectionReview,
)


def adopted_claims(
    initial: Sequence[ClaimEvidenceRecord], sections: Sequence[ReportSectionDraft],
) -> list[ClaimEvidenceRecord]:
    """Keep input guards and the current drafts' declarations, never old revisions.

    Claim ids supplied by different sections need not be globally unique. Keep
    both records instead of silently replacing one section's evidence with another.
    These are model declarations, not independently verified support.
    """
    return [*initial, *(claim for section in sections for claim in section.claims)]


def narrative_context(
    memory: ReportMemory, section: ReportSectionPlan,
    adopted: Sequence[ReportSectionDraft],
) -> dict:
    """Expose section responsibilities and actual preceding prose with coverage.

    No semantic summary is invented when the Writer omits optional claim metadata.
    Head/tail excerpts keep both a section's introduction and qualification visible;
    explicit omitted counts prevent treating this view as a full-document review.
    """
    plans = memory.document_plan.sections if memory.document_plan else memory.section_plan
    plan_by_id = {row.section_id: row for row in plans}
    others = [row for row in adopted if row.section_id != section.section_id]
    # Normal plans have at most twelve sections. Bound custom templates too;
    # favor recent drafts rather than allowing early provenance guards to crowd out prose.
    visible = others[-12:]
    return {
        "section_purpose": section.goal,
        "section_responsibilities": [
            {"section_id": row.section_id, "heading": row.heading, "purpose": row.goal}
            for row in plans[:24]
        ],
        "responsibilities_omitted": max(0, len(plans) - 24),
        "adopted_sections": [
            {
                "section_id": row.section_id, "heading": row.heading,
                "purpose": plan_by_id[row.section_id].goal if row.section_id in plan_by_id else "",
                **_excerpt(row.draft_markdown),
                "declared_claims": [claim.model_dump(mode="json") for claim in row.claims[:4]],
                "declared_claims_omitted": max(0, len(row.claims) - 4),
                "support_status": "not_independently_verified_by_this_projection",
            }
            for row in visible
        ],
        "adopted_sections_omitted": max(0, len(others) - len(visible)),
        "writing_rules": [
            "Address this section's purpose; use other sections' responsibilities to give each detailed fact a home.",
            "Use adopted prose to avoid contradictory or duplicated explanations; excerpts are not primary-source evidence.",
            "Abstract and conclusion synthesize the actual body, including negative results and limitations; do not add findings.",
            "Revisit a fact only for a different analytical purpose, not by repeating setup and provenance in every section.",
            "Review the prose itself for important claims even when optional claim metadata is empty or incomplete.",
            "Request source context for material uncertainties; missing excerpts do not establish absence from the original source.",
        ],
    }


def _excerpt(text: str, limit: int = 1000) -> dict:
    if len(text) <= limit:
        windows = [{"start": 0, "end": len(text), "text": text}]
    else:
        half = limit // 2
        windows = [{"start": 0, "end": half, "text": text[:half]},
                   {"start": len(text) - half, "end": len(text), "text": text[-half:]}]
    return {"prose_windows": windows, "position_unit": "unicode_characters",
            "prose_characters": len(text),
            "prose_characters_omitted": len(text) - sum(len(row["text"]) for row in windows)}


def pending_revision_review(
    iterations: Sequence[ReportIterationRecord], section_id: str,
) -> tuple[ReportSectionReview | None, ReportSectionDraft | None]:
    """Recover the last revision's request and baseline from existing events."""
    events = [row for row in iterations if row.section_id == section_id]
    candidate_index = next((i for i in range(len(events) - 1, -1, -1)
                            if events[i].draft is not None), None)
    if candidate_index is None or events[candidate_index].action != "revise":
        return None, None
    prior = events[:candidate_index]
    review = next((row for row in reversed(prior) if row.action in {"review", "review_revision"}), None)
    baseline = next((row.draft for row in reversed(prior) if row.draft is not None), None)
    if review is None:
        return None, baseline
    return ReportSectionReview(section_id=section_id, verdict=review.status,
                               findings=review.findings, revision_instructions=review.revision_instructions), baseline


def revision_context(
    review: ReportSectionReview | None, baseline: ReportSectionDraft | None,
) -> dict:
    if review is None:
        return {}
    return {
        "target_findings": [row.model_dump(mode="json") for row in review.findings],
        "revision_instructions": review.revision_instructions,
        "original_section": {"section_id": baseline.section_id, **_excerpt(baseline.draft_markdown, 6000)} if baseline else {},
        "verification_rules": [
            "Check each original finding and instruction against the candidate, not just its fluency or the generic template.",
            "Do not clear an unresolved defect solely because the wording or finding id changed.",
            "Removing unsupported or duplicated text is a valid correction; preserve supported content needed for this section, not the original word count.",
            "Reject new unsupported claims or lost necessary qualifications/citations. Use source evidence, not the original draft, to establish facts.",
            "If the baseline is absent or excerpted, state the visibility limit; omitted text is not proof that a fact was absent.",
        ],
    }


def pending_document_revisions(
    iterations: Sequence[ReportIterationRecord],
    *, include_rejected: bool = False,
) -> dict[str, tuple[ReportIterationRecord, ReportSectionReview]]:
    """Recover the last candidate and correction contract from existing events.

    Rejected drafts may be continued within the configured allowance. Carry the
    original request as well as the verifier's new defects, never just the latter.
    Historical verification events without instructions remain readable.
    """
    pending = {}
    seen = set()
    for index in range(len(iterations) - 1, -1, -1):
        row = iterations[index]
        if row.action != "document_revise" or row.section_id in seen:
            continue
        seen.add(row.section_id)
        verification = next((later for later in iterations[index + 1:]
                             if later.section_id == row.section_id and later.action == "document_verify"), None)
        if row.draft is None or row.adopted is True or (verification and not include_rejected):
            continue
        request = next((earlier for earlier in reversed(iterations[:index])
                        if earlier.section_id == row.section_id and earlier.action == "document_review"), None)
        if request is not None:
            pending[row.section_id] = (row, ReportSectionReview(section_id=row.section_id,
                verdict=request.status,
                findings=[*request.findings, *(verification.findings if verification else [])],
                revision_instructions=list(dict.fromkeys([
                    *request.revision_instructions,
                    *(verification.revision_instructions if verification else []),
                ]))))
    return pending

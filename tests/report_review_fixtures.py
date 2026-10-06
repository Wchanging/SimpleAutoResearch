"""Literal draft anchors for synthetic orchestration clients, not fact proofs."""
import json

from simple_ar.report.schema import (ReportContext, ReportMemory, ReportRuntimeConfig,
    ReportSectionPlan, ReportSectionDraft, ReviewerFinding, ReportDraftQuote)
from simple_ar.report.templates import load_report_template_bundle


def finding_check_objects():
    """Fresh report and historical opinion inputs; no test-case dependency."""
    context = ReportContext(topic="Recorded observations", report_mode="experiment")
    old = ReviewerFinding(finding_id="old-assumption", section_id="scope",
        type="unsupported_claim", severity="major", required_action="revise",
        message="An earlier reviewer treated an unverified interpretation as fact.",
        draft_quotes=[ReportDraftQuote(section_id="scope", quote="The identity was not independently verified.")])
    memory = ReportMemory(section_plan=[ReportSectionPlan(section_id=sid, heading=sid,
        goal="Explain the evidence", draft_order=index, final_order=index)
        for index, sid in enumerate(("scope", "limits"), start=1)], reviewer_findings=[old])
    config = ReportRuntimeConfig(document_review=True, max_review_iterations=0)
    drafts = [ReportSectionDraft(section_id=sid, heading=sid,
        draft_markdown="The identity was not independently verified.") for sid in ("scope", "limits")]
    checkpoint = {"memory": memory.model_dump(mode="json"),
        "sections": [row.model_dump(mode="json") for row in drafts], "iterations": [],
        "reviewer_findings": [old.model_dump(mode="json")], "tool_results": [],
        "document_review_done": False, "pending_draft": None}
    kwargs = dict(context=context, memory=memory, config=config,
        template=load_report_template_bundle(report_mode="experiment", config=config))
    return kwargs, checkpoint, old, drafts


def draft_quotes(prompt, section_id):
    view = json.JSONDecoder().raw_decode(prompt[prompt.index('{'):])[0]
    draft = view.get('draft', {})
    if draft.get('section_id') == section_id:
        text = draft['draft_markdown']
    else:
        text = next(row['markdown'] for row in view['sections'] if row['section_id'] == section_id)
    return [{'section_id': section_id, 'quote': text}]

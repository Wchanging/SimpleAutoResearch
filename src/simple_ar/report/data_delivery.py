"""One read-only description of assembly-owned supplied-data prose.

Assembly rechecks/copies the package; planning and review read its saved inputs.
Sharing text and placement prevents a post-review caption from escaping the
document's length/scope checks. This projection does not certify those inputs.
"""
from collections.abc import Mapping, Sequence
from typing import Any

from simple_ar.report.schema import ReportContext, ReportDocumentPlan, ReportRuntimeConfig, ReportSectionDraft


def attach_delivery_block(
    sections: Sequence[ReportSectionDraft], block: Mapping[str, Any],
) -> tuple[ReportSectionDraft, ...]:
    """Place one immutable block identically in preview and actual assembly."""
    from simple_ar.report.assembler import strip_report_references

    if not block["heading"]:
        if not any(row.section_id == block["section_id"] for row in sections):
            raise ValueError("Assembly-owned block refers to an absent section; protected prose was not silently dropped.")
        # Remove only Writer-owned references before appending protected prose;
        # doing it later would also discard the required data/caption block.
        return tuple(row.model_copy(update={"draft_markdown": strip_report_references(row.draft_markdown) + "\n" + block["markdown"]})
                     if row.section_id == block["section_id"] else row for row in sections)
    return (*sections, ReportSectionDraft(section_id=block["section_id"], heading=block["heading"],
                                          draft_markdown=block["markdown"]))


def analysis_delivery_block(
    result: Mapping[str, Any], *, index: int, handle: str,
    config: ReportRuntimeConfig, plan: ReportDocumentPlan | None,
    section_ids: Sequence[str],
) -> dict[str, Any]:
    """Build the exact attachment prose, without files, rendering or mutations."""
    from simple_ar.result_analysis.table import data_attribution_markdown, table_values_markdown

    prefix = f"analyses/analysis-{index:03d}"
    blocks = [f"Descriptive analysis of {result['row_count']} supplied rows; "
              f"[copied data, numerical records and editable figures]({prefix}/analysis.md).",
              "Arithmetic was rechecked; data collection and scientific validity were not independently verified."]
    if attribution := data_attribution_markdown(dict(result)):
        blocks.append(attribution)
    if config.data_tables == "full":
        blocks.extend(["", table_values_markdown(dict(result))])
    else:
        blocks.append(f"[Complete numerical records]({prefix}/analysis.json); full row tables are not repeated in this prose report.")
    placements = [row.section_id for row in plan.visual_intents
                  if handle and row.kind == "figure" and row.view == "supplied-data"
                  and row.evidence_handles == [handle]] if plan else []
    owners = placements or ([row.section_id for row in plan.sections if handle and handle in row.evidence_handles] if plan else [])
    owner = owners[0] if len(owners) == 1 and owners[0] in section_ids else ""
    figures = []
    if config.figures.enabled and config.figures.mode != "off":
        for figure in result["figures"]:
            path = f"{prefix}/{figure['path']}"
            blocks.extend(["", f"![Descriptive data]({path})", "", figure["caption"]])
            figures.append({"path": path, "caption": figure["caption"]})
    markdown = "\n".join(blocks)
    return {"section_id": owner or f"supplied_analysis_{index}",
            "heading": "" if owner else f"Supplied Descriptive Data {index}",
            "handle": handle, "markdown": markdown,
            "markdown_token_count": len(markdown.split()), "figures": figures}


def supplied_data_delivery(
    context: ReportContext, *, config: ReportRuntimeConfig,
    plan: ReportDocumentPlan | None, section_ids: Sequence[str],
) -> list[dict[str, Any]]:
    """Project the same registered packages/order consumed by report assembly."""
    handles = {str(row.metadata.get("document_id")): row.handle
               for row in context.source_handles if row.metadata.get("document_id")}
    output = []
    for index, row in enumerate(context.results.get("supplied_analyses", []), 1):
        handle = handles.get(str(row.get("document_id")), "")
        try:
            block = analysis_delivery_block(row, index=index, handle=handle, config=config,
                plan=plan, section_ids=section_ids)
        except KeyError as exc:
            # Some older/external snapshots contain only figure identity. They
            # are not an exact package preview; never invent missing captions,
            # numerical records or a zero-length final attachment from them.
            output.append({"handle": handle, "preview_status": "unavailable",
                "missing_field": str(exc.args[0]), "markdown": "", "markdown_token_count": 0})
        else:
            output.append({**{key: value for key, value in block.items() if key != "figures"},
                           "preview_status": "recorded_package_preview"})
    return output


DELIVERY_RULES = (
    "assembly_owned_content is additional reader-facing prose, not a model draft or independently verified source. Its links, data qualifications, tables and captions are attached once at the indicated section after writing.",
    "Reserve space for these additions within the requested document length. Do not copy their image links, tables or full provenance disclaimers into drafts; interpret the supplied data and keep required qualifications concise. Do not remove required facts to make a length check pass.",
    "Assembly owns these blocks, so a defect in them needs an assembly/input correction, not an instruction to rewrite immutable text through the Writer. State the scope of any such unresolved defect. delivery_text_observation, when available, counts canonical title/headings, citation display, references and experiment appendix along with current drafts and registered additions. Future renderer output is not certified; this is not the complete exported artifact.",
    "An unavailable package preview means unknown added prose, not zero final length or no figures. Recorded package text is rechecked/rebuilt during assembly; final delivery still needs its own inspection if rendering changes it.",
)

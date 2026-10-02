"""Bounded writing context projected from adopted drafts, not a second memory.

The controller already saves complete sections in its checkpoint. Rebuild this
view from those sections on every call, including recovery; a rejected candidate
or stale claim record must not become evidence for the next section.
"""

from collections.abc import Mapping, Sequence
import json
from typing import Any
import re

from simple_ar.research.contracts import CLAIM_SCOPE_RULES
from simple_ar.report.execution_evidence import report_execution_evidence
from simple_ar.report.document_plan import supplied_figure_sources

from simple_ar.report.schema import (
    ClaimEvidenceRecord, ReportContext, ReportIterationRecord, ReportMemory, ReportRuntimeConfig, ReportSectionDraft,
    ReportSectionPlan, ReportSectionReview,
)


def evidence_outline_context(
    context: ReportContext, memory: ReportMemory, config: ReportRuntimeConfig, *, retry: bool = False,
) -> dict[str, Any]:
    """Project existing inputs for organization without declaring new facts.

    The persisted context/plan remains authoritative. Every shortened input
    reports its coverage; omitted content is not treated as absent evidence.
    """
    def excerpt(value: str, limit: int) -> dict[str, Any]:
        return {"text": value[:limit], "total_characters": len(value), "truncated": len(value) > limit}

    metrics = _prompt_metrics(memory, detail="summary")
    metric_rows = metrics["rows"]
    handles = memory.source_handles
    claims = memory.claims_evidence_matrix
    payload = {
        "task": "plan_evidence_organized_document", "topic": context.topic,
        "report_mode": context.report_mode, "template": memory.template,
        "objective": excerpt(memory.objective or context.goal_markdown, 3000),
        "problem": excerpt(context.problem_markdown, 2000),
        "synthesis": excerpt(context.synthesis_markdown, 3000),
        "evidence_summary": excerpt(context.evidence_summary, 3000),
        "template_responsibilities": [
            {"heading": section.heading, "goal": section.goal, "evidence_handles": section.evidence_handles,
             "target_words": section.target_words} for section in memory.section_plan
        ],
        "sources": [_prompt_handle_view(handle) for handle in handles[:40]],
        "sources_omitted": max(0, len(handles) - 40),
        "supplied_figures": supplied_figure_sources(context),
        "input_claims": [{"claim_id": claim.claim_id, "claim": excerpt(claim.claim, 600),
                          "status": claim.status, "evidence_handles": claim.evidence_handles,
                          "metric_ids": claim.metric_ids, "notes": excerpt(claim.notes, 600)} for claim in claims[:24]],
        "claims_omitted": max(0, len(claims) - 24),
        "recorded_metrics": {"columns": metrics["columns"], "rows": metric_rows[:48],
                             "rows_omitted": max(0, len(metric_rows) - 48)},
        "results": _compact_execution_results(context.results),
        "execution_evidence": report_execution_evidence(context),
        "limits": [*memory.limitations, *memory.open_questions][:16],
        "limits_omitted": max(0, len(memory.limitations) + len(memory.open_questions) - 16),
        "delivery_constraints": {"max_cited_sources": config.max_cited_sources or None,
                                 "max_section_sources": config.max_section_sources or None},
        "planning_rules": [
            "Use the requested genre and actual evidence to define concise sections with distinct responsibilities. Template headings are starting points, not compulsory new claims.",
            "Distinguish a research paper draft, reproduction report, analysis report and supplied-material account; do not invent novelty, theorems, experiments, baselines or ablations to resemble a reference paper.",
            "Name the question, what the inputs establish, how comparisons were made, findings and limitations. Put detailed numeric comparisons in one responsible section; others interpret rather than repeat them.",
            "Source summaries and input claims are recorded assertions, not independent verification. A source handle or completed invocation is not proof of a method claim.",
            "No new experiment or source retrieval is authorized. Unknowns and omitted material remain unknown; scope results to recorded conditions, and distinguish reported paper values from local observations.",
            "Keep goals and headings reader-facing, not pipeline steps. Give each section only supplied evidence handles; no minimum citations or word quota beyond the user's existing configuration.",
            "Propose a concise reader-facing title describing the actual scope, not a copy of task instructions or a stronger claim than the evidence. Give scope and validity details one primary section; other sections use brief qualifications without repeating the full disclaimer.",
            "Do not propose fabricated data charts. For an existing supplied_figures package, optionally assign exactly one owner using visual_intents kind=figure, view=supplied-data and one exact registered handle. The owner must include that handle in its section evidence. One source cannot have multiple owners; other sections can still cite it. Assembly attaches the existing figures, not model-created paths.",
            "Return 2-12 sections as needed. References are appended separately. Do not return a References section.",
        ],
        "output_schema": {"title": "Concise evidence-scoped title", "sections": [{"heading": "Short heading", "goal": "Purpose, claim boundaries and evidence to use",
                                         "evidence_handles": ["exact supplied handle"], "target_words": 0,
                                         "subsections": ["optional purposeful subsection"]}],
                          "visual_intents": [{"kind": "figure", "view": "supplied-data", "section_heading": "exact heading from your sections",
                              "title": "What the supplied data figure compares", "purpose": "Why this figure belongs here",
                              "evidence_handles": ["one exact supplied_figures handle"]}]},
    }
    if retry:
        payload["retry_instruction"] = "Correct the invalid structure or source pointers. Use only supplied handles and valid sections; do not add evidence to make the plan pass."
    return payload


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
    view = {
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
                "table_excerpt": _table_excerpt(row.draft_markdown),
                "declared_claims": [claim.model_dump(mode="json") for claim in row.claims[:4]],
                "declared_claims_omitted": max(0, len(row.claims) - 4),
                "support_status": "not_independently_verified_by_this_projection",
            }
            for row in visible
        ],
        "adopted_sections_omitted": max(0, len(others) - len(visible)),
        "writing_rules": [
            *CLAIM_SCOPE_RULES,
            "Address this section's purpose; use other sections' responsibilities to give each detailed fact a home.",
            "Use adopted prose to avoid contradictory or duplicated explanations; excerpts are not primary-source evidence.",
            "Abstract and conclusion synthesize the actual body, including negative results and limitations; do not add findings.",
            "Revisit a fact only for a different analytical purpose, not by repeating setup and provenance in every section.",
            "Numeric tables in adopted sections already own those detailed values; refer to them when relevant instead of duplicating the table or listing all values again. The table excerpt is adopted prose, not source verification.",
            "Review the prose itself for important claims even when optional claim metadata is empty or incomplete.",
            "Request source context for material uncertainties; missing excerpts do not establish absence from the original source.",
        ],
    }
    if memory.document_plan is not None:
        # All section prompts already include the frozen document_plan. Do not
        # repeat every long goal here (and again for each adopted section).
        # Unplanned/legacy callers retain the self-contained responsibility view.
        view["responsibilities_source"] = "document_plan.sections"
        view.pop("section_responsibilities")
        view.pop("responsibilities_omitted")
        for row in view["adopted_sections"]:
            row.pop("purpose")
            row["purpose_section_id"] = row["section_id"]
    return view


def _table_excerpt(text: str, limit: int = 1000) -> dict:
    """Expose literal table rows hidden between prose head/tail windows.

    Character positions point to the adopted draft. No inferred facts, summaries
    or persistent memory; this view cannot certify its table values.
    """
    matches = list(re.finditer(r"(?m)^[ \t]*\|[^\n]+\|[ \t]*$", text))
    windows = []
    remaining = limit
    for match in matches[:12]:
        if remaining <= 0:
            break
        end = min(match.end(), match.start() + remaining)
        windows.append({"start": match.start(), "end": end, "text": text[match.start():end]})
        remaining -= end - match.start()
    return {"windows": windows, "position_unit": "unicode_characters",
            "table_rows_available": len(matches), "table_rows_shown": len(windows),
            "characters_omitted": sum(len(match.group()) for match in matches) - (limit - remaining)}


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

def _compact_source_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    keys = ("method", "contribution", "evaluation", "relevance", "venue", "year")
    compact: dict[str, str] = {}
    for key in keys:
        value = metadata.get(key) if isinstance(metadata, dict) else None
        if value not in (None, ""):
            compact[key] = str(value)[:240]
    return compact


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


def _compact_document_plan(memory: ReportMemory) -> dict[str, Any]:
    """Expose the frozen plan without reintroducing parallel planning state."""
    plan = memory.document_plan
    if plan is None:
        return {}
    return {
        "schema_version": plan.schema_version,
        "status": plan.status,
        "title": plan.title,
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


def _prompt_handle_view(handle: Any) -> dict[str, Any]:
    """Return a compact model-facing handle with short citation guidance.

    The raw handle is retained for source provenance and chunk backtracking, but
    prose citations should use ``cite_as``. This keeps long provider ids out of
    normal body citation generation.
    """
    data = handle.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    if handle.kind == "experiment_output":
        data["attribution_guidance"] = (
            "Name this recorded producer attachment in prose. It is not a literature source and has no paper citation key. "
            "The handle is for read tools and provenance, not a Markdown link target; do not fabricate bibliography citations."
        )
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
            "consistency_issues": [str(issue)[:240] for issue in bibliography.get("consistency_issues", [])[:4]],
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

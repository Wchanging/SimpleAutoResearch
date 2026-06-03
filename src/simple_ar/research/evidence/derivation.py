from __future__ import annotations

import re
from typing import Any

from simple_ar.research.contracts import ExperimentContract, IdeaCandidate, NoveltyCheck


def build_gap_summary(pack: dict[str, Any]) -> str:
    """Render a conservative gap summary from the evidence package."""
    paper_cards = _list(pack.get("paper_cards"))
    claim_cards = _list(pack.get("claim_cards"))
    method_cards = _list(pack.get("method_cards"))
    dataset_cards = _list(pack.get("dataset_cards"))
    coverage = _dict(pack.get("coverage"))
    limitations = _string_list(pack.get("limitations"))
    lines = [
        "# Gap Summary",
        "",
        "This summary is generated from local evidence cards. It should be treated as a scoped research brief, not a novelty claim.",
        "",
        "## Evidence Surface",
        "",
        f"- Paper cards: {len(paper_cards)}",
        f"- Claim cards: {len(claim_cards)}",
        f"- Method cards: {len(method_cards)}",
        f"- Dataset cards: {len(dataset_cards)}",
        f"- Coverage status: {coverage.get('status', 'unknown')}",
        "",
        "## Observed Gaps",
        "",
    ]
    gaps = _observed_gaps(pack)
    lines.extend(f"- {gap}" for gap in gaps)
    if limitations:
        lines.extend(["", "## Evidence Limitations", ""])
        lines.extend(f"- {item}" for item in limitations)
    return "\n".join(lines).rstrip() + "\n"


def build_idea_candidates(pack: dict[str, Any], *, limit: int = 3) -> list[IdeaCandidate]:
    """Create bounded idea candidates grounded in cards and evidence refs."""
    method_cards = _list(pack.get("method_cards"))
    dataset_cards = _list(pack.get("dataset_cards"))
    claim_cards = _list(pack.get("claim_cards"))
    paper_cards = _list(pack.get("paper_cards"))
    topic = str(pack.get("topic") or "the topic")

    ideas: list[IdeaCandidate] = []
    if method_cards and dataset_cards:
        method = method_cards[0]
        dataset = dataset_cards[0]
        refs = _refs(method) + _refs(dataset)
        ideas.append(
            IdeaCandidate(
                idea_id="idea-001",
                title=f"Evaluate a small controlled improvement for {topic}",
                hypothesis=(
                    "A focused implementation change inspired by the observed method cards can improve "
                    "at least one reported evaluation metric without exceeding the local resource budget."
                ),
                motivation_refs=_unique(refs),
                proposed_change=_method_change(method),
                expected_outcome=_metric_outcome(dataset),
                required_baselines=_baseline_hints(method_cards),
                required_datasets=_dataset_names(dataset_cards),
                metrics=_metric_hints(dataset_cards, paper_cards),
                feasibility="medium" if refs else "low",
                risks=_risks_from_pack(pack),
            )
        )
    if claim_cards:
        claim = claim_cards[0]
        ideas.append(
            IdeaCandidate(
                idea_id=f"idea-{len(ideas) + 1:03d}",
                title="Test whether a reported claim holds under a smaller reproducible setting",
                hypothesis=_sentence(claim.get("claim")) or "A reported claim can be stress-tested in a bounded setting.",
                motivation_refs=_refs(claim),
                proposed_change="Create a small ablation or stress test around the cited claim before expanding scope.",
                expected_outcome="The run should expose whether the claim is robust enough for a later larger experiment.",
                required_baselines=_baseline_hints(method_cards),
                required_datasets=_dataset_names(dataset_cards),
                metrics=_metric_hints(dataset_cards, paper_cards),
                feasibility="medium" if _refs(claim) else "low",
                risks=_risks_from_pack(pack),
            )
        )
    if not ideas:
        ideas.append(
            IdeaCandidate(
                idea_id="idea-001",
                title=f"Build a literature-grounded baseline for {topic}",
                hypothesis="A baseline should be established before stronger claims or automated code changes are attempted.",
                motivation_refs=[],
                proposed_change="Summarize available methods, identify missing evaluation evidence, and defer implementation claims.",
                expected_outcome="A clearer baseline task definition and evidence checklist.",
                feasibility="low",
                risks=_risks_from_pack(pack) or ["Evidence is too sparse for experiment design."],
            )
        )
    return ideas[:limit]


def build_novelty_checks(
    ideas: list[IdeaCandidate],
    pack: dict[str, Any],
    *,
    backend: str = "local",
) -> list[NoveltyCheck]:
    """Build local novelty-risk hints.

    ``local`` is intentionally not a definitive novelty checker. It looks for
    lexical overlap with known claims and titles, records similar refs, and
    makes the uncertainty explicit so external MCP/agent backends can replace
    it later without changing downstream contracts.
    """
    known_text = _known_evidence_text(pack)
    checks: list[NoveltyCheck] = []
    for idea in ideas:
        terms = _terms(" ".join([idea.title, idea.hypothesis, idea.proposed_change]))
        similar_refs = _similar_refs(terms, known_text)
        risk_level = "high" if len(similar_refs) >= 3 else "medium" if similar_refs else "unknown"
        checks.append(
            NoveltyCheck(
                idea_id=idea.idea_id,
                status=f"{backend}_risk_hint",
                similar_work_refs=similar_refs,
                risk_level=risk_level,
                rationale=(
                    "Local lexical overlap suggests possible prior work; this is not a definitive novelty judgment."
                    if similar_refs
                    else "No close local overlap was found, but the local evidence set may be incomplete."
                ),
            )
        )
    return checks


def build_experiment_contract(
    ideas: list[IdeaCandidate],
    pack: dict[str, Any],
) -> ExperimentContract:
    """Turn the top grounded idea into a cautious experiment contract."""
    idea = ideas[0] if ideas else None
    dataset_cards = _list(pack.get("dataset_cards"))
    method_cards = _list(pack.get("method_cards"))
    if idea is None:
        return ExperimentContract(
            contract_id="experiment-contract-001",
            hypothesis="No experiment is recommended until evidence coverage improves.",
            risks=["No idea candidates were generated."],
            report_claim_plan=["Report insufficient evidence instead of experimental claims."],
        )
    metrics = idea.metrics or _metric_hints(dataset_cards, _list(pack.get("paper_cards")))
    return ExperimentContract(
        contract_id="experiment-contract-001",
        hypothesis=idea.hypothesis,
        motivation_refs=idea.motivation_refs,
        baseline=_first_nonempty(_baseline_hints(method_cards), "existing baseline or user-provided project"),
        dataset=_first_nonempty(idea.required_datasets, "dataset to be selected by the code task"),
        metrics=metrics,
        proposed_change=idea.proposed_change,
        implementation_scope=[
            "Use the Code Workspace Engine or an external coding agent to inspect the target repository.",
            "Keep changes bounded to the files required by the approved task and workspace edit scope.",
            "Run baseline and patched validation before reporting improvements.",
        ],
        validation_hints=[
            "Compare baseline and patched metrics with the same command and seed when possible.",
            "Treat failures, timeouts, and parser limitations as first-class evidence.",
        ],
        resource_budget={
            "mode": _dict(pack.get("source_plan")).get("mode", "standard"),
            "requires_human_review": True,
            "allow_large_changes_without_review": False,
        },
        risks=idea.risks,
        report_claim_plan=[
            "Only claim improvements supported by benchmark outputs.",
            "Cite motivation references from the evidence pack.",
            "Separate implementation observations from literature-backed claims.",
        ],
    )


def experiment_contract_markdown(contract: ExperimentContract) -> str:
    """Render an experiment contract for review."""
    rows = contract.to_row()
    lines = [
        "# Experiment Contract",
        "",
        f"Contract: {contract.contract_id}",
        "",
        "## Hypothesis",
        "",
        contract.hypothesis,
        "",
        "## Proposed Change",
        "",
        contract.proposed_change or "unknown",
        "",
        "## Setup",
        "",
        f"- Baseline: {contract.baseline}",
        f"- Dataset: {contract.dataset}",
        f"- Metrics: {_join_or_unknown(contract.metrics)}",
        "",
        "## Motivation References",
        "",
    ]
    lines.extend(f"- {ref}" for ref in contract.motivation_refs or ["none"])
    lines.extend(["", "## Implementation Scope", ""])
    lines.extend(f"- {item}" for item in contract.implementation_scope)
    lines.extend(["", "## Risks", ""])
    lines.extend(f"- {item}" for item in contract.risks or ["No explicit risks captured."])
    lines.extend(["", "## JSON Fields", ""])
    lines.extend(f"- {key}: {type(value).__name__}" for key, value in rows.items())
    return "\n".join(lines).rstrip() + "\n"


def build_tool_context(
    *,
    pack: dict[str, Any],
    contract: ExperimentContract,
    novelty_checks: list[NoveltyCheck],
) -> tuple[dict[str, Any], str]:
    """Create a read-only context handoff for future tools/MCP/coding agents."""
    context = {
        "schema_version": "tool_context.v1",
        "mode": "read_only_research_handoff",
        "topic": pack.get("topic"),
        "evidence_pack_schema": pack.get("schema_version"),
        "experiment_contract_id": contract.contract_id,
        "novelty_check_count": len(novelty_checks),
        "novelty_risk_levels": _novelty_risk_counts(novelty_checks),
        "allowed_actions": [
            "read evidence artifacts",
            "summarize cited cards",
            "prepare a code-task task.md from the experiment contract",
        ],
        "forbidden_actions": [
            "modify repository files without an approved code-task workspace",
            "claim novelty from local checks alone",
            "download additional content unless the run config allows it",
        ],
        "primary_artifacts": {
            "evidence_pack": "04-synthesize/evidence/evidence_pack.json",
            "gap_summary": "04-synthesize/evidence/gap_summary.md",
            "idea_candidates": "04-synthesize/evidence/idea_candidates.jsonl",
            "novelty_checks": "04-synthesize/evidence/novelty_checks.jsonl",
            "experiment_contract": "05-design/evidence/experiment_contract.json",
        },
        "human_review_required": True,
    }
    markdown = [
        "# Tool Context",
        "",
        "This context is a read-only handoff for later tool, MCP, or coding-agent integration.",
        "",
        "## Allowed Actions",
        "",
        *[f"- {item}" for item in context["allowed_actions"]],
        "",
        "## Forbidden Actions",
        "",
        *[f"- {item}" for item in context["forbidden_actions"]],
        "",
        "## Primary Artifacts",
        "",
        *[f"- {name}: {path}" for name, path in context["primary_artifacts"].items()],
        "",
        "## Human Review",
        "",
        "- Required before turning this contract into code edits.",
    ]
    return context, "\n".join(markdown).rstrip() + "\n"


def build_evidence_review(
    *,
    pack: dict[str, Any],
    ideas: list[IdeaCandidate],
    novelty_checks: list[NoveltyCheck],
    contract: ExperimentContract,
) -> tuple[str, list[dict[str, Any]]]:
    """Create a lightweight HITL review page and machine-readable decisions."""
    decisions = [
        {
            "decision_id": "review-001",
            "item": "evidence_pack",
            "status": "pending_user_review",
            "recommendation": _coverage_recommendation(pack),
        },
        {
            "decision_id": "review-002",
            "item": "experiment_contract",
            "status": "pending_user_review",
            "recommendation": "Approve only after confirming that the target repository and benchmark are available.",
        },
    ]
    lines = [
        "# Evidence Review",
        "",
        "Review this file before using the evidence pack to drive code changes or strong report claims.",
        "",
        "## Checklist",
        "",
        "- Are the selected papers relevant to the topic?",
        "- Are full-text and section extraction sufficient for the intended claim strength?",
        "- Are idea candidates grounded by motivation references?",
        "- Does the experiment contract fit the available local compute and target repository?",
        "",
        "## Idea Candidates",
        "",
    ]
    lines.extend(f"- {idea.idea_id}: {idea.title}" for idea in ideas)
    lines.extend(["", "## Novelty Risk Hints", ""])
    lines.extend(f"- {check.idea_id}: {check.risk_level} ({check.status})" for check in novelty_checks)
    lines.extend(["", "## Proposed Experiment", "", contract.hypothesis])
    return "\n".join(lines).rstrip() + "\n", decisions


def build_research_eval(
    *,
    pack: dict[str, Any],
    ideas: list[IdeaCandidate],
    contract: ExperimentContract,
) -> tuple[dict[str, Any], str]:
    """Build a simple research artifact quality report."""
    counts = _dict(pack.get("counts"))
    coverage = _dict(pack.get("coverage"))
    checks = [
        _check("has_documents", int(counts.get("documents") or 0) > 0, "At least one document record exists."),
        _check("has_chunks", int(counts.get("chunks") or 0) > 0, "At least one local evidence chunk exists."),
        _check("has_cards", int(counts.get("paper_cards") or 0) > 0, "At least one paper card exists."),
        _check("has_grounded_idea", any(idea.motivation_refs for idea in ideas), "At least one idea has evidence refs."),
        _check("has_experiment_contract", bool(contract.hypothesis), "Experiment contract was generated."),
    ]
    passed = sum(1 for check in checks if check["passed"])
    status = "passed" if passed == len(checks) and coverage.get("status") != "missing" else "needs_review"
    report = {
        "schema_version": "research_eval.v1",
        "status": status,
        "passed": passed,
        "total": len(checks),
        "checks": checks,
    }
    markdown = [
        "# Research Eval",
        "",
        f"Status: {status}",
        "",
        "## Checks",
        "",
    ]
    markdown.extend(
        f"- {'PASS' if check['passed'] else 'REVIEW'} {check['name']}: {check['description']}"
        for check in checks
    )
    return report, "\n".join(markdown).rstrip() + "\n"


def _observed_gaps(pack: dict[str, Any]) -> list[str]:
    coverage = _dict(pack.get("coverage"))
    gaps = [f"Missing facet `{facet}` should be resolved before strong claims." for facet in _string_list(coverage.get("missing_facets"))]
    counts = _dict(pack.get("counts"))
    if int(counts.get("method_cards") or 0) == 0:
        gaps.append("Method structure is not well captured yet.")
    if int(counts.get("dataset_cards") or 0) == 0:
        gaps.append("Dataset and metric evidence is sparse.")
    if int(counts.get("code_links") or 0) == 0:
        gaps.append("Runnable code links are not visible from the current evidence.")
    return gaps or ["No major deterministic gap was detected, but human review is still required."]


def _known_evidence_text(pack: dict[str, Any]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for paper in _list(pack.get("papers")):
        rows.append((str(paper.get("id") or paper.get("title") or "paper"), str(paper.get("title") or "")))
    for card in _list(pack.get("claim_cards")):
        rows.append((str(card.get("claim_id") or card.get("paper_id") or "claim"), str(card.get("claim") or "")))
    for card in _list(pack.get("method_cards")):
        rows.append((str(card.get("method_id") or "method"), " ".join([str(card.get("name") or ""), " ".join(_string_list(card.get("components")))])))
    return rows


def _similar_refs(terms: set[str], known_text: list[tuple[str, str]], *, limit: int = 5) -> list[str]:
    scored: list[tuple[int, str]] = []
    for ref, text in known_text:
        overlap = len(terms & _terms(text))
        if overlap:
            scored.append((overlap, ref))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [ref for _, ref in scored[:limit]]


def _method_change(method: dict[str, Any]) -> str:
    name = _clip(str(method.get("name") or "the observed method"), 100)
    components = _string_list(method.get("components"))
    if components:
        return f"Implement or ablate the component suggested by `{_clip(components[0], 100)}` while preserving the baseline path."
    return f"Implement a bounded ablation around `{name}` while preserving the baseline path."


def _metric_outcome(dataset: dict[str, Any]) -> str:
    metrics = _string_list(dataset.get("metrics"))
    if metrics:
        return f"Improve or clarify {_clip(metrics[0], 100)} relative to the baseline."
    return "Produce a measurable baseline comparison without making unsupported performance claims."


def _baseline_hints(method_cards: list[dict[str, Any]]) -> list[str]:
    rows: list[str] = []
    for card in method_cards:
        rows.extend(_string_list(card.get("comparison_baselines")))
    return [_clip(item, 140) for item in _unique(rows)[:5]]


def _dataset_names(dataset_cards: list[dict[str, Any]]) -> list[str]:
    names = (
        str(card.get("name") or "")
        for card in dataset_cards
        if str(card.get("name") or "").strip()
    )
    return [_clip(item, 140) for item in _unique(names)[:5]]


def _metric_hints(dataset_cards: list[dict[str, Any]], paper_cards: list[dict[str, Any]]) -> list[str]:
    rows: list[str] = []
    for card in dataset_cards + paper_cards:
        rows.extend(_string_list(card.get("metrics")))
    return [_clip(item, 140) for item in _unique(rows)[:6]]


def _risks_from_pack(pack: dict[str, Any]) -> list[str]:
    risks = _string_list(pack.get("limitations"))
    if not risks:
        risks.append("Evidence-derived idea may be too broad without target-repository inspection.")
    return risks[:5]


def _coverage_recommendation(pack: dict[str, Any]) -> str:
    status = str(_dict(pack.get("coverage")).get("status") or "unknown")
    if status == "covered":
        return "Evidence coverage looks acceptable for a bounded experiment proposal."
    if status == "partially_covered":
        return "Proceed only with cautious claims and document missing facets."
    return "Collect more evidence before experiment planning."


def _check(name: str, passed: bool, description: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "description": description}


def _refs(row: dict[str, Any]) -> list[str]:
    return _string_list(row.get("evidence_refs"))


def _first_nonempty(values: list[str], fallback: str) -> str:
    for value in values:
        if value.strip():
            return value
    return fallback


def _sentence(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    pieces = re.split(r"(?<=[.!?])\s+", text)
    return pieces[0].strip()


def _terms(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z0-9][a-z0-9_+-]{2,}", text.lower())
        if word not in {"and", "are", "for", "from", "the", "that", "this", "with"}
    }


def _novelty_risk_counts(checks: list[NoveltyCheck]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for check in checks:
        key = check.risk_level or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _unique(values: Any) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        key = text.lower()
        if text and key not in seen:
            rows.append(text)
            seen.add(key)
    return rows


def _join_or_unknown(values: list[str]) -> str:
    return ", ".join(values) if values else "unknown"


def _clip(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    clipped = compact[: limit - 3].rsplit(" ", 1)[0].strip()
    return f"{clipped}..." if clipped else compact[: limit - 3].strip() + "..."


def _dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]

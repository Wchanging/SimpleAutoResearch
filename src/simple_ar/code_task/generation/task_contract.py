from __future__ import annotations

import re
from typing import Any, Mapping

from simple_ar.code_task.generation.common import string_list


_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)]|\[[ xX]\])\s+(?P<text>.+?)\s*$")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(?P<text>.+?)\s*$")


def build_greenfield_task_contract(
    task_text: str,
    *,
    benchmark_command: str,
    max_files: int,
    max_generated_lines: int,
    result_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a generic implementation contract from a code-task request.

    The extractor is deliberately domain-neutral. Benchmark adapters and user
    tasks should put task-specific requirements in ``task.md``; the code-task
    generator then turns those explicit requirements into a durable contract
    that every planning, writing, review, and repair prompt can see.
    """

    objective = _first_meaningful_line(task_text) or "Implement the requested greenfield project."
    requirements = _extract_requirement_lines(task_text)
    deliverables = _filter_by_keywords(requirements, _DELIVERABLE_KEYWORDS)
    constraints = _filter_by_keywords(requirements, _CONSTRAINT_KEYWORDS)
    evaluation_focus = _filter_by_keywords(requirements, _EVALUATION_KEYWORDS)
    data_requirements = _filter_by_keywords(requirements, _DATA_KEYWORDS)
    dependency_hints = _filter_by_keywords(requirements, _DEPENDENCY_KEYWORDS)
    required_metrics = _required_metrics(result_schema)
    evidence_plan = _build_evidence_plan(
        requirements=requirements,
        required_metrics=required_metrics,
        result_schema=result_schema,
    )
    success_criteria = [
        "Generated project lives under code_task/workspace/generated_project.",
        f"The configured benchmark command exits with status 0 exactly as written: `{benchmark_command}`.",
        "The entrypoint prints parseable metric lines when metrics are requested.",
        "No network access or destructive filesystem behavior is required.",
    ]
    for metric in required_metrics[:20]:
        success_criteria.append(f"Required metric `{metric}` is produced from measured project outputs, not a default fill value.")
    for item in deliverables[:12]:
        success_criteria.append(f"Task deliverable is present and populated: {item}")
    for item in evaluation_focus[:12]:
        success_criteria.append(f"Evaluation requirement is addressed: {item}")
    for item in evidence_plan.get("hypotheses", [])[:12]:
        success_criteria.append(f"Hypothesis evidence is captured and reported: {item}")
    return {
        "schema_version": "code_task_greenfield_contract.v2",
        "contract_id": "code-task-greenfield",
        "task_kind": "greenfield",
        "objective": objective,
        "task": task_text,
        "benchmark_command": benchmark_command,
        "success_criteria": _dedupe(success_criteria)[:40],
        "explicit_requirements": requirements,
        "deliverables": deliverables,
        "constraints": constraints,
        "evaluation_focus": evaluation_focus,
        "data_requirements": data_requirements,
        "dependency_hints": dependency_hints,
        "evidence_plan": evidence_plan,
        "metric_contract": {
            "primary_metric": result_schema.get("primary_metric", "score"),
            "required_metrics": required_metrics,
            "metric_directions": result_schema.get("metric_directions", {}),
            "default_fill_policy": "do_not_fill_missing_required_metrics_with_zero",
        },
        "generation_plan": {
            "max_files": max_files,
            "max_generated_lines": max_generated_lines,
            "files_per_batch": 4,
        },
    }


def contract_prompt_view(
    contract: Mapping[str, Any],
    *,
    max_task_chars: int = 2400,
    max_requirements: int = 50,
    max_success_criteria: int = 30,
) -> dict[str, Any]:
    """Return a compact prompt-facing view of a greenfield task contract.

    The persisted contract keeps the full task text for auditability, but
    planning and per-file generation should not repeatedly serialize a long
    task document. This view keeps the hard contract fields and a bounded task
    excerpt so large benchmark tasks do not turn the first architecture request
    into a slow, fragile monolith.
    """

    task_text = str(contract.get("task") or "")
    return {
        "schema_version": contract.get("schema_version", "code_task_greenfield_contract.v2"),
        "contract_id": contract.get("contract_id", ""),
        "task_kind": contract.get("task_kind", ""),
        "objective": contract.get("objective", ""),
        "task_excerpt": _clean(task_text, max_chars=max_task_chars) if task_text else "",
        "benchmark_command": contract.get("benchmark_command", ""),
        "success_criteria": string_list(contract.get("success_criteria"), limit=max_success_criteria),
        "explicit_requirements": string_list(contract.get("explicit_requirements"), limit=max_requirements),
        "deliverables": string_list(contract.get("deliverables"), limit=30),
        "constraints": string_list(contract.get("constraints"), limit=30),
        "evaluation_focus": string_list(contract.get("evaluation_focus"), limit=30),
        "data_requirements": string_list(contract.get("data_requirements"), limit=30),
        "dependency_hints": string_list(contract.get("dependency_hints"), limit=20),
        "evidence_plan": dict(contract.get("evidence_plan", {}))
        if isinstance(contract.get("evidence_plan"), Mapping)
        else {},
        "metric_contract": dict(contract.get("metric_contract", {}))
        if isinstance(contract.get("metric_contract"), Mapping)
        else {},
        "generation_plan": dict(contract.get("generation_plan", {}))
        if isinstance(contract.get("generation_plan"), Mapping)
        else {},
    }


def _extract_requirement_lines(task_text: str, *, max_items: int = 80, max_chars: int = 260) -> list[str]:
    items: list[str] = []
    active_heading = ""
    for raw in task_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            active_heading = _clean(heading.group("text"), max_chars=max_chars)
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            item = _clean(bullet.group("text"), max_chars=max_chars)
        elif _line_has_requirement_signal(line):
            item = _clean(line, max_chars=max_chars)
        else:
            continue
        if active_heading:
            item = f"{active_heading}: {item}"
        items.append(item)
        if len(items) >= max_items:
            break
    if not items:
        first = _first_meaningful_line(task_text)
        if first:
            items.append(_clean(first, max_chars=max_chars))
    return _dedupe(items)


def _line_has_requirement_signal(line: str) -> bool:
    lowered = line.lower()
    return any(keyword in lowered for keyword in _REQUIREMENT_SIGNAL_KEYWORDS)


def _filter_by_keywords(items: list[str], keywords: tuple[str, ...], *, limit: int = 30) -> list[str]:
    rows = [item for item in items if any(keyword in item.lower() for keyword in keywords)]
    return rows[:limit]


def _required_metrics(result_schema: Mapping[str, Any]) -> list[str]:
    raw = result_schema.get("required_metrics")
    metrics = [str(item).strip() for item in raw if str(item).strip()] if isinstance(raw, list) else []
    primary = str(result_schema.get("primary_metric") or "").strip()
    if primary:
        metrics.insert(0, primary)
    return _dedupe(metrics)


def _build_evidence_plan(
    *,
    requirements: list[str],
    required_metrics: list[str],
    result_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Extract a lightweight evidence matrix from explicit task text.

    The plan is intentionally domain-neutral. It does not try to understand a
    benchmark's hidden scoring code; it preserves user/adapter-written
    hypotheses, dataset/condition requirements, and artifact obligations so
    planning, writing, review, repair, and result analysis can all reason from
    the same contract.
    """

    hypotheses = _filter_by_keywords(requirements, _HYPOTHESIS_KEYWORDS, limit=20)
    conditions = _filter_by_keywords(requirements, _CONDITION_KEYWORDS, limit=30)
    datasets = _filter_by_keywords(requirements, _DATA_KEYWORDS, limit=30)
    artifacts = _filter_by_keywords(requirements, _ARTIFACT_KEYWORDS, limit=30)
    comparisons = _filter_by_keywords(requirements, _COMPARISON_KEYWORDS, limit=30)
    return {
        "schema_version": "code_task_evidence_plan.v1",
        "hypotheses": hypotheses,
        "required_conditions": conditions,
        "required_datasets": datasets,
        "required_metrics": list(required_metrics),
        "required_artifacts": artifacts,
        "required_comparisons": comparisons,
        "record_granularity": [
            "Prefer per-dataset/per-condition/per-seed records when the task asks for repeated experiments.",
            "Aggregate tables should preserve enough cell-level evidence to justify every hypothesis verdict.",
        ],
        "claim_policy": [
            "Supported claims must cite measured metrics or explicit run artifacts.",
            "Unsupported or inconclusive hypotheses should be stated directly instead of hidden.",
        ],
        "primary_metric": result_schema.get("primary_metric", ""),
    }


def _first_meaningful_line(text: str) -> str:
    for raw in text.splitlines():
        line = raw.strip(" #\t")
        if line:
            return _clean(line, max_chars=220)
    return ""


def _clean(value: str, *, max_chars: int) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= max_chars:
        return value
    return value[: max(0, max_chars - 1)].rstrip() + "..."


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    rows: list[str] = []
    for value in values:
        key = value.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        rows.append(value.strip())
    return rows


_REQUIREMENT_SIGNAL_KEYWORDS = (
    "must",
    "should",
    "need",
    "required",
    "requirement",
    "deliver",
    "output",
    "write",
    "produce",
    "report",
    "metric",
    "evaluate",
    "benchmark",
    "dataset",
    "condition",
    "experiment",
    "compare",
    "artifact",
    "constraint",
    "timeout",
    "seed",
    "reproduc",
)

_DELIVERABLE_KEYWORDS = (
    "readme",
    "report",
    "artifact",
    "json",
    "jsonl",
    "csv",
    "table",
    "plot",
    "figure",
    "output",
    "write",
    "produce",
    "deliver",
)

_CONSTRAINT_KEYWORDS = (
    "must",
    "must not",
    "no network",
    "without network",
    "timeout",
    "budget",
    "resource",
    "cpu",
    "gpu",
    "memory",
    "deterministic",
    "seed",
    "reproduc",
    "constraint",
)

_EVALUATION_KEYWORDS = (
    "metric",
    "accuracy",
    "f1",
    "auc",
    "rmse",
    "mae",
    "loss",
    "runtime",
    "score",
    "evaluate",
    "benchmark",
    "compare",
    "condition",
    "hypothesis",
)

_HYPOTHESIS_KEYWORDS = (
    "hypothesis",
    "hypotheses",
    "h1",
    "h2",
    "h3",
    "claim",
    "verdict",
    "supported",
    "refuted",
    "inconclusive",
)

_CONDITION_KEYWORDS = (
    "condition",
    "conditions",
    "baseline",
    "method",
    "model",
    "algorithm",
    "variant",
    "treatment",
    "control",
    "ablation",
)

_ARTIFACT_KEYWORDS = (
    "artifact",
    "artifacts",
    "results.json",
    "metrics.json",
    "report.md",
    "readme",
    "jsonl",
    "csv",
    "table",
    "claims",
)

_COMPARISON_KEYWORDS = (
    "compare",
    "comparison",
    "versus",
    "vs",
    "against",
    "higher than",
    "lower than",
    "at least",
    "greater than",
    "less than",
    "best",
    "winner",
    "difference",
    "delta",
)

_DATA_KEYWORDS = (
    "dataset",
    "data",
    "source",
    "split",
    "train",
    "test",
    "validation",
    "record",
    "sample",
    "condition",
)

_DEPENDENCY_KEYWORDS = (
    "package",
    "library",
    "dependency",
    "numpy",
    "pandas",
    "sklearn",
    "scikit",
    "torch",
    "rich",
    "pydantic",
)

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from simple_ar.core.artifacts import read_json, write_json
from simple_ar.code_task.generation.common import string_list


_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)]|\[[ xX]\])\s+(?P<text>.+?)\s*$")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(?P<text>.+?)\s*$")
CANONICAL_CONTRACT_SCHEMA_VERSION = "code_task_contract.v4"
LEGACY_GREENFIELD_CONTRACT_SCHEMA_VERSION = "code_task_greenfield_contract.v2"
TASK_CONTRACT_FILENAME = "task_contract.json"
TASK_CONTRACT_COVERAGE_FILENAME = "task_contract_coverage.json"


def build_greenfield_task_contract(
    task_text: str,
    *,
    benchmark_command: str,
    max_files: int,
    max_generated_lines: int,
    result_schema: Mapping[str, Any],
    task_kind: str = "greenfield",
    source: Mapping[str, Any] | None = None,
    extra_contract: Mapping[str, Any] | None = None,
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
    required_metrics = _dedupe(_required_metrics(result_schema) + _required_metrics_from_requirements(requirements))
    source_data = dict(source or {})
    extra = _merge_extra_contracts(_embedded_extra_contract(task_text), extra_contract or {})
    extra_requirements = string_list(extra.get("explicit_requirements"), limit=80)
    extra_deliverables = string_list(extra.get("deliverables"), limit=40)
    extra_constraints = string_list(extra.get("constraints"), limit=40)
    extra_success = string_list(extra.get("success_criteria"), limit=60)
    extra_metrics = string_list(extra.get("required_metrics"), limit=60)
    if extra_requirements:
        requirements = _dedupe(requirements + extra_requirements)
        deliverables = _filter_by_keywords(requirements, _DELIVERABLE_KEYWORDS)
        constraints = _filter_by_keywords(requirements, _CONSTRAINT_KEYWORDS)
        evaluation_focus = _filter_by_keywords(requirements, _EVALUATION_KEYWORDS)
        data_requirements = _filter_by_keywords(requirements, _DATA_KEYWORDS)
        dependency_hints = _filter_by_keywords(requirements, _DEPENDENCY_KEYWORDS)
    if extra_deliverables:
        deliverables = _dedupe(deliverables + extra_deliverables)
    if extra_constraints:
        constraints = _dedupe(constraints + extra_constraints)
    if extra_metrics:
        required_metrics = _dedupe(required_metrics + extra_metrics)
    evidence_plan = _build_evidence_plan(
        requirements=requirements,
        required_metrics=required_metrics,
        result_schema=result_schema,
    )
    if isinstance(extra.get("evidence_plan"), Mapping):
        evidence_plan = _merge_evidence_plan(evidence_plan, extra["evidence_plan"])
    normalized_kind = str(task_kind or "greenfield").strip().lower().replace("-", "_")
    success_criteria = [
        f"The configured benchmark command exits with status 0 exactly as written: `{benchmark_command}`.",
        "No network access or destructive filesystem behavior is required.",
    ]
    if normalized_kind == "greenfield":
        success_criteria.insert(0, "Generated project lives under code_task/workspace/generated_project.")
        success_criteria.append("The entrypoint prints parseable metric lines when metrics are requested.")
    else:
        success_criteria.insert(0, "Code changes stay within the configured editable workspace and edit scope.")
        success_criteria.append("Patched code preserves existing public APIs unless the task explicitly requires a change.")
    for metric in required_metrics[:20]:
        success_criteria.append(f"Required metric `{metric}` is produced from measured project outputs, not a default fill value.")
    for item in deliverables[:12]:
        success_criteria.append(f"Task deliverable is present and populated: {item}")
    for item in evaluation_focus[:12]:
        success_criteria.append(f"Evaluation requirement is addressed: {item}")
    for item in evidence_plan.get("hypotheses", [])[:12]:
        success_criteria.append(f"Hypothesis evidence is captured and reported: {item}")
    success_criteria = _dedupe(success_criteria + extra_success)
    contract_id = str(extra.get("contract_id") or source_data.get("contract_id") or f"code-task-{normalized_kind}").strip()
    implementation_obligations = _normalize_implementation_obligations(
        extra.get("implementation_obligations"),
        fallback_requirements=requirements,
        evidence_plan=evidence_plan,
    )
    contract = {
        "schema_version": CANONICAL_CONTRACT_SCHEMA_VERSION,
        "legacy_schema_version": LEGACY_GREENFIELD_CONTRACT_SCHEMA_VERSION,
        "contract_id": contract_id or f"code-task-{normalized_kind}",
        "source": _contract_source(source_data),
        "task_kind": normalized_kind,
        "objective": objective,
        "task": task_text,
        "benchmark_command": benchmark_command,
        "success_criteria": success_criteria[:60],
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
        "implementation_contract": {
            "schema_version": "code_task_implementation_contract.v1",
            "obligations": implementation_obligations,
            "ownership_policy": str(
                extra.get("ownership_policy")
                or "Every nontrivial obligation should be owned by at least one planned source/reporting artifact."
            ),
        },
    }
    if isinstance(extra.get("artifact_contract"), Mapping):
        contract["artifact_contract"] = dict(extra["artifact_contract"])
    else:
        contract["artifact_contract"] = _artifact_contract_from_evidence(evidence_plan)
    if isinstance(extra.get("claim_specs"), list):
        contract["claim_specs"] = [dict(row) for row in extra["claim_specs"] if isinstance(row, Mapping)]
    else:
        contract["claim_specs"] = []
    return finalize_task_contract(contract)


def finalize_task_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a task contract and attach stable canonical metadata."""

    data = dict(contract)
    data["schema_version"] = CANONICAL_CONTRACT_SCHEMA_VERSION
    data.setdefault("contract_id", "code-task")
    data.setdefault("source", _contract_source({}))
    for key in (
        "success_criteria",
        "explicit_requirements",
        "deliverables",
        "constraints",
        "evaluation_focus",
        "data_requirements",
        "dependency_hints",
    ):
        data[key] = string_list(data.get(key), limit=120)
    if not isinstance(data.get("metric_contract"), Mapping):
        data["metric_contract"] = {}
    if not isinstance(data.get("evidence_plan"), Mapping):
        data["evidence_plan"] = {}
    if not isinstance(data.get("artifact_contract"), Mapping):
        data["artifact_contract"] = _artifact_contract_from_evidence(data["evidence_plan"])
    if not isinstance(data.get("generation_plan"), Mapping):
        data["generation_plan"] = {}
    if not isinstance(data.get("claim_specs"), list):
        data["claim_specs"] = []
    data["implementation_contract"] = _normalize_implementation_contract(data.get("implementation_contract"))
    data["version_hash"] = task_contract_hash(data)
    return data


def save_task_contract(meta_dir: Path, contract: Mapping[str, Any]) -> Path:
    """Persist the canonical code-task contract under ``code_task/meta``."""

    meta_dir.mkdir(parents=True, exist_ok=True)
    final = finalize_task_contract(contract)
    path = meta_dir / TASK_CONTRACT_FILENAME
    write_json(path, final)
    write_json(meta_dir / TASK_CONTRACT_COVERAGE_FILENAME, contract_coverage(final))
    return path


def load_task_contract(meta_dir: Path) -> dict[str, Any]:
    """Load the canonical contract when present, returning an empty dict otherwise."""

    path = meta_dir / TASK_CONTRACT_FILENAME
    if not path.is_file():
        return {}
    try:
        data = read_json(path)
    except Exception:
        return {}
    return finalize_task_contract(data) if isinstance(data, Mapping) else {}


def task_contract_hash(contract: Mapping[str, Any]) -> str:
    """Return a stable hash for contract content excluding derived metadata."""

    normalized = {
        str(key): value
        for key, value in contract.items()
        if key not in {"version_hash", "coverage", "derived_views"}
    }
    raw = json.dumps(normalized, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def contract_coverage(contract: Mapping[str, Any], derived: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return deterministic coverage metadata for the canonical contract.

    The check is intentionally lightweight. It does not judge scientific
    correctness; it exposes whether hard contract surfaces exist and whether a
    derived plan can be tied back to the same contract version.
    """

    derived_data = dict(derived or {})
    required_metrics = string_list(
        (contract.get("metric_contract") or {}).get("required_metrics")
        if isinstance(contract.get("metric_contract"), Mapping)
        else [],
        limit=80,
    )
    required_artifacts = string_list(
        (contract.get("artifact_contract") or {}).get("required_artifacts")
        if isinstance(contract.get("artifact_contract"), Mapping)
        else [],
        limit=80,
    )
    required_evidence = []
    evidence = contract.get("evidence_plan")
    if isinstance(evidence, Mapping):
        for key in ("hypotheses", "required_conditions", "required_datasets", "required_comparisons"):
            required_evidence.extend(f"{key}: {item}" for item in string_list(evidence.get(key), limit=40))
    obligations = implementation_obligations(contract, limit=120)
    omitted: list[str] = []
    if derived_data:
        text = json.dumps(derived_data, ensure_ascii=False, default=str).lower()
        for metric in required_metrics:
            if metric.lower() not in text:
                omitted.append(f"metric:{metric}")
        for artifact in required_artifacts:
            probe = artifact.split()[-1].strip("`'\"") if artifact else ""
            if probe and probe.lower() not in text:
                omitted.append(f"artifact:{artifact[:120]}")
        for obligation in obligations:
            obligation_id = str(obligation.get("id") or "").strip()
            requirement = str(obligation.get("requirement") or "").strip()
            evidence_terms = string_list(obligation.get("evidence_terms"), limit=8)
            probes = [obligation_id, *evidence_terms]
            if requirement:
                probes.extend(_important_terms(requirement, limit=4))
            if probes and not any(str(item).lower() in text for item in probes if str(item).strip()):
                omitted.append(f"obligation:{obligation_id or requirement[:80]}")
    return {
        "schema_version": "code_task_contract_coverage.v1",
        "contract_id": contract.get("contract_id", ""),
        "version_hash": contract.get("version_hash") or task_contract_hash(contract),
        "requirement_count": len(string_list(contract.get("explicit_requirements"), limit=200)),
        "success_criteria_count": len(string_list(contract.get("success_criteria"), limit=200)),
        "required_metric_count": len(required_metrics),
        "required_artifact_count": len(required_artifacts),
        "required_evidence_count": len(required_evidence),
        "implementation_obligation_count": len(obligations),
        "derived_view_checked": bool(derived_data),
        "omitted_contract_items": omitted[:80],
        "status": "warning" if omitted else "passed",
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
        "schema_version": contract.get("schema_version", CANONICAL_CONTRACT_SCHEMA_VERSION),
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
        "implementation_contract": implementation_contract_prompt_view(contract, max_items=24),
    }


def implementation_obligations(contract: Mapping[str, Any], *, limit: int = 80) -> list[dict[str, Any]]:
    """Return normalized implementation obligations from a task contract.

    This is intentionally benchmark-neutral. Adapters may provide obligations
    from rubric leaves, paper sections, or competition specs, but the core only
    sees stable ids, requirements, evidence terms, and optional owner files.
    """

    implementation = contract.get("implementation_contract")
    if isinstance(implementation, Mapping):
        return _normalize_implementation_obligations(implementation.get("obligations"))[:limit]
    return []


def implementation_contract_prompt_view(
    contract: Mapping[str, Any],
    *,
    max_items: int = 24,
) -> dict[str, Any]:
    implementation = contract.get("implementation_contract")
    if not isinstance(implementation, Mapping):
        return {"schema_version": "code_task_implementation_contract.v1", "obligations": []}
    obligations = implementation_obligations(contract, limit=max_items)
    return {
        "schema_version": implementation.get("schema_version", "code_task_implementation_contract.v1"),
        "ownership_policy": str(implementation.get("ownership_policy") or ""),
        "obligations": [
            {
                "id": row.get("id", ""),
                "category": row.get("category", ""),
                "requirement": row.get("requirement", ""),
                "acceptance_criteria": row.get("acceptance_criteria", []),
                "evidence_terms": row.get("evidence_terms", []),
                "owner_files": row.get("owner_files", []),
            }
            for row in obligations
        ],
    }


def _merge_extra_contracts(first: Mapping[str, Any], second: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(first or {})
    for key, value in dict(second or {}).items():
        if key in {
            "explicit_requirements",
            "deliverables",
            "constraints",
            "success_criteria",
            "required_metrics",
            "implementation_obligations",
            "claim_specs",
        }:
            existing = result.get(key)
            rows: list[Any] = []
            if isinstance(existing, list):
                rows.extend(existing)
            elif existing not in (None, "", {}, []):
                rows.append(existing)
            if isinstance(value, list):
                rows.extend(value)
            elif value not in (None, "", {}, []):
                rows.append(value)
            result[key] = rows
        elif key == "evidence_plan" and isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _merge_evidence_plan(result[key], value)
        else:
            result[key] = value
    return result


def _embedded_extra_contract(task_text: str) -> dict[str, Any]:
    """Parse optional machine-readable contract hints embedded in task text.

    The marker is generic on purpose, so benchmark adapters can pass structured
    obligations without adding benchmark-specific configuration to core code.
    """

    pattern = re.compile(
        r"```(?:json\s+)?simple_ar_extra_contract\s*\n(?P<body>.*?)\n```",
        re.IGNORECASE | re.DOTALL,
    )
    merged: dict[str, Any] = {}
    for match in pattern.finditer(task_text or ""):
        body = match.group("body").strip()
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, Mapping):
            merged = _merge_extra_contracts(merged, parsed)
    return merged


def _normalize_implementation_contract(value: object) -> dict[str, Any]:
    data = dict(value) if isinstance(value, Mapping) else {}
    data["schema_version"] = "code_task_implementation_contract.v1"
    data["obligations"] = _normalize_implementation_obligations(data.get("obligations"))
    data.setdefault(
        "ownership_policy",
        "Every nontrivial obligation should be owned by at least one planned source/reporting artifact.",
    )
    return data


def _normalize_implementation_obligations(
    value: object,
    *,
    fallback_requirements: list[str] | None = None,
    evidence_plan: Mapping[str, Any] | None = None,
    limit: int = 120,
) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows[:limit], start=1):
        if isinstance(row, Mapping):
            requirement = _clean(str(row.get("requirement") or row.get("description") or row.get("text") or ""), max_chars=500)
            raw_id = str(row.get("id") or row.get("leaf_id") or row.get("criterion_id") or "").strip()
            category = _clean(str(row.get("category") or row.get("task_category") or ""), max_chars=120)
            source = _clean(str(row.get("source") or ""), max_chars=120)
            acceptance = string_list(row.get("acceptance_criteria") or row.get("checks"), limit=12)
            evidence_terms = string_list(row.get("evidence_terms") or row.get("terms"), limit=16)
            owner_files = string_list(row.get("owner_files") or row.get("owners"), limit=12)
            weight = row.get("weight", row.get("scoring_weight", 1.0))
        else:
            requirement = _clean(str(row), max_chars=500)
            raw_id = ""
            category = ""
            source = ""
            acceptance = []
            evidence_terms = []
            owner_files = []
            weight = 1.0
        if not requirement:
            continue
        obligation_id = _obligation_id(raw_id or requirement, index=index)
        if not evidence_terms:
            evidence_terms = _important_terms(requirement, limit=10)
        normalized.append(
            {
                "id": obligation_id,
                "category": category or "implementation",
                "source": source,
                "requirement": requirement,
                "acceptance_criteria": acceptance[:12],
                "evidence_terms": evidence_terms[:16],
                "owner_files": owner_files[:12],
                "weight": _safe_float(weight, default=1.0),
            }
        )
    if normalized:
        return _dedupe_obligations(normalized)

    derived: list[str] = []
    evidence = evidence_plan if isinstance(evidence_plan, Mapping) else {}
    for key in ("hypotheses", "required_conditions", "required_datasets", "required_artifacts", "required_comparisons"):
        for item in string_list(evidence.get(key), limit=20):
            derived.append(f"{key}: {item}")
    for item in (fallback_requirements or [])[:24]:
        if any(token in item.lower() for token in ("must", "required", "condition", "dataset", "hypothesis", "artifact", "compare")):
            derived.append(item)
    return _dedupe_obligations(
        [
            {
                "id": _obligation_id(item, index=index),
                "category": "derived",
                "source": "task_contract",
                "requirement": _clean(item, max_chars=500),
                "acceptance_criteria": [],
                "evidence_terms": _important_terms(item, limit=10),
                "owner_files": [],
                "weight": 1.0,
            }
            for index, item in enumerate(_dedupe(derived)[:40], start=1)
            if item
        ]
    )


def _dedupe_obligations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.get("id") or row.get("requirement") or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _obligation_id(value: str, *, index: int) -> str:
    text = value.strip().lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{1,80}", text):
        return text[:80]
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if not slug:
        slug = f"obligation-{index:03d}"
    return slug[:80]


def _important_terms(text: str, *, limit: int) -> list[str]:
    terms: list[str] = []
    for quoted in re.findall(r"`([^`]{2,80})`", text):
        terms.append(quoted.strip())
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", text):
        lowered = token.lower()
        if lowered in _OBLIGATION_TERM_STOPWORDS:
            continue
        terms.append(token)
    return _dedupe(terms)[:limit]


def _safe_float(value: object, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_requirement_lines(task_text: str, *, max_items: int = 80, max_chars: int = 320) -> list[str]:
    items: list[str] = []
    active_heading = ""
    pending: str | None = None

    def flush_pending() -> None:
        nonlocal pending
        if pending:
            items.append(_clean(pending, max_chars=max_chars))
            pending = None

    for raw in task_text.splitlines():
        line = raw.strip()
        if not line:
            flush_pending()
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            flush_pending()
            active_heading = _clean(heading.group("text"), max_chars=max_chars)
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            flush_pending()
            item = _clean(bullet.group("text"), max_chars=max_chars)
        elif _line_has_requirement_signal(line):
            flush_pending()
            item = _clean(line, max_chars=max_chars)
        elif pending and raw[:1].isspace():
            pending = f"{pending} {line}"
            continue
        else:
            flush_pending()
            continue
        if active_heading:
            item = f"{active_heading}: {item}"
        pending = item
        if len(items) >= max_items:
            break
    flush_pending()
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


def _required_metrics_from_requirements(requirements: list[str], *, limit: int = 40) -> list[str]:
    metrics: list[str] = []
    in_metric_section = False
    for item in requirements:
        heading, _, body = item.partition(":")
        heading_l = heading.lower()
        if "required metric" in heading_l or "metric" in heading_l:
            in_metric_section = True
        elif heading_l and not any(token in heading_l for token in ("metric", "evaluation")):
            in_metric_section = False
        if not in_metric_section and "metric" not in item.lower():
            continue
        for match in re.findall(r"`([^`]+)`", item):
            name = _metric_name(match)
            if name:
                metrics.append(name)
        if in_metric_section:
            name = _metric_name(body or item)
            if name:
                metrics.append(name)
        if len(metrics) >= limit:
            break
    return _dedupe(metrics)[:limit]


def _metric_name(value: str) -> str:
    text = value.strip().strip("`")
    if not text:
        return ""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{1,80}", text):
        return ""
    return text


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


def _merge_evidence_plan(base: Mapping[str, Any], extra: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key in (
        "hypotheses",
        "required_conditions",
        "required_datasets",
        "required_metrics",
        "required_artifacts",
        "required_comparisons",
        "record_granularity",
        "claim_policy",
    ):
        result[key] = _dedupe(string_list(base.get(key), limit=120) + string_list(extra.get(key), limit=120))
    for key, value in extra.items():
        if key not in result and value not in (None, "", [], {}):
            result[key] = value
    return result


def _artifact_contract_from_evidence(evidence_plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "code_task_artifact_contract.v1",
        "required_artifacts": string_list(evidence_plan.get("required_artifacts"), limit=80),
        "required_comparisons": string_list(evidence_plan.get("required_comparisons"), limit=80),
        "hypotheses": string_list(evidence_plan.get("hypotheses"), limit=40),
        "claim_policy": string_list(evidence_plan.get("claim_policy"), limit=20),
    }


def _contract_source(value: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(value.get("kind") or value.get("source_kind") or "user_task").strip() or "user_task"
    result = {
        "kind": kind,
        "task_file": str(value.get("task_file") or ""),
        "origin": str(value.get("origin") or ""),
        "contract_id": str(value.get("contract_id") or ""),
    }
    artifacts = value.get("artifacts")
    if isinstance(artifacts, list):
        result["artifacts"] = [str(item) for item in artifacts if str(item).strip()][:40]
    return result


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

_OBLIGATION_TERM_STOPWORDS = {
    "about",
    "above",
    "after",
    "against",
    "analysis",
    "artifact",
    "artifacts",
    "baseline",
    "bench",
    "benchmark",
    "condition",
    "conditions",
    "data",
    "dataset",
    "datasets",
    "evidence",
    "experiment",
    "experiments",
    "generated",
    "implementation",
    "include",
    "method",
    "methods",
    "metric",
    "metrics",
    "model",
    "models",
    "output",
    "project",
    "report",
    "required",
    "requires",
    "result",
    "results",
    "should",
    "source",
    "task",
    "that",
    "this",
    "with",
    "without",
}

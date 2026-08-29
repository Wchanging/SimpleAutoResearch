"""Standalone result-analysis boundary.

The existing ``result_analysis`` package remains the implementation backend.
This module only gives callers a small request boundary and keeps persistence
opt-in, so execution and analysis can be composed without coupling either one
to the eight-stage pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Mapping

from simple_ar.core.capabilities import ArtifactRef, CapabilityContext, CapabilityResult
from simple_ar.result_analysis.metrics import normalize_direction
from simple_ar.result_analysis.schema import AnalysisContext, AnalysisResult
from simple_ar.result_analysis.service import run_result_analysis


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    """Inputs for one deterministic or LLM-assisted result analysis."""

    context: AnalysisContext | Mapping[str, Any]
    use_llm: bool = False
    output_dir: Path | None = None
    label: str = "result-analysis"

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("AnalysisRequest.label cannot be empty.")


@dataclass(frozen=True, slots=True)
class AnalysisHandoff:
    """Typed representation of one persisted execution-to-analysis handoff."""

    execution_ref: ArtifactRef
    execution_status: str
    analysis: AnalysisResult

    def __post_init__(self) -> None:
        if not self.execution_status.strip():
            raise ValueError("AnalysisHandoff.execution_status cannot be empty.")

    def to_handoff_dict(self) -> dict[str, Any]:
        """Return the stable ``analysis_handoff.v1`` representation."""

        return {
            "schema_version": "analysis_handoff.v1",
            "execution_ref": self.execution_ref.to_dict(),
            "execution_status": self.execution_status,
            "analysis": self.analysis.model_dump(mode="json"),
        }

    @classmethod
    def from_handoff_dict(cls, data: Mapping[str, Any]) -> "AnalysisHandoff":
        """Restore an analysis handoff without reading or rerunning execution."""

        if str(data.get("schema_version") or "") != "analysis_handoff.v1":
            raise ValueError("Expected an analysis_handoff.v1 object.")
        execution_payload = data.get("execution_ref")
        analysis_payload = data.get("analysis")
        if not isinstance(execution_payload, Mapping):
            raise ValueError("Analysis handoff is missing execution_ref.")
        if not isinstance(analysis_payload, Mapping):
            raise ValueError("Analysis handoff is missing analysis.")
        execution_status = str(data.get("execution_status") or "").strip()
        if not execution_status:
            raise ValueError("Analysis handoff is missing execution_status.")
        return cls(
            execution_ref=ArtifactRef.from_dict(dict(execution_payload)),
            execution_status=execution_status,
            analysis=AnalysisResult.model_validate(analysis_payload),
        )


def analyze_results(
    request: AnalysisRequest,
    *,
    client: Any | None = None,
) -> AnalysisResult:
    """Analyze one result context and optionally persist its artifacts.

    No files are written unless ``request.output_dir`` is explicitly supplied.
    The existing result-analysis service owns metric normalization, claim
    grounding, audit behavior, and optional LLM use.
    """

    return run_result_analysis(
        request.context,
        output_dir=request.output_dir,
        client=client,
        use_llm=request.use_llm,
        label=request.label,
    )


def compare_experiment_results(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    primary_metric: str = "",
    metric_directions: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Compare two canonical experiment results without running a policy.

    The comparison is deliberately small and conservative.  It consumes the
    top-level ``status`` and ``metrics`` fields emitted by the experiment
    boundary, uses only explicit metric directions, and returns
    ``inconclusive`` when the required evidence is absent.  It does not pick a
    winner, retry a run, or decide whether the research session should move.
    """

    baseline_data = _mapping_value(baseline)
    candidate_data = _mapping_value(candidate)
    primary = str(primary_metric or "").strip()
    directions, direction_sources = _comparison_directions(
        baseline_data,
        candidate_data,
        metric_directions,
    )
    if not primary:
        primary = _embedded_primary_metric(candidate_data) or _embedded_primary_metric(
            baseline_data
        )

    baseline_status = _result_status(baseline_data)
    candidate_status = _result_status(candidate_data)
    baseline_metrics = _numeric_metrics(baseline_data.get("metrics"))
    candidate_metrics = _numeric_metrics(candidate_data.get("metrics"))
    metric_rows = _comparison_metric_rows(
        baseline_metrics,
        candidate_metrics,
        directions,
        direction_sources,
        primary,
    )
    verdict, reasons = _comparison_verdict(
        baseline_data=baseline_data,
        candidate_data=candidate_data,
        baseline_status=baseline_status,
        candidate_status=candidate_status,
        metric_rows=metric_rows,
        primary_metric=primary,
    )
    return {
        "schema_version": "experiment_comparison.v1",
        "status": "ready" if baseline_data and candidate_data else "incomplete",
        "verdict": verdict,
        "reasons": reasons,
        "metric_config": {
            "primary_metric": primary,
            "metric_directions": directions,
        },
        "baseline": _comparison_result_summary(baseline_data, baseline_status, baseline_metrics),
        "candidate": _comparison_result_summary(candidate_data, candidate_status, candidate_metrics),
        "metrics": metric_rows,
        "deltas": {row["name"]: row["delta"] for row in metric_rows},
    }


def _comparison_directions(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    explicit: Mapping[str, str] | None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Collect embedded directions, with caller configuration taking priority."""

    directions: dict[str, str] = {}
    sources: dict[str, str] = {}
    for result in (baseline, candidate):
        schema = result.get("result_schema")
        if not isinstance(schema, Mapping):
            continue
        embedded = schema.get("metric_directions")
        if isinstance(embedded, Mapping):
            for name, value in embedded.items():
                _set_direction(directions, sources, name, value, "embedded")
        schema_primary = str(schema.get("primary_metric") or "").strip()
        schema_direction = schema.get("direction")
        if schema_primary and schema_direction is not None:
            _set_direction(
                directions,
                sources,
                schema_primary,
                schema_direction,
                "embedded",
            )
    for name, value in (explicit or {}).items():
        _set_direction(directions, sources, name, value, "configured")
    return directions, sources


def _set_direction(
    directions: dict[str, str],
    sources: dict[str, str],
    name: object,
    value: object,
    source: str,
) -> None:
    metric_name = str(name).strip()
    if not metric_name:
        return
    directions[metric_name] = normalize_direction(value)
    sources[metric_name] = source


def _comparison_metric_rows(
    baseline: Mapping[str, float],
    candidate: Mapping[str, float],
    directions: Mapping[str, str],
    direction_sources: Mapping[str, str],
    primary_metric: str,
) -> list[dict[str, Any]]:
    baseline_names = {_metric_key(name): (str(name), value) for name, value in baseline.items()}
    candidate_names = {_metric_key(name): (str(name), value) for name, value in candidate.items()}
    rows: list[dict[str, Any]] = []
    for key in sorted(set(baseline_names) & set(candidate_names)):
        baseline_name, baseline_value = baseline_names[key]
        _, candidate_value = candidate_names[key]
        name = baseline_name
        direction, source = _direction_for_metric(name, directions, direction_sources)
        delta = candidate_value - baseline_value
        if abs(delta) <= 1e-12:
            interpretation = "unchanged"
        elif direction == "higher":
            interpretation = "improved" if delta > 0 else "regressed"
        elif direction == "lower":
            interpretation = "improved" if delta < 0 else "regressed"
        elif direction == "resource":
            interpretation = "decreased" if delta < 0 else "increased"
        elif direction == "ignore":
            interpretation = "ignored"
        else:
            interpretation = "changed"
        rows.append(
            {
                "name": name,
                "baseline": baseline_value,
                "candidate": candidate_value,
                "delta": delta,
                "direction": direction,
                "direction_source": source,
                "interpretation": interpretation,
                "is_primary": bool(primary_metric and _same_metric(name, primary_metric)),
            }
        )
    return rows


def _comparison_verdict(
    *,
    baseline_data: Mapping[str, Any],
    candidate_data: Mapping[str, Any],
    baseline_status: str,
    candidate_status: str,
    metric_rows: list[dict[str, Any]],
    primary_metric: str,
) -> tuple[str, list[str]]:
    if not baseline_data or not candidate_data:
        return "inconclusive", ["baseline or candidate result is missing"]
    if baseline_status != "passed" and candidate_status == "passed":
        return "improved", [f"candidate passed after baseline status `{baseline_status}`"]
    if baseline_status == "passed" and candidate_status != "passed":
        return "regressed", [f"candidate status `{candidate_status}` after passing baseline"]
    if candidate_status != "passed":
        return "inconclusive", [
            "both results are non-passing or blocked: "
            f"baseline={baseline_status}, candidate={candidate_status}"
        ]

    directional = [
        row
        for row in metric_rows
        if row.get("direction") in {"higher", "lower"}
    ]
    if primary_metric:
        primary_rows = [
            row for row in metric_rows if _same_metric(str(row.get("name", "")), primary_metric)
        ]
        if not primary_rows:
            return "inconclusive", [
                f"configured primary metric `{primary_metric}` was not shared by both results"
            ]
        if primary_rows[0].get("direction") not in {"higher", "lower"}:
            return "inconclusive", [
                f"primary metric `{primary_metric}` has no directional comparison"
            ]
        directional = [row for row in directional if row in primary_rows]
    if not directional:
        return "inconclusive", ["no directional numeric metrics were shared by both results"]

    improved = [row for row in directional if row["interpretation"] == "improved"]
    regressed = [row for row in directional if row["interpretation"] == "regressed"]
    if improved and not regressed:
        return "improved", _comparison_reasons(improved, "improved")
    if regressed and not improved:
        return "regressed", _comparison_reasons(regressed, "regressed")
    if improved and regressed:
        return "mixed", _comparison_reasons(improved, "improved") + _comparison_reasons(
            regressed, "regressed"
        )
    return "unchanged", ["directional metrics were unchanged within tolerance"]


def _comparison_reasons(rows: list[dict[str, Any]], prefix: str) -> list[str]:
    return [
        f"{prefix} `{row['name']}` by {float(row['delta']):+.6g}"
        for row in rows[:6]
    ]


def _comparison_result_summary(
    result: Mapping[str, Any],
    status: str,
    metrics: Mapping[str, float],
) -> dict[str, Any]:
    execution = result.get("execution")
    execution = execution if isinstance(execution, Mapping) else {}
    return {
        "status": status,
        "returncode": result.get("returncode"),
        "timed_out": result.get("timed_out"),
        "duration_sec": execution.get("duration_sec"),
        "metrics": dict(metrics),
    }


def _result_status(result: Mapping[str, Any]) -> str:
    status = str(result.get("status") or "").strip()
    if status:
        return status
    execution = result.get("execution")
    if isinstance(execution, Mapping):
        return str(execution.get("status") or "missing").strip() or "missing"
    return "missing"


def _numeric_metrics(value: object) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    metrics: dict[str, float] = {}
    for name, raw in value.items():
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        numeric = float(raw)
        if math.isfinite(numeric):
            metrics[str(name).strip()] = numeric
    return {name: value for name, value in metrics.items() if name}


def _embedded_primary_metric(result: Mapping[str, Any]) -> str:
    primary = str(result.get("primary_metric") or "").strip()
    if primary:
        return primary
    schema = result.get("result_schema")
    if isinstance(schema, Mapping):
        return str(schema.get("primary_metric") or "").strip()
    return ""


def _direction_for_metric(
    name: str,
    directions: Mapping[str, str],
    sources: Mapping[str, str],
) -> tuple[str, str]:
    if name in directions:
        return str(directions[name]), str(sources.get(name) or "configured")
    key = _metric_key(name)
    for configured_name, direction in directions.items():
        if _metric_key(configured_name) == key:
            return str(direction), str(sources.get(configured_name) or "configured")
    return "unknown", "none"


def _metric_key(name: object) -> str:
    return str(name).strip().lower()


def _same_metric(left: str, right: str) -> bool:
    return _metric_key(left) == _metric_key(right)


def _mapping_value(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _merge_result_schema_into_analysis_context(
    context: AnalysisContext,
    result_schema: Mapping[str, Any],
) -> AnalysisContext:
    """Expose execution-required metrics without mutating the context."""

    schema = dict(result_schema)
    expected = [
        dict(item) for item in context.expected_metrics if isinstance(item, Mapping)
    ]
    known = {
        str(item.get("name")).strip()
        for item in expected
        if str(item.get("name") or "").strip()
    }
    names: list[str] = []
    primary = str(schema.get("primary_metric") or "").strip()
    if primary:
        names.append(primary)
    required = schema.get("required_metrics")
    if isinstance(required, (list, tuple)):
        names.extend(str(name).strip() for name in required if str(name).strip())
    direction = str(schema.get("direction") or "").strip()
    schema_directions = schema.get("metric_directions")
    schema_directions = (
        schema_directions if isinstance(schema_directions, Mapping) else {}
    )
    for name in dict.fromkeys(names):
        if name in known:
            continue
        row: dict[str, Any] = {"name": name}
        metric_direction = str(schema_directions.get(name) or direction).strip()
        if metric_direction:
            row["direction"] = metric_direction
        expected.append(row)
    if expected == context.expected_metrics:
        return context
    return context.model_copy(update={"expected_metrics": expected})


def analyze_experiment_capability(
    *,
    context: CapabilityContext,
    result_ref: ArtifactRef,
    analysis_context: AnalysisContext | Mapping[str, Any],
    client: Any | None = None,
    use_llm: bool = False,
    label: str = "experiment-analysis",
) -> CapabilityResult:
    """Analyze a declared execution result through the session boundary.

    The execution artifact is read from an explicit input reference. The
    adapter writes one analysis handoff and keeps the execution reference
    instead of copying the canonical result into another file. It analyzes
    failed executions as well, so a failed run remains evidence rather than
    disappearing from the research session.
    """
    payload = context.read_input_json(result_ref)
    if not isinstance(payload, Mapping):
        raise ValueError("Experiment result artifact must be a JSON object.")
    execution_status = str(payload.get("status") or "unknown")
    result_schema = payload.get("result_schema")
    result_schema = result_schema if isinstance(result_schema, Mapping) else {}
    base_context = (
        analysis_context
        if isinstance(analysis_context, AnalysisContext)
        else AnalysisContext.model_validate(analysis_context)
    )
    base_context = _merge_result_schema_into_analysis_context(base_context, result_schema)
    metrics = dict(base_context.metrics)
    observed_metrics = payload.get("metrics")
    if isinstance(observed_metrics, Mapping):
        metrics.update(
            {
                str(name): float(value)
                for name, value in observed_metrics.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
        )
    project_results = dict(base_context.project_results)
    project_results["execution_result"] = dict(payload)
    analysis = analyze_results(
        AnalysisRequest(
            context=base_context.model_copy(
                update={"metrics": metrics, "project_results": project_results}
            ),
            use_llm=use_llm,
            label=label,
        ),
        client=client,
    )
    handoff = AnalysisHandoff(
        execution_ref=result_ref,
        execution_status=execution_status,
        analysis=analysis,
    )
    capability_status = _capability_status_for_analysis(analysis.status)
    output = context.store.write_json(
        "analysis.json",
        handoff.to_handoff_dict(),
        kind="analysis_result",
        schema="analysis_handoff.v1",
        producer="research.analysis",
    )
    diagnostics = (
        (f"Execution input status: {execution_status}.",)
        if execution_status != "passed"
        else ()
    )
    diagnostics = tuple(
        dict.fromkeys((*diagnostics, *analysis.status_reasons))
    )
    return CapabilityResult(
        status=capability_status,
        artifacts=(output,),
        diagnostics=diagnostics,
        usage={
            "execution_status": execution_status,
            "metric_count": len(metrics),
            "claim_count": len(analysis.claims),
        },
        provenance={
            "capability": "analysis",
            "execution_ref": result_ref.path,
            "result_schema": "analysis_handoff.v1",
        },
    )


def _capability_status_for_analysis(status: str) -> str:
    """Map analysis semantics to the smaller capability status vocabulary."""

    return {
        "passed": "completed",
        "incomplete": "partial",
        "metric_below_target": "partial",
        "failed": "failed",
        "blocked": "blocked",
    }.get(status, "failed")


__all__ = [
    "AnalysisRequest",
    "AnalysisHandoff",
    "analyze_results",
    "compare_experiment_results",
    "analyze_experiment_capability",
]

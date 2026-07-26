"""Offline evidence extraction for the ARC-Bench paper supplement.

This utility deliberately reads existing batch summaries and artifacts only.  It
does not invoke an LLM, execute a benchmark task, or alter a run directory.
Missing local artifacts remain ``missing`` rather than being inferred from an
aggregate score.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / "benchmark" / "arc_bench" / "batch_state"
PREPARED_DIR = ROOT / "benchmark" / "arc_bench" / "prepared" / "ml"
DEFAULT_OUTPUT_DIR = ROOT / "MDfiles" / "Paper-ADD"


@dataclass(frozen=True)
class VariantSpec:
    label: str
    state_name: str
    summary_name: str
    description: str


VARIANTS = (
    VariantSpec("Full", "20260713-101237-all.json", "20260713-101237-all.manual-strict.summary.json", "Full workflow with structured contract propagation, structured repair context, and repair memory."),
    VariantSpec("Minimal Contract", "ablation-minimal-contract-manual-strict.json", "ablation-minimal-contract-manual-strict.summary.json", "Minimal contract context during task execution."),
    VariantSpec("Raw-Log Repair", "ablation-no-failure-graph-manual-strict.json", "ablation-no-failure-graph-manual-strict.summary.json", "Repair context restricted to raw logs rather than structured diagnostics."),
    VariantSpec("No Repair Memory", "ablation-no-repair-memory-manual-strict.json", "ablation-no-repair-memory-manual-strict.summary.json", "Repair memory disabled."),
)


REPEATED_TASKS = ("ML02", "ML03", "ML07", "ML21", "ML23")
REPEATED_SUMMARIES = (
    ("Full", "r0", "20260713-101237-all.manual-strict.summary.json", "historical"),
    ("Minimal Contract", "r0", "ablation-minimal-contract-manual-strict.summary.json", "historical"),
    ("Full", "r1", "repeat-full-r1-manual-strict.summary.json", "new"),
    ("Full", "r2", "repeat-full-r2-manual-strict.summary.json", "new"),
    ("Minimal Contract", "r1", "repeat-minimal-r1-manual-strict.summary.json", "new"),
    ("Minimal Contract", "r2", "repeat-minimal-r2-manual-strict.summary.json", "new"),
)


TASK_DOMAINS = {
    "ML01": "neural_regularization", "ML02": "tabular_regression", "ML03": "optimization",
    "ML04": "tabular_classification", "ML05": "dimensionality_reduction", "ML06": "tabular_classification",
    "ML07": "NLP", "ML08": "imbalanced_classification", "ML09": "AutoML_hyperparameter_optimization",
    "ML10": "validation", "ML11": "anomaly_detection", "ML12": "clustering",
    "ML13": "gaussian_process", "ML14": "conformal_prediction", "ML15": "feature_selection",
    "ML16": "bandits", "ML17": "NLP_topic_modeling", "ML18": "calibration",
    "ML19": "semi_supervised_learning", "ML20": "time_series", "ML21": "causal_discovery",
    "ML22": "active_learning", "ML23": "learning_to_rank", "ML24": "online_learning",
    "ML25": "dynamical_time_series",
}


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def text(value: Any, default: str = "missing") -> str:
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


def number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def task_name(task_id: str) -> str:
    path = PREPARED_DIR / task_id / "task.md"
    if not path.exists():
        return "missing"
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("# "):
            return line.removeprefix("# ").strip()
    return "missing"


def normalize_failure(raw: Any, details: str = "") -> str:
    source = f"{text(raw, '')} {details}".lower()
    if not source.strip():
        return "unknown"
    if "artifact" in source and ("missing" in source or "not found" in source):
        return "missing artifact"
    if "metric" in source and ("missing" in source or "not found" in source):
        return "missing metric"
    if any(term in source for term in ("syntaxerror", "importerror", "module not found", "no module named")):
        return "syntax/import"
    if any(term in source for term in ("attribute/interface", "type/interface", "interface mismatch", "attributeerror", "typeerror")):
        return "interface mismatch"
    if any(term in source for term in ("value/schema", "key/schema", "schema mismatch", "validationerror", "valueerror", "keyerror")):
        return "schema/value mismatch"
    if any(term in source for term in ("analysis", "unsupported claim", "claim audit")):
        return "invalid analysis"
    if any(term in source for term in ("timeout", "resource", "memoryerror", "killed", "runtime/other", "connection", "subprocess")):
        return "runtime/resource"
    return "unknown"


def summary_error(row: dict[str, Any]) -> str:
    for key in ("failure_cause", "failure_signal", "last_error", "stderr_tail", "review_blockers"):
        value = text(row.get(key), "")
        if value:
            return re.sub(r"\s+", " ", value)[:800]
    return "missing"


def run_path(row: dict[str, Any]) -> Path | None:
    for key in ("source_run_dir", "run_dir"):
        value = row.get(key)
        if isinstance(value, str) and value:
            path = ROOT / value
            if path.exists():
                return path
    return None


def failure_graphs(row: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    run_dir = run_path(row)
    if run_dir is None:
        return []
    root = run_dir / "code_task" / "run" / "patched" / "attempts"
    graphs: list[tuple[int, dict[str, Any]]] = []
    if not root.exists():
        return graphs
    for candidate in root.glob("attempt-*/failure_graph.json"):
        match = re.search(r"attempt-(\d+)", candidate.parent.name)
        payload = load_json(candidate)
        if match and payload is not None:
            graphs.append((int(match.group(1)), payload))
    return sorted(graphs)


def first_failure_stage(row: dict[str, Any]) -> str:
    graphs = failure_graphs(row)
    if not graphs:
        return "missing"
    _, graph = graphs[0]
    for key in ("stage", "failure_stage", "primary_signal"):
        value = text(graph.get(key), "")
        if value:
            return value[:300]
    return "missing"


def artifact_presence(row: dict[str, Any]) -> tuple[str, str]:
    run_dir = run_path(row)
    if run_dir is None:
        return "missing", "missing"
    run_available = "present"
    output = row.get("output_dir")
    if not isinstance(output, str) or not output:
        return run_available, "missing"
    judge = ROOT / output / "judge_manual_strict" / "judge_result.json"
    return run_available, "present" if judge.exists() else "missing"


def collect_rows() -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    notes: list[str] = []
    for spec in VARIANTS:
        summary_path = STATE_DIR / spec.summary_name
        summary = load_json(summary_path)
        if summary is None:
            notes.append(f"- `{summary_path.relative_to(ROOT)}`: missing or unreadable; all rows for `{spec.label}` are omitted.")
            continue
        for row in summary.get("rows", []):
            if not isinstance(row, dict):
                continue
            task_id = text(row.get("topic"))
            completed = int(row.get("status") == "completed")
            cd, ce, ra, overall = (number(row.get(key)) for key in ("code_development", "code_execution", "result_analysis", "overall_score"))
            scored = int(all(value is not None for value in (cd, ce, ra, overall)))
            error = summary_error(row)
            raw_failure = text(row.get("failure_type"), "")
            run_available, judge_available = artifact_presence(row)
            records.append({
                "task_id": task_id,
                "task_name": task_name(task_id),
                "task_domain": TASK_DOMAINS.get(task_id, "missing"),
                "variant": spec.label,
                "completed": completed,
                "scored": scored,
                "CD": cd,
                "CE": ce,
                "RA": ra,
                "overall_scored": overall,
                "overall_all_contribution": overall if scored else 0.0,
                "runtime_minutes": (number(row.get("duration_sec")) or 0.0) / 60 if number(row.get("duration_sec")) is not None else None,
                "llm_calls": number(row.get("llm_request_count")),
                "tokens": number(row.get("llm_total_tokens")),
                "repair_attempts": number(row.get("total_repair_count")),
                "first_failure_stage": first_failure_stage(row),
                "final_failure_category": normalize_failure(raw_failure, error),
                "final_error_summary": error,
                "raw_failure_type": text(raw_failure),
                "run_artifacts": run_available,
                "judge_artifact": judge_available,
                "summary_source": str(summary_path.relative_to(ROOT)).replace("\\", "/"),
                "run_dir": text(row.get("source_run_dir") or row.get("run_dir")),
                "output_dir": text(row.get("output_dir")),
            })
    return records, notes


def write_csv(path: Path, rows: Iterable[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def percentile(values: list[float], fraction: float) -> float:
    values = sorted(values)
    if not values:
        return math.nan
    position = (len(values) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def bootstrap_ci(full: list[float], ablation: list[float], rng: random.Random, samples: int) -> tuple[float, float]:
    differences: list[float] = []
    for _ in range(samples):
        indexes = [rng.randrange(len(full)) for _ in full]
        differences.append(statistics.fmean(full[index] - ablation[index] for index in indexes))
    return percentile(differences, 0.025), percentile(differences, 0.975)


def mcnemar_exact(b: int, c: int) -> float:
    total = b + c
    if not total:
        return 1.0
    return min(1.0, 2 * sum(math.comb(total, index) for index in range(min(b, c) + 1)) / 2**total)


def paired_statistics(records: list[dict[str, Any]], samples: int, seed: int) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for row in records:
        grouped.setdefault(row["task_id"], {})[row["variant"]] = row
    comparisons = ["Minimal Contract", "Raw-Log Repair", "No Repair Memory"]
    rows: list[dict[str, Any]] = []
    for index, ablation_name in enumerate(comparisons):
        pairs = [(variants.get("Full"), variants.get(ablation_name)) for variants in grouped.values()]
        pairs = [(full, ablation) for full, ablation in pairs if full is not None and ablation is not None]
        full_completion = [float(full["completed"]) for full, _ in pairs]
        ablation_completion = [float(ablation["completed"]) for _, ablation in pairs]
        full_scores = [float(full["overall_all_contribution"]) for full, _ in pairs]
        ablation_scores = [float(ablation["overall_all_contribution"]) for _, ablation in pairs]
        both_success = sum(full["completed"] and ablation["completed"] for full, ablation in pairs)
        full_success_ablation_failure = sum(full["completed"] and not ablation["completed"] for full, ablation in pairs)
        full_failure_ablation_success = sum(not full["completed"] and ablation["completed"] for full, ablation in pairs)
        rng = random.Random(seed + index)
        score_ci = bootstrap_ci(full_scores, ablation_scores, rng, samples)
        completion_ci = bootstrap_ci(full_completion, ablation_completion, rng, samples)
        rows.append({
            "comparison": f"Full vs {ablation_name}", "n_tasks": len(pairs),
            "both_success": both_success,
            "full_success_ablation_failure": full_success_ablation_failure,
            "full_failure_ablation_success": full_failure_ablation_success,
            "both_failure": sum(not full["completed"] and not ablation["completed"] for full, ablation in pairs),
            "completion_rate_full": statistics.fmean(full_completion),
            "completion_rate_ablation": statistics.fmean(ablation_completion),
            "completion_difference": statistics.fmean(full_completion) - statistics.fmean(ablation_completion),
            "completion_bootstrap_ci_low": completion_ci[0], "completion_bootstrap_ci_high": completion_ci[1],
            "mcnemar_exact_p": mcnemar_exact(full_success_ablation_failure, full_failure_ablation_success),
            "mean_overall_all_full": statistics.fmean(full_scores),
            "mean_overall_all_ablation": statistics.fmean(ablation_scores),
            "paired_mean_difference": statistics.fmean(full - ablation for full, ablation in zip(full_scores, ablation_scores)),
            "median_paired_difference": statistics.median(full - ablation for full, ablation in zip(full_scores, ablation_scores)),
            "overall_all_bootstrap_ci_low": score_ci[0], "overall_all_bootstrap_ci_high": score_ci[1],
        })
    return rows


def recovery_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_task: dict[str, dict[str, dict[str, Any]]] = {}
    for row in records:
        by_task.setdefault(row["task_id"], {})[row["variant"]] = row
    matrix: list[dict[str, Any]] = []
    for task_id, variants in sorted(by_task.items()):
        full, raw, memory = (variants.get(name) for name in ("Full", "Raw-Log Repair", "No Repair Memory"))
        if not all((full, raw, memory)):
            continue
        graphs = failure_graphs_from_record(full)
        observed = sorted({normalize_failure("", text(graph.get("primary_signal"), "")) for _, graph in graphs})
        observed_text = "; ".join(item for item in observed if item != "unknown") or ("none_observed" if full["run_artifacts"] == "present" else "missing")
        resolved = observed_text if full["completed"] and observed_text not in {"missing", "none_observed"} else "missing"
        matrix.append({
            "task_id": task_id,
            "full_completed": full["completed"], "without_diagnostics_completed": raw["completed"], "without_memory_completed": memory["completed"],
            "full_repair_attempts": full["repair_attempts"], "without_diagnostics_repair_attempts": raw["repair_attempts"], "without_memory_repair_attempts": memory["repair_attempts"],
            "full_failure_types_encountered": observed_text, "full_failure_types_resolved": resolved,
            "without_diagnostics_final_failure": raw["final_failure_category"], "without_memory_final_failure": memory["final_failure_category"],
            "repeated_failed_edit_detected": "missing", "memory_avoided_repeated_strategy": "missing",
            "full_run_artifacts": full["run_artifacts"], "raw_log_run_artifacts": raw["run_artifacts"], "memory_run_artifacts": memory["run_artifacts"],
        })
    return matrix


def strict_judge_dir(record: dict[str, Any]) -> Path | None:
    output_dir = text(record.get("output_dir"), "")
    if not output_dir or output_dir == "missing":
        return None
    candidate = ROOT / output_dir / "judge_manual_strict"
    return candidate if candidate.is_dir() else None


def _score_map(payload: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in payload.get("leaf_grades", []):
        if not isinstance(row, dict):
            continue
        leaf_id = text(row.get("id"), "")
        score = number(row.get("score"))
        if leaf_id and score is not None:
            result[leaf_id] = score
    return result


def _pearson(values_a: list[float], values_b: list[float]) -> float | None:
    if len(values_a) < 2 or len(values_a) != len(values_b):
        return None
    mean_a, mean_b = statistics.fmean(values_a), statistics.fmean(values_b)
    numerator = sum((a - mean_a) * (b - mean_b) for a, b in zip(values_a, values_b))
    denominator_a = sum((a - mean_a) ** 2 for a in values_a) ** 0.5
    denominator_b = sum((b - mean_b) ** 2 for b in values_b) ** 0.5
    if denominator_a == 0 or denominator_b == 0:
        return None
    return numerator / (denominator_a * denominator_b)


def _ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(indexed):
        end = index + 1
        while end < len(indexed) and indexed[end][1] == indexed[index][1]:
            end += 1
        average_rank = (index + 1 + end) / 2
        for original_index, _ in indexed[index:end]:
            ranks[original_index] = average_rank
        index = end
    return ranks


def write_evaluator_reliability(output_dir: Path, records: list[dict[str, Any]]) -> None:
    """Audit only the existing Full manual-strict reviewer artifacts.

    This deliberately reports score agreement, not an invalid claim of blinded
    inter-rater reliability: the stored reviewer prompt contains resolved paths.
    """

    rows: list[dict[str, Any]] = []
    values_a: list[float] = []
    values_b: list[float] = []
    absolute_differences: list[float] = []
    final_shifts: list[float] = []
    missing: list[str] = []
    for record in records:
        if record["variant"] != "Full":
            continue
        task_id = record["task_id"]
        judge_dir = strict_judge_dir(record)
        if judge_dir is None:
            missing.append(f"{task_id}: judge_manual_strict directory missing")
            continue
        reviewer_a = load_json(judge_dir / "reviewer_a.json")
        reviewer_b = load_json(judge_dir / "reviewer_b.json")
        final = load_json(judge_dir / "judge_result.json")
        disagreements = load_json(judge_dir / "disagreements.json") or {}
        if not reviewer_a or not reviewer_b or not final:
            missing.append(f"{task_id}: reviewer_a/reviewer_b/judge_result incomplete")
            continue
        grades_a, grades_b, final_grades = _score_map(reviewer_a), _score_map(reviewer_b), _score_map(final)
        shared = sorted(set(grades_a) & set(grades_b))
        if not shared:
            missing.append(f"{task_id}: no shared reviewer leaf grades")
            continue
        task_differences = [abs(grades_a[item] - grades_b[item]) for item in shared]
        values_a.extend(grades_a[item] for item in shared)
        values_b.extend(grades_b[item] for item in shared)
        absolute_differences.extend(task_differences)
        for leaf_id in shared:
            if leaf_id in final_grades:
                final_shifts.append(abs(final_grades[leaf_id] - (grades_a[leaf_id] + grades_b[leaf_id]) / 2))
        disagreement_items = disagreements.get("items") if isinstance(disagreements, dict) else []
        rows.append({
            "task_id": task_id,
            "leaf_pairs": len(shared),
            "mean_abs_difference": round(statistics.fmean(task_differences), 6),
            "median_abs_difference": round(statistics.median(task_differences), 6),
            "strict_disagreement_leaves": len(disagreement_items) if isinstance(disagreement_items, list) else 0,
            "adjudicated_leaves": len((final.get("adjudication") or {}).get("leaf_grades") or []) if isinstance(final, dict) else 0,
            "reviewer_a_model": text(reviewer_a.get("model")),
            "reviewer_b_model": text(reviewer_b.get("model")),
            "judge_dir": str(judge_dir.relative_to(ROOT)).replace("\\", "/"),
        })
    write_csv(
        output_dir / "arc_manual_strict_reliability.csv",
        rows,
        ["task_id", "leaf_pairs", "mean_abs_difference", "median_abs_difference", "strict_disagreement_leaves", "adjudicated_leaves", "reviewer_a_model", "reviewer_b_model", "judge_dir"],
    )
    summary = {
        "tasks_with_complete_dual_reviews": len(rows),
        "leaf_pairs": len(absolute_differences),
        "mean_absolute_difference": statistics.fmean(absolute_differences) if absolute_differences else None,
        "median_absolute_difference": statistics.median(absolute_differences) if absolute_differences else None,
        "pearson": _pearson(values_a, values_b),
        "spearman": _pearson(_ranks(values_a), _ranks(values_b)) if values_a else None,
        "disagreement_leaves": sum(int(row["strict_disagreement_leaves"]) for row in rows),
        "adjudicated_leaves": sum(int(row["adjudicated_leaves"]) for row in rows),
        "mean_absolute_final_shift_from_reviewer_mean": statistics.fmean(final_shifts) if final_shifts else None,
    }
    write_json = lambda path, value: path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_json(output_dir / "arc_manual_strict_reliability.json", {"summary": summary, "rows": rows, "missing": missing})
    markdown = [
        "# ARC Manual-Strict Evaluator Reliability Audit", "",
        "This is an audit of the stored `reviewer_a.json`, `reviewer_b.json`, `disagreements.json`, and final `judge_result.json` for the Full 25-task manual-strict bundle. It is not a blinded-rater study.", "",
        "## Aggregate", "",
        *[f"- `{key}`: {value}" for key, value in summary.items()], "",
        "## Blindness Limitation", "",
        "`benchmark/arc_bench/adapter.py:build_manual_strict_review_prompt` explicitly supplies resolved artifact paths and a final code block to reviewers. The saved prompts can therefore reveal run/output naming. Report this as dual independent review with adjudication, **not** blind evaluation.", "",
        "## Per-Task Evidence", "", markdown_table(rows, ["task_id", "leaf_pairs", "mean_abs_difference", "median_abs_difference", "strict_disagreement_leaves", "adjudicated_leaves", "reviewer_a_model", "reviewer_b_model"]), "",
        "## Missing Artifacts", "", *([f"- {item}" for item in missing] or ["- none"]), "",
    ]
    (output_dir / "arc_manual_strict_reliability.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")


def write_contract_provenance_audit(output_dir: Path, records: list[dict[str, Any]]) -> None:
    """Describe exactly which obligation provenance is persisted in current runs."""

    rows: list[dict[str, Any]] = []
    obligation_sources: Counter[str] = Counter()
    missing: list[str] = []
    for record in records:
        if record["variant"] != "Full":
            continue
        run_dir = ROOT / text(record.get("run_dir"), "")
        contract = load_json(run_dir / "code_task" / "meta" / "task_contract.json")
        if not contract:
            missing.append(f"{record['task_id']}: task_contract.json missing")
            continue
        implementation = contract.get("implementation_contract") if isinstance(contract, dict) else {}
        obligations = implementation.get("obligations") if isinstance(implementation, dict) else []
        source_counts = Counter()
        for obligation in obligations if isinstance(obligations, list) else []:
            if isinstance(obligation, dict):
                source = text(obligation.get("source"), "missing")
                source_counts[source] += 1
                obligation_sources[source] += 1
        source = contract.get("source") if isinstance(contract.get("source"), dict) else {}
        rows.append({
            "task_id": record["task_id"],
            "contract_schema": text(contract.get("schema_version")),
            "contract_source_kind": text(source.get("kind")),
            "task_file": text(source.get("task_file")),
            "obligation_count": sum(source_counts.values()),
            "obligation_source_counts": "; ".join(f"{key}={value}" for key, value in sorted(source_counts.items())),
            "has_per_obligation_original_source_kind": "no",
            "contract_path": str((run_dir / "code_task" / "meta" / "task_contract.json").relative_to(ROOT)).replace("\\", "/"),
        })
    write_csv(
        output_dir / "arc_contract_provenance.csv",
        rows,
        ["task_id", "contract_schema", "contract_source_kind", "task_file", "obligation_count", "obligation_source_counts", "has_per_obligation_original_source_kind", "contract_path"],
    )
    markdown = [
        "# ARC Contract Provenance Audit", "",
        "## Persisted Provenance", "",
        "The run-level contract persists `source.kind` and `source.task_file`. Each normalized implementation obligation also has a `source` field, but the inspected Full runs label derived entries as `task_contract`; they do not preserve a machine-readable original-source category such as `rubric`, `metric_config`, `artifact_config`, or `adapter_metadata`.", "",
        f"- Full contracts available: **{len(rows)}/25**.",
        f"- Persisted obligation source labels: **{dict(sorted(obligation_sources.items()))}**.",
        "- Exact per-obligation source-kind for historical runs: **NOT RECOVERABLE** from `task_contract.json` alone.",
        "- Consequence: the paper may describe deterministic construction from task/config/adapter metadata, but may not report a historical per-source obligation coverage rate without a new provenance field and fresh runs.", "",
        "## Per-Task Evidence", "", markdown_table(rows, ["task_id", "contract_source_kind", "task_file", "obligation_count", "obligation_source_counts", "has_per_obligation_original_source_kind"]), "",
        "## Missing Artifacts", "", *([f"- {item}" for item in missing] or ["- none"]), "",
    ]
    (output_dir / "arc_contract_provenance.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")


def write_recovery_quality_audit(output_dir: Path, records: list[dict[str, Any]]) -> None:
    """Separate reliability transitions from quality conditional on shared completion."""

    by_task: dict[str, dict[str, dict[str, Any]]] = {}
    for row in records:
        by_task.setdefault(row["task_id"], {})[row["variant"]] = row
    comparisons = [("Raw-Log Repair", "raw_log"), ("No Repair Memory", "no_memory")]
    aggregate_rows: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []
    for label, suffix in comparisons:
        paired = [(task, data["Full"], data[label]) for task, data in sorted(by_task.items()) if "Full" in data and label in data]
        shared = [(task, full, ablation) for task, full, ablation in paired if full["completed"] and ablation["completed"]]
        wins = ties = losses = 0
        deltas: dict[str, list[float]] = {"CD": [], "CE": [], "RA": [], "Overall": []}
        for task, full, ablation in shared:
            overall_delta = float(full["overall_scored"]) - float(ablation["overall_scored"])
            wins += overall_delta > 1e-9
            losses += overall_delta < -1e-9
            ties += abs(overall_delta) <= 1e-9
            row = {"comparison": label, "task_id": task, "overall_delta_full_minus_ablation": overall_delta}
            for source, target in (("CD", "CD"), ("CE", "CE"), ("RA", "RA"), ("overall_scored", "Overall")):
                delta = float(full[source]) - float(ablation[source])
                deltas[target].append(delta)
                row[f"{target}_delta"] = delta
            detail_rows.append(row)
        full_only = sum(full["completed"] and not ablation["completed"] for _, full, ablation in paired)
        ablation_only = sum(not full["completed"] and ablation["completed"] for _, full, ablation in paired)
        aggregate_rows.append({
            "comparison": label,
            "tasks": len(paired),
            "shared_completed": len(shared),
            "full_only_completed": full_only,
            "ablation_only_completed": ablation_only,
            "both_failed": sum(not full["completed"] and not ablation["completed"] for _, full, ablation in paired),
            "mean_CD_delta_shared": statistics.fmean(deltas["CD"]) if deltas["CD"] else None,
            "mean_CE_delta_shared": statistics.fmean(deltas["CE"]) if deltas["CE"] else None,
            "mean_RA_delta_shared": statistics.fmean(deltas["RA"]) if deltas["RA"] else None,
            "mean_Overall_delta_shared": statistics.fmean(deltas["Overall"]) if deltas["Overall"] else None,
            "overall_wins_ties_losses": f"{wins}/{ties}/{losses}",
        })
    write_csv(output_dir / "arc_recovery_quality.csv", aggregate_rows, list(aggregate_rows[0]) if aggregate_rows else [])
    write_csv(output_dir / "arc_recovery_quality_per_task.csv", detail_rows, list(detail_rows[0]) if detail_rows else [])
    markdown = [
        "# ARC Recovery Reliability and Conditional-Quality Audit", "",
        "Completion transitions answer a reliability question. CD/CE/RA/Overall deltas below are calculated only where both Full and the ablation completed, so they answer a separate conditional-quality question.", "",
        markdown_table(aggregate_rows, list(aggregate_rows[0]) if aggregate_rows else []), "",
        "Per-task paired values: `arc_recovery_quality_per_task.csv`.",
    ]
    (output_dir / "arc_recovery_quality.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")


def failure_graphs_from_record(record: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    candidate = {"source_run_dir": record.get("run_dir")}
    return failure_graphs(candidate)


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows:
        body.append("| " + " | ".join(str(row.get(column, "")).replace("|", "\\|") for column in columns) + " |")
    return "\n".join([header, divider, *body])


def find_first(values: Any, needle: str) -> str:
    if isinstance(values, list):
        for value in values:
            if needle.lower() in text(value, "").lower():
                return text(value)
    return "missing"


def write_trace(output_dir: Path) -> None:
    task_id = "ML07"
    run_dir = ROOT / "benchmark" / "arc_bench" / "runs" / "ml" / task_id / "20260706-125821-arc-bench-ml07"
    contract = load_json(run_dir / "code_task" / "meta" / "task_contract.json") or {}
    graph = load_json(run_dir / "code_task" / "run" / "patched" / "attempts" / "attempt-001" / "failure_graph.json") or {}
    plan = load_json(run_dir / "code_task" / "meta" / "run_repair_plan.json") or {}
    repair = load_json(run_dir / "code_task" / "meta" / "run_repair.json") or {}
    execution = load_json(run_dir / "code_task" / "run" / "patched" / "execution_report.json") or {}
    metrics = load_json(run_dir / "code_task" / "run" / "patched" / "metrics.json") or {}
    data_requirement = find_first(contract.get("data_requirements"), "Required Datasets")
    trace = {
        "task_id": task_id,
        "original_task": task_name(task_id),
        "relevant_contract_field": "data_requirements",
        "exact_obligation": data_requirement,
        "stage_specific_view": "runtime execution of generated_project/main.py; the contract does not expose an explicit source_kind field ID.",
        "generated_artifact_or_code_before_repair": "generated_project/config.py, datasets.py, experiment.py, main.py, analysis.py, and pipelines.py (see failure graph candidate_files).",
        "detected_violation": text(graph.get("primary_signal")),
        "validator_type": "runtime failure graph (structured diagnostic record)",
        "finding_severity": "runtime-blocking",
        "diagnosis": text(plan.get("root_cause")),
        "target_file": "; ".join(str(value) for value in plan.get("target_files", []) if value) or "missing",
        "repair_action": text(plan.get("repair_strategy")),
        "repair_diff_or_summary": "; ".join(str(value) for value in repair.get("changed_files", []) if value) or "missing",
        "guard_check_result": text(repair.get("status")),
        "rerun_result": text(execution.get("status") or execution.get("return_code")),
        "produced_metric_or_artifact": "metrics.json: present" if metrics else "missing",
        "final_analysis_statement": "missing: this offline trace does not infer a report statement from the task score.",
        "explicit_contract_to_failure_link": "No explicit requirement ID links the high-level data requirement to source_kind. The relation is an implementation-level producer/consumer diagnosis, not an explicit contract-ID edge.",
        "artifact_paths": {
            "task_contract": str((run_dir / "code_task" / "meta" / "task_contract.json").relative_to(ROOT)).replace("\\", "/"),
            "failure_graph": str((run_dir / "code_task" / "run" / "patched" / "attempts" / "attempt-001" / "failure_graph.json").relative_to(ROOT)).replace("\\", "/"),
            "repair_plan": str((run_dir / "code_task" / "meta" / "run_repair_plan.json").relative_to(ROOT)).replace("\\", "/"),
            "repair_record": str((run_dir / "code_task" / "meta" / "run_repair.json").relative_to(ROOT)).replace("\\", "/"),
            "execution_report": str((run_dir / "code_task" / "run" / "patched" / "execution_report.json").relative_to(ROOT)).replace("\\", "/"),
            "metrics": str((run_dir / "code_task" / "run" / "patched" / "metrics.json").relative_to(ROOT)).replace("\\", "/"),
        },
    }
    (output_dir / "contract_repair_trace.json").write_text(json.dumps(trace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = ["# Contract-to-Repair Trace", "", "This trace is extracted from existing ML07 artifacts only. Fields with no direct evidence are `missing`.", ""]
    for key, value in trace.items():
        if key == "artifact_paths":
            lines.append("## Artifact Paths")
            lines.extend(f"- `{name}`: `{path}`" for name, path in value.items())
        else:
            lines.extend([f"## {key.replace('_', ' ').title()}", str(value), ""])
    (output_dir / "contract_repair_trace.md").write_text("\n".join(lines), encoding="utf-8")


def write_notes(output_dir: Path, notes: list[str]) -> None:
    lines = [
        "# Experiment Notes", "",
        "## Scope", "",
        "This supplement was generated offline from downloaded ARC-Bench batch summaries and locally available run artifacts. It did not rerun tasks, scores, judges, or external systems.", "",
        "## Evaluation Separation", "",
        "The rows here use the `manual-strict` controlled-ablation summaries. They must not be mixed with the separate native-judge main-result bundle in a single numerical table.", "",
        "## Missingness Policy", "",
        "A missing local run or judge artifact is recorded as `missing`. Aggregate scores are retained only when they exist in a downloaded batch summary; no score, failure stage, repair behavior, model version, commit hash, or artifact relation is inferred from another run.", "",
        "## Bootstrap Interpretation", "",
        "Bootstrap intervals resample benchmark tasks. They measure variation across these tasks, not run-to-run LLM sampling variance.", "",
        "## Source Summaries", "",
    ]
    lines.extend(f"- `{spec.summary_name}`: {spec.description}" for spec in VARIANTS)
    if notes:
        lines.extend(["", "## Read Errors", *notes])
    (output_dir / "experiment_notes.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_overview(output_dir: Path, records: list[dict[str, Any]]) -> None:
    by_variant: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        by_variant.setdefault(row["variant"], []).append(row)
    compact_rows: list[dict[str, Any]] = []
    for spec in VARIANTS:
        rows = by_variant.get(spec.label, [])
        if not rows:
            continue
        scored = [row for row in rows if row["scored"]]
        compact_rows.append({
            "variant": spec.label,
            "tasks": len(rows),
            "completed": sum(row["completed"] for row in rows),
            "scored": len(scored),
            "CD_scored": f"{statistics.fmean(row['CD'] for row in scored):.4f}" if scored else "missing",
            "CE_scored": f"{statistics.fmean(row['CE'] for row in scored):.4f}" if scored else "missing",
            "RA_scored": f"{statistics.fmean(row['RA'] for row in scored):.4f}" if scored else "missing",
            "Overall_scored": f"{statistics.fmean(row['overall_scored'] for row in scored):.4f}" if scored else "missing",
            "Overall_all": f"{statistics.fmean(row['overall_all_contribution'] for row in rows):.4f}",
            "mean_runtime_min": f"{statistics.fmean(row['runtime_minutes'] for row in rows if row['runtime_minutes'] is not None):.2f}",
            "mean_tokens": f"{statistics.fmean(row['tokens'] for row in rows if row['tokens'] is not None):.0f}",
            "mean_repairs": f"{statistics.fmean(row['repair_attempts'] for row in rows if row['repair_attempts'] is not None):.2f}",
        })
    lines = [
        "# Existing ARC-Bench Result Overview", "",
        "This is an offline consolidation of the downloaded `manual-strict` batch summaries. It is a controlled-ablation evidence table, not the native-judge main-result table.", "",
        "## Aggregate Results", "", markdown_table(compact_rows, list(compact_rows[0])) if compact_rows else "missing", "",
        "## Evidence Boundaries", "",
        "- All Full rows have a locally present manual-strict judge artifact.",
        "- Minimal Contract has summary-level scores but no locally downloaded per-task judge artifacts; do not use it for leaf-level judge rationale analysis until those directories are retrieved.",
        "- Compact/Complex PTC variants are intentionally excluded from this consolidation because their later repair-extension provenance is not clean enough for paper use.",
        "- `Overall_scored` averages only tasks with a score. `Overall_all` uses a zero contribution for an unscored or failed task so it reflects completion failures as well.",
        "- The manual-strict state records `claude-opus-4-6,gpt-5.4` as reviewer models and `gpt-5.4` as adjudicator for Full. Other historical model, commit, and environment details are not inferred when absent from the downloaded state/logs.",
        "",
        "## Recommended Paper Use", "",
        "Use the native-judge full-run bundle for the principal ARC-Bench table. Use this manual-strict table for within-system controlled ablations only, preserving the stated score profile and failed-task accounting.",
    ]
    (output_dir / "arc_existing_results_overview.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_repeated_subset_selection(output_dir: Path, records: list[dict[str, Any]]) -> None:
    by_key = {(row["task_id"], row["variant"]): row for row in records}
    chosen = [
        ("ML02", "tabular learning"), ("ML07", "NLP"), ("ML03", "optimization"),
        ("ML21", "causal discovery"), ("ML23", "learning to rank"),
    ]
    rows: list[dict[str, Any]] = []
    for task_id, coverage in chosen:
        full = by_key.get((task_id, "Full"))
        minimal = by_key.get((task_id, "Minimal Contract"))
        rows.append({
            "task_id": task_id,
            "domain": coverage,
            "original_full_result": text(full.get("overall_scored") if full else None),
            "original_minimal_result": text(minimal.get("overall_scored") if minimal else None),
            "full_completed": full.get("completed") if full else "missing",
            "minimal_completed": minimal.get("completed") if minimal else "missing",
            "estimated_cost": "missing: no cost forecast is inferred from historical token totals",
            "selection_reason": "Covers a distinct task family and has an existing Full-versus-Minimal result for reproducibility checking.",
        })
    lines = [
        "# Repeated ARC Subset Selection", "",
        "This is a prospective selection record only; it contains no new runs. The five tasks span tabular learning, NLP, optimization, causal discovery, and ranking. Historical results are included as context, not counted as independent repetitions.", "",
        markdown_table(rows, list(rows[0])), "",
        "For a future repeated-run study, keep model names, prompts, environment, timeout, repair budget, validators, and manual-strict judge configuration fixed between Full and Minimal Contract. If the provider has no seed control, describe outcomes as independent workflow runs rather than seeded runs.",
    ]
    (output_dir / "repeated_subset_selection.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_failure_taxonomy(output_dir: Path, records: list[dict[str, Any]]) -> None:
    counts: dict[str, Counter[str]] = {}
    for row in records:
        if row["completed"]:
            continue
        counts.setdefault(row["variant"], Counter())[row["final_failure_category"]] += 1
    categories = ["schema/value mismatch", "interface mismatch", "missing artifact", "missing metric", "syntax/import", "runtime/resource", "invalid analysis", "unknown"]
    rows = []
    for spec in VARIANTS:
        counter = counts.get(spec.label, Counter())
        rows.append({"variant": spec.label, "failed_tasks": sum(counter.values()), **{category: counter[category] for category in categories}})
    lines = [
        "# ARC-Bench Final Failure Taxonomy", "",
        "Counts classify only tasks whose downloaded batch summary has final status other than `completed`. They are normalized from recorded `failure_type` and error text. They do not establish that a given repair mechanism caused a failure or a recovery.", "",
        markdown_table(rows, ["variant", "failed_tasks", *categories]), "",
        "`warning/noise` and other non-specific historical labels remain `unknown` unless the captured error text supports a more specific normalization.",
    ]
    (output_dir / "arc_failure_taxonomy.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_consolidated_documents(output_dir: Path) -> None:
    """Keep Paper-ADD human-facing material intentionally small.

    CSV/JSON are retained as audit inputs.  The intermediate Markdown reports
    are combined into one reading document so the paper workflow does not turn
    into a directory of tiny notes.
    """
    sections = [
        ("Existing Results", "arc_existing_results_overview.md"),
        ("Paired Statistics", "arc_paired_statistics.md"),
        ("Repair Summary", "arc_recovery_summary.md"),
        ("Failure Taxonomy", "arc_failure_taxonomy.md"),
        ("Evidence Notes", "experiment_notes.md"),
    ]
    content = ["# ARC-Bench Existing Evidence", "", "Offline consolidation of existing manual-strict ablation artifacts. It contains no new runs.", ""]
    for _, filename in sections:
        path = output_dir / filename
        if not path.exists():
            continue
        body = path.read_text(encoding="utf-8").strip()
        body = re.sub(r"^# .*?\n", "", body, count=1)
        content.extend([body, ""])
    (output_dir / "ARC_EVIDENCE_SUMMARY.md").write_text("\n".join(content).strip() + "\n", encoding="utf-8")

    runbook = """# ARC-Bench Repeated-Subset Runbook

## Purpose

Run only the prospective repeated study: Full versus Minimal Contract on ML02, ML03, ML07, ML21, and ML23. Existing runs are historical context, not independent repetitions.

## Freeze Before Running

On the server, use the same checked-out commit for all four batches and record `git rev-parse HEAD` beside the downloaded state files. Confirm the configured code-task models are available before launching; do not silently substitute a different writer model.

The commands deliberately leave repair/planning budgets unset, so each topic keeps the committed `code_task.toml` defaults. The sole experimental difference is `--contract-context minimal`.

## Independent Runs

Run each command once. The two state files per configuration represent two new independent workflow runs.

```bash
# Full, repetition 1
uv run python benchmark/arc_bench/batch_runner.py run \\
  --topics ML02 ML03 ML07 ML21 ML23 \\
  --analyze --score --score-profile manual-strict \\
  --strict-reviewer-models claude-opus-4-6,gpt-5.4 \\
  --strict-reviewer-apis chat,responses \\
  --strict-adjudicator-model gpt-5.4 \\
  --state-file benchmark/arc_bench/batch_state/repeat-full-r1-manual-strict.json

# Full, repetition 2: same command; change only the state file to repeat-full-r2-manual-strict.json

# Minimal Contract, repetition 1
uv run python benchmark/arc_bench/batch_runner.py run \\
  --topics ML02 ML03 ML07 ML21 ML23 \\
  --contract-context minimal \\
  --analyze --score --score-profile manual-strict \\
  --strict-reviewer-models claude-opus-4-6,gpt-5.4 \\
  --strict-reviewer-apis chat,responses \\
  --strict-adjudicator-model gpt-5.4 \\
  --state-file benchmark/arc_bench/batch_state/repeat-minimal-r1-manual-strict.json

# Minimal Contract, repetition 2: same command; change only the state file to repeat-minimal-r2-manual-strict.json
```

## Interrupted Batches

For the same state file, rerun only unfinished topics:

```bash
uv run python benchmark/arc_bench/batch_runner.py retry-unfinished \\
  --state-file benchmark/arc_bench/batch_state/repeat-full-r1-manual-strict.json \\
  --topics ML02 ML03 ML07 ML21 ML23 \\
  --analyze --score --score-profile manual-strict \\
  --strict-reviewer-models claude-opus-4-6,gpt-5.4 \\
  --strict-reviewer-apis chat,responses \\
  --strict-adjudicator-model gpt-5.4 \\
  --resume-existing
```

For a Minimal Contract batch, add `--contract-context minimal`. Do not add `--extend-repair-rounds` unless the planned study explicitly changes the repair budget; that would create a different experimental condition.

## Summaries and Download

After each state completes, create a manual-strict summary:

```bash
uv run python benchmark/arc_bench/batch_runner.py summarize \\
  --state-file benchmark/arc_bench/batch_state/repeat-full-r1-manual-strict.json \\
  --judge-source manual-strict
```

Download all four state JSON files, their generated summary JSON/Markdown files, and the corresponding `runs/ml/` plus `submissions/ml/` directories for these five topics. This preserves task-level scores, repair counts, failure evidence, and judge provenance for later repeated-run aggregation.

## Reporting Rule

Report every independent run, including failures. Use completion rate and `Overall_all` (unscored/failed task contribution equals zero) beside scored-only averages. Do not treat the original historical Full/Minimal runs as a third controlled repetition unless their model, commit, prompts, environment, timeout, repair budget, and judge configuration are documented as identical.
"""
    (output_dir / "ARC_EXPERIMENT_RUNBOOK.md").write_text(runbook, encoding="utf-8")

    for filename in (
        "arc_existing_results_overview.md", "arc_paired_statistics.md", "arc_recovery_summary.md",
        "arc_failure_taxonomy.md", "experiment_notes.md", "repeated_subset_selection.md",
    ):
        (output_dir / filename).unlink(missing_ok=True)


def collect_repeated_rows() -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    rows: list[dict[str, Any]] = []
    excluded: dict[str, list[str]] = {}
    for variant, repetition, filename, origin in REPEATED_SUMMARIES:
        source = STATE_DIR / filename
        summary = load_json(source)
        if summary is None:
            continue
        all_rows = {text(row.get("topic")): row for row in summary.get("rows", []) if isinstance(row, dict)}
        excluded[f"{variant} {repetition}"] = sorted(task for task in all_rows if task not in REPEATED_TASKS)
        for task_id in REPEATED_TASKS:
            row = all_rows.get(task_id)
            if row is None:
                rows.append({
                    "task_id": task_id, "variant": variant, "repetition": repetition,
                    "run_origin": origin,
                    "state_summary": str(source.relative_to(ROOT)).replace("\\", "/"),
                    "completed": 0, "scored": 0, "CD": None, "CE": None, "RA": None,
                    "overall": None, "overall_all_contribution": 0.0, "runtime_minutes": None,
                    "llm_calls": None, "input_tokens": None, "output_tokens": None, "tokens": None,
                    "repair_attempts": None, "execution_attempts": None, "failed_execution_attempts": None,
                    "failure_type": "missing", "final_error_summary": "missing row in summary",
                })
                continue
            cd, ce, ra, overall = (number(row.get(key)) for key in ("code_development", "code_execution", "result_analysis", "overall_score"))
            scored = int(all(value is not None for value in (cd, ce, ra, overall)))
            rows.append({
                "task_id": task_id, "variant": variant, "repetition": repetition,
                "run_origin": origin,
                "state_summary": str(source.relative_to(ROOT)).replace("\\", "/"),
                "completed": int(row.get("status") == "completed"), "scored": scored,
                "CD": cd, "CE": ce, "RA": ra, "overall": overall,
                "overall_all_contribution": overall if scored else 0.0,
                "runtime_minutes": (number(row.get("duration_sec")) or 0.0) / 60 if number(row.get("duration_sec")) is not None else None,
                "llm_calls": number(row.get("llm_request_count")),
                "input_tokens": number(row.get("llm_input_tokens")),
                "output_tokens": number(row.get("llm_output_tokens")),
                "tokens": number(row.get("llm_total_tokens")),
                "repair_attempts": number(row.get("total_repair_count")),
                "execution_attempts": number(row.get("execution_attempts")),
                "failed_execution_attempts": number(row.get("failed_execution_attempts")),
                "failure_type": text(row.get("failure_type")), "final_error_summary": summary_error(row),
            })
    return rows, excluded


def repeated_command_provenance() -> list[str]:
    """Verify the intended Full/Minimal execution difference from state data."""
    findings: list[str] = []
    for variant, repetition, filename, origin in REPEATED_SUMMARIES:
        if origin != "new":
            continue
        state_path = STATE_DIR / f"{filename.removesuffix('.summary.json')}.json"
        state = load_json(state_path)
        commands: list[list[str]] = []
        if state is not None:
            topics = state.get("topics", {})
            for task_id in REPEATED_TASKS:
                topic = topics.get(task_id, {}) if isinstance(topics, dict) else {}
                for command in topic.get("commands", []) if isinstance(topic, dict) else []:
                    if isinstance(command, list) and "execute" in command:
                        commands.append(command)
                        break
        expected_flag = variant == "Minimal Contract"
        observed = len(commands) == len(REPEATED_TASKS) and all(("--contract-context" in command) == expected_flag for command in commands)
        exact_minimal = not expected_flag or all(
            command[command.index("--contract-context") + 1] == "minimal"
            for command in commands if "--contract-context" in command
        )
        if observed and exact_minimal:
            detail = "only Minimal commands include `--contract-context minimal`" if expected_flag else "commands contain no `--contract-context` override"
            findings.append(f"- `{variant} {repetition}`: verified; {detail}.")
        else:
            findings.append(f"- `{variant} {repetition}`: missing or inconsistent command provenance.")
    return findings


def task_cluster_bootstrap(full: dict[str, float], minimal: dict[str, float], *, seed: int, samples: int) -> tuple[float, float]:
    tasks = sorted(set(full) & set(minimal))
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(samples):
        selected = [rng.choice(tasks) for _ in tasks]
        values.append(statistics.fmean(full[task] - minimal[task] for task in selected))
    return percentile(values, 0.025), percentile(values, 0.975)


def _repetition_aggregate(rows: list[dict[str, Any]], *, label: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["variant"], []).append(row)
    aggregate_rows: list[dict[str, Any]] = []
    for variant in ("Full", "Minimal Contract"):
        variant_rows = grouped.get(variant, [])
        aggregate_rows.append({
            "view": label,
            "variant": variant,
            "runs": len(variant_rows),
            "completed": sum(row["completed"] for row in variant_rows),
            "completion_rate": f"{statistics.fmean(row['completed'] for row in variant_rows):.3f}",
            "CD": f"{statistics.fmean(row['CD'] for row in variant_rows if row['CD'] is not None):.4f}",
            "CE": f"{statistics.fmean(row['CE'] for row in variant_rows if row['CE'] is not None):.4f}",
            "RA": f"{statistics.fmean(row['RA'] for row in variant_rows if row['RA'] is not None):.4f}",
            "Overall_all": f"{statistics.fmean(row['overall_all_contribution'] for row in variant_rows):.4f}",
            "mean_runtime_min": f"{statistics.fmean(row['runtime_minutes'] for row in variant_rows if row['runtime_minutes'] is not None):.2f}",
            "mean_llm_calls": f"{statistics.fmean(row['llm_calls'] for row in variant_rows if row['llm_calls'] is not None):.1f}",
            "mean_tokens": f"{statistics.fmean(row['tokens'] for row in variant_rows if row['tokens'] is not None):.0f}",
            "mean_repairs": f"{statistics.fmean(row['repair_attempts'] for row in variant_rows if row['repair_attempts'] is not None):.2f}",
        })
    return aggregate_rows


def _task_paired_summary(rows: list[dict[str, Any]], *, seed: int, samples: int) -> tuple[list[dict[str, Any]], list[float], tuple[float, float]]:
    by_task: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        by_task.setdefault(row["task_id"], {}).setdefault(row["variant"], []).append(row["overall_all_contribution"])
    full_values = {task: statistics.fmean(values["Full"]) for task, values in by_task.items() if "Full" in values and "Minimal Contract" in values}
    minimal_values = {task: statistics.fmean(values["Minimal Contract"]) for task, values in by_task.items() if "Full" in values and "Minimal Contract" in values}
    paired_differences = [full_values[task] - minimal_values[task] for task in full_values]
    ci = task_cluster_bootstrap(full_values, minimal_values, seed=seed, samples=samples)
    task_rows = [
        {"task_id": task, "Full_mean_Overall_all": f"{full_values[task]:.4f}", "Minimal_mean_Overall_all": f"{minimal_values[task]:.4f}", "Full_minus_Minimal": f"{full_values[task] - minimal_values[task]:.4f}"}
        for task in sorted(full_values)
    ]
    return task_rows, paired_differences, ci


def write_repeated_results(output_dir: Path, *, samples: int, seed: int) -> None:
    rows, excluded = collect_repeated_rows()
    if not rows:
        return
    columns = ["task_id", "variant", "repetition", "run_origin", "state_summary", "completed", "scored", "CD", "CE", "RA", "overall", "overall_all_contribution", "runtime_minutes", "llm_calls", "input_tokens", "output_tokens", "tokens", "repair_attempts", "execution_attempts", "failed_execution_attempts", "failure_type", "final_error_summary"]
    write_csv(output_dir / "arc_repeated_subset_runs.csv", rows, columns)
    new_rows = [row for row in rows if row["run_origin"] == "new"]
    all_rows = list(rows)
    aggregate_rows = _repetition_aggregate(new_rows, label="new r1/r2")
    historical_aggregate_rows = _repetition_aggregate(all_rows, label="historical r0 + new r1/r2")
    task_rows, paired_differences, (ci_low, ci_high) = _task_paired_summary(new_rows, seed=seed, samples=samples)
    historical_task_rows, historical_differences, (historical_ci_low, historical_ci_high) = _task_paired_summary(all_rows, seed=seed + 1, samples=samples)
    excluded_lines = []
    for label, items in excluded.items():
        if label.endswith(" r0"):
            excluded_lines.append(f"- `{label}`: historical all-topic source; only the five preselected tasks were retained.")
        else:
            excluded_lines.append(f"- `{label}` excluded topic(s): {', '.join(items) if items else 'none'}.")
    command_lines = repeated_command_provenance()
    content = [
        "# ARC-Bench Repeated Subset Results", "",
        "## Design", "",
        "Two new independent workflow runs were collected for each of Full and Minimal Contract on ML02, ML03, ML07, ML21, and ML23. A separately labelled historical observation (`r0`) is also retained for descriptive three-observation reporting. The score source is `manual-strict` for every row.", "",
        "## Strict New-Repetition Result (Primary)", "", markdown_table(aggregate_rows, list(aggregate_rows[0])), "",
        "## Task-Clustered Paired Overall_all Difference", "",
        f"- Mean Full minus Minimal Contract: **{statistics.fmean(paired_differences):.4f}**.",
        f"- Median task mean difference: **{statistics.median(paired_differences):.4f}**.",
        f"- Task-cluster bootstrap 95% interval: **[{ci_low:.4f}, {ci_high:.4f}]**.",
        "- The interval resamples the five tasks after averaging their two repetitions; it quantifies task variation, not provider/model sampling variance.",
        "- Both variants completed all 10 included runs, so this subset does not distinguish completion rates.", "",
        "## Per-Task Means", "", markdown_table(task_rows, list(task_rows[0])), "",
        "## Historical-Inclusive Three-Observation View (Secondary)", "",
        "This view uses r0, r1, and r2. It is useful for a descriptive three-observation appendix, but is not the primary repeated-run estimate: r0 Full originates from a June run that was later manually re-scored, whereas r0 Minimal originates from a July execution state. The downloaded r0 Full state retains manual-strict judge provenance but not the original execute command, so exact code/prompt/environment equality cannot be certified from the available files.", "",
        markdown_table(historical_aggregate_rows, list(historical_aggregate_rows[0])), "",
        f"- Historical-inclusive mean Full minus Minimal Contract: **{statistics.fmean(historical_differences):.4f}**.",
        f"- Historical-inclusive task-cluster bootstrap 95% interval: **[{historical_ci_low:.4f}, {historical_ci_high:.4f}]**.",
        "", "### Historical-Inclusive Per-Task Means", "", markdown_table(historical_task_rows, list(historical_task_rows[0])), "",
        "## Scope Filtering", "", "Only ML02, ML03, ML07, ML21, and ML23 are included in every table and statistic above:", *excluded_lines, "",
        "## Command Provenance", "", "The downloaded state records show the intended execution contrast for every included task:", *command_lines, "",
        "## Repair and Failure Interpretation", "",
        "The downloaded summaries contain repair counts, execution attempts, and historical failure-type labels for completed runs. Without corresponding run directories, these labels are treated as intermediate workflow signals only; the report does not assert a per-failure repair cause or recovery mechanism.",
        "",
        "Raw per-run evidence is in `arc_repeated_subset_runs.csv`.",
    ]
    (output_dir / "arc_repeated_subset_summary.md").write_text("\n".join(content) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260724)
    args = parser.parse_args()
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    records, notes = collect_rows()
    columns = ["task_id", "task_name", "task_domain", "variant", "completed", "scored", "CD", "CE", "RA", "overall_scored", "overall_all_contribution", "runtime_minutes", "llm_calls", "tokens", "repair_attempts", "first_failure_stage", "final_failure_category", "final_error_summary", "raw_failure_type", "run_artifacts", "judge_artifact", "summary_source", "run_dir", "output_dir"]
    write_csv(output_dir / "arc_per_task_results.csv", records, columns)

    stats = paired_statistics(records, args.bootstrap_samples, args.seed)
    stat_columns = list(stats[0]) if stats else []
    write_csv(output_dir / "arc_paired_statistics.csv", stats, stat_columns)
    statistics_md = "# ARC-Bench Paired Statistics\n\n"
    statistics_md += "All comparisons are task-paired against the Full workflow. `Overall_all` assigns 0 only to unscored/failed tasks; `overall_scored` remains blank in the raw table when no score exists.\n\n"
    statistics_md += markdown_table(stats, ["comparison", "n_tasks", "both_success", "full_success_ablation_failure", "full_failure_ablation_success", "both_failure", "completion_rate_full", "completion_rate_ablation", "completion_difference", "mcnemar_exact_p", "mean_overall_all_full", "mean_overall_all_ablation", "paired_mean_difference", "median_paired_difference", "overall_all_bootstrap_ci_low", "overall_all_bootstrap_ci_high"])
    statistics_md += "\n\nBootstrap intervals measure variation across benchmark tasks, not run-to-run LLM sampling variance.\n"
    (output_dir / "arc_paired_statistics.md").write_text(statistics_md, encoding="utf-8")

    recovery = recovery_rows(records)
    recovery_columns = list(recovery[0]) if recovery else []
    write_csv(output_dir / "arc_recovery_matrix.csv", recovery, recovery_columns)
    categories = Counter()
    for row in recovery:
        for item in str(row["full_failure_types_encountered"]).split("; "):
            if item not in {"missing", "none_observed", "unknown"}:
                categories[item] += 1
    full_success_raw_fail = sum(row["full_completed"] and not row["without_diagnostics_completed"] for row in recovery)
    full_success_memory_fail = sum(row["full_completed"] and not row["without_memory_completed"] for row in recovery)
    recovery_md = ["# ARC-Bench Recovery Summary", "", f"- Full completed while Raw-Log Repair failed: **{full_success_raw_fail}** task(s).", f"- Full completed while No Repair Memory failed: **{full_success_memory_fail}** task(s).", f"- Full average repair attempts: **{statistics.fmean(float(row['full_repair_attempts']) for row in recovery):.2f}**.", "", "## Failure Categories Observed in Available Full Run Artifacts", ""]
    recovery_md.extend(f"- `{category}`: {count}" for category, count in sorted(categories.items()))
    recovery_md.extend(["", "`repeated_failed_edit_detected` and `memory_avoided_repeated_strategy` are intentionally `missing` unless a downloaded log explicitly proves them; this extraction does not infer those causal claims."])
    (output_dir / "arc_recovery_summary.md").write_text("\n".join(recovery_md) + "\n", encoding="utf-8")

    write_trace(output_dir)
    write_notes(output_dir, notes)
    write_overview(output_dir, records)
    write_repeated_subset_selection(output_dir, records)
    write_failure_taxonomy(output_dir, records)
    write_consolidated_documents(output_dir)
    write_repeated_results(output_dir, samples=args.bootstrap_samples, seed=args.seed)
    write_evaluator_reliability(output_dir, records)
    write_contract_provenance_audit(output_dir, records)
    write_recovery_quality_audit(output_dir, records)
    print(f"Wrote {len(records)} task rows and {len(stats)} paired comparisons to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

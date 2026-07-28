"""Create offline evidence reports for the ARC same-plan lifecycle experiment.

The utility reads already completed ARC-Bench artifacts only. It does not call
an LLM, execute generated projects, or mutate a benchmark run. Its obligation
output is an evidence packet and lexical-anchor diagnostic, not a semantic
claim that a requirement was satisfied.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / "benchmark" / "arc_bench" / "batch_state"
PREPARED_DIR = ROOT / "benchmark" / "arc_bench" / "prepared" / "ml"
DEFAULT_OUTPUT_DIR = ROOT / "MDfiles" / "Paper-ADD"
CONDITIONS = {
    "Full SharedPlan": "ablation-same-plan-full-manual-strict.json",
    "Plan-Only SharedPlan": "ablation-same-plan-plan-only-manual-strict.json",
}


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def read_text(path: Path, *, limit: int = 400_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def write_csv(path: Path, rows: Iterable[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = ["| " + " | ".join(str(row.get(column, "")).replace("|", "\\|") for column in columns) + " |" for row in rows]
    return "\n".join([header, divider, *body])


def score_dimensions(path: Path) -> tuple[dict[str, float | None], Path | None]:
    judge_dir = path / "judge_manual_strict"
    result = load_json(judge_dir / "judge_result.json")
    if result is None:
        return {"CD": None, "CE": None, "RA": None, "Overall": None}, None
    dimensions = result.get("category_scores") if isinstance(result.get("category_scores"), dict) else {}
    values = {
        "CD": number((dimensions.get("Code Development") or {}).get("score")),
        "CE": number((dimensions.get("Code Execution") or {}).get("score")),
        "RA": number((dimensions.get("Result Analysis") or {}).get("score")),
        "Overall": number(result.get("overall_score")),
    }
    return values, judge_dir


def number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def collect_condition_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for condition, state_name in CONDITIONS.items():
        state = load_json(STATE_DIR / state_name)
        if state is None:
            raise FileNotFoundError(f"State file missing or unreadable: {state_name}")
        topics = state.get("topics") if isinstance(state.get("topics"), dict) else {}
        for topic, state_row in sorted(topics.items()):
            if not isinstance(state_row, dict):
                continue
            run_dir = ROOT / str(state_row.get("run_dir") or "")
            output_dir = ROOT / str(state_row.get("output_dir") or "")
            scores, judge_dir = score_dimensions(output_dir)
            implementation = load_json(run_dir / "code_task" / "meta" / "implementation_plan.json") or {}
            snapshot = load_json(run_dir / "code_task" / "meta" / "planning_snapshot.json") or {}
            rows.append({
                "condition": condition,
                "topic": topic,
                "status": str(state_row.get("status") or "missing"),
                "completed": str(state_row.get("status") or "") == "completed",
                "run_dir": rel(run_dir),
                "output_dir": rel(output_dir),
                "judge_dir": rel(judge_dir) if judge_dir else "missing",
                "CD": scores["CD"],
                "CE": scores["CE"],
                "RA": scores["RA"],
                "Overall": scores["Overall"],
                "duration_min": round(float(state_row.get("duration_sec") or 0.0) / 60, 3) if state_row.get("duration_sec") is not None else None,
                "llm_calls": (state_row.get("stats") or {}).get("llm_request_count") if isinstance(state_row.get("stats"), dict) else None,
                "total_tokens": (state_row.get("stats") or {}).get("llm_total_tokens") if isinstance(state_row.get("stats"), dict) else None,
                "repairs": (state_row.get("stats") or {}).get("total_repair_count") if isinstance(state_row.get("stats"), dict) else None,
                "contract_context": ((implementation.get("ablation") or {}).get("contract_context") if isinstance(implementation.get("ablation"), dict) else "missing"),
                "source_run_dir": str(snapshot.get("source_run_dir") or "missing"),
                "source_architecture_hash": str(snapshot.get("architecture_hash") or "missing"),
                "source_file_plan_hash": str(snapshot.get("file_plan_hash") or "missing"),
                "contract_match_mode": str(snapshot.get("contract_match_mode") or "missing"),
            })
    return rows


def paired_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["topic"]), {})[str(row["condition"])] = row
    paired: list[dict[str, Any]] = []
    for topic, data in sorted(grouped.items()):
        full, plan = data.get("Full SharedPlan"), data.get("Plan-Only SharedPlan")
        if full is None or plan is None:
            continue
        full_done, plan_done = bool(full["completed"]), bool(plan["completed"])
        transition = (
            "both_completed" if full_done and plan_done else
            "full_only_completed" if full_done else
            "plan_only_completed" if plan_done else "both_failed"
        )
        result = {
            "topic": topic,
            "transition": transition,
            "same_source_run": full["source_run_dir"] == plan["source_run_dir"],
            "same_architecture_hash": full["source_architecture_hash"] == plan["source_architecture_hash"],
            "full_status": full["status"],
            "plan_only_status": plan["status"],
        }
        for metric in ("CD", "CE", "RA", "Overall"):
            full_value, plan_value = full[metric], plan[metric]
            result[f"full_{metric}"] = full_value
            result[f"plan_only_{metric}"] = plan_value
            result[f"delta_full_minus_plan_only_{metric}"] = (
                round(float(full_value) - float(plan_value), 6)
                if full_value is not None and plan_value is not None else None
            )
        paired.append(result)
    return paired


def write_lifecycle_audit(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    paired = paired_rows(rows)
    write_csv(output_dir / "arc_same_plan_lifecycle_per_task.csv", paired, list(paired[0]) if paired else [])
    transitions = Counter(str(row["transition"]) for row in paired)
    shared = [row for row in paired if row["transition"] == "both_completed"]
    means: dict[str, float] = {}
    for metric in ("CD", "CE", "RA", "Overall"):
        values = [
            float(row[f"delta_full_minus_plan_only_{metric}"])
            for row in shared
            if row[f"delta_full_minus_plan_only_{metric}"] is not None
        ]
        if values:
            means[metric] = statistics.fmean(values)
    summary = {
        "paired_topics": len(paired),
        "same_source_run_pairs": sum(bool(row["same_source_run"]) for row in paired),
        "same_architecture_hash_pairs": sum(bool(row["same_architecture_hash"]) for row in paired),
        "completion_transitions": dict(sorted(transitions.items())),
        "shared_completed_tasks": len(shared),
        "shared_scored_tasks": len([row for row in shared if row["delta_full_minus_plan_only_Overall"] is not None]),
        "mean_full_minus_plan_only_on_shared_completed": means,
    }
    write_json(output_dir / "arc_same_plan_lifecycle_summary.json", summary)
    markdown = [
        "# ARC Same-Plan Lifecycle Audit", "",
        "> **Condition definition.** This completed v1 experiment is a valid controlled same-plan comparison: both branches import the same accepted architecture plan per topic and generate in fresh workspaces. `Plan-Only` omits the canonical task-contract prompt view downstream, while retaining the accepted plan and manifest execution interface. It therefore tests lifecycle contract propagation, not removal of every task-derived execution field. A later v2 implementation makes that boundary stricter for future runs; it does not invalidate these paired v1 results.", "",
        "Both conditions use the same prepared task, source planning snapshot, resource profile, repair budget, and manual-strict evaluator configuration. Completion transitions must be reported alongside conditional score differences.", "",
        "## Integrity Checks", "",
        *[f"- `{key}`: **{value}**" for key, value in summary.items() if key != "mean_full_minus_plan_only_on_shared_completed"], "",
        "## Shared-Completion Score Deltas", "",
        *[f"- Full minus Plan-Only mean `{key}`: **{value:.4f}**" for key, value in means.items()], "",
        "These are conditional scores over shared completions only. Completion transitions must be reported alongside them.", "",
        "## Per-Task Transitions", "",
        markdown_table(paired, ["topic", "transition", "same_source_run", "same_architecture_hash", "delta_full_minus_plan_only_CD", "delta_full_minus_plan_only_CE", "delta_full_minus_plan_only_RA", "delta_full_minus_plan_only_Overall"]), "",
    ]
    (output_dir / "arc_same_plan_lifecycle.md").write_text("\n".join(markdown), encoding="utf-8")


def leaf_map(payload: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for leaf in payload.get("leaf_grades") or []:
        if isinstance(leaf, dict) and isinstance(leaf.get("id"), str) and number(leaf.get("score")) is not None:
            result[str(leaf["id"])] = float(leaf["score"])
    return result


def pearson(values_a: list[float], values_b: list[float]) -> float | None:
    if len(values_a) < 2 or len(values_a) != len(values_b):
        return None
    mean_a, mean_b = statistics.fmean(values_a), statistics.fmean(values_b)
    numerator = sum((a - mean_a) * (b - mean_b) for a, b in zip(values_a, values_b))
    denom_a = sum((a - mean_a) ** 2 for a in values_a) ** 0.5
    denom_b = sum((b - mean_b) ** 2 for b in values_b) ** 0.5
    return numerator / (denom_a * denom_b) if denom_a and denom_b else None


def ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    output = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        rank = (index + 1 + end) / 2
        for original, _ in ordered[index:end]:
            output[original] = rank
        index = end
    return output


def write_evaluator_audit(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    summary_rows: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    for condition in CONDITIONS:
        values_a: list[float] = []
        values_b: list[float] = []
        absolute: list[float] = []
        disagreements = 0
        complete_tasks = 0
        for row in (item for item in rows if item["condition"] == condition and item["completed"]):
            judge_dir = ROOT / str(row["judge_dir"])
            reviewer_a = load_json(judge_dir / "reviewer_a.json")
            reviewer_b = load_json(judge_dir / "reviewer_b.json")
            disagreement = load_json(judge_dir / "disagreements.json") or {}
            if not reviewer_a or not reviewer_b:
                continue
            a, b = leaf_map(reviewer_a), leaf_map(reviewer_b)
            shared = sorted(set(a) & set(b))
            if not shared:
                continue
            diffs = [abs(a[item] - b[item]) for item in shared]
            values_a.extend(a[item] for item in shared)
            values_b.extend(b[item] for item in shared)
            absolute.extend(diffs)
            items = disagreement.get("items") if isinstance(disagreement, dict) else []
            disagreements += len(items) if isinstance(items, list) else 0
            complete_tasks += 1
            details.append({"condition": condition, "topic": row["topic"], "leaf_pairs": len(shared), "mean_abs_difference": round(statistics.fmean(diffs), 6), "strict_disagreement_leaves": len(items) if isinstance(items, list) else 0, "judge_dir": row["judge_dir"]})
        summary_rows.append({
            "condition": condition,
            "tasks_with_dual_reviews": complete_tasks,
            "leaf_pairs": len(absolute),
            "mean_abs_difference": round(statistics.fmean(absolute), 6) if absolute else None,
            "median_abs_difference": round(statistics.median(absolute), 6) if absolute else None,
            "pearson": round(pearson(values_a, values_b), 6) if pearson(values_a, values_b) is not None else None,
            "spearman": round(pearson(ranks(values_a), ranks(values_b)), 6) if pearson(ranks(values_a), ranks(values_b)) is not None else None,
            "strict_disagreement_leaves": disagreements,
            "disagreement_rate": round(disagreements / len(absolute), 6) if absolute else None,
        })
    write_csv(output_dir / "arc_same_plan_evaluator_reliability.csv", summary_rows, list(summary_rows[0]) if summary_rows else [])
    write_csv(output_dir / "arc_same_plan_evaluator_reliability_per_task.csv", details, list(details[0]) if details else [])
    markdown = [
        "# ARC Same-Plan Manual-Strict Evaluator Reliability", "",
        "Scores below come from stored independent reviewer artifacts. This is not a blinded-rater study: the review prompt provides artifact paths and final code to both reviewers.", "",
        markdown_table(summary_rows, list(summary_rows[0]) if summary_rows else []), "",
        "Per-task evidence is in `arc_same_plan_evaluator_reliability_per_task.csv`.", "",
    ]
    (output_dir / "arc_same_plan_evaluator_reliability.md").write_text("\n".join(markdown), encoding="utf-8")


def manifest_metrics(topic: str) -> set[str]:
    try:
        config = tomllib.loads((PREPARED_DIR / topic / "code_task.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    benchmark = config.get("benchmark") if isinstance(config.get("benchmark"), dict) else {}
    directions = benchmark.get("metric_directions") if isinstance(benchmark.get("metric_directions"), dict) else {}
    return {str(key) for key in directions}


def obligation_schema_ok(obligations: Any) -> bool:
    required = {"id", "source", "requirement", "evidence_terms", "weight"}
    return isinstance(obligations, list) and all(isinstance(item, dict) and required <= set(item) and isinstance(item.get("evidence_terms"), list) for item in obligations)


def write_contract_audit(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    details: list[dict[str, Any]] = []
    sources: Counter[str] = Counter()
    for row in rows:
        run_dir = ROOT / str(row["run_dir"])
        meta = run_dir / "code_task" / "meta"
        contract = load_json(meta / "task_contract.json") or {}
        snapshot = load_json(meta / "planning_snapshot.json") or {}
        task_text = read_text(run_dir / "code_task" / "task.md")
        source = contract.get("source") if isinstance(contract.get("source"), dict) else {}
        implementation = contract.get("implementation_contract") if isinstance(contract.get("implementation_contract"), dict) else {}
        obligations = implementation.get("obligations")
        for obligation in obligations if isinstance(obligations, list) else []:
            if isinstance(obligation, dict):
                sources[str(obligation.get("source") or "missing")] += 1
        metrics = contract.get("metric_contract") if isinstance(contract.get("metric_contract"), dict) else {}
        contract_metrics = {str(item) for item in metrics.get("required_metrics", []) if isinstance(item, str)}
        manifest = manifest_metrics(str(row["topic"]))
        details.append({
            "condition": row["condition"],
            "topic": row["topic"],
            "contract_schema": contract.get("schema_version", "missing"),
            "task_text_normalized_match": bool(task_text) and _normalized_task_text(contract.get("task")) == _normalized_task_text(task_text),
            "source_kind": source.get("kind", "missing"),
            "source_task_file": source.get("task_file", "missing"),
            "metric_keys_match_manifest": bool(manifest) and contract_metrics == manifest,
            "obligation_count": len(obligations) if isinstance(obligations, list) else 0,
            "obligation_schema_complete": obligation_schema_ok(obligations),
            "snapshot_present": bool(snapshot),
            "snapshot_contract_match_mode": snapshot.get("contract_match_mode", "missing"),
            "snapshot_source_run": snapshot.get("source_run_dir", "missing"),
            "contract_path": rel(meta / "task_contract.json"),
        })
    checks = ("task_text_normalized_match", "metric_keys_match_manifest", "obligation_schema_complete", "snapshot_present")
    aggregate = {check: f"{sum(bool(row[check]) for row in details)}/{len(details)}" for check in checks}
    aggregate["contract_schema_counts"] = dict(Counter(str(row["contract_schema"]) for row in details))
    aggregate["persisted_obligation_source_labels"] = dict(sorted(sources.items()))
    write_csv(output_dir / "arc_same_plan_contract_provenance.csv", details, list(details[0]) if details else [])
    write_json(output_dir / "arc_same_plan_contract_provenance.json", {"aggregate": aggregate, "rows": details})
    markdown = [
        "# ARC Same-Plan Contract Correctness and Provenance Audit", "",
        "## Deterministic Checks", "",
        *[f"- `{key}`: **{value}**" for key, value in aggregate.items()], "",
        "All observed obligation source labels are normalized `task_contract` labels. They do not preserve original per-obligation classes such as rubric, metric configuration, or adapter metadata; such a historical source breakdown must not be reported.", "",
        "## Per-Run Evidence", "",
        markdown_table(details, ["condition", "topic", "contract_schema", "task_text_normalized_match", "metric_keys_match_manifest", "obligation_count", "obligation_schema_complete", "snapshot_contract_match_mode"]), "",
    ]
    (output_dir / "arc_same_plan_contract_provenance.md").write_text("\n".join(markdown), encoding="utf-8")


def _normalized_task_text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").rstrip("\n")


def artifact_text(run_dir: Path, output_dir: Path, stage: str) -> str:
    if stage == "plan":
        return read_text(run_dir / "code_task" / "meta" / "architecture_plan.json") + read_text(run_dir / "code_task" / "meta" / "file_plan.json")
    if stage == "code":
        root = run_dir / "code_task" / "workspace" / "generated_project"
        return "\n".join(read_text(path, limit=120_000) for path in root.rglob("*.py") if "__pycache__" not in path.parts)
    if stage == "execution":
        return read_text(run_dir / "code_task" / "run" / "patched" / "metrics.json") + read_text(run_dir / "code_task" / "run" / "patched" / "execution_report.json")
    return read_text(output_dir / "result_analysis" / "analysis_report.md") + read_text(output_dir / "result_analysis" / "claims.json") + read_text(output_dir / "submission" / "README.md")


def anchor_coverage(text: str, terms: Any) -> tuple[int, int]:
    usable = [str(term).strip().lower() for term in terms if isinstance(term, str) and len(str(term).strip()) >= 3] if isinstance(terms, list) else []
    hits = sum(term in text.lower() for term in usable)
    return hits, len(usable)


def write_obligation_packets(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    packets: list[dict[str, Any]] = []
    for row in rows:
        run_dir, submission_dir = ROOT / str(row["run_dir"]), ROOT / str(row["output_dir"])
        contract = load_json(run_dir / "code_task" / "meta" / "task_contract.json") or {}
        implementation = contract.get("implementation_contract") if isinstance(contract.get("implementation_contract"), dict) else {}
        artifacts = {stage: artifact_text(run_dir, submission_dir, stage) for stage in ("plan", "code", "execution", "analysis")}
        for obligation in implementation.get("obligations") if isinstance(implementation.get("obligations"), list) else []:
            if not isinstance(obligation, dict):
                continue
            packet = {"condition": row["condition"], "topic": row["topic"], "status": row["status"], "obligation_id": obligation.get("id", ""), "source": obligation.get("source", ""), "requirement": obligation.get("requirement", ""), "evidence_terms": "; ".join(str(item) for item in obligation.get("evidence_terms", [])[:16]), "weight": obligation.get("weight", "")}
            for stage, text in artifacts.items():
                hits, total = anchor_coverage(text, obligation.get("evidence_terms"))
                packet[f"{stage}_anchor_hits"] = hits
                packet[f"{stage}_anchor_terms"] = total
            packet["plan_artifact"] = rel(run_dir / "code_task" / "meta" / "architecture_plan.json")
            packet["code_artifact"] = rel(run_dir / "code_task" / "workspace" / "generated_project")
            packet["execution_artifact"] = rel(run_dir / "code_task" / "run" / "patched" / "execution_report.json")
            packet["analysis_artifact"] = rel(submission_dir / "result_analysis" / "analysis_report.md")
            packets.append(packet)
    columns = list(packets[0]) if packets else []
    write_csv(output_dir / "arc_same_plan_obligation_evidence_packets.csv", packets, columns)
    aggregate = {stage: statistics.fmean((row[f"{stage}_anchor_hits"] / row[f"{stage}_anchor_terms"]) if row[f"{stage}_anchor_terms"] else 0.0 for row in packets) for stage in ("plan", "code", "execution", "analysis")} if packets else {}
    markdown = [
        "# ARC Same-Plan Obligation Evidence Packets", "",
        "This file is a deterministic review packet, not a semantic fidelity score. Each row links one persisted obligation to plan, code, execution, and analysis artifacts. Anchor rates below only measure whether normalized evidence terms occur in those artifacts; they do **not** establish that the requirement is correctly implemented or satisfied.", "",
        "## Anchor-Coverage Diagnostic", "",
        *[f"- Mean `{stage}` anchor coverage: **{value:.3f}**" for stage, value in aggregate.items()], "",
        "## Required Next Step for a Claim-Level Audit", "",
        "A blinded semantic reviewer must label each selected obligation as `preserved`, `partial`, `missing`, or `not_applicable` after reading the linked artifacts. Until those labels exist, report this only as trace availability / deterministic evidence-packet coverage.", "",
        f"- Packets written: **{len(packets)}**; full table: `arc_same_plan_obligation_evidence_packets.csv`.", "",
    ]
    (output_dir / "arc_same_plan_obligation_evidence_packets.md").write_text("\n".join(markdown), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = collect_condition_rows()
    write_csv(output_dir / "arc_same_plan_per_task.csv", rows, list(rows[0]) if rows else [])
    write_lifecycle_audit(output_dir, rows)
    write_evaluator_audit(output_dir, rows)
    write_contract_audit(output_dir, rows)
    write_obligation_packets(output_dir, rows)
    print(f"Wrote same-plan evidence reports for {len(rows)} condition-task rows to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

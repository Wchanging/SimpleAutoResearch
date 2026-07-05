#!/usr/bin/env python3
"""Standalone ARC-Bench adapter for SimpleAutoResearch.

This script intentionally lives outside src/simple_ar and does not import
SimpleAutoResearch internals. It prepares ARC-Bench topics as greenfield
code-task configs and finalizes SimpleAutoResearch run artifacts into an
ARC-Bench-style submission layout.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import shutil
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from benchmark.adapter_contract import build_adapter_manifest
except ModuleNotFoundError:  # pragma: no cover - direct script execution path safety
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from benchmark.adapter_contract import build_adapter_manifest


PREFIX_TO_DOMAIN = {
    "ML": "ml",
    "P": "physics",
    "B": "biology",
    "S": "statistics",
    "Q": "quantum",
}


@dataclass
class PrepareOptions:
    arc_root: Path
    topic: str
    output_dir: Path
    simple_ar_output_root: Path
    benchmark_command: str
    timeout_sec: int = 1800
    repair_rounds: int = 5
    llm_retry_attempts: int = 4
    max_files: int = 48
    max_generated_lines: int = 6000
    budget_profile: str = "large"
    provider: str = "local"
    agent_mode: str = "model"
    allow_external_agent: bool = False
    agent_model: str = ""
    agent_binary: str = ""


def main() -> int:
    parser = argparse.ArgumentParser(description="ARC-Bench adapter for SimpleAutoResearch")
    parser.add_argument("--config", type=Path, help="Optional TOML config file.")
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="Print resolved manifest/rubric summary.")
    add_common_topic_args(show)

    prepare = sub.add_parser("prepare", help="Generate SimpleAutoResearch task/config files.")
    add_common_topic_args(prepare)
    prepare.add_argument("--output-dir", type=Path)
    prepare.add_argument("--simple-ar-output-root", type=Path)
    prepare.add_argument("--benchmark-command")
    prepare.add_argument("--timeout-sec", type=int)
    prepare.add_argument("--repair-rounds", type=int)
    prepare.add_argument("--llm-retry-attempts", type=int)
    prepare.add_argument("--max-files", type=int)
    prepare.add_argument("--max-generated-lines", type=int)
    prepare.add_argument("--budget-profile")
    prepare.add_argument("--provider")
    prepare.add_argument("--agent-mode")
    prepare.add_argument("--allow-external-agent", action="store_true", default=None)
    prepare.add_argument("--agent-model")
    prepare.add_argument("--agent-binary")

    prepare_ml = sub.add_parser("prepare-ml", help="Generate prepared packages for all ML topics.")
    prepare_ml.add_argument("--arc-root", type=Path, help="Path to AutoResearchClaw/experiments/arc_bench.")
    prepare_ml.add_argument("--topics", nargs="*", help="Optional subset, for example ML01 ML02.")
    prepare_ml.add_argument("--prepared-root", type=Path)
    prepare_ml.add_argument("--run-root", type=Path)
    prepare_ml.add_argument("--benchmark-command")
    prepare_ml.add_argument("--timeout-sec", type=int)
    prepare_ml.add_argument("--repair-rounds", type=int)
    prepare_ml.add_argument("--llm-retry-attempts", type=int)
    prepare_ml.add_argument("--max-files", type=int)
    prepare_ml.add_argument("--max-generated-lines", type=int)
    prepare_ml.add_argument("--budget-profile")
    prepare_ml.add_argument("--provider")
    prepare_ml.add_argument("--agent-mode")
    prepare_ml.add_argument("--allow-external-agent", action="store_true", default=None)
    prepare_ml.add_argument("--agent-model")
    prepare_ml.add_argument("--agent-binary")

    finalize = sub.add_parser("finalize", help="Project a SimpleAutoResearch run into ARC submission layout.")
    finalize.add_argument("--prepared-dir", type=Path)
    finalize.add_argument("--run-dir", type=Path)
    finalize.add_argument("--output-dir", type=Path)
    finalize.add_argument(
        "--force",
        action="store_true",
        default=None,
        help="Overwrite output dir if it already contains files.",
    )
    finalize.add_argument(
        "--analyze",
        action="store_true",
        default=None,
        help="Call the configured LLM to regenerate README and claims from run results.",
    )
    finalize.add_argument("--analysis-model", help="Optional model override for --analyze.")

    judge = sub.add_parser("judge", help="Run an external ARC-Bench judge command and save raw output.")
    judge.add_argument("--submission-dir", type=Path)
    judge.add_argument("--judge-command")
    judge.add_argument("--output-dir", type=Path)
    judge.add_argument("--timeout-sec", type=int)

    score = sub.add_parser("score", help="Score a finalized submission against ARC rubric leaves with an LLM.")
    score.add_argument("--prepared-dir", type=Path)
    score.add_argument("--submission-dir", type=Path)
    score.add_argument("--output-dir", type=Path)
    score.add_argument("--model", help="Optional model override for scoring.")
    score.add_argument(
        "--score-profile",
        choices=("proxy", "arc-auto", "strict"),
        help="Scoring profile. proxy is the lightweight internal scorer; strict runs two reviewers and adjudication.",
    )
    score.add_argument(
        "--strict-reviewers",
        type=int,
        help="Number of independent reviewers for --score-profile strict. Use 1 only for low-cost previews.",
    )
    score.add_argument(
        "--disagreement-threshold",
        type=float,
        help="Per-leaf strict reviewer disagreement threshold that triggers adjudication.",
    )
    score.add_argument("--max-code-chars", type=int)
    score.add_argument("--max-result-chars", type=int)
    score.add_argument("--max-writeup-chars", type=int)

    args = parser.parse_args()
    cfg = load_toml(args.config)

    if args.command == "show":
        arc_root = resolve_arc_root(args, cfg)
        manifest = load_manifest(arc_root, value_from(args, cfg, "prepare", "topic", "ML02"))
        rubric = load_rubric(arc_root, manifest)
        print_topic_summary(arc_root, manifest, rubric)
        return 0

    if args.command == "prepare":
        options = build_prepare_options(args, cfg)
        manifest = load_manifest(options.arc_root, options.topic)
        rubric = load_rubric(options.arc_root, manifest)
        write_prepared_package(options, manifest, rubric)
        return 0

    if args.command == "prepare-ml":
        prepared = prepare_ml_batch(args, cfg)
        print(f"Prepared {len(prepared)} ML topic package(s).")
        for path in prepared:
            print(f"  {path}")
        return 0

    if args.command == "finalize":
        prepared_dir = Path(value_from(args, cfg, "finalize", "prepared_dir", ""))
        run_dir = Path(value_from(args, cfg, "finalize", "run_dir", ""))
        output_dir = Path(value_from(args, cfg, "finalize", "output_dir", ""))
        force = bool(value_from(args, cfg, "finalize", "force", False))
        analyze = bool(value_from(args, cfg, "finalize", "analyze", False))
        analysis_model = str(value_from(args, cfg, "finalize", "analysis_model", ""))
        if not prepared_dir:
            raise SystemExit("--prepared-dir is required")
        if not run_dir:
            raise SystemExit("--run-dir is required")
        if not output_dir:
            output_dir = prepared_dir / "submission_from_run"
        finalize_submission(
            prepared_dir,
            run_dir,
            output_dir,
            force=force,
            analyze=analyze,
            analysis_model=analysis_model,
        )
        return 0

    if args.command == "judge":
        return run_judge_command(args, cfg)

    if args.command == "score":
        return run_score_command(args, cfg)

    raise SystemExit(f"unknown command: {args.command}")


def add_common_topic_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--arc-root", type=Path, help="Path to AutoResearchClaw/experiments/arc_bench.")
    parser.add_argument("--topic", help="ARC topic id, for example ML02.")
    parser.add_argument("--manifest", type=Path, help="Optional explicit manifest YAML path.")
    parser.add_argument("--rubric", type=Path, help="Optional explicit rubric JSON path.")


def load_toml(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    if not path.is_file():
        raise SystemExit(f"config file not found: {path}")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def value_from(args: argparse.Namespace, cfg: dict[str, Any], section: str, key: str, default: Any) -> Any:
    cli_value = getattr(args, key.replace("-", "_"), None)
    if cli_value not in (None, ""):
        return cli_value
    section_data = cfg.get(section, {})
    if isinstance(section_data, dict) and key in section_data:
        return section_data[key]
    if section == "prepare" and isinstance(cfg.get("arc"), dict) and key in cfg["arc"]:
        return cfg["arc"][key]
    return default


def resolve_arc_root(args: argparse.Namespace, cfg: dict[str, Any]) -> Path:
    raw = value_from(args, cfg, "arc", "arc_root", "")
    if not raw:
        raw = os.environ.get("ARC_BENCH_ROOT", "")
    if not raw:
        candidate = Path("AutoResearchClaw") / "experiments" / "arc_bench"
        if candidate.is_dir():
            raw = str(candidate)
    if not raw:
        raise SystemExit("ARC root is required. Use --arc-root, [arc].arc_root, or ARC_BENCH_ROOT.")
    root = Path(raw).expanduser()
    if not root.is_dir():
        raise SystemExit(f"ARC root does not exist: {root}")
    return root


def build_prepare_options(args: argparse.Namespace, cfg: dict[str, Any]) -> PrepareOptions:
    arc_root = resolve_arc_root(args, cfg)
    topic = str(value_from(args, cfg, "prepare", "topic", "ML02")).upper()
    output_dir = Path(value_from(args, cfg, "prepare", "output_dir", f"benchmark/arc_bench/prepared/{topic}"))
    output_root = Path(value_from(args, cfg, "prepare", "simple_ar_output_root", f"benchmark/arc_bench/runs/{topic}"))
    benchmark_command = str(
        value_from(args, cfg, "prepare", "benchmark_command", "python generated_project/main.py --preset standard")
    )
    return PrepareOptions(
        arc_root=arc_root,
        topic=topic,
        output_dir=output_dir,
        simple_ar_output_root=output_root,
        benchmark_command=benchmark_command,
        timeout_sec=int(value_from(args, cfg, "prepare", "timeout_sec", 1800)),
        repair_rounds=int(value_from(args, cfg, "prepare", "repair_rounds", 5)),
        llm_retry_attempts=int(value_from(args, cfg, "prepare", "llm_retry_attempts", 4)),
        max_files=int(value_from(args, cfg, "prepare", "max_files", 48)),
        max_generated_lines=int(value_from(args, cfg, "prepare", "max_generated_lines", 6000)),
        budget_profile=str(value_from(args, cfg, "prepare", "budget_profile", "large")),
        provider=str(value_from(args, cfg, "prepare", "provider", "local")),
        agent_mode=str(value_from(args, cfg, "prepare", "agent_mode", "model")),
        allow_external_agent=bool(value_from(args, cfg, "prepare", "allow_external_agent", False)),
        agent_model=str(value_from(args, cfg, "prepare", "agent_model", "")),
        agent_binary=str(value_from(args, cfg, "prepare", "agent_binary", "")),
    )


def prepare_ml_batch(args: argparse.Namespace, cfg: dict[str, Any]) -> list[Path]:
    arc_root = resolve_arc_root(args, cfg)
    topics = selected_ml_topics(args, cfg, arc_root)
    prepared_root = Path(
        multi_section_value(args, cfg, ("prepare_ml",), "prepared_root", "benchmark/arc_bench/prepared/ml")
    )
    run_root = Path(
        multi_section_value(args, cfg, ("prepare_ml",), "run_root", "benchmark/arc_bench/runs/ml")
    )
    prepared_dirs: list[Path] = []
    for topic in topics:
        options = build_batch_prepare_options(args, cfg, arc_root, topic, prepared_root, run_root)
        manifest = load_manifest(options.arc_root, options.topic)
        rubric = load_rubric(options.arc_root, manifest)
        write_prepared_package(options, manifest, rubric)
        prepared_dirs.append(options.output_dir)
    write_batch_index(prepared_root, topics, arc_root, run_root)
    return prepared_dirs


def selected_ml_topics(args: argparse.Namespace, cfg: dict[str, Any], arc_root: Path) -> list[str]:
    raw = getattr(args, "topics", None)
    if raw:
        topics = [str(item).upper() for item in raw]
    else:
        section = cfg.get("prepare_ml", {})
        configured = section.get("topics") if isinstance(section, dict) else None
        if isinstance(configured, str):
            topics = [part.strip().upper() for part in configured.split(",") if part.strip()]
        elif isinstance(configured, list) and configured:
            topics = [str(item).upper() for item in configured]
        else:
            topics = discover_ml_topics(arc_root)
    if not topics:
        raise SystemExit("No ML topics selected.")
    invalid = [topic for topic in topics if not topic.startswith("ML")]
    if invalid:
        raise SystemExit("prepare-ml only accepts ML topics, got: " + ", ".join(invalid))
    return sorted(dict.fromkeys(topics))


def discover_ml_topics(arc_root: Path) -> list[str]:
    manifest_dir = arc_root / "config" / "ml" / "manifests"
    if not manifest_dir.is_dir():
        raise SystemExit(f"ML manifest directory not found: {manifest_dir}")
    return sorted(path.stem.upper() for path in manifest_dir.glob("ML*.yaml"))


def build_batch_prepare_options(
    args: argparse.Namespace,
    cfg: dict[str, Any],
    arc_root: Path,
    topic: str,
    prepared_root: Path,
    run_root: Path,
) -> PrepareOptions:
    benchmark_command = str(
        multi_section_value(
            args,
            cfg,
            ("prepare_ml", "prepare"),
            "benchmark_command",
            "python generated_project/main.py --preset standard",
        )
    )
    return PrepareOptions(
        arc_root=arc_root,
        topic=topic,
        output_dir=prepared_root / topic,
        simple_ar_output_root=run_root / topic,
        benchmark_command=benchmark_command,
        timeout_sec=int(multi_section_value(args, cfg, ("prepare_ml", "prepare"), "timeout_sec", 1800)),
        repair_rounds=int(multi_section_value(args, cfg, ("prepare_ml", "prepare"), "repair_rounds", 5)),
        llm_retry_attempts=int(
            multi_section_value(args, cfg, ("prepare_ml", "prepare"), "llm_retry_attempts", 4)
        ),
        max_files=int(multi_section_value(args, cfg, ("prepare_ml", "prepare"), "max_files", 48)),
        max_generated_lines=int(
            multi_section_value(args, cfg, ("prepare_ml", "prepare"), "max_generated_lines", 6000)
        ),
        budget_profile=str(multi_section_value(args, cfg, ("prepare_ml", "prepare"), "budget_profile", "large")),
        provider=str(multi_section_value(args, cfg, ("prepare_ml", "prepare"), "provider", "local")),
        agent_mode=str(multi_section_value(args, cfg, ("prepare_ml", "prepare"), "agent_mode", "model")),
        allow_external_agent=bool(
            multi_section_value(args, cfg, ("prepare_ml", "prepare"), "allow_external_agent", False)
        ),
        agent_model=str(multi_section_value(args, cfg, ("prepare_ml", "prepare"), "agent_model", "")),
        agent_binary=str(multi_section_value(args, cfg, ("prepare_ml", "prepare"), "agent_binary", "")),
    )


def multi_section_value(
    args: argparse.Namespace,
    cfg: dict[str, Any],
    sections: tuple[str, ...],
    key: str,
    default: Any,
) -> Any:
    cli_value = getattr(args, key.replace("-", "_"), None)
    if cli_value not in (None, ""):
        return cli_value
    for section_name in sections:
        section = cfg.get(section_name, {})
        if isinstance(section, dict) and key in section:
            return section[key]
    return default


def write_batch_index(prepared_root: Path, topics: list[str], arc_root: Path, run_root: Path) -> None:
    lines = [
        "# ARC-Bench ML Prepared Packages",
        "",
        f"- ARC root: `{arc_root}`",
        f"- Run root: `{run_root}`",
        f"- Topic count: `{len(topics)}`",
        "",
        "| Topic | Config | Task | Run root |",
        "| --- | --- | --- | --- |",
    ]
    for topic in topics:
        topic_dir = prepared_root / topic
        lines.append(
            f"| `{topic}` | `{topic_dir / 'code_task.toml'}` | "
            f"`{topic_dir / 'task.md'}` | `{run_root / topic}` |"
        )
    write_text(prepared_root / "INDEX.md", "\n".join(lines) + "\n")


def load_manifest(arc_root: Path, topic: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise SystemExit("PyYAML is required to read ARC-Bench manifests. Install with `uv add pyyaml`.") from exc

    manifest_path = resolve_manifest_path(arc_root, topic)
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    data["_manifest_path"] = str(manifest_path)
    return data


def resolve_manifest_path(arc_root: Path, topic: str) -> Path:
    domain = topic_domain(topic)
    candidates = [
        arc_root / "config" / domain / "manifests" / f"{topic}.yaml",
        arc_root / "config" / "manifests" / f"{topic}.yaml",
        arc_root / "manifests" / f"{topic}.yaml",
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise SystemExit("manifest not found. Tried:\n" + "\n".join(f"  {p}" for p in candidates))


def load_rubric(arc_root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    topic = str(manifest.get("id") or "").upper()
    explicit = manifest.get("rubric_path")
    candidates: list[Path] = []
    if explicit:
        raw = Path(str(explicit))
        candidates.extend(resolve_relative_arc_path(arc_root, raw))
    domain = topic_domain(topic)
    candidates.extend(
        [
            arc_root / "config" / domain / "rubrics" / f"{topic}.json",
            arc_root / "config" / "rubrics" / f"{topic}.json",
            arc_root / "rubrics" / f"{topic}.json",
        ]
    )
    for path in candidates:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            data["_rubric_path"] = str(path)
            return data
    raise SystemExit("rubric not found. Tried:\n" + "\n".join(f"  {p}" for p in candidates))


def resolve_relative_arc_path(arc_root: Path, raw: Path) -> list[Path]:
    if raw.is_absolute():
        return [raw]
    return [
        arc_root / raw,
        arc_root.parent / raw,
        arc_root.parent.parent / raw,
        Path.cwd() / raw,
    ]


def topic_domain(topic: str) -> str:
    upper = topic.upper()
    for prefix in sorted(PREFIX_TO_DOMAIN, key=len, reverse=True):
        if upper.startswith(prefix):
            return PREFIX_TO_DOMAIN[prefix]
    return "ml"


def write_prepared_package(options: PrepareOptions, manifest: dict[str, Any], rubric: dict[str, Any]) -> None:
    out = options.output_dir
    out.mkdir(parents=True, exist_ok=True)

    task_path = out / "task.md"
    config_path = out / "code_task.toml"
    meta_path = out / "arc_meta.json"
    write_text(task_path, render_task_markdown(manifest, rubric, options))
    write_text(config_path, render_code_task_toml(options, manifest, rubric, task_path))
    write_json(out / "manifest.json", scrub_private_keys(manifest))
    write_json(out / "rubric.json", scrub_private_keys(rubric))
    write_json(
        meta_path,
        build_adapter_manifest(
            suite="arc_bench",
            operation="prepare",
            status="prepared",
            inputs={
                "arc_root": options.arc_root,
                "manifest_path": manifest.get("_manifest_path"),
                "rubric_path": rubric.get("_rubric_path"),
            },
            outputs={
                "prepared_dir": out,
                "task_file": task_path,
                "code_task_config": config_path,
                "simple_ar_output_root": options.simple_ar_output_root,
            },
            metadata={
                "topic": manifest.get("id"),
                "benchmark_command": options.benchmark_command,
                "adapter_schema_version": "simple_ar_arc_adapter.v1",
            },
        ),
    )
    write_text(out / "commands.md", render_commands(options, config_path))
    print(f"Prepared ARC-Bench topic {manifest.get('id')} at {out}")
    print(f"Task:   {task_path}")
    print(f"Config: {config_path}")


def render_task_markdown(manifest: dict[str, Any], rubric: dict[str, Any], options: PrepareOptions) -> str:
    topic = manifest.get("id", options.topic)
    title = manifest.get("title", "")
    design = manifest.get("experiment_design") or {}
    conditions = design.get("conditions") or []
    datasets = design.get("datasets") or []
    metrics = design.get("metrics") or []
    hypotheses = manifest.get("hypotheses") or []
    leaves = list(iter_rubric_leaves(rubric))

    return "\n".join(
        [
            f"# ARC-Bench Task: {topic} - {title}",
            "",
            "You are solving an ARC-Bench autonomous-research topic through a",
            "SimpleAutoResearch greenfield code task. Build a runnable Python",
            "project under `generated_project/` that designs, executes, and",
            "summarizes a credible experiment for the topic below.",
            "",
            "This task is evaluated by ARC-Bench-style rubric leaves. Do not",
            "optimize only for a single stdout metric; implement the experiment",
            "contract, record condition-level evidence, and write a grounded",
            "analysis that answers each hypothesis with measured numbers.",
            "",
            "## Research Question",
            "",
            str(design.get("research_question") or title),
            "",
            "## Synthesis / Background",
            "",
            str(manifest.get("synthesis") or "(not provided)").strip(),
            "",
            "## Hypotheses",
            "",
            render_bullets([f"{h.get('id', '?')}: {h.get('statement', '')}" for h in hypotheses]),
            "",
            "## Required Conditions",
            "",
            render_bullets([f"{c.get('name', '?')}: {c.get('description', '')}" for c in conditions]),
            "",
            "## Required Datasets",
            "",
            render_bullets([f"{d.get('name', '?')}: {d.get('source', '')}" for d in datasets]),
            "",
            "## Required Metrics",
            "",
            render_bullets([f"{m.get('name', '?')} ({m.get('direction', 'unknown')}): {m.get('description', '')}" for m in metrics]),
            "",
            "## Rubric Leaves",
            "",
            render_bullets([f"{leaf.get('id', '?')} [{leaf.get('task_category', 'uncategorized')}]: {leaf.get('requirements', '')}" for leaf in leaves]),
            "",
            "## Implementation Requirements",
            "",
            "- Create all source files inside `generated_project/`.",
            "- Provide a CLI entrypoint at `generated_project/main.py`.",
            f"- The benchmark command is `{options.benchmark_command}` and must exit with code 0.",
            "- Print stable numeric metric lines as `metric_name: value` or `METRIC metric_name=value`.",
            "- Write `generated_project/artifacts/results.json` with condition/dataset/seed-level evidence when possible.",
            "- Write `generated_project/artifacts/report.md` with Method, Results, Hypothesis Verdicts, and Limitations.",
            "- If a required dataset or optional dependency is unavailable, use a bounded fallback and disclose it in metrics and report.",
            "- Prefer packaged/local datasets and CPU-bounded algorithms; do not require runtime downloads.",
            "- Use multiple seeds when the manifest or rubric asks for seed coverage. If the selected preset uses fewer seeds, say so clearly.",
            "- Claims must be grounded in captured metrics; do not invent numbers in the report.",
            "",
            "## Expected ARC Submission Signals",
            "",
            "A downstream adapter will convert this run into `submission/code/`,",
            "`submission/results/metrics.json`, `submission/README.md`, and",
            "`submission/claims.json`. Make that conversion easy by keeping",
            "code, metrics, report, and hypothesis verdicts structured.",
            "",
        ]
    )


def render_code_task_toml(
    options: PrepareOptions,
    manifest: dict[str, Any],
    rubric: dict[str, Any],
    task_path: Path,
) -> str:
    design = manifest.get("experiment_design") or {}
    metrics = design.get("metrics") or []
    primary = metrics[0] if metrics else {}
    primary_metric = str(primary.get("name") or "primary_metric")
    directions = metric_directions_from_manifest(metrics)
    allow_external = "true" if options.allow_external_agent else "false"
    allow_gpu = "true" if (design.get("compute_requirements") or {}).get("gpu_required") else "false"
    lines = [
        "# Generated by benchmark/arc_bench/adapter.py.",
        "# The topic-specific requirements live in task.md; this TOML only",
        "# configures SimpleAutoResearch runtime, resource, and metric handling.",
        "",
        "[code_task]",
        'kind = "greenfield"',
        f'task_file = "{path_for_toml(task_path)}"',
        f'output_root = "{path_for_toml(options.simple_ar_output_root)}"',
        f'name = "arc-bench-{manifest.get("id", options.topic)}"',
        "",
        "[benchmark]",
        f'command = "{escape_toml_string(options.benchmark_command)}"',
        f"timeout = {options.timeout_sec}",
        f'primary_metric = "{primary_metric}"',
        "",
        "[benchmark.metric_directions]",
    ]
    lines.extend(f'{name} = "{direction}"' for name, direction in sorted(directions.items()))
    lines.extend(
        [
            "",
            "[workspace]",
            'mode = "empty"',
            "",
            "[environment]",
            'mode = "current"',
            "",
            "[llm]",
            "enabled = true",
            "",
            "[execute]",
            'to_step = "run"',
            f"timeout_sec = {options.timeout_sec}",
            'stream_benchmark_output = "auto"',
            'baseline_policy = "none"',
            f"llm_retry_attempts = {max(1, options.llm_retry_attempts)}",
            f"repair_rounds = {options.repair_rounds}",
            f"max_files = {options.max_files}",
            "max_source_chars_per_file = 12000",
            f"max_generated_lines = {options.max_generated_lines}",
            "",
            "[implementation]",
            f'provider = "{options.provider}"',
            f'agent_mode = "{options.agent_mode}"',
            f"allow_external_agent = {allow_external}",
            f'agent_model = "{escape_toml_string(options.agent_model)}"',
            f'agent_binary = "{escape_toml_string(options.agent_binary)}"',
            "agent_args = []",
            f"agent_timeout_sec = {options.timeout_sec}",
            "",
            "[resource]",
            f"max_runtime_sec = {options.timeout_sec}",
            f"max_files = {options.max_files}",
            f"max_generated_lines = {options.max_generated_lines}",
            "max_memory_mb = 24576",
            f"allow_gpu = {allow_gpu}",
            "",
            "[budget]",
            f'profile = "{options.budget_profile}"',
            "max_batches = 6",
            "cost_cap_usd = 20.0",
            "",
        ]
    )
    return "\n".join(lines)


def metric_directions_from_manifest(metrics: list[Any]) -> dict[str, str]:
    directions: dict[str, str] = {}
    for metric in metrics:
        if not isinstance(metric, dict) or not metric.get("name"):
            continue
        name = str(metric["name"])
        directions[name] = arc_direction_to_simple(metric.get("direction"))

    # Runtime is a generic resource signal. Completeness counters such as
    # dataset_count or hypothesis_coverage can still appear in result artifacts,
    # but they should not be mistaken for topic-required benchmark objectives.
    directions.setdefault("runtime_sec", "resource")
    return directions


def render_commands(options: PrepareOptions, config_path: Path) -> str:
    return "\n".join(
        [
            "# Commands",
            "",
            "## Run SimpleAutoResearch",
            "",
            "`code-task execute` requires the run manifest created by `code-task init`.",
            "Initialize first, then pass the printed run directory into `execute`.",
            "",
            "```bash",
            f"uv run simple-ar code-task init --config {path_for_shell(config_path)}",
            f"uv run simple-ar code-task execute <RUN_DIR> --config {path_for_shell(config_path)} --yes",
            "```",
            "",
            "## Finalize",
            "",
            "```bash",
            "uv run python benchmark/arc_bench/adapter.py finalize \\",
            f"  --prepared-dir {path_for_shell(options.output_dir)} \\",
            "  --run-dir <RUN_DIR> \\",
            "  --output-dir benchmark/arc_bench/submissions/<TOPIC>/<RUN_ID>",
            "```",
            "",
            "Add `--analyze` if you want the adapter to call the configured LLM",
            "and regenerate `submission/README.md` plus `submission/claims.json`",
            "from the measured metrics and project results.",
            "",
            "## Score",
            "",
            "After finalization, use the built-in LLM scorer to create",
            "`judge/judge_result.json` and `judge/scorecard.md`:",
            "",
            "```bash",
            "uv run python benchmark/arc_bench/adapter.py score \\",
            f"  --prepared-dir {path_for_shell(options.output_dir)} \\",
            "  --submission-dir benchmark/arc_bench/submissions/<TOPIC>/<RUN_ID>/submission \\",
            "  --output-dir benchmark/arc_bench/submissions/<TOPIC>/<RUN_ID>/judge",
            "```",
            "",
        ]
    )


def finalize_submission(
    prepared_dir: Path,
    run_dir: Path,
    output_dir: Path,
    *,
    force: bool = False,
    analyze: bool = False,
    analysis_model: str = "",
) -> None:
    if not prepared_dir.is_dir():
        raise SystemExit(f"prepared dir not found: {prepared_dir}")
    if not run_dir.is_dir():
        raise SystemExit(f"run dir not found: {run_dir}")
    manifest = read_json(prepared_dir / "manifest.json")
    rubric = read_json(prepared_dir / "rubric.json")

    if output_dir.exists() and any(output_dir.iterdir()):
        if not force:
            raise SystemExit(
                f"output dir already exists and is not empty: {output_dir}\n"
                "Choose a new --output-dir or pass --force to overwrite it."
            )
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    submission = output_dir / "submission"
    code_dst = submission / "code"
    results_dst = submission / "results"
    results_dst.mkdir(parents=True, exist_ok=True)
    code_src = find_generated_project(run_dir)
    if code_src:
        copy_tree_clean(code_src, code_dst)
    else:
        code_dst.mkdir(parents=True, exist_ok=True)
        write_text(code_dst / "MISSING_CODE.txt", "No generated_project directory was found.\n")

    project_results = load_project_results(run_dir, code_src)
    metrics = merge_metrics(numeric_metrics(project_results), load_simple_ar_metrics(run_dir))
    experiment_summary = build_experiment_summary(manifest, rubric, metrics, project_results, run_dir)
    readme = build_submission_readme(manifest, run_dir, code_src)
    claims = build_claims(manifest, metrics, project_results)
    print("Building result-analysis artifacts.")
    analysis = analyze_submission_results(
        manifest=manifest,
        rubric=rubric,
        metrics=metrics,
        project_results=project_results,
        run_dir=run_dir,
        code_src=code_src,
        fallback_readme=readme,
        fallback_claims=claims,
        output_dir=output_dir / "result_analysis",
        use_llm=analyze,
        model=analysis_model or None,
    )
    analysis_audit = analysis["analysis_audit"]
    if analyze:
        print("Using LLM-analyzed README and claims for submission.")
        readme = analysis["readme_markdown"]
        claims = analysis["claims"]
    write_json(results_dst / "metrics.json", {"metrics": metrics, "project_results": project_results})
    write_json(output_dir / "stage-14" / "experiment_summary.json", experiment_summary)
    write_text(submission / "README.md", readme)
    write_json(submission / "claims.json", claims)
    write_text(submission / "reproduce.sh", build_reproduce_script(run_dir))
    write_json(
        output_dir / "arc_adapter_meta.json",
        build_adapter_manifest(
            suite="arc_bench",
            operation="finalize",
            status="finalized",
            inputs={
                "prepared_dir": prepared_dir,
                "run_dir": run_dir,
                "code_source": code_src or "",
            },
            outputs={
                "output_dir": output_dir,
                "submission_dir": submission,
                "code_dir": code_dst,
                "results_dir": results_dst,
            },
            metadata={
                "topic_id": manifest.get("id"),
                "metric_count": len(metrics),
                "analysis": analysis_audit,
                "adapter_generated_analysis": bool(analyze),
                "analysis_source": "adapter-generated" if analyze else "agent-original",
                "adapter_schema_version": "simple_ar_arc_submission.v1",
            },
        ),
    )
    print(f"Finalized ARC-style submission at {output_dir}")


def analyze_submission_results(
    *,
    manifest: dict[str, Any],
    rubric: dict[str, Any],
    metrics: dict[str, float],
    project_results: dict[str, Any],
    run_dir: Path,
    code_src: Path | None,
    fallback_readme: str,
    fallback_claims: dict[str, Any],
    output_dir: Path,
    use_llm: bool,
    model: str | None,
) -> dict[str, Any]:
    print("Preparing result-analysis context.")
    context = build_analysis_context(
        manifest=manifest,
        rubric=rubric,
        metrics=metrics,
        project_results=project_results,
        run_dir=run_dir,
        code_src=code_src,
        task_contract=load_code_task_contract(run_dir),
        fallback_readme=fallback_readme,
        fallback_claims=fallback_claims,
    )
    try:
        from simple_ar.result_analysis import record_result_analysis_memory, run_result_analysis
    except Exception as exc:  # pragma: no cover - environment issue
        raise SystemExit(
            "result analysis requires SimpleAutoResearch's result-analysis integration to be importable."
        ) from exc

    usage_rows: list[dict[str, Any]] = []
    client = None
    if use_llm:
        print("Calling LLM for ARC result analysis.")
        try:
            from simple_ar.integrations.llm import LLMClient
        except Exception as exc:  # pragma: no cover - environment issue
            raise SystemExit("--analyze requires SimpleAutoResearch's LLM integration to be importable.") from exc

        def usage_callback(usage: Any) -> None:
            row = usage.to_row() if hasattr(usage, "to_row") else dict(usage)
            usage_rows.append(row)
            append_jsonl(output_dir / "llm_usage.jsonl", row)

        client = LLMClient.from_env(model=model, usage_callback=usage_callback)

    result = run_result_analysis(
        context,
        output_dir=output_dir,
        client=client,
        use_llm=use_llm,
        label="arc-bench-result-analysis",
    )
    record_result_analysis_memory(run_dir, result, output_dir=output_dir)
    if usage_rows:
        write_json(output_dir / "llm_usage_summary.json", summarize_usage_rows(usage_rows))
    print(f"Result-analysis artifacts written to {output_dir}")
    return {
        "readme_markdown": result.readme_markdown,
        "claims": result.claims_payload,
        "analysis_audit": result.audit.model_dump(mode="json"),
    }


def run_score_command(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    prepared_raw = value_from(args, cfg, "score", "prepared_dir", "")
    submission_raw = value_from(args, cfg, "score", "submission_dir", "")
    output_raw = value_from(args, cfg, "score", "output_dir", "")
    model = str(value_from(args, cfg, "score", "model", ""))
    max_code_chars = int(value_from(args, cfg, "score", "max_code_chars", 30000))
    max_result_chars = int(value_from(args, cfg, "score", "max_result_chars", 24000))
    max_writeup_chars = int(value_from(args, cfg, "score", "max_writeup_chars", 16000))
    score_profile = str(value_from(args, cfg, "score", "score_profile", "proxy") or "proxy").strip().lower()
    strict_reviewers = int(value_from(args, cfg, "score", "strict_reviewers", 2))
    disagreement_threshold = float(value_from(args, cfg, "score", "disagreement_threshold", 0.20))

    if not prepared_raw:
        raise SystemExit("--prepared-dir is required")
    prepared_dir = Path(prepared_raw)
    if not prepared_dir.is_dir():
        raise SystemExit(f"prepared dir not found: {prepared_dir}")
    if not submission_raw:
        raise SystemExit("--submission-dir is required")
    submission_dir = Path(submission_raw)
    if not submission_dir.is_dir():
        raise SystemExit(f"submission dir not found: {submission_dir}")
    output_dir = Path(output_raw) if output_raw else submission_dir.parent / "judge"

    manifest = read_json(prepared_dir / "manifest.json")
    rubric = read_json(prepared_dir / "rubric.json")
    result = score_submission_with_llm(
        manifest=manifest,
        rubric=rubric,
        prepared_dir=prepared_dir,
        submission_dir=submission_dir,
        output_dir=output_dir,
        model=model or None,
        score_profile=score_profile,
        strict_reviewers=strict_reviewers,
        disagreement_threshold=disagreement_threshold,
        max_code_chars=max_code_chars,
        max_result_chars=max_result_chars,
        max_writeup_chars=max_writeup_chars,
    )
    print(f"ARC score overall={result['overall_score']:.3f}; wrote {output_dir / 'judge_result.json'}")
    return 0


def score_submission_with_llm(
    *,
    manifest: dict[str, Any],
    rubric: dict[str, Any],
    prepared_dir: Path,
    submission_dir: Path,
    output_dir: Path,
    model: str | None,
    score_profile: str = "proxy",
    strict_reviewers: int = 2,
    disagreement_threshold: float = 0.20,
    max_code_chars: int,
    max_result_chars: int,
    max_writeup_chars: int,
) -> dict[str, Any]:
    leaves = list(iter_rubric_leaves(rubric))
    if not leaves:
        raise SystemExit("rubric has no leaf criteria")
    output_dir.mkdir(parents=True, exist_ok=True)
    score_profile = normalize_score_profile(score_profile)
    artifacts = load_score_artifacts(
        submission_dir,
        manifest=manifest,
        rubric=rubric,
        max_code_chars=max_code_chars,
        max_result_chars=max_result_chars,
        max_writeup_chars=max_writeup_chars,
    )
    artifacts["evidence_bundle"] = build_submission_evidence_bundle(
        manifest=manifest,
        rubric=rubric,
        prepared_dir=prepared_dir,
        submission_dir=submission_dir,
        artifacts=artifacts,
        score_profile=score_profile,
    )
    write_json(output_dir / "evidence_bundle.json", artifacts["evidence_bundle"])

    usage_rows: list[dict[str, Any]] = []
    try:
        from simple_ar.integrations.llm import LLMClient
    except Exception as exc:  # pragma: no cover - environment issue
        raise SystemExit("score requires SimpleAutoResearch's LLM integration to be importable.") from exc

    def usage_callback(usage: Any) -> None:
        row = usage.to_row() if hasattr(usage, "to_row") else dict(usage)
        usage_rows.append(row)
        append_jsonl(output_dir / "llm_usage.jsonl", row)

    client = LLMClient.from_env(model=model, usage_callback=usage_callback)

    if score_profile == "strict":
        result = score_submission_strict(
            client=client,
            manifest=manifest,
            rubric=rubric,
            prepared_dir=prepared_dir,
            submission_dir=submission_dir,
            output_dir=output_dir,
            artifacts=artifacts,
            leaves=leaves,
            reviewer_count=strict_reviewers,
            disagreement_threshold=disagreement_threshold,
        )
        if usage_rows:
            write_json(output_dir / "llm_usage_summary.json", summarize_llm_usage_rows(usage_rows))
        return result

    manifest_context = build_manifest_context(manifest)
    code_leaves = [leaf for leaf in leaves if is_code_development_leaf(leaf)]
    result_leaves = [leaf for leaf in leaves if not is_code_development_leaf(leaf)]
    all_warnings: list[str] = []
    raw_responses: dict[str, Any] = {}
    leaf_grades: list[dict[str, Any]] = []

    if code_leaves:
        prompt = build_code_round_prompt(manifest_context, code_leaves, artifacts)
        raw_response, grades, warnings_ = run_score_round(
            client,
            prompt,
            code_leaves,
            label="arc-bench-score-code",
            round_name="code",
            raw_response_path=output_dir / "score_round_code_response.json",
            prompt_debug_path=output_dir / "score_round_code_prompt.txt",
        )
        raw_responses["code"] = raw_response
        leaf_grades.extend(grades)
        all_warnings.extend(warnings_)

    if result_leaves:
        prompt = build_results_round_prompt(manifest_context, result_leaves, artifacts)
        raw_response, grades, warnings_ = run_score_round(
            client,
            prompt,
            result_leaves,
            label="arc-bench-score-results",
            round_name="results",
            raw_response_path=output_dir / "score_round_results_response.json",
            prompt_debug_path=output_dir / "score_round_results_prompt.txt",
        )
        raw_responses["results"] = raw_response
        leaf_grades.extend(grades)
        all_warnings.extend(warnings_)

    leaf_grades = order_leaf_grades(leaf_grades, leaves)
    category_scores = compute_category_scores(leaf_grades)
    overall = compute_overall_score(leaf_grades)
    results_only = compute_results_only_score(leaf_grades)
    scoring_summary = build_scoring_summary(category_scores, overall, results_only)
    result = {
        "schema_version": "simple_ar_arc_judge_result.v1",
        "backend": "llm",
        "scoring_profile": score_profile,
        "profile_notes": profile_notes(score_profile),
        "prompt_version": "simple_ar_arc_score_v2",
        "topic_id": manifest.get("id"),
        "title": manifest.get("title"),
        "prepared_dir": str(prepared_dir),
        "submission_dir": str(submission_dir),
        "artifact_paths": artifacts["paths"],
        "evidence_bundle_path": str(output_dir / "evidence_bundle.json"),
        "analysis_source": artifacts["evidence_bundle"].get("analysis_source"),
        "leaf_grades": leaf_grades,
        "category_scores": category_scores,
        "scoring_summary": scoring_summary,
        "overall_score": overall,
        "overall_strict": overall,
        "results_only": results_only,
        "overall_reasoning": summarize_round_reasoning(raw_responses),
        "warnings": all_warnings,
        "limitations": extract_round_limitations(raw_responses),
        "raw_rounds": raw_responses,
        "model": client.model,
        "scored_at": time.time(),
    }
    write_json(output_dir / "judge_result.json", result)
    write_text(output_dir / "scorecard.md", render_scorecard(result))
    if usage_rows:
        write_json(output_dir / "llm_usage_summary.json", summarize_llm_usage_rows(usage_rows))
    return result


def normalize_score_profile(value: str) -> str:
    profile = (value or "proxy").strip().lower()
    aliases = {
        "quick": "proxy",
        "fast": "proxy",
        "arc": "arc-auto",
        "arc_compatible": "arc-auto",
        "arc-compatible": "arc-auto",
    }
    profile = aliases.get(profile, profile)
    if profile not in {"proxy", "arc-auto", "strict"}:
        raise SystemExit(f"unknown score profile: {value!r}; expected proxy, arc-auto, or strict")
    return profile


def profile_notes(profile: str) -> list[str]:
    if profile == "strict":
        return [
            "Strict profile uses independent reviewers and disagreement adjudication.",
            "Use this profile for paper-facing comparisons; it is slower and more expensive than proxy.",
        ]
    if profile == "arc-auto":
        return [
            "ARC-auto profile approximates ARC-Bench's automatic two-round LLM judge.",
            "It is useful for regression testing but is not a substitute for strict two-reviewer evaluation.",
        ]
    return [
        "Proxy profile is a lightweight internal two-round scorer for development regression.",
        "Do not compare proxy scores directly with paper-reported ARC-Bench strict scores.",
    ]


def build_submission_evidence_bundle(
    *,
    manifest: dict[str, Any],
    rubric: dict[str, Any],
    prepared_dir: Path,
    submission_dir: Path,
    artifacts: dict[str, Any],
    score_profile: str,
) -> dict[str, Any]:
    parent = submission_dir.parent
    meta_path = parent / "arc_adapter_meta.json"
    meta = read_json(meta_path) if meta_path.is_file() else {}
    analysis_source = infer_analysis_source(submission_dir, parent, meta)
    return {
        "schema_version": "simple_ar_submission_evidence_bundle.v1",
        "benchmark_id": "arc_bench",
        "topic_id": manifest.get("id"),
        "title": manifest.get("title"),
        "score_profile": score_profile,
        "prepared_dir": str(prepared_dir),
        "submission_dir": str(submission_dir),
        "rubric_leaf_count": len(list(iter_rubric_leaves(rubric))),
        "rubric_categories": summarize_rubric_categories(rubric),
        "manifest_context": build_manifest_context(manifest),
        "artifact_paths": artifacts.get("paths", {}),
        "code_files": artifacts.get("code_files", []),
        "all_code_files": artifacts.get("all_code_files", []),
        "code_selection_notes": artifacts.get("code_selection_notes", []),
        "code_evidence_excerpt": clip_text(str(artifacts.get("code_text") or ""), 12000),
        "metrics_digest": artifacts.get("metrics", {}),
        "claims_digest": compact_claims_for_bundle(artifacts.get("claims")),
        "experiment_summary_digest": compact_experiment_summary_for_bundle(artifacts.get("experiment_summary")),
        "writeup_number_claims": extract_number_claims(str(artifacts.get("writeup") or "")),
        "analysis_source": analysis_source,
        "adapter_generated_analysis": analysis_source == "adapter-generated",
        "adapter_notes": profile_notes(score_profile),
        "arc_adapter_meta": clip_data(meta, 6000),
    }


def infer_analysis_source(submission_dir: Path, parent: Path, meta: dict[str, Any]) -> str:
    metadata = meta.get("metadata") if isinstance(meta, dict) else {}
    if isinstance(metadata, dict):
        if metadata.get("adapter_generated_analysis") is True:
            return "adapter-generated"
        if metadata.get("analysis_source"):
            return str(metadata["analysis_source"])
    readme = submission_dir / "README.md"
    analysis_report = parent / "result_analysis" / "analysis_report.md"
    if readme.is_file() and analysis_report.is_file():
        try:
            if readme.read_text(encoding="utf-8", errors="ignore").strip() == analysis_report.read_text(
                encoding="utf-8", errors="ignore"
            ).strip():
                return "adapter-generated"
        except OSError:
            pass
    return "agent-original"


def summarize_rubric_categories(rubric: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for leaf in iter_rubric_leaves(rubric):
        category = str(leaf.get("task_category") or "Uncategorized")
        group = groups.setdefault(category, {"category": category, "leaf_count": 0, "weight": 0.0})
        group["leaf_count"] += 1
        group["weight"] += coerce_weight(leaf.get("weight"))
    return list(groups.values())


def compact_claims_for_bundle(value: Any) -> Any:
    if not isinstance(value, dict):
        return clip_data(value, 8000)
    keys = ("summary_metrics", "hypothesis_verdicts", "claims", "rubric_coverage", "metric_summary")
    return {key: clip_data(value.get(key), 8000) for key in keys if key in value}


def compact_experiment_summary_for_bundle(value: Any) -> Any:
    if not isinstance(value, dict):
        return clip_data(value, 8000)
    keys = (
        "topic_id",
        "title",
        "expected_conditions",
        "expected_datasets",
        "expected_metrics",
        "hypotheses",
        "metric_values",
        "project_results",
    )
    return {key: clip_data(value.get(key), 12000) for key in keys if key in value}


def extract_number_claims(text: str, *, limit: int = 24) -> list[str]:
    rows: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or not re.search(r"\d", stripped):
            continue
        if len(stripped) > 260:
            stripped = stripped[:257] + "..."
        rows.append(stripped)
        if len(rows) >= limit:
            break
    return rows


def score_submission_strict(
    *,
    client: Any,
    manifest: dict[str, Any],
    rubric: dict[str, Any],
    prepared_dir: Path,
    submission_dir: Path,
    output_dir: Path,
    artifacts: dict[str, Any],
    leaves: list[dict[str, Any]],
    reviewer_count: int,
    disagreement_threshold: float,
) -> dict[str, Any]:
    reviewer_count = max(1, min(int(reviewer_count or 2), 3))
    disagreement_threshold = max(0.0, float(disagreement_threshold))
    reviewers: list[dict[str, Any]] = []
    all_warnings: list[str] = []
    for index in range(reviewer_count):
        reviewer_id = chr(ord("A") + index)
        reviewer = run_strict_reviewer(
            client=client,
            manifest=manifest,
            leaves=leaves,
            artifacts=artifacts,
            output_dir=output_dir,
            reviewer_id=reviewer_id,
        )
        reviewers.append(reviewer)
        all_warnings.extend(str(item) for item in reviewer.get("warnings", []))
        write_json(output_dir / f"reviewer_{reviewer_id.lower()}.json", reviewer)

    disagreements = find_strict_disagreements(reviewers, leaves, threshold=disagreement_threshold)
    write_json(output_dir / "disagreements.json", {"threshold": disagreement_threshold, "items": disagreements})
    adjudication: dict[str, Any] = {"leaf_grades": [], "raw_round": {}, "warnings": []}
    if disagreements:
        adjudication = run_strict_adjudication(
            client=client,
            manifest=manifest,
            leaves=leaves,
            artifacts=artifacts,
            disagreements=disagreements,
            output_dir=output_dir,
        )
        all_warnings.extend(str(item) for item in adjudication.get("warnings", []))
    else:
        write_json(output_dir / "adjudication.json", adjudication)

    leaf_grades = combine_strict_reviewer_grades(
        reviewers=reviewers,
        adjudication=adjudication,
        leaves=leaves,
    )
    category_scores = compute_category_scores(leaf_grades)
    overall = compute_overall_score(leaf_grades)
    results_only = compute_results_only_score(leaf_grades)
    scoring_summary = build_scoring_summary(category_scores, overall, results_only)
    scoring_summary.update(
        {
            "reviewer_count": reviewer_count,
            "disagreement_threshold": disagreement_threshold,
            "disagreement_count": len(disagreements),
            "disagreement_rate": round(len(disagreements) / len(leaves), 4) if leaves else 0.0,
            "adjudicated_leaf_count": len(adjudication.get("leaf_grades") or []),
            "strict_profile": True,
        }
    )
    result = {
        "schema_version": "simple_ar_arc_judge_result.v1",
        "backend": "llm",
        "scoring_profile": "strict",
        "profile_notes": profile_notes("strict"),
        "prompt_version": "simple_ar_arc_strict_score_v2",
        "topic_id": manifest.get("id"),
        "title": manifest.get("title"),
        "prepared_dir": str(prepared_dir),
        "submission_dir": str(submission_dir),
        "artifact_paths": artifacts["paths"],
        "evidence_bundle_path": str(output_dir / "evidence_bundle.json"),
        "analysis_source": artifacts["evidence_bundle"].get("analysis_source"),
        "leaf_grades": leaf_grades,
        "category_scores": category_scores,
        "scoring_summary": scoring_summary,
        "overall_score": overall,
        "overall_strict": overall,
        "results_only": results_only,
        "overall_reasoning": summarize_strict_reasoning(reviewers, adjudication),
        "warnings": sorted(set(all_warnings)),
        "limitations": sorted(set(extract_strict_limitations(reviewers, adjudication))),
        "reviewers": summarize_reviewer_results(reviewers),
        "disagreements": disagreements,
        "adjudication": adjudication,
        "model": client.model,
        "scored_at": time.time(),
    }
    write_json(output_dir / "judge_result.json", result)
    write_text(output_dir / "scorecard.md", render_scorecard(result))
    return result


def run_strict_reviewer(
    *,
    client: Any,
    manifest: dict[str, Any],
    leaves: list[dict[str, Any]],
    artifacts: dict[str, Any],
    output_dir: Path,
    reviewer_id: str,
) -> dict[str, Any]:
    manifest_context = build_manifest_context(manifest)
    code_leaves = [leaf for leaf in leaves if is_code_development_leaf(leaf)]
    result_leaves = [leaf for leaf in leaves if not is_code_development_leaf(leaf)]
    leaf_grades: list[dict[str, Any]] = []
    raw_rounds: dict[str, Any] = {}
    warnings_: list[str] = []
    system = strict_score_system_prompt(reviewer_id)
    if code_leaves:
        prompt = strict_prompt_addendum(build_code_round_prompt(manifest_context, code_leaves, artifacts), artifacts)
        raw, grades, round_warnings = run_score_round(
            client,
            prompt,
            code_leaves,
            label=f"arc-bench-strict-reviewer-{reviewer_id.lower()}-code",
            round_name=f"reviewer_{reviewer_id.lower()}_code",
            raw_response_path=output_dir / f"reviewer_{reviewer_id.lower()}_code_response.json",
            prompt_debug_path=output_dir / f"reviewer_{reviewer_id.lower()}_code_prompt.txt",
            system_prompt_text=system,
        )
        raw_rounds["code"] = raw
        leaf_grades.extend(grades)
        warnings_.extend(round_warnings)
    if result_leaves:
        prompt = strict_prompt_addendum(build_results_round_prompt(manifest_context, result_leaves, artifacts), artifacts)
        raw, grades, round_warnings = run_score_round(
            client,
            prompt,
            result_leaves,
            label=f"arc-bench-strict-reviewer-{reviewer_id.lower()}-results",
            round_name=f"reviewer_{reviewer_id.lower()}_results",
            raw_response_path=output_dir / f"reviewer_{reviewer_id.lower()}_results_response.json",
            prompt_debug_path=output_dir / f"reviewer_{reviewer_id.lower()}_results_prompt.txt",
            system_prompt_text=system,
        )
        raw_rounds["results"] = raw
        leaf_grades.extend(grades)
        warnings_.extend(round_warnings)
    leaf_grades = order_leaf_grades(leaf_grades, leaves)
    category_scores = compute_category_scores(leaf_grades)
    overall = compute_overall_score(leaf_grades)
    return {
        "reviewer_id": reviewer_id,
        "leaf_grades": leaf_grades,
        "category_scores": category_scores,
        "overall_score": overall,
        "results_only": compute_results_only_score(leaf_grades),
        "raw_rounds": raw_rounds,
        "warnings": warnings_,
        "limitations": extract_round_limitations(raw_rounds),
        "overall_reasoning": summarize_round_reasoning(raw_rounds),
    }


def strict_score_system_prompt(reviewer_id: str) -> str:
    return (
        score_system_prompt()
        + f" You are strict reviewer {reviewer_id}. Work independently. "
        "For each leaf, quote concrete evidence using file path + line number, JSON key path, metric name, or numeric value. "
        "Do not give high scores for fluent prose alone. Code Development requires real executable implementation, not labels. "
        "Code Execution requires completed runs, persisted artifacts, and coverage over required conditions/datasets/seeds/metrics. "
        "Result Analysis receives double weight conceptually: verify that each conclusion is traceable to measured numbers and that limitations are honest. "
        "If adapter-generated analysis is being scored, state whether the score relies on adapter-facing artifacts rather than original agent writeup. "
        "Apply the strict audit contract: implementation correctness, number grounding, verdict-data consistency, coverage, and unfinished-run penalties."
    )


def strict_prompt_addendum(prompt: str, artifacts: dict[str, Any]) -> str:
    bundle = artifacts.get("evidence_bundle") or {}
    return (
        f"{prompt}\n\n"
        "## ARC-Style Strict Audit Contract\n"
        "- Implementation correctness: verify the algorithmic behavior from code, not just function names, labels, comments, or README claims.\n"
        "- Number grounding: every credited numerical claim must trace to captured JSON, metrics, logs, tables, or code-produced artifacts.\n"
        "- Verdict-data consistency: a supported/refuted/inconclusive hypothesis verdict must agree with the measured evidence.\n"
        "- Coverage: missing required conditions, datasets, seeds, metrics, or persisted outputs should reduce the relevant execution/result leaves proportionally.\n"
        "- Unfinished science loop: if execution did not complete or no final writeup/claims were produced, Code Execution should be very low and Result Analysis cannot receive high credit.\n"
        "- Common false-positive patterns to watch for: methods that are named but not implemented, baselines that share identical code paths, stochastic/seed requirements that produce only one effective run, and writeups with invented or untraceable numbers.\n\n"
        "## Strict Evidence Bundle Summary\n"
        f"{json.dumps(clip_data(bundle, 18000), ensure_ascii=False, indent=2, default=str)}\n\n"
        "Strict checks:\n"
        "- Evidence must be artifact-grounded, not inferred from task text alone.\n"
        "- Scores above 0.7 require direct implementation or numeric evidence.\n"
        "- Missing leaf evidence should be scored 0.5 or lower even if the writeup sounds plausible.\n"
    )


def find_strict_disagreements(
    reviewers: list[dict[str, Any]],
    leaves: list[dict[str, Any]],
    *,
    threshold: float,
) -> list[dict[str, Any]]:
    if len(reviewers) < 2:
        return []
    by_reviewer = [
        {str(row.get("id")): row for row in reviewer.get("leaf_grades", [])}
        for reviewer in reviewers
    ]
    items: list[dict[str, Any]] = []
    for leaf in leaves:
        leaf_id = str(leaf.get("id"))
        scores = [
            float(rows[leaf_id].get("score") or 0.0)
            for rows in by_reviewer
            if leaf_id in rows
        ]
        if len(scores) < 2:
            continue
        delta = max(scores) - min(scores)
        if delta > threshold + 1e-9:
            items.append(
                {
                    "leaf_id": leaf_id,
                    "delta": round(delta, 4),
                    "scores": scores,
                    "reviewer_rows": [rows.get(leaf_id, {}) for rows in by_reviewer],
                    "requirements": str(leaf.get("requirements") or ""),
                }
            )
    return items


def run_strict_adjudication(
    *,
    client: Any,
    manifest: dict[str, Any],
    leaves: list[dict[str, Any]],
    artifacts: dict[str, Any],
    disagreements: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, Any]:
    disputed_ids = {str(item.get("leaf_id")) for item in disagreements}
    disputed_leaves = [leaf for leaf in leaves if str(leaf.get("id")) in disputed_ids]
    prompt = (
        "## Topic Context\n"
        f"{build_manifest_context(manifest)}\n\n"
        "## Disputed Leaves\n"
        f"{format_leaves_for_prompt(disputed_leaves)}\n\n"
        "## Reviewer Disagreements\n"
        f"{json.dumps(disagreements, ensure_ascii=False, indent=2, default=str)}\n\n"
        "## Evidence Bundle\n"
        f"{json.dumps(clip_data(artifacts.get('evidence_bundle') or {}, 22000), ensure_ascii=False, indent=2, default=str)}\n\n"
        "Adjudicate only the disputed leaves. Prefer the score best supported by concrete artifacts; do not split the difference by default.\n\n"
        f"{score_output_instruction(disputed_leaves)}"
    )
    raw, grades, warnings_ = run_score_round(
        client,
        prompt,
        disputed_leaves,
        label="arc-bench-strict-adjudication",
        round_name="adjudication",
        raw_response_path=output_dir / "adjudication_response.json",
        prompt_debug_path=output_dir / "adjudication_prompt.txt",
        system_prompt_text=strict_adjudicator_system_prompt(),
    )
    result = {
        "leaf_grades": grades,
        "raw_round": raw,
        "warnings": warnings_,
        "overall_reasoning": raw.get("overall_reasoning") if isinstance(raw, dict) else "",
        "limitations": raw.get("limitations", []) if isinstance(raw, dict) else [],
    }
    write_json(output_dir / "adjudication.json", result)
    return result


def strict_adjudicator_system_prompt() -> str:
    return (
        score_system_prompt()
        + " You are the strict adjudicator. Resolve only disputed rubric leaves. "
        "Use reviewer reasoning as hypotheses, then decide from artifact evidence. "
        "Return concrete scores and cite the strongest evidence or absence of evidence."
    )


def combine_strict_reviewer_grades(
    *,
    reviewers: list[dict[str, Any]],
    adjudication: dict[str, Any],
    leaves: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    reviewer_maps = [
        {str(row.get("id")): row for row in reviewer.get("leaf_grades", [])}
        for reviewer in reviewers
    ]
    adjudicated = {str(row.get("id")): row for row in adjudication.get("leaf_grades", []) if row.get("id")}
    combined: list[dict[str, Any]] = []
    for leaf in leaves:
        leaf_id = str(leaf.get("id"))
        rows = [mapping[leaf_id] for mapping in reviewer_maps if leaf_id in mapping]
        if leaf_id in adjudicated:
            row = dict(adjudicated[leaf_id])
            row["source_round"] = "strict_adjudication"
            row["reviewer_scores"] = [round(float(item.get("score") or 0.0), 4) for item in rows]
        elif rows:
            avg = sum(float(row.get("score") or 0.0) for row in rows) / len(rows)
            base = dict(rows[0])
            base["score"] = round(avg, 4)
            base["source_round"] = "strict_reviewer_average"
            base["reviewer_scores"] = [round(float(item.get("score") or 0.0), 4) for item in rows]
            base["reasoning"] = combine_reasoning(rows)
            row = base
        else:
            row = {
                "id": leaf_id,
                "category": str(leaf.get("task_category") or "Uncategorized"),
                "fine_category": str(leaf.get("finegrained_task_category") or ""),
                "weight": coerce_weight(leaf.get("weight")),
                "score": 0.5,
                "reasoning": "No reviewer returned this leaf; defaulted to 0.5.",
                "requirements": str(leaf.get("requirements") or ""),
                "source_round": "strict_missing_default",
            }
        row.setdefault("category", str(leaf.get("task_category") or "Uncategorized"))
        row.setdefault("fine_category", str(leaf.get("finegrained_task_category") or ""))
        row.setdefault("weight", coerce_weight(leaf.get("weight")))
        row.setdefault("requirements", str(leaf.get("requirements") or ""))
        combined.append(row)
    return combined


def combine_reasoning(rows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for index, row in enumerate(rows, start=1):
        reasoning = str(row.get("reasoning") or row.get("evidence") or "").strip()
        if reasoning:
            parts.append(f"Reviewer {index}: {reasoning}")
    return " | ".join(parts)[:1600]


def summarize_strict_reasoning(reviewers: list[dict[str, Any]], adjudication: dict[str, Any]) -> str:
    parts = [
        f"Reviewer {row.get('reviewer_id')}: overall={float(row.get('overall_score') or 0.0):.3f}"
        for row in reviewers
    ]
    adjudicated = adjudication.get("leaf_grades") or []
    if adjudicated:
        parts.append(f"Adjudicated {len(adjudicated)} disputed leaf/leaves.")
    return "; ".join(parts)


def summarize_reviewer_results(reviewers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "reviewer_id": row.get("reviewer_id"),
            "overall_score": row.get("overall_score"),
            "results_only": row.get("results_only"),
            "category_scores": row.get("category_scores"),
            "warning_count": len(row.get("warnings") or []),
        }
        for row in reviewers
    ]


def extract_strict_limitations(reviewers: list[dict[str, Any]], adjudication: dict[str, Any]) -> list[str]:
    limitations: list[str] = []
    for row in reviewers:
        limitations.extend(str(item) for item in row.get("limitations", []) if item)
    limitations.extend(str(item) for item in adjudication.get("limitations", []) if item)
    return limitations


def load_score_artifacts(
    submission_dir: Path,
    *,
    manifest: dict[str, Any],
    rubric: dict[str, Any],
    max_code_chars: int,
    max_result_chars: int,
    max_writeup_chars: int,
) -> dict[str, Any]:
    code_dir = submission_dir / "code"
    results_dir = submission_dir / "results"
    readme_path = submission_dir / "README.md"
    claims_path = submission_dir / "claims.json"
    parent = submission_dir.parent
    summary_path = parent / "stage-14" / "experiment_summary.json"
    metrics_path = results_dir / "metrics.json"

    focus_terms = score_focus_terms(manifest, rubric)
    code_text, code_files, all_code_files, code_selection_notes = collect_code_text(
        code_dir,
        max_chars=max_code_chars,
        focus_terms=focus_terms,
    )
    metrics = read_json(metrics_path) if metrics_path.is_file() else {}
    claims = read_json(claims_path) if claims_path.is_file() else {}
    summary = read_json(summary_path) if summary_path.is_file() else {}
    writeup = readme_path.read_text(encoding="utf-8", errors="ignore") if readme_path.is_file() else ""
    return {
        "paths": {
            "code_dir": str(code_dir) if code_dir.is_dir() else "",
            "metrics": str(metrics_path) if metrics_path.is_file() else "",
            "claims": str(claims_path) if claims_path.is_file() else "",
            "readme": str(readme_path) if readme_path.is_file() else "",
            "experiment_summary": str(summary_path) if summary_path.is_file() else "",
        },
        "code_files": code_files,
        "all_code_files": all_code_files,
        "code_selection_notes": code_selection_notes,
        "code_text": code_text,
        "metrics": clip_data(metrics, max_result_chars),
        "claims": clip_data(claims, max_result_chars),
        "experiment_summary": clip_data(summary, max_result_chars),
        "writeup": clip_text(writeup, max_writeup_chars),
    }


def collect_code_text(
    code_dir: Path,
    *,
    max_chars: int,
    focus_terms: set[str] | None = None,
) -> tuple[str, list[str], list[str], list[str]]:
    if not code_dir.is_dir():
        return "(no submission/code directory found)", [], [], ["submission/code directory was not found"]
    focus_terms = focus_terms or set()
    file_infos = collect_code_file_infos(code_dir, focus_terms=focus_terms)
    all_files = [info["rel"] for info in file_infos]
    if not file_infos:
        return "(no Python source files found)", [], [], ["no Python source files found"]
    ordered = order_code_files_for_review(file_infos)
    parts: list[str] = []
    included: list[str] = []
    notes: list[str] = []
    used = 0
    per_file_limit = max(3500, min(10000, max_chars // 4 if max_chars > 0 else 3500))
    for info in ordered:
        rel = str(info["rel"])
        text = str(info["text"])
        header = f"\n# === {rel} ===\n"
        remaining = max_chars - used - len(header)
        if remaining <= 0:
            break
        numbered = add_line_numbers(text)
        file_budget = min(remaining, per_file_limit)
        snippet = numbered if len(numbered) <= file_budget else numbered[:file_budget] + "\n# ... clipped ...\n"
        parts.append(header + snippet)
        included.append(rel)
        used += len(header) + len(snippet)
        if len(numbered) > file_budget:
            notes.append(f"{rel} clipped to {file_budget} chars for code-review evidence.")
        if used >= max_chars:
            break
    omitted = [rel for rel in all_files if rel not in set(included)]
    if omitted:
        notes.append(f"Omitted {len(omitted)} Python file(s) after evidence budget: {', '.join(omitted[:12])}.")
    notes.insert(
        0,
        "Code evidence is selected by entrypoint/import graph and task-term relevance, not raw filename order.",
    )
    return "\n".join(parts) if parts else "(no Python source files found)", included, all_files, notes


def score_focus_terms(manifest: dict[str, Any], rubric: dict[str, Any]) -> set[str]:
    text_parts = [
        json.dumps(manifest, ensure_ascii=False, default=str),
        json.dumps(rubric, ensure_ascii=False, default=str),
    ]
    terms: set[str] = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", "\n".join(text_parts).lower()):
        if token in SCORE_TERM_STOPWORDS:
            continue
        terms.add(token)
    return terms


SCORE_TERM_STOPWORDS = {
    "category",
    "code",
    "data",
    "dataset",
    "datasets",
    "description",
    "evidence",
    "experiment",
    "implementation",
    "metric",
    "metrics",
    "method",
    "methods",
    "model",
    "models",
    "result",
    "results",
    "score",
    "task",
    "test",
    "train",
    "with",
    "without",
}


def collect_code_file_infos(code_dir: Path, *, focus_terms: set[str]) -> list[dict[str, Any]]:
    infos: list[dict[str, Any]] = []
    for path in sorted(code_dir.rglob("*.py")):
        if not path.is_file():
            continue
        rel = path.relative_to(code_dir).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not text.strip():
            continue
        imports = local_import_names(text)
        lower = (rel + "\n" + text[:20000]).lower()
        term_hits = sum(1 for term in focus_terms if term in lower)
        infos.append(
            {
                "path": path,
                "rel": rel,
                "stem": path.stem,
                "text": text,
                "imports": imports,
                "term_hits": min(term_hits, 40),
                "has_main_guard": "__name__" in text and "__main__" in text,
            }
        )
    return infos


def local_import_names(text: str) -> set[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
            for alias in node.names:
                if node.level:
                    names.add(alias.name.split(".")[0])
    return names


def order_code_files_for_review(infos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stems = {str(info["stem"]): str(info["rel"]) for info in infos}
    entry_stems = {
        str(info["stem"])
        for info in infos
        if str(info["rel"]).endswith("main.py")
        or str(info["rel"]).endswith("run.py")
        or str(info["rel"]).endswith("train.py")
        or bool(info.get("has_main_guard"))
    }
    imported_from_entry = reachable_import_stems(infos, entry_stems, max_depth=2)

    def score(info: dict[str, Any]) -> tuple[int, str]:
        rel = str(info["rel"])
        stem = str(info["stem"])
        value = int(info.get("term_hits", 0))
        if stem in entry_stems:
            value += 100
        if stem in imported_from_entry:
            value += 70
        value += path_role_score(rel)
        if stem not in stems:
            value -= 10
        if rel.endswith("__init__.py"):
            value -= 80
        return (-value, rel)

    return sorted(infos, key=score)


def reachable_import_stems(infos: list[dict[str, Any]], entry_stems: set[str], *, max_depth: int) -> set[str]:
    known = {str(info["stem"]) for info in infos}
    imports_by_stem = {str(info["stem"]): set(info.get("imports") or set()) & known for info in infos}
    reached: set[str] = set()
    frontier = set(entry_stems)
    for _ in range(max_depth):
        next_frontier: set[str] = set()
        for stem in frontier:
            for imported in imports_by_stem.get(stem, set()):
                if imported not in reached:
                    reached.add(imported)
                    next_frontier.add(imported)
        frontier = next_frontier
        if not frontier:
            break
    return reached


def path_role_score(rel_path: str) -> int:
    path = rel_path.lower()
    score = 0
    high_value = (
        "model",
        "method",
        "algorithm",
        "optimizer",
        "forecast",
        "runner",
        "experiment",
        "evaluation",
        "metrics",
        "synth",
        "dataset",
        "data",
        "core",
    )
    medium_value = ("feature", "preprocess", "pipeline", "analysis")
    low_value = ("artifact", "report", "readme", "config")
    if any(term in path for term in high_value):
        score += 45
    if any(term in path for term in medium_value):
        score += 25
    if any(term in path for term in low_value):
        score -= 10
    return score


def add_line_numbers(text: str) -> str:
    return "\n".join(f"{index:04d}: {line}" for index, line in enumerate(text.splitlines(), start=1))


def build_manifest_context(manifest: dict[str, Any]) -> str:
    design = manifest.get("experiment_design") or {}
    lines = [
        f"Topic: {manifest.get('id')} - {manifest.get('title', '')}",
        f"Research question: {design.get('research_question') or manifest.get('synthesis') or ''}",
        "Expected conditions:",
    ]
    for condition in design.get("conditions") or []:
        if isinstance(condition, dict):
            lines.append(f"- {condition.get('name', '?')}: {condition.get('description', '')}")
    lines.append("Expected datasets:")
    for dataset in design.get("datasets") or []:
        if isinstance(dataset, dict):
            lines.append(f"- {dataset.get('name', '?')}: {dataset.get('source', '')}")
    lines.append("Expected metrics:")
    for metric in design.get("metrics") or []:
        if isinstance(metric, dict):
            lines.append(f"- {metric.get('name', '?')} ({metric.get('direction', 'unknown')}): {metric.get('description', '')}")
    lines.append("Hypotheses:")
    for hypothesis in manifest.get("hypotheses") or []:
        if isinstance(hypothesis, dict):
            lines.append(f"- {hypothesis.get('id', '?')}: {hypothesis.get('statement', '')}")
    return "\n".join(lines)


def is_code_development_leaf(leaf: dict[str, Any]) -> bool:
    return str(leaf.get("task_category") or "").lower().startswith("code dev")


def score_system_prompt() -> str:
    return (
        "You are a strict scientific reviewer for ARC-Bench-style autonomous "
        "research submissions. You grade rubric leaves with scores in [0.0, 1.0]. "
        "Return valid JSON only. The top-level object MUST contain a `grades` array "
        "and MUST NOT wrap grades under category names, prose sections, markdown, or "
        "any alternate object. Use this schema exactly: "
        "{\"grades\":[{\"leaf_id\":\"<id>\",\"score\":<float 0-1>,"
        "\"reasoning\":\"<specific evidence-grounded explanation>\"}],"
        "\"overall_reasoning\":\"<short optional summary>\","
        "\"limitations\":[\"<optional limitation>\"]}. "
        "Scoring guide: 1.0 fully met with clear evidence; 0.7 mostly met with "
        "minor gaps; 0.5 partially met or unclear; 0.3 attempted but substantially "
        "incomplete; 0.0 absent, contradicted, fabricated, or not evidenced. "
        "Apply ARC-style strict criteria uniformly. Implementation correctness: "
        "verify that the code actually implements the requested method or baseline, "
        "not merely a similarly named placeholder. Number grounding: every numerical "
        "claim you credit must be traceable to captured JSON, metrics, logs, tables, "
        "or other persisted artifacts. Verdict-data consistency: hypothesis verdicts "
        "must agree with the measured evidence. Coverage: missing conditions, "
        "datasets, seeds, metrics, or persisted outputs must reduce the relevant "
        "score proportionally. Unfinished runs or missing final analysis cannot "
        "receive high Code Execution or Result Analysis credit. Do not reward intent, "
        "fluent prose, or task restatement alone."
    )


def format_leaves_for_prompt(leaves: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for leaf in leaves:
        lines.append(f"- id: {leaf.get('id')}")
        lines.append(f"  category: {leaf.get('task_category', '')}")
        lines.append(f"  fine_category: {leaf.get('finegrained_task_category', '')}")
        lines.append(f"  weight: {leaf.get('weight', 1)}")
        lines.append(f"  requirements: {leaf.get('requirements', '')}")
    return "\n".join(lines)


def score_output_instruction(leaves: list[dict[str, Any]]) -> str:
    leaf_ids = [str(leaf.get("id")) for leaf in leaves if leaf.get("id")]
    return (
        "## Required JSON Output\n"
        "Return exactly one JSON object. No markdown fences. No prose before or after JSON.\n"
        "The top-level key `grades` is required. It must be an array with exactly one row for each leaf id below.\n"
        f"Required leaf ids: {json.dumps(leaf_ids, ensure_ascii=False)}\n"
        "Each row must contain: leaf_id, score, reasoning. Reasoning should cite concrete evidence or missing evidence using file path, line number, JSON key, metric name, or numeric value when available. If evidence is unclear, still return the leaf with score 0.5 or lower and explain why.\n"
    )


def build_code_round_prompt(manifest_context: str, leaves: list[dict[str, Any]], artifacts: dict[str, Any]) -> str:
    return (
        "## Topic Context\n"
        f"{manifest_context}\n\n"
        "## Rubric Leaves To Grade: Code Development\n"
        f"{format_leaves_for_prompt(leaves)}\n\n"
        "## Code Artifact Paths\n"
        f"{json.dumps(artifacts['paths'], ensure_ascii=False, indent=2)}\n\n"
        "## Code Files Included\n"
        f"{json.dumps(artifacts['code_files'], ensure_ascii=False, indent=2)}\n\n"
        "## All Python Files Discovered\n"
        f"{json.dumps(artifacts.get('all_code_files', []), ensure_ascii=False, indent=2)}\n\n"
        "## Code Evidence Selection Notes\n"
        f"{json.dumps(artifacts.get('code_selection_notes', []), ensure_ascii=False, indent=2)}\n\n"
        "## Final Agent-Produced Code With Line Numbers\n"
        "```python\n"
        f"{artifacts['code_text']}\n"
        "```\n\n"
        "Grade only the Code Development leaves above. Read the code semantically: "
        "check whether algorithms are genuinely implemented, not merely named. "
        "Cite file paths and line numbers from the code block when giving credit or docking. "
        "Dock code that routes all conditions through the same baseline, silently ignores requested datasets/seeds/metrics, "
        "or implements a simplified proxy while claiming the full method.\n\n"
        f"{score_output_instruction(leaves)}"
    )


def build_results_round_prompt(manifest_context: str, leaves: list[dict[str, Any]], artifacts: dict[str, Any]) -> str:
    result_payload = {
        "paths": artifacts["paths"],
        "experiment_summary": artifacts["experiment_summary"],
        "metrics": artifacts["metrics"],
        "claims": artifacts["claims"],
    }
    return (
        "## Topic Context\n"
        f"{manifest_context}\n\n"
        "## Rubric Leaves To Grade: Code Execution + Result Analysis\n"
        f"{format_leaves_for_prompt(leaves)}\n\n"
        "## Captured Execution Artifacts\n"
        "```json\n"
        f"{json.dumps(result_payload, ensure_ascii=False, indent=2, default=str)}\n"
        "```\n\n"
        "## Agent Writeup / README\n"
        f"{artifacts['writeup']}\n\n"
        "Grade only the Code Execution and Result Analysis leaves above. Verify "
        "that metrics exist on disk, conditions/datasets/seeds are covered, and "
        "hypothesis verdicts match measured numbers. Penalize fabricated or "
        "unsupported writeup claims. Strong Result Analysis requires explicit "
        "per-hypothesis verdicts, traceable numeric evidence, and honest limitations; "
        "a generic summary of overall metrics is not enough.\n\n"
        f"{score_output_instruction(leaves)}"
    )


def run_score_round(
    client: Any,
    prompt: str,
    leaves: list[dict[str, Any]],
    *,
    label: str,
    round_name: str,
    raw_response_path: Path,
    prompt_debug_path: Path | None = None,
    system_prompt_text: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    warnings_: list[str] = []
    last_response: dict[str, Any] | None = None
    last_error = ""
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        attempt_label = label if attempt == 1 else f"{label}-schema-retry-{attempt}"
        attempt_prompt = prompt if attempt == 1 else score_schema_retry_prompt(prompt, leaves, last_error=last_error)
        try:
            response = client.ask_json(system_prompt_text or score_system_prompt(), attempt_prompt, label=attempt_label)
        except Exception as exc:
            if prompt_debug_path is not None:
                write_text(prompt_debug_path, prompt)
            raise SystemExit(f"ARC {round_name} scoring failed before valid JSON was produced: {exc}") from exc
        last_response = response
        write_json(raw_response_path, response)
        if attempt > 1:
            write_json(
                raw_response_path.with_name(f"{raw_response_path.stem}_attempt_{attempt}{raw_response_path.suffix}"),
                response,
            )
        try:
            grades, round_warnings = normalize_round_grades(response, leaves, round_name=round_name)
        except ValueError as exc:
            last_error = str(exc)
            warnings_.append(f"{round_name}: score response schema attempt {attempt} failed: {last_error}")
            continue
        warnings_.extend(round_warnings)
        if attempt > 1:
            warnings_.append(f"{round_name}: recovered score schema after {attempt} attempts")
        return response, grades, warnings_
    if prompt_debug_path is not None:
        write_text(prompt_debug_path, prompt)
    raise SystemExit(
        f"ARC {round_name} score response did not contain `grades` after {max_attempts} attempt(s). "
        f"Raw response saved to {raw_response_path}."
    )


def score_schema_retry_prompt(prompt: str, leaves: list[dict[str, Any]], *, last_error: str) -> str:
    return (
        f"{prompt}\n\n"
        "## Previous Scoring Response Was Rejected\n"
        f"{last_error}\n"
        "Return a smaller JSON object now. The top-level object must contain `grades`; do not use any other top-level grade key.\n"
        f"{score_output_instruction(leaves)}"
    )


def normalize_round_grades(
    response: dict[str, Any],
    leaves: list[dict[str, Any]],
    *,
    round_name: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows = response.get("grades")
    if not isinstance(rows, list):
        rows = response.get("leaf_grades")
    if not isinstance(rows, list):
        rows = find_nested_grade_rows(response)
    if not isinstance(rows, list):
        raise ValueError(f"missing top-level grades array; response keys={sorted(str(key) for key in response.keys())}")

    leaves_by_id = {str(leaf.get("id")): leaf for leaf in leaves if leaf.get("id")}
    rows_by_id: dict[str, dict[str, Any]] = {}
    warnings_: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        leaf_id = str(row.get("leaf_id") or row.get("id") or row.get("criterion_id") or "").strip()
        if not leaf_id:
            continue
        if leaf_id not in leaves_by_id:
            warnings_.append(f"{round_name}: ignored unknown leaf id {leaf_id}")
            continue
        rows_by_id[leaf_id] = row

    grades: list[dict[str, Any]] = []
    for leaf_id, leaf in leaves_by_id.items():
        row = rows_by_id.get(leaf_id)
        if row is None:
            warnings_.append(f"{round_name}: missing grade for {leaf_id}; defaulted to 0.5 like ARC judge.py")
            row = {
                "score": 0.5,
                "reasoning": "(ungraded; LLM did not return this leaf)",
            }
        score, score_warning = coerce_round_score(
            row.get("score", row.get("grade")),
            leaf_id=leaf_id,
            round_name=round_name,
        )
        if score_warning:
            warnings_.append(score_warning)
        grades.append(
            {
                "id": leaf_id,
                "category": str(leaf.get("task_category") or "Uncategorized"),
                "fine_category": str(leaf.get("finegrained_task_category") or ""),
                "weight": coerce_weight(leaf.get("weight")),
                "score": score,
                "reasoning": str(row.get("reasoning") or row.get("reason") or "").strip(),
                "evidence": str(row.get("evidence") or "").strip(),
                "requirements": str(leaf.get("requirements") or ""),
                "source_round": round_name,
            }
        )
    return grades, warnings_


def find_nested_grade_rows(response: dict[str, Any]) -> list[Any] | None:
    """Recover common wrapper mistakes without weakening grading criteria."""
    candidate_keys = (
        "grades",
        "leaf_grades",
        "scores",
        "rubric_grades",
        "grade_rows",
        "evaluations",
    )
    for key in candidate_keys:
        value = response.get(key)
        if isinstance(value, list) and looks_like_grade_rows(value):
            return value
    for value in response.values():
        if isinstance(value, dict):
            for key in candidate_keys:
                nested = value.get(key)
                if isinstance(nested, list) and looks_like_grade_rows(nested):
                    return nested
        if isinstance(value, list) and looks_like_grade_rows(value):
            return value
    return None


def looks_like_grade_rows(rows: list[Any]) -> bool:
    if not rows:
        return False
    dict_rows = [row for row in rows if isinstance(row, dict)]
    if not dict_rows:
        return False
    return any(
        ("score" in row or "grade" in row)
        and ("leaf_id" in row or "id" in row or "criterion_id" in row)
        for row in dict_rows
    )


def coerce_round_score(value: Any, *, leaf_id: str, round_name: str) -> tuple[float, str]:
    warning = ""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.5, f"{round_name}: invalid score for {leaf_id}; defaulted to 0.5"
    if score < 0.0 or score > 1.0:
        warning = f"{round_name}: score for {leaf_id} was clamped from {score} into [0, 1]"
        score = max(0.0, min(1.0, score))
    return round(score, 4), warning


def order_leaf_grades(leaf_grades: list[dict[str, Any]], leaves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(row.get("id")): row for row in leaf_grades}
    ordered: list[dict[str, Any]] = []
    for leaf in leaves:
        leaf_id = str(leaf.get("id"))
        if leaf_id in by_id:
            ordered.append(by_id[leaf_id])
    return ordered


def coerce_weight(value: Any) -> float:
    try:
        weight = float(value)
    except (TypeError, ValueError):
        weight = 1.0
    return weight if weight > 0 else 1.0


def compute_overall_score(leaf_grades: list[dict[str, Any]]) -> float:
    total_weight = sum(float(row.get("weight") or 0.0) for row in leaf_grades)
    if total_weight <= 0:
        return 0.0
    weighted = sum(float(row["score"]) * float(row.get("weight") or 0.0) for row in leaf_grades)
    return round(weighted / total_weight, 4)


def compute_results_only_score(leaf_grades: list[dict[str, Any]]) -> float:
    rows = [row for row in leaf_grades if not str(row.get("category") or "").lower().startswith("code dev")]
    total_weight = sum(float(row.get("weight") or 0.0) for row in rows)
    if total_weight <= 0:
        return 0.0
    weighted = sum(float(row["score"]) * float(row.get("weight") or 0.0) for row in rows)
    return round(weighted / total_weight, 4)


def compute_category_scores(leaf_grades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in leaf_grades:
        category = str(row.get("category") or "Uncategorized")
        group = groups.setdefault(category, {"leaf_count": 0, "weight": 0.0, "weighted_sum": 0.0})
        weight = float(row.get("weight") or 0.0)
        group["leaf_count"] += 1
        group["weight"] += weight
        group["weighted_sum"] += weight * float(row.get("score") or 0.0)
    for group in groups.values():
        weight = float(group.get("weight") or 0.0)
        group["score"] = round(float(group.pop("weighted_sum")) / weight, 4) if weight else 0.0
        group["weight"] = round(weight, 4)
    return groups


def build_scoring_summary(
    category_scores: dict[str, dict[str, Any]],
    overall: float,
    results_only: float,
) -> dict[str, Any]:
    return {
        "category_normalized": {
            category: row.get("score", 0.0)
            for category, row in sorted(category_scores.items())
        },
        "category_weights": {
            category: row.get("weight", 0.0)
            for category, row in sorted(category_scores.items())
        },
        "overall_strict": overall,
        "results_only": results_only,
        "weighting_scheme": "leaf weighted average; ARC-Bench ML rubrics generally target CD:CE:RA = 25:25:50",
        "timeout_zero_exec_applied": False,
    }


def summarize_round_reasoning(raw_responses: dict[str, Any]) -> str:
    parts: list[str] = []
    for name in ("code", "results"):
        response = raw_responses.get(name)
        if isinstance(response, dict) and response.get("overall_reasoning"):
            parts.append(f"{name}: {response['overall_reasoning']}")
    return " ".join(parts)


def extract_round_limitations(raw_responses: dict[str, Any]) -> list[str]:
    limitations: list[str] = []
    for response in raw_responses.values():
        if isinstance(response, dict) and isinstance(response.get("limitations"), list):
            limitations.extend(str(item) for item in response["limitations"] if item)
    return sorted(set(limitations))


def render_scorecard(result: dict[str, Any]) -> str:
    summary = result.get("scoring_summary") if isinstance(result.get("scoring_summary"), dict) else {}
    lines = [
        f"# ARC-Bench Scorecard: {result.get('topic_id')}",
        "",
        f"- Overall strict: `{float(result.get('overall_strict') or result.get('overall_score') or 0.0):.3f}`",
        f"- Results only: `{float(result.get('results_only') or 0.0):.3f}`",
        f"- Backend: `{result.get('backend')}`",
        f"- Scoring profile: `{result.get('scoring_profile')}`",
        f"- Model: `{result.get('model')}`",
    ]
    if result.get("analysis_source"):
        lines.append(f"- Analysis source: `{result.get('analysis_source')}`")
    if result.get("scoring_profile") == "strict":
        lines.extend(
            [
                f"- Reviewers: `{summary.get('reviewer_count', 0)}`",
                f"- Disagreement rate: `{float(summary.get('disagreement_rate') or 0.0):.3f}`",
                f"- Adjudicated leaves: `{summary.get('adjudicated_leaf_count', 0)}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Category Scores",
            "",
            "| Category | Leaves | Weight | Score |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for category, row in sorted(result.get("category_scores", {}).items()):
        lines.append(
            f"| {category} | {row.get('leaf_count', 0)} | "
            f"{float(row.get('weight') or 0.0):.1f} | {float(row.get('score') or 0.0):.3f} |"
        )
    lines.extend(
        [
            "",
            "## Leaf Grades",
            "",
            "| Leaf | Category | Weight | Score | Evidence |",
            "| --- | --- | ---: | ---: | --- |",
        ]
    )
    for row in result.get("leaf_grades", []):
        evidence = escape_table_text(row.get("evidence") or row.get("reasoning") or "")
        reviewer_scores = row.get("reviewer_scores")
        if isinstance(reviewer_scores, list) and reviewer_scores:
            evidence = escape_table_text(f"reviewer_scores={reviewer_scores}; {evidence}")
        lines.append(
            f"| `{row.get('id')}` | {row.get('category')} | {float(row.get('weight') or 0.0):.1f} | "
            f"{float(row.get('score') or 0.0):.3f} | {evidence} |"
        )
    if result.get("reviewers"):
        lines.extend(["", "## Strict Reviewers", ""])
        for row in result.get("reviewers", []):
            lines.append(
                f"- Reviewer {row.get('reviewer_id')}: overall "
                f"`{float(row.get('overall_score') or 0.0):.3f}`, results-only "
                f"`{float(row.get('results_only') or 0.0):.3f}`"
            )
    if result.get("disagreements"):
        lines.extend(["", "## Disagreements", ""])
        for item in result.get("disagreements", []):
            lines.append(
                f"- `{item.get('leaf_id')}` delta `{float(item.get('delta') or 0.0):.3f}` "
                f"scores `{item.get('scores')}`"
            )
    if result.get("overall_reasoning"):
        lines.extend(["", "## Overall Reasoning", "", str(result["overall_reasoning"])])
    if result.get("limitations"):
        lines.extend(["", "## Limitations", ""])
        lines.extend(f"- {item}" for item in result["limitations"])
    if result.get("warnings"):
        lines.extend(["", "## Judge Warnings", ""])
        lines.extend(f"- {item}" for item in result["warnings"])
    return "\n".join(lines).strip() + "\n"


def escape_table_text(value: Any) -> str:
    text = str(value or "").replace("\n", " ").replace("|", "\\|")
    return re.sub(r"\s+", " ", text).strip()


def summarize_llm_usage_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def token(row: dict[str, Any], *keys: str) -> int:
        for key in keys:
            if row.get(key) is not None:
                return int(row.get(key) or 0)
        return 0

    return {
        "request_count": len(rows),
        "input_tokens": sum(token(row, "input_tokens", "prompt_tokens") for row in rows),
        "output_tokens": sum(token(row, "output_tokens", "completion_tokens") for row in rows),
        "total_tokens": sum(token(row, "total_tokens") for row in rows),
        "estimated_cost_usd": sum(float(row.get("estimated_cost_usd") or 0.0) for row in rows),
        "labels": [row.get("label") for row in rows if row.get("label")],
    }


def build_analysis_context(
    *,
    manifest: dict[str, Any],
    rubric: dict[str, Any],
    metrics: dict[str, float],
    project_results: dict[str, Any],
    run_dir: Path,
    code_src: Path | None,
    task_contract: dict[str, Any] | None = None,
    fallback_readme: str,
    fallback_claims: dict[str, Any],
) -> dict[str, Any]:
    design = manifest.get("experiment_design") or {}
    return {
        "task_id": str(manifest.get("id") or ""),
        "title": str(manifest.get("title") or ""),
        "research_question": str(design.get("research_question") or ""),
        "hypotheses": manifest.get("hypotheses", []),
        "criteria": list(iter_rubric_leaves(rubric)),
        "expected_metrics": design.get("metrics", []),
        "metric_directions": metric_directions_from_manifest(design.get("metrics") or []),
        "metrics": metrics,
        "project_results": project_results,
        "task_contract": task_contract or {},
        "existing_writeup": clip_text(extract_project_writeup(run_dir, code_src) or fallback_readme, 16000),
        "run_dir": str(run_dir),
        "benchmark": "arc-bench",
        "artifacts": {"code_source": str(code_src) if code_src else ""},
        "metadata": {
            "schema_version": "simple_ar_arc_analysis_context.v2",
            "fallback_claims": clip_data(fallback_claims, max_chars=12000),
            "expected_conditions": design.get("conditions", []),
            "expected_datasets": design.get("datasets", []),
        },
    }


def run_judge_command(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    submission_dir = Path(value_from(args, cfg, "judge", "submission_dir", ""))
    command = str(value_from(args, cfg, "judge", "judge_command", ""))
    output_dir = Path(value_from(args, cfg, "judge", "output_dir", ""))
    timeout_sec = int(value_from(args, cfg, "judge", "timeout_sec", 1800))

    if not submission_dir:
        raise SystemExit("--submission-dir is required")
    if not submission_dir.is_dir():
        raise SystemExit(f"submission dir not found: {submission_dir}")
    if not command:
        raise SystemExit(
            "--judge-command is required. Use placeholders like "
            "`{submission_dir}` and `{output_dir}` if the judge needs paths."
        )
    if not output_dir:
        output_dir = submission_dir / "judge"

    output_dir.mkdir(parents=True, exist_ok=True)
    resolved = format_judge_command(command, submission_dir=submission_dir, output_dir=output_dir)
    started = time.time()
    try:
        completed = subprocess.run(
            resolved,
            shell=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_sec,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        status = "completed"
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = decode_process_output(exc.stdout)
        stderr = decode_process_output(exc.stderr) + f"\nJudge timed out after {timeout_sec}s.\n"
        status = "timed_out"
    ended = time.time()
    write_text(output_dir / "stdout.txt", stdout)
    write_text(output_dir / "stderr.txt", stderr)
    write_json(
        output_dir / "judge_result.json",
        {
            "schema_version": "simple_ar_arc_judge_result.v1",
            "command": resolved,
            "submission_dir": str(submission_dir),
            "output_dir": str(output_dir),
            "status": status,
            "returncode": returncode,
            "started_at": started,
            "ended_at": ended,
            "duration_sec": round(ended - started, 3),
            "stdout_path": str(output_dir / "stdout.txt"),
            "stderr_path": str(output_dir / "stderr.txt"),
        },
    )
    print(f"Judge {status} with return code {returncode}; output saved to {output_dir}")
    return returncode


def decode_process_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def format_judge_command(command: str, *, submission_dir: Path, output_dir: Path) -> str:
    replacements = {
        "submission_dir": submission_dir.as_posix(),
        "submission": submission_dir.as_posix(),
        "output_dir": output_dir.as_posix(),
        "output": output_dir.as_posix(),
    }
    try:
        return command.format(**replacements)
    except KeyError as exc:
        raise SystemExit(f"unknown judge command placeholder: {exc}") from exc


def find_generated_project(run_dir: Path) -> Path | None:
    candidates = [
        run_dir / "code_task" / "workspace" / "generated_project",
        run_dir / "06-code" / "generated_project",
        run_dir / "06-code" / "code_task_run" / "code_task" / "workspace" / "generated_project",
    ]
    for path in candidates:
        if path.is_dir():
            return path
    matches = sorted(run_dir.glob("**/code_task/workspace/generated_project"))
    return matches[-1] if matches else None


def load_simple_ar_metrics(run_dir: Path) -> dict[str, float]:
    candidates = [
        run_dir / "code_task" / "run" / "patched" / "metrics.json",
        run_dir / "07-run" / "results.json",
        run_dir / "07-run" / "execution_report.json",
    ]
    for path in candidates:
        if path.is_file():
            data = read_json(path)
            metrics = numeric_metrics(data)
            if metrics:
                return metrics
    return {}


def load_project_results(run_dir: Path, code_src: Path | None) -> dict[str, Any]:
    for root in result_artifact_roots(run_dir, code_src):
        for name in ("results.json", "metrics.json"):
            path = root / name
            if path.is_file():
                data = read_json(path)
                if isinstance(data, dict):
                    data.setdefault("_artifact_source", str(path))
                    return data
    return {}


def load_code_task_contract(run_dir: Path) -> dict[str, Any]:
    candidates = [
        run_dir / "code_task" / "meta" / "task_contract.json",
        run_dir / "06-code" / "code_task_run" / "code_task" / "meta" / "task_contract.json",
    ]
    for path in candidates:
        if path.is_file():
            data = read_json(path)
            return data if isinstance(data, dict) else {}
    return {}


def merge_metrics(*sources: dict[str, float]) -> dict[str, float]:
    merged: dict[str, float] = {}
    for source in sources:
        merged.update(source)
    return merged


def build_experiment_summary(
    manifest: dict[str, Any],
    rubric: dict[str, Any],
    metrics: dict[str, float],
    project_results: dict[str, Any],
    run_dir: Path,
) -> dict[str, Any]:
    design = manifest.get("experiment_design") or {}
    return {
        "schema_version": "arc_experiment_summary.v1",
        "topic_id": manifest.get("id"),
        "title": manifest.get("title"),
        "research_question": design.get("research_question"),
        "expected_conditions": design.get("conditions", []),
        "expected_datasets": design.get("datasets", []),
        "expected_metrics": design.get("metrics", []),
        "hypotheses": manifest.get("hypotheses", []),
        "rubric_leaf_count": len(list(iter_rubric_leaves(rubric))),
        "metric_values": metrics,
        "project_results": project_results,
        "simple_ar_run_dir": str(run_dir),
    }


def build_submission_readme(manifest: dict[str, Any], run_dir: Path, code_src: Path | None) -> str:
    report = extract_project_writeup(run_dir, code_src)
    if not report:
        summary = run_dir / "code_task" / "summary.md"
        if summary.is_file():
            report = summary.read_text(encoding="utf-8", errors="ignore")
    if not report:
        report = "(SimpleAutoResearch run produced no readable report artifact.)"
    return "\n".join(
        [
            f"# SimpleAutoResearch ARC-Bench Submission: {manifest.get('id')} - {manifest.get('title', '')}",
            "",
            "## Agent-produced writeup",
            "",
            report,
            "",
        ]
    )


def extract_project_writeup(run_dir: Path, code_src: Path | None) -> str:
    for root in result_artifact_roots(run_dir, code_src):
        for name in ("report.md", "analysis.md", "README.md"):
            candidate = root / name
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8", errors="ignore")
    if code_src:
        candidate = code_src / "README.md"
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8", errors="ignore")
    for candidate in [
        run_dir / "code_task" / "summary.md",
        run_dir / "07-run" / "report.md",
        run_dir / "08-report" / "report.md",
    ]:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8", errors="ignore")
    return ""


def result_artifact_roots(run_dir: Path, code_src: Path | None) -> list[Path]:
    candidates: list[Path] = []
    if code_src:
        candidates.extend(
            [
                code_src / "artifacts",
                code_src.parent / "artifacts",
                code_src.parent / "submission" / "results",
            ]
        )
    candidates.extend(
        [
            run_dir / "code_task" / "workspace" / "artifacts",
            run_dir / "code_task" / "workspace" / "submission" / "results",
            run_dir / "06-code" / "generated_project" / "artifacts",
            run_dir / "06-code" / "artifacts",
            run_dir / "07-run",
        ]
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_dir():
            unique.append(path)
    return unique


def build_claims(manifest: dict[str, Any], metrics: dict[str, float], project_results: dict[str, Any]) -> dict[str, Any]:
    existing = extract_existing_claims(project_results)
    if existing:
        return existing
    verdicts = []
    for h in manifest.get("hypotheses") or []:
        verdicts.append(
            {
                "hypothesis_id": h.get("id"),
                "statement": h.get("statement"),
                "verdict": "not_evaluated",
                "evidence": "No structured hypothesis verdict was found; see metric_values and writeup.",
            }
        )
    return {
        "topic_id": manifest.get("id"),
        "summary_metrics": metrics,
        "hypothesis_verdicts": verdicts,
        "claims": verdicts,
    }


def extract_existing_claims(project_results: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("claims", "hypothesis_verdicts", "hypotheses"):
        value = project_results.get(key) if isinstance(project_results, dict) else None
        if isinstance(value, list) and value:
            return {"claims": value, "hypothesis_verdicts": value}
    return None


def build_reproduce_script(run_dir: Path) -> str:
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "# This submission was finalized from an existing SimpleAutoResearch run.",
            f'echo "Source run: {run_dir}"',
            'echo "Re-run with the prepared code_task.toml if exact regeneration is needed."',
            "",
        ]
    )


def print_topic_summary(arc_root: Path, manifest: dict[str, Any], rubric: dict[str, Any]) -> None:
    design = manifest.get("experiment_design") or {}
    print(json.dumps(
        {
            "arc_root": str(arc_root),
            "topic": manifest.get("id"),
            "title": manifest.get("title"),
            "manifest_path": manifest.get("_manifest_path"),
            "rubric_path": rubric.get("_rubric_path"),
            "conditions": [c.get("name") for c in design.get("conditions", [])],
            "datasets": [d.get("name") for d in design.get("datasets", [])],
            "metrics": [m.get("name") for m in design.get("metrics", [])],
            "hypotheses": [h.get("id") for h in manifest.get("hypotheses", [])],
            "rubric_leaves": len(list(iter_rubric_leaves(rubric))),
        },
        indent=2,
        ensure_ascii=False,
    ))


def iter_rubric_leaves(node: dict[str, Any]):
    children = node.get("sub_tasks") or []
    if not children:
        yield node
        return
    for child in children:
        yield from iter_rubric_leaves(child)


def render_bullets(items: list[str]) -> str:
    if not items:
        return "- (not provided)"
    return "\n".join(f"- {item}" for item in items)


def arc_direction_to_simple(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"minimize", "lower", "decrease"}:
        return "lower"
    if text in {"maximize", "higher", "increase"}:
        return "higher"
    if text in {"resource", "cost"}:
        return "resource"
    return "ignore"


def numeric_metrics(data: Any) -> dict[str, float]:
    source = data
    if isinstance(data, dict):
        for key in ("metric_values", "metrics", "metric_bundle"):
            if isinstance(data.get(key), dict):
                source = data[key]
                break
    if not isinstance(source, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in source.items():
        if str(key) in {"schema_version"}:
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            out[str(key)] = float(value)
    return out


def copy_tree_clean(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".ruff_cache")
    shutil.copytree(src, dst, ignore=ignore)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def summarize_usage_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "request_count": len(rows),
        "input_tokens": sum(int(row.get("input_tokens") or 0) for row in rows),
        "output_tokens": sum(int(row.get("output_tokens") or 0) for row in rows),
        "total_tokens": sum(int(row.get("total_tokens") or 0) for row in rows),
        "estimated_cost_usd": sum(float(row.get("estimated_cost_usd") or 0.0) for row in rows),
        "labels": [row.get("label") for row in rows if row.get("label")],
    }


def clip_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return text[:max_chars] + f"\n\n[... clipped {omitted} characters ...]"


def clip_data(data: Any, max_chars: int) -> Any:
    text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    if len(text) <= max_chars:
        return data
    return {
        "clipped": True,
        "max_chars": max_chars,
        "preview": clip_text(text, max_chars),
    }


def scrub_private_keys(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if not k.startswith("_")}


def path_for_toml(path: Path) -> str:
    return escape_toml_string(path.as_posix())


def path_for_shell(path: Path) -> str:
    text = path.as_posix()
    return f'"{text}"' if " " in text else text


def escape_toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


if __name__ == "__main__":
    raise SystemExit(main())

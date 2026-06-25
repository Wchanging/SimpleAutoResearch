#!/usr/bin/env python3
"""Standalone ARC-Bench adapter for SimpleAutoResearch.

This script intentionally lives outside src/simple_ar and does not import
SimpleAutoResearch internals. It prepares ARC-Bench topics as greenfield
code-task configs and finalizes SimpleAutoResearch run artifacts into an
ARC-Bench-style submission layout.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import shutil
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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

    judge = sub.add_parser("judge", help="Run an external ARC-Bench judge command and save raw output.")
    judge.add_argument("--submission-dir", type=Path)
    judge.add_argument("--judge-command")
    judge.add_argument("--output-dir", type=Path)
    judge.add_argument("--timeout-sec", type=int)

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
        if not prepared_dir:
            raise SystemExit("--prepared-dir is required")
        if not run_dir:
            raise SystemExit("--run-dir is required")
        if not output_dir:
            output_dir = prepared_dir / "submission_from_run"
        finalize_submission(prepared_dir, run_dir, output_dir, force=force)
        return 0

    if args.command == "judge":
        return run_judge_command(args, cfg)

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
        {
            "schema_version": "simple_ar_arc_adapter.v1",
            "written_at": time.time(),
            "arc_root": str(options.arc_root),
            "topic": manifest.get("id"),
            "manifest_path": manifest.get("_manifest_path"),
            "rubric_path": rubric.get("_rubric_path"),
            "task_file": str(task_path),
            "code_task_config": str(config_path),
            "simple_ar_output_root": str(options.simple_ar_output_root),
            "benchmark_command": options.benchmark_command,
        },
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
            "llm_retry_attempts = 2",
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
        ]
    )


def finalize_submission(prepared_dir: Path, run_dir: Path, output_dir: Path, *, force: bool = False) -> None:
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

    metrics = load_simple_ar_metrics(run_dir)
    project_results = load_project_results(code_src) if code_src else {}
    experiment_summary = build_experiment_summary(manifest, rubric, metrics, project_results, run_dir)
    write_json(results_dst / "metrics.json", {"metrics": metrics, "project_results": project_results})
    write_json(output_dir / "stage-14" / "experiment_summary.json", experiment_summary)
    write_text(submission / "README.md", build_submission_readme(manifest, run_dir, code_src))
    write_json(submission / "claims.json", build_claims(manifest, metrics, project_results))
    write_text(submission / "reproduce.sh", build_reproduce_script(run_dir))
    write_json(
        output_dir / "arc_adapter_meta.json",
        {
            "schema_version": "simple_ar_arc_submission.v1",
            "written_at": time.time(),
            "prepared_dir": str(prepared_dir),
            "run_dir": str(run_dir),
            "code_source": str(code_src) if code_src else "",
            "metric_count": len(metrics),
            "topic_id": manifest.get("id"),
        },
    )
    print(f"Finalized ARC-style submission at {output_dir}")


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


def load_project_results(code_src: Path) -> dict[str, Any]:
    candidates = [
        code_src / "artifacts" / "results.json",
        code_src / "artifacts" / "metrics.json",
    ]
    for path in candidates:
        if path.is_file():
            return read_json(path)
    return {}


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
    report = ""
    if code_src:
        for candidate in [code_src / "artifacts" / "report.md", code_src / "README.md"]:
            if candidate.is_file():
                report = candidate.read_text(encoding="utf-8", errors="ignore")
                break
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
        for key in ("metric_values", "metrics"):
            if isinstance(data.get(key), dict):
                source = data[key]
                break
    if not isinstance(source, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in source.items():
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

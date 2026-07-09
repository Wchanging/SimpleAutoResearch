from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from dotenv import load_dotenv


DEFAULT_SURVEYBENCH_ROOT = "SurveyBench"
DEFAULT_METHOD = "SimpleAutoResearch"
LOCAL_ROOT = Path(__file__).resolve().parent
DEFAULT_RESULTS_ROOT = LOCAL_ROOT / "results"


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    topic: str
    message: str

    def to_json(self) -> dict[str, str]:
        return {"severity": self.severity, "topic": self.topic, "message": self.message}


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="SurveyBench adapter for SimpleAutoResearch")
    sub = parser.add_subparsers(dest="command", required=True)

    topics = sub.add_parser("topics", help="List SurveyBench topics from HumanSurvey.")
    add_root_args(topics)
    topics.set_defaults(func=cmd_topics)

    validate = sub.add_parser("validate", help="Validate generated survey markdown format and filename alignment.")
    add_root_args(validate)
    validate.add_argument("--survey-dir", type=Path, required=True, help="Directory containing generated .md surveys.")
    validate.add_argument("--human-dir", type=Path, default=None, help="Optional human reference dir. Defaults to SurveyBench/data/HumanSurvey.")
    validate.add_argument("--output", type=Path, default=None, help="Optional JSON validation report path.")
    validate.add_argument("--allow-subset", action="store_true", help="Allow generated survey dir to contain only a subset of topics.")
    validate.set_defaults(func=cmd_validate)

    export = sub.add_parser("export", help="Copy generated surveys into SurveyBench/data/<method> with optional format normalization.")
    add_root_args(export)
    export.add_argument("--source-dir", type=Path, required=True, help="Directory containing generated .md surveys.")
    export.add_argument("--method", default=DEFAULT_METHOD, help="SurveyBench method directory name.")
    export.add_argument("--output-dir", type=Path, default=None, help="Override destination dir. Defaults to SurveyBench/data/<method>.")
    export.add_argument("--allow-subset", action="store_true", help="Allow exporting only a subset of SurveyBench topics.")
    export.add_argument("--normalize-headings", action="store_true", help="Add numeric heading prefixes required by SurveyBench outline parsing when missing.")
    export.add_argument("--force", action="store_true", help="Overwrite existing destination directory.")
    export.set_defaults(func=cmd_export)

    content = sub.add_parser("eval-content", help="Run SurveyBench native content/outline/richness evaluation.")
    add_eval_args(content)
    content.add_argument("--mode", default="overall", choices=["overall", "content", "outline", "richness"])
    content.add_argument("--setting", default="with_ref", help="Content mode setting.")
    content.set_defaults(func=cmd_eval_content)

    quiz = sub.add_parser("eval-quiz", help="Run SurveyBench native quiz-based evaluation.")
    add_eval_args(quiz)
    quiz.add_argument("--emb-model", default="text-embedding-3-small")
    quiz.add_argument("--emb-dimension", type=int, default=1536)
    quiz.add_argument("--emb-api-key", default=None)
    quiz.add_argument("--emb-api-url", default=None)
    quiz.set_defaults(func=cmd_eval_quiz)

    run_native = sub.add_parser("run-native", help="Run native content evaluation and, optionally, quiz evaluation.")
    add_eval_args(run_native)
    run_native.add_argument("--skip-content", action="store_true")
    run_native.add_argument("--quiz", action="store_true", help="Also run quiz-based evaluation.")
    run_native.add_argument("--emb-model", default="text-embedding-3-small")
    run_native.add_argument("--emb-dimension", type=int, default=1536)
    run_native.add_argument("--emb-api-key", default=None)
    run_native.add_argument("--emb-api-url", default=None)
    run_native.set_defaults(func=cmd_run_native)

    summarize = sub.add_parser("summarize", help="Summarize native SurveyBench result artifacts.")
    add_root_args(summarize)
    summarize.add_argument("--method", default=DEFAULT_METHOD)
    summarize.add_argument("--content-dir", type=Path, default=None, help="Directory containing content result JSON.")
    summarize.add_argument("--quiz-dir", type=Path, default=None, help="Directory containing quiz result JSON files.")
    summarize.add_argument("--output-dir", type=Path, default=None, help="Directory for summary JSON/Markdown.")
    summarize.set_defaults(func=cmd_summarize)

    args = parser.parse_args(argv)
    return args.func(args)


def add_root_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--surveybench-root", type=Path, default=Path(DEFAULT_SURVEYBENCH_ROOT), help="Path to external SurveyBench checkout.")


def add_eval_args(parser: argparse.ArgumentParser) -> None:
    add_root_args(parser)
    parser.add_argument("--method", default=DEFAULT_METHOD)
    parser.add_argument("--survey-dir", type=Path, default=None, help="Generated survey dir. Defaults to SurveyBench/data/<method>.")
    parser.add_argument("--human-dir", type=Path, default=None, help="Human reference dir. Defaults to SurveyBench/data/HumanSurvey.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory for native results.")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--api-url", default=None)


def cmd_topics(args: argparse.Namespace) -> int:
    root = resolve_surveybench_root(args.surveybench_root)
    topics = discover_topics(root)
    for topic in topics:
        print(topic)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    root = resolve_surveybench_root(args.surveybench_root)
    human_dir = resolve_human_dir(root, args.human_dir)
    report = validate_survey_dir(
        args.survey_dir,
        human_dir=human_dir,
        allow_subset=args.allow_subset,
    )
    write_validation_report(report, args.output)
    print_validation_report(report)
    return 0 if not report["error_count"] else 1


def cmd_export(args: argparse.Namespace) -> int:
    root = resolve_surveybench_root(args.surveybench_root)
    human_dir = resolve_human_dir(root, None)
    destination = args.output_dir or root / "data" / args.method
    report = validate_survey_dir(args.source_dir, human_dir=human_dir, allow_subset=args.allow_subset)
    blocking = [issue for issue in report["issues"] if issue["severity"] == "error"]
    if blocking:
        print_validation_report(report)
        return 1
    if destination.exists():
        if not args.force:
            raise SystemExit(f"Destination exists: {destination}. Pass --force to overwrite.")
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    exported: list[str] = []
    exported_assets: list[str] = []
    for topic in report["matched_topics"]:
        source = args.source_dir / f"{topic}.md"
        text = read_text(source)
        if args.normalize_headings:
            text = normalize_markdown_headings(text, topic=topic)
        write_text(destination / f"{topic}.md", text)
        exported_assets.extend(
            copy_markdown_assets(text, source_dir=args.source_dir, destination_dir=destination)
        )
        exported.append(topic)
    meta = {
        "schema_version": "survey_bench_export.v1",
        "created_at": utcnow(),
        "method": args.method,
        "source_dir": str(args.source_dir),
        "destination": str(destination),
        "normalize_headings": bool(args.normalize_headings),
        "topic_count": len(exported),
        "topics": exported,
        "asset_count": len(exported_assets),
        "assets": sorted(set(exported_assets)),
        "validation": report,
    }
    write_json(destination / "_simple_ar_export.json", meta)
    print(f"Exported {len(exported)} survey file(s) to {destination}")
    return 0


def cmd_eval_content(args: argparse.Namespace) -> int:
    root = resolve_surveybench_root(args.surveybench_root)
    survey_dir = resolve_survey_dir(root, args.method, args.survey_dir)
    human_dir = resolve_human_dir(root, args.human_dir)
    output_dir = args.output_dir or DEFAULT_RESULTS_ROOT / args.method / "content"
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "run_content_eval.py",
        "--mode",
        args.mode,
        "--setting",
        args.setting,
        "--survey_dir",
        path_for_native(survey_dir, root / "src"),
        "--human_dir",
        path_for_native(human_dir, root / "src"),
        "--model",
        args.model,
        "--api_key",
        env_or_value(args.api_key, "OPENAI_API_KEY"),
        "--api_url",
        env_or_value(args.api_url, "OPENAI_BASE_URL"),
        "--output_dir",
        str(output_dir.resolve()),
    ]
    return run_native_command(root, command, label="content")


def cmd_eval_quiz(args: argparse.Namespace) -> int:
    root = resolve_surveybench_root(args.surveybench_root)
    survey_dir = resolve_survey_dir(root, args.method, args.survey_dir)
    human_dir = resolve_human_dir(root, args.human_dir)
    output_dir = args.output_dir or DEFAULT_RESULTS_ROOT / args.method / "quiz"
    output_arg = absolute_quiz_output_arg(root, output_dir)
    command = [
        sys.executable,
        "run_quiz_eval.py",
        "--survey_dir",
        path_for_native(survey_dir, root / "src"),
        "--human_dir",
        path_for_native(human_dir, root / "src"),
        "--output_dir",
        output_arg,
        "--llm",
        args.model,
        "--llm_api_key",
        env_or_value(args.api_key, "OPENAI_API_KEY"),
        "--llm_api_url",
        env_or_value(args.api_url, "OPENAI_BASE_URL"),
        "--emb_model",
        args.emb_model,
        "--emb_dimension",
        str(args.emb_dimension),
        "--emb_api_key",
        env_or_value(args.emb_api_key or args.api_key, "OPENAI_API_KEY"),
        "--emb_api_url",
        env_or_value(args.emb_api_url or args.api_url, "OPENAI_BASE_URL"),
    ]
    return run_native_command(root, command, label="quiz")


def cmd_run_native(args: argparse.Namespace) -> int:
    if not args.skip_content:
        code = cmd_eval_content(argparse.Namespace(**{**vars(args), "mode": "overall", "setting": "with_ref"}))
        if code != 0:
            return code
    if args.quiz:
        return cmd_eval_quiz(args)
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    root = resolve_surveybench_root(args.surveybench_root)
    output_dir = args.output_dir or DEFAULT_RESULTS_ROOT / args.method
    content_dir = args.content_dir or output_dir / "content"
    quiz_dir = args.quiz_dir or output_dir / "quiz"
    summary = summarize_results(method=args.method, content_dir=content_dir, quiz_dir=quiz_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "summary.json"
    md_path = output_dir / "summary.md"
    write_json(json_path, summary)
    write_text(md_path, render_summary_markdown(summary))
    print(f"Summary written to {json_path}")
    print(f"Markdown written to {md_path}")
    return 0


def resolve_surveybench_root(path: Path) -> Path:
    root = path.resolve()
    if not root.exists():
        raise SystemExit(f"SurveyBench root does not exist: {root}")
    required = [root / "src" / "run_content_eval.py", root / "src" / "run_quiz_eval.py", root / "data" / "HumanSurvey"]
    missing = [str(item) for item in required if not item.exists()]
    if missing:
        raise SystemExit("Invalid SurveyBench root; missing: " + ", ".join(missing))
    return root


def resolve_human_dir(root: Path, value: Path | None) -> Path:
    path = (value or root / "data" / "HumanSurvey").resolve()
    if not path.is_dir():
        raise SystemExit(f"Human survey directory not found: {path}")
    return path


def resolve_survey_dir(root: Path, method: str, value: Path | None) -> Path:
    path = (value or root / "data" / method).resolve()
    if not path.is_dir():
        raise SystemExit(f"Generated survey directory not found: {path}")
    return path


def discover_topics(root: Path) -> list[str]:
    return sorted(path.stem for path in (root / "data" / "HumanSurvey").glob("*.md"))


def validate_survey_dir(survey_dir: Path, *, human_dir: Path, allow_subset: bool = False) -> dict[str, Any]:
    survey_dir = survey_dir.resolve()
    if not survey_dir.is_dir():
        raise SystemExit(f"Survey directory not found: {survey_dir}")
    human_topics = sorted(path.stem for path in human_dir.glob("*.md"))
    survey_topics = sorted(path.stem for path in survey_dir.glob("*.md"))
    human_set = set(human_topics)
    survey_set = set(survey_topics)
    issues: list[ValidationIssue] = []
    missing = sorted(human_set - survey_set)
    extra = sorted(survey_set - human_set)
    if missing and not allow_subset:
        for topic in missing:
            issues.append(ValidationIssue("error", topic, "Missing generated survey file aligned to HumanSurvey."))
    elif missing:
        for topic in missing:
            issues.append(ValidationIssue("warning", topic, "Topic absent from generated subset."))
    for topic in extra:
        issues.append(ValidationIssue("warning", topic, "Generated file has no matching HumanSurvey reference."))
    matched = sorted(survey_set & human_set)
    for topic in matched:
        issues.extend(validate_markdown_file(survey_dir / f"{topic}.md", topic=topic))
    json_issues = [issue.to_json() for issue in issues]
    return {
        "schema_version": "survey_bench_validation.v1",
        "created_at": utcnow(),
        "survey_dir": str(survey_dir),
        "human_dir": str(human_dir),
        "allow_subset": bool(allow_subset),
        "human_topic_count": len(human_topics),
        "survey_topic_count": len(survey_topics),
        "matched_topic_count": len(matched),
        "matched_topics": matched,
        "missing_topics": missing,
        "extra_topics": extra,
        "issues": json_issues,
        "error_count": sum(1 for issue in json_issues if issue["severity"] == "error"),
        "warning_count": sum(1 for issue in json_issues if issue["severity"] == "warning"),
    }


def validate_markdown_file(path: Path, *, topic: str) -> list[ValidationIssue]:
    text = read_text(path)
    issues: list[ValidationIssue] = []
    if not re.search(r"(?m)^#\s+\S+", text):
        issues.append(ValidationIssue("warning", topic, "No top-level '# Title' heading found."))
    numbered_h2 = re.findall(r"(?m)^##\s+\d+(?:\.\d+)*\s+\S+", text)
    if len(numbered_h2) < 3:
        issues.append(
            ValidationIssue(
                "warning",
                topic,
                "Fewer than 3 numbered '## N ...' sections; SurveyBench outline parser may score outline poorly.",
            )
        )
    if not re.search(r"(?im)^#{1,6}\s+references\b", text):
        issues.append(ValidationIssue("warning", topic, "No Markdown References heading found."))
    if len(text.strip()) < 5000:
        issues.append(ValidationIssue("warning", topic, "Survey is short; quiz-based evaluation may lack answerable detail."))
    return issues


def normalize_markdown_headings(text: str, *, topic: str) -> str:
    lines = text.splitlines()
    if not any(re.match(r"^#\s+\S+", line) for line in lines):
        lines.insert(0, f"# {topic}")
        lines.insert(1, "")
    counters = [0, 0, 0, 0, 0, 0, 0]
    normalized: list[str] = []
    for line in lines:
        match = re.match(r"^(#{2,6})\s+(.*\S)\s*$", line)
        if not match:
            normalized.append(line)
            continue
        hashes, title = match.groups()
        title = title.strip()
        if re.match(r"(?i)^references\b", title):
            normalized.append("## References")
            continue
        if re.match(r"^\d+(?:\.\d+)*\s+", title):
            normalized.append(line)
            continue
        level = len(hashes)
        counters[level] += 1
        for idx in range(level + 1, len(counters)):
            counters[idx] = 0
        if level == 2:
            number = str(counters[2])
        else:
            parent_numbers = [str(counters[idx] or 1) for idx in range(2, level + 1)]
            number = ".".join(parent_numbers)
        normalized.append(f"{hashes} {number} {title}")
    return "\n".join(normalized).rstrip() + "\n"


def run_native_command(root: Path, command: list[str], *, label: str) -> int:
    meta_dir = DEFAULT_RESULTS_ROOT / "_native_commands"
    meta_dir.mkdir(parents=True, exist_ok=True)
    started = utcnow()
    safe_command = sanitize_command(command)
    print("$ " + " ".join(safe_command))
    proc = subprocess.run(command, cwd=root / "src")
    row = {
        "schema_version": "survey_bench_native_command.v1",
        "label": label,
        "started_at": started,
        "finished_at": utcnow(),
        "returncode": proc.returncode,
        "cwd": str(root / "src"),
        "command": safe_command,
        "native_judge": True,
    }
    write_json(meta_dir / f"{timestamp_slug()}-{label}.json", row)
    return proc.returncode


def sanitize_command(command: Sequence[str]) -> list[str]:
    secret_flags = {
        "--api_key",
        "--api-key",
        "--llm_api_key",
        "--emb_api_key",
        "--emb-api-key",
    }
    safe: list[str] = []
    redact_next = False
    for item in command:
        if redact_next:
            safe.append("<redacted>")
            redact_next = False
            continue
        safe.append(item)
        if item in secret_flags:
            redact_next = True
    return safe


def summarize_results(*, method: str, content_dir: Path, quiz_dir: Path) -> dict[str, Any]:
    content = summarize_content(content_dir)
    quiz = summarize_quiz(quiz_dir)
    return {
        "schema_version": "survey_bench_summary.v1",
        "created_at": utcnow(),
        "method": method,
        "content_dir": str(content_dir),
        "quiz_dir": str(quiz_dir),
        "content": content,
        "quiz": quiz,
    }


def summarize_content(content_dir: Path) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for path in sorted(content_dir.glob("*.json")) if content_dir.exists() else []:
        try:
            data = read_json(path)
        except Exception as exc:  # noqa: BLE001
            rows[path.name] = {"error": str(exc)}
            continue
        result = data.get("result") if isinstance(data, dict) else None
        rows[path.name] = result if isinstance(result, dict) else data
    overall = rows.get("overall.json")
    flattened: dict[str, Any] = {}
    if isinstance(overall, dict):
        for group, metrics in overall.items():
            if isinstance(metrics, dict):
                for name, value in metrics.items():
                    flattened[f"{group}.{name}"] = value
    table = build_content_quality_table(overall if isinstance(overall, dict) else {})
    return {"files": rows, "overall_flat": flattened, "quality_table": table}


def build_content_quality_table(overall: dict[str, Any]) -> dict[str, Any]:
    outline = metric_group(overall.get("outline_avg"), ["coverage", "relevance", "structure"])
    content = metric_group(overall.get("content_avg"), ["coverage", "depth", "focus", "coherence", "fluency"])
    richness_raw = overall.get("richness_avg")
    richness = richness_raw if isinstance(richness_raw, dict) else {}
    return {
        "outline_quality": {
            **outline,
            "scale": "1-5",
        },
        "content_quality": {
            **content,
            "scale": "1-5",
        },
        "richness": {
            "figures": numeric_or_none(richness.get("figures")),
            "tables": numeric_or_none(richness.get("tables")),
            "total": numeric_or_none(richness.get("richness")),
        },
    }


def metric_group(raw: Any, ordered_keys: Sequence[str]) -> dict[str, Any]:
    metrics = raw if isinstance(raw, dict) else {}
    values = {key: numeric_or_none(metrics.get(key)) for key in ordered_keys}
    return {"metrics": values, "average": mean(value for value in values.values() if value is not None)}


def numeric_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def summarize_quiz(quiz_dir: Path) -> dict[str, Any]:
    compare_files = sorted(quiz_dir.glob("*_compare_results.json")) if quiz_dir.exists() else []
    specific_files = sorted(quiz_dir.glob("*_specific_results.json")) if quiz_dir.exists() else []
    compare_rows = []
    specific_rows = []
    for path in compare_files:
        data = read_json(path)
        total = int(data.get("total_questions") or 0) if isinstance(data, dict) else 0
        wins = int(data.get("better_answer_2_count") or 0) if isinstance(data, dict) else 0
        compare_rows.append({"topic": topic_from_result_name(path.name, "_compare_results.json"), "wins": wins, "total": total, "win_rate": wins / total if total else None})
    for path in specific_files:
        data = read_json(path)
        stats = data.get("overall_statistics") if isinstance(data, dict) else None
        average = stats.get("average") if isinstance(stats, dict) else None
        total_scores = stats.get("total_scores") if isinstance(stats, dict) else None
        specific_rows.append({"topic": topic_from_result_name(path.name, "_specific_results.json"), "average": average, "total_scores": total_scores})
    return {
        "compare_topic_count": len(compare_rows),
        "specific_topic_count": len(specific_rows),
        "compare_win_rate_avg": mean(row["win_rate"] for row in compare_rows if row["win_rate"] is not None),
        "specific_score_avg": mean(row["average"] for row in specific_rows if isinstance(row["average"], (int, float))),
        "compare": compare_rows,
        "specific": specific_rows,
    }


def render_summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"# SurveyBench Summary: {summary.get('method', '')}",
        "",
        "## Content-Based Evaluation",
        "",
    ]
    table = summary.get("content", {}).get("quality_table", {})
    if table:
        lines.extend(render_content_quality_table(str(summary.get("method", "")), table))
    else:
        lines.append("_No content result found._")
    quiz = summary.get("quiz", {})
    lines.extend(["", "## Quiz-Based Evaluation", ""])
    lines.extend(
        [
            f"- Compare topics: {quiz.get('compare_topic_count', 0)}",
            f"- Specific topics: {quiz.get('specific_topic_count', 0)}",
            f"- Avg generated-survey win rate: {fmt(quiz.get('compare_win_rate_avg'))}",
            f"- Avg topic-specific score: {fmt(quiz.get('specific_score_avg'))}",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_content_quality_table(method: str, table: dict[str, Any]) -> list[str]:
    method_label = method or "Method"
    lines = [
        f"| Dimension | {method_label} |",
        "| --- | ---: |",
    ]
    append_group_header(lines, "Outline Quality (1-5)")
    append_metric_rows(lines, table.get("outline_quality", {}), ["coverage", "relevance", "structure"])
    append_average_row(lines, table.get("outline_quality", {}))
    append_group_header(lines, "Content Quality (1-5)")
    append_metric_rows(lines, table.get("content_quality", {}), ["coverage", "depth", "focus", "coherence", "fluency"])
    append_average_row(lines, table.get("content_quality", {}))
    richness = table.get("richness", {})
    append_group_header(lines, "Richness")
    lines.append(f"| Avg. Fig. Num. | {fmt(richness.get('figures'))} |")
    lines.append(f"| Avg. Table Num. | {fmt(richness.get('tables'))} |")
    lines.append(f"| Total Avg. | {fmt(richness.get('total'))} |")
    return lines


def append_group_header(lines: list[str], title: str) -> None:
    lines.append(f"| **{title}** |  |")


def append_metric_rows(lines: list[str], group: dict[str, Any], ordered_keys: Sequence[str]) -> None:
    metrics = group.get("metrics") if isinstance(group, dict) else {}
    metrics = metrics if isinstance(metrics, dict) else {}
    labels = {
        "coverage": "Coverage",
        "relevance": "Relevance",
        "structure": "Structure",
        "depth": "Depth",
        "focus": "Focus",
        "coherence": "Coherence",
        "fluency": "Fluency",
    }
    for key in ordered_keys:
        lines.append(f"| {labels.get(key, key)} | {fmt(metrics.get(key))} |")


def append_average_row(lines: list[str], group: dict[str, Any]) -> None:
    average = group.get("average") if isinstance(group, dict) else None
    lines.append(f"| **Average** | {fmt(average)} |")


def write_validation_report(report: dict[str, Any], output: Path | None) -> None:
    if output is None:
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, report)


def print_validation_report(report: dict[str, Any]) -> None:
    print(
        f"Matched {report['matched_topic_count']}/{report['human_topic_count']} topic(s); "
        f"errors={report['error_count']}, warnings={report['warning_count']}"
    )
    for issue in report["issues"][:40]:
        print(f"- {issue['severity']}: {issue['topic']}: {issue['message']}")
    if len(report["issues"]) > 40:
        print(f"... {len(report['issues']) - 40} more issue(s)")


def path_for_native(path: Path, cwd: Path) -> str:
    try:
        return str(path.resolve().relative_to(cwd.resolve()))
    except ValueError:
        return str(path.resolve())


def absolute_quiz_output_arg(root: Path, output_dir: Path) -> str:
    """Work around SurveyBench's native '../results' prefix while keeping output local."""

    native_results_root = (root / "results").resolve()
    output_dir = output_dir.resolve()
    try:
        return str(output_dir.relative_to(native_results_root))
    except ValueError:
        return str(output_dir)


def env_or_value(value: str | None, env_name: str) -> str:
    if value:
        return value
    import os

    return os.environ.get(env_name, "")


def topic_from_result_name(name: str, suffix: str) -> str:
    return name[: -len(suffix)] if name.endswith(suffix) else Path(name).stem


def mean(values: Iterable[float]) -> float | None:
    rows = list(values)
    return sum(rows) / len(rows) if rows else None


def fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    if value is None:
        return "-"
    return str(value)


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def copy_markdown_assets(text: str, *, source_dir: Path, destination_dir: Path) -> list[str]:
    copied: list[str] = []
    source_root = source_dir.resolve()
    for raw_target in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text):
        target = raw_target.strip().split()[0].strip("<>")
        if not target or "://" in target or target.startswith("#"):
            continue
        source = (source_dir / target).resolve()
        if source.is_absolute():
            try:
                source.relative_to(source_root)
            except ValueError:
                continue
        if not source.exists() or not source.is_file():
            continue
        destination = destination_dir / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(target.replace("\\", "/"))
    return copied


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def read_json(path: Path) -> Any:
    return json.loads(read_text(path))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import os
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
DEFAULT_TOPIC_RESULTS_ROOT = DEFAULT_RESULTS_ROOT / "topics"
DEFAULT_THOROUGH_TOPIC_RESULTS_ROOT = DEFAULT_RESULTS_ROOT / "topics-thorough"
DEFAULT_SCORE_ROOT = DEFAULT_RESULTS_ROOT / "score"
DEFAULT_THOROUGH_SCORE_ROOT = DEFAULT_RESULTS_ROOT / "score-thorough"


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    topic: str
    message: str

    def to_json(self) -> dict[str, str]:
        return {"severity": self.severity, "topic": self.topic, "message": self.message}


@dataclass(frozen=True)
class TopicRef:
    topic_id: str
    key: str
    name: str


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="SurveyBench adapter for SimpleAutoResearch")
    sub = parser.add_subparsers(dest="command", required=True)

    topics = sub.add_parser("topics", help="List SurveyBench topics from HumanSurvey.")
    add_root_args(topics)
    topics.add_argument("--with-ids", action="store_true", help="Show stable topic ids and result keys.")
    topics.set_defaults(func=cmd_topics)

    run_topic = sub.add_parser("run-topic", help="Run SimpleAutoResearch generation for one SurveyBench topic id.")
    add_root_args(run_topic)
    run_topic.add_argument("--topic-id", required=True, help="SurveyBench topic id such as topic16.")
    run_topic.add_argument("--thorough", action="store_true", help="Use configs/topics-thorough and the thorough result namespace.")
    run_topic.add_argument("--to-stage", default="", help="Optional stage override passed to simple-ar run.")
    run_topic.set_defaults(func=cmd_run_topic)

    resume_latest = sub.add_parser("resume-latest", help="Resume the latest SimpleAutoResearch run for one SurveyBench topic id.")
    add_root_args(resume_latest)
    resume_latest.add_argument("--topic-id", required=True, help="SurveyBench topic id such as topic16.")
    resume_latest.add_argument("--thorough", action="store_true", help="Use configs/topics-thorough and the thorough result namespace.")
    resume_latest.add_argument("--run-dir", type=Path, default=None, help="Optional run dir. Defaults to the latest topic run, even if report failed.")
    resume_latest.add_argument("--from-stage", default="report", help="Stage to resume from. Defaults to report.")
    resume_latest.add_argument("--to-stage", default="report", help="Stage to stop at. Defaults to report.")
    resume_latest.add_argument("--model", default="", help="Optional model override passed to simple-ar resume.")
    resume_latest.add_argument("--llm-workers", type=int, default=None, help="Optional LLM worker override passed to simple-ar resume.")
    resume_latest.add_argument("--quiet", action="store_true", help="Pass --quiet to simple-ar resume.")
    resume_latest.add_argument(
        "--overwrite-stage-artifacts",
        action="store_true",
        help="Pass --overwrite-stage-artifacts to simple-ar resume.",
    )
    resume_latest.set_defaults(func=cmd_resume_latest)

    validate = sub.add_parser("validate", help="Validate generated survey markdown format and filename alignment.")
    add_root_args(validate)
    validate.add_argument("--topic-id", default="", help="SurveyBench topic id such as topic16; infers survey/output dirs.")
    validate.add_argument("--thorough", action="store_true", help="Infer the thorough method and score namespace for --topic-id.")
    validate.add_argument("--survey-dir", type=Path, default=None, help="Directory containing generated .md surveys.")
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

    export_report = sub.add_parser("export-report", help="Export one SimpleAutoResearch report.md as one SurveyBench topic file.")
    add_root_args(export_report)
    export_report.add_argument("--report-file", type=Path, required=True, help="Generated report.md path.")
    export_report.add_argument("--topic-id", default="", help="SurveyBench topic id such as topic16; infers topic and method.")
    export_report.add_argument("--thorough", action="store_true", help="Infer the thorough method name when --topic-id is used.")
    export_report.add_argument("--topic", default="", help="SurveyBench topic filename stem, for example 'LLM-based Multi-Agent'.")
    export_report.add_argument("--method", default=DEFAULT_METHOD, help="SurveyBench method directory name.")
    export_report.add_argument("--output-dir", type=Path, default=None, help="Override destination dir. Defaults to SurveyBench/data/<method>.")
    export_report.add_argument("--normalize-headings", action="store_true", help="Add numeric heading prefixes required by SurveyBench outline parsing when missing.")
    export_report.add_argument("--force", action="store_true", help="Overwrite the destination topic file if it exists.")
    export_report.set_defaults(func=cmd_export_report)

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

    finalize = sub.add_parser("finalize-latest", help="Export, validate, optionally judge, and summarize the latest topic run.")
    add_root_args(finalize)
    finalize.add_argument("--topic-id", required=True, help="SurveyBench topic id such as topic16.")
    finalize.add_argument("--thorough", action="store_true", help="Use the latest thorough generation run and write thorough score artifacts.")
    finalize.add_argument("--run-dir", type=Path, default=None, help="Optional run dir. Defaults to latest results/topics/<topic-key> run with 08-report/report.md.")
    finalize.add_argument("--model", default="gpt-4o", help="Judge model when --eval-content is set.")
    finalize.add_argument("--eval-content", action="store_true", help="Run native SurveyBench content/outline/richness judge.")
    finalize.add_argument("--no-normalize-headings", action="store_true", help="Do not normalize headings during export.")
    finalize.add_argument("--no-force", action="store_true", help="Do not overwrite existing SurveyBench topic file.")
    finalize.set_defaults(func=cmd_finalize_latest)

    summarize = sub.add_parser("summarize", help="Summarize native SurveyBench result artifacts.")
    add_root_args(summarize)
    summarize.add_argument("--topic-id", default="", help="SurveyBench topic id such as topic16; infers method/output dirs.")
    summarize.add_argument("--thorough", action="store_true", help="Infer the thorough method and score namespace for --topic-id.")
    summarize.add_argument("--method", default=DEFAULT_METHOD)
    summarize.add_argument("--content-dir", type=Path, default=None, help="Directory containing content result JSON.")
    summarize.add_argument("--quiz-dir", type=Path, default=None, help="Directory containing quiz result JSON files.")
    summarize.add_argument("--output-dir", type=Path, default=None, help="Directory for summary JSON/Markdown.")
    summarize.set_defaults(func=cmd_summarize)

    summarize_batch = sub.add_parser("summarize-batch", help="Aggregate per-topic SurveyBench summaries into one table.")
    add_root_args(summarize_batch)
    summarize_batch.add_argument("--score-root", type=Path, default=DEFAULT_SCORE_ROOT, help="Root containing per-topic score directories.")
    summarize_batch.add_argument(
        "--fallback-score-root",
        type=Path,
        default=None,
        help="Optional fallback score root for topics missing in --score-root.",
    )
    summarize_batch.add_argument("--thorough", action="store_true", help="Use the thorough score root by default.")
    summarize_batch.add_argument("--from-topic", type=int, default=0, help="Optional first topic number, e.g. 10.")
    summarize_batch.add_argument("--to-topic", type=int, default=0, help="Optional last topic number, e.g. 20.")
    summarize_batch.add_argument("--output-dir", type=Path, default=None, help="Output directory for batch_summary.json/md.")
    summarize_batch.set_defaults(func=cmd_summarize_batch)

    args = parser.parse_args(argv)
    return args.func(args)


def add_root_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--surveybench-root", type=Path, default=Path(DEFAULT_SURVEYBENCH_ROOT), help="Path to external SurveyBench checkout.")


def add_eval_args(parser: argparse.ArgumentParser) -> None:
    add_root_args(parser)
    parser.add_argument("--topic-id", default="", help="SurveyBench topic id such as topic16; infers method and survey dirs.")
    parser.add_argument("--thorough", action="store_true", help="Infer the thorough method and score namespace for --topic-id.")
    parser.add_argument("--method", default=DEFAULT_METHOD)
    parser.add_argument("--survey-dir", type=Path, default=None, help="Generated survey dir. Defaults to SurveyBench/data/<method>.")
    parser.add_argument("--human-dir", type=Path, default=None, help="Human reference dir. Defaults to SurveyBench/data/HumanSurvey.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory for native results.")
    parser.add_argument("--model", default="gpt-4o")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--api-url", default=None)


def cmd_topics(args: argparse.Namespace) -> int:
    root = resolve_surveybench_root(args.surveybench_root)
    topics = discover_topics(root)
    for index, topic in enumerate(topics, start=1):
        if args.with_ids:
            print(f"topic{index:02d}\t{topic_key(index, topic)}\t{topic}")
        else:
            print(topic)
    return 0


def cmd_run_topic(args: argparse.Namespace) -> int:
    root = resolve_surveybench_root(args.surveybench_root)
    topic_ref = resolve_topic_ref(root, args.topic_id)
    config_path = topic_config_path(topic_ref, thorough=bool(args.thorough))
    if not config_path.is_file():
        raise SystemExit(f"Missing topic config: {config_path}")
    from simple_ar.cli.main import main as simple_ar_main

    cli_args = ["run", "--config", str(config_path)]
    if args.to_stage:
        cli_args.extend(["--to-stage", str(args.to_stage)])
    simple_ar_main(cli_args)
    return 0


def cmd_resume_latest(args: argparse.Namespace) -> int:
    root = resolve_surveybench_root(args.surveybench_root)
    topic_ref = resolve_topic_ref(root, args.topic_id)
    thorough = bool(args.thorough)
    config_path = topic_config_path(topic_ref, thorough=thorough)
    if not config_path.is_file():
        raise SystemExit(f"Missing topic config: {config_path}")
    run_dir = args.run_dir or latest_topic_run_dir(topic_ref.key, thorough=thorough, require_report=False)
    if not run_dir.is_dir():
        raise SystemExit(f"Run directory does not exist: {run_dir}")
    from simple_ar.cli.main import main as simple_ar_main

    cli_args = [
        "resume",
        str(run_dir),
        "--config",
        str(config_path),
        "--from-stage",
        str(args.from_stage or "report"),
        "--to-stage",
        str(args.to_stage or "report"),
    ]
    if args.model:
        cli_args.extend(["--model", str(args.model)])
    if args.llm_workers is not None:
        cli_args.extend(["--llm-workers", str(args.llm_workers)])
    if args.quiet:
        cli_args.append("--quiet")
    if args.overwrite_stage_artifacts:
        cli_args.append("--overwrite-stage-artifacts")
    simple_ar_main(cli_args)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    root = resolve_surveybench_root(args.surveybench_root)
    topic_ref = resolve_topic_ref(root, args.topic_id) if getattr(args, "topic_id", "") else None
    method = _method_for_topic(DEFAULT_METHOD, topic_ref, thorough=bool(getattr(args, "thorough", False)))
    survey_dir = args.survey_dir or (root / "data" / method if topic_ref else None)
    if survey_dir is None:
        raise SystemExit("Pass --survey-dir or --topic-id.")
    output = args.output or (
        _score_dir_for_topic(DEFAULT_METHOD, topic_ref, thorough=bool(getattr(args, "thorough", False))) / "validation.json"
        if topic_ref
        else None
    )
    human_dir = resolve_human_dir(root, args.human_dir)
    report = validate_survey_dir(
        survey_dir,
        human_dir=human_dir,
        allow_subset=args.allow_subset,
        expected_topics=[topic_ref.name] if topic_ref else None,
    )
    write_validation_report(report, output)
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


def cmd_export_report(args: argparse.Namespace) -> int:
    root = resolve_surveybench_root(args.surveybench_root)
    topic_ref = resolve_topic_ref(root, args.topic_id) if getattr(args, "topic_id", "") else None
    topic = args.topic or (topic_ref.name if topic_ref else "")
    method = _method_for_topic(args.method, topic_ref, thorough=bool(getattr(args, "thorough", False)))
    if not topic:
        raise SystemExit("Pass --topic or --topic-id.")
    topics = set(discover_topics(root))
    if topic not in topics:
        raise SystemExit(
            f"Unknown SurveyBench topic: {topic}. Run `adapter.py topics --with-ids` to list valid names."
        )
    report_file = args.report_file
    if not report_file.exists():
        raise SystemExit(f"Report file does not exist: {report_file}")
    target = export_report_file(
        root=root,
        report_file=report_file,
        topic=topic,
        method=method,
        output_dir=args.output_dir,
        normalize_headings=bool(args.normalize_headings),
        force=bool(args.force),
        topic_ref=topic_ref,
    )
    print(f"Exported `{topic}` survey to {target}")
    return 0


def cmd_finalize_latest(args: argparse.Namespace) -> int:
    root = resolve_surveybench_root(args.surveybench_root)
    topic_ref = resolve_topic_ref(root, args.topic_id)
    thorough = bool(args.thorough)
    run_dir = args.run_dir or latest_topic_run_dir(topic_ref.key, thorough=thorough)
    report_file = run_dir / "08-report" / "report.md"
    if not report_file.is_file():
        raise SystemExit(f"Missing report file: {report_file}")
    method = _method_for_topic(DEFAULT_METHOD, topic_ref, thorough=thorough)
    score_dir = _score_dir_for_topic(DEFAULT_METHOD, topic_ref, thorough=thorough)
    target = export_report_file(
        root=root,
        report_file=report_file,
        topic=topic_ref.name,
        method=method,
        output_dir=None,
        normalize_headings=not bool(args.no_normalize_headings),
        force=not bool(args.no_force),
        topic_ref=topic_ref,
    )
    print(f"Exported latest `{topic_ref.topic_id}` report from {run_dir}")
    print(f"SurveyBench file: {target}")

    validation = validate_survey_dir(
        root / "data" / method,
        human_dir=resolve_human_dir(root, None),
        allow_subset=True,
        expected_topics=[topic_ref.name],
    )
    validation_path = score_dir / "validation.json"
    write_validation_report(validation, validation_path)
    print_validation_report(validation)
    if validation["error_count"]:
        return 1

    if args.eval_content:
        code = cmd_eval_content(
            argparse.Namespace(
                surveybench_root=args.surveybench_root,
                topic_id=topic_ref.topic_id,
                method=method,
                survey_dir=None,
                human_dir=None,
                output_dir=score_dir / "content",
                model=args.model,
                api_key=None,
                api_url=None,
                mode="overall",
                setting="with_ref",
            )
        )
        if code != 0:
            return code
    return cmd_summarize(
        argparse.Namespace(
            surveybench_root=args.surveybench_root,
            topic_id=topic_ref.topic_id,
            method=method,
            content_dir=None,
            quiz_dir=None,
            output_dir=score_dir,
            thorough=thorough,
        )
    )


def cmd_eval_content(args: argparse.Namespace) -> int:
    root = resolve_surveybench_root(args.surveybench_root)
    topic_ref = resolve_topic_ref(root, args.topic_id) if getattr(args, "topic_id", "") else None
    method = _method_for_topic(args.method, topic_ref, thorough=bool(getattr(args, "thorough", False)))
    survey_dir = resolve_survey_dir(root, method, args.survey_dir)
    human_dir = resolve_human_dir(root, args.human_dir)
    output_dir = args.output_dir or _score_dir_for_topic(args.method, topic_ref, thorough=bool(getattr(args, "thorough", False))) / "content"
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
    topic_ref = resolve_topic_ref(root, args.topic_id) if getattr(args, "topic_id", "") else None
    method = _method_for_topic(args.method, topic_ref, thorough=bool(getattr(args, "thorough", False)))
    survey_dir = resolve_survey_dir(root, method, args.survey_dir)
    human_dir = resolve_human_dir(root, args.human_dir)
    output_dir = args.output_dir or _score_dir_for_topic(args.method, topic_ref, thorough=bool(getattr(args, "thorough", False))) / "quiz"
    # SurveyBench's native quiz script writes ../results/quiz_logs.txt before
    # it creates the requested output directory. Create that native results
    # directory up front without changing judge prompts or scoring logic.
    (root / "results").mkdir(parents=True, exist_ok=True)
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
    topic_ref = resolve_topic_ref(root, args.topic_id) if getattr(args, "topic_id", "") else None
    method = _method_for_topic(args.method, topic_ref, thorough=bool(getattr(args, "thorough", False)))
    output_dir = args.output_dir or _score_dir_for_topic(args.method, topic_ref, thorough=bool(getattr(args, "thorough", False)))
    content_dir = args.content_dir or _score_artifact_dir(method, "content", output_dir)
    quiz_dir = args.quiz_dir or _score_artifact_dir(method, "quiz", output_dir)
    summary = summarize_results(method=method, content_dir=content_dir, quiz_dir=quiz_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "summary.json"
    md_path = output_dir / "summary.md"
    write_json(json_path, summary)
    write_text(md_path, render_summary_markdown(summary))
    print(f"Summary written to {json_path}")
    print(f"Markdown written to {md_path}")
    return 0


def cmd_summarize_batch(args: argparse.Namespace) -> int:
    root = resolve_surveybench_root(args.surveybench_root)
    topic_refs = discover_topic_refs(root)
    score_root = DEFAULT_THOROUGH_SCORE_ROOT if bool(args.thorough) and args.score_root == DEFAULT_SCORE_ROOT else args.score_root
    output_dir = args.output_dir or score_root / "batch_summary"
    summary = summarize_batch_results(
        score_root=score_root,
        fallback_score_root=args.fallback_score_root,
        topic_refs=topic_refs,
        from_topic=int(args.from_topic or 0),
        to_topic=int(args.to_topic or 0),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "batch_summary.json"
    md_path = output_dir / "batch_summary.md"
    write_json(json_path, summary)
    write_text(md_path, render_batch_summary_markdown(summary))
    print(f"Batch summary written to {json_path}")
    print(f"Markdown written to {md_path}")
    return 0


def _score_artifact_dir(method: str, name: str, output_dir: Path) -> Path:
    preferred = output_dir / name
    if preferred.exists():
        return preferred
    legacy = DEFAULT_RESULTS_ROOT / method / name
    if legacy.exists():
        return legacy
    return preferred


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


def topic_config_path(topic_ref: TopicRef, *, thorough: bool = False) -> Path:
    group = "topics-thorough" if thorough else "topics"
    return LOCAL_ROOT / "configs" / group / f"{topic_ref.key}.toml"


def latest_topic_run_dir(topic_key_value: str, *, thorough: bool = False, require_report: bool = True) -> Path:
    topic_root = (DEFAULT_THOROUGH_TOPIC_RESULTS_ROOT if thorough else DEFAULT_TOPIC_RESULTS_ROOT) / topic_key_value
    if not topic_root.is_dir():
        raise SystemExit(f"No generation runs found for topic key: {topic_key_value}")
    candidates = [
        path
        for path in topic_root.iterdir()
        if path.is_dir()
        and (
            (path / "08-report" / "report.md").is_file()
            if require_report
            else (path / "config_snapshot.json").is_file()
        )
    ]
    if not candidates:
        detail = "completed report run" if require_report else "resumable run"
        raise SystemExit(f"No {detail} found under: {topic_root}")
    return sorted(candidates, key=_run_sort_key, reverse=True)[0]


def _run_sort_key(path: Path) -> tuple[str, float]:
    timestamp = path.name.split("-", 1)[0]
    normalized_timestamp = timestamp if re.fullmatch(r"\d{8}", timestamp) else ""
    return normalized_timestamp, path.stat().st_mtime


def resolve_topic_ref(root: Path, value: str) -> TopicRef:
    raw = value.strip()
    if not raw:
        raise SystemExit("Empty --topic-id.")
    topics = discover_topics(root)
    lowered = raw.lower()
    for index, topic in enumerate(topics, start=1):
        topic_id = f"topic{index:02d}"
        key = topic_key(index, topic)
        if lowered in {topic_id, key.lower(), topic.lower()}:
            return TopicRef(topic_id=topic_id, key=key, name=topic)
    raise SystemExit(f"Unknown topic id/name: {value}. Run `adapter.py topics --with-ids`.")


def _method_for_topic(method: str, topic_ref: TopicRef | None, *, thorough: bool = False) -> str:
    if topic_ref is not None and method == DEFAULT_METHOD:
        return f"{topic_ref.key}-thorough" if thorough else topic_ref.key
    return method


def _score_dir_for_topic(method: str, topic_ref: TopicRef | None, *, thorough: bool = False) -> Path:
    root = DEFAULT_THOROUGH_SCORE_ROOT if thorough else DEFAULT_SCORE_ROOT
    if topic_ref is not None and method == DEFAULT_METHOD:
        return root / topic_ref.key
    return root / _method_for_topic(method, topic_ref, thorough=thorough)


def export_report_file(
    *,
    root: Path,
    report_file: Path,
    topic: str,
    method: str,
    output_dir: Path | None,
    normalize_headings: bool,
    force: bool,
    topic_ref: TopicRef | None = None,
) -> Path:
    destination = output_dir or root / "data" / method
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{topic}.md"
    if target.exists() and not force:
        raise SystemExit(f"Destination topic file exists: {target}. Pass --force to overwrite.")
    text = read_text(report_file)
    if normalize_headings:
        text = normalize_markdown_headings(text, topic=topic)
    write_text(target, text)
    exported_assets = copy_markdown_assets(text, source_dir=report_file.parent, destination_dir=destination)
    meta = {
        "schema_version": "survey_bench_export.v1",
        "created_at": utcnow(),
        "method": method,
        "topic_id": topic_ref.topic_id if topic_ref else "",
        "topic_key": topic_ref.key if topic_ref else "",
        "source_report_file": str(report_file),
        "destination": str(destination),
        "normalize_headings": bool(normalize_headings),
        "topic_count": 1,
        "topics": [topic],
        "asset_count": len(exported_assets),
        "assets": sorted(set(exported_assets)),
    }
    write_json(destination / "_simple_ar_export.json", meta)
    return target


def discover_topics(root: Path) -> list[str]:
    return sorted(path.stem for path in (root / "data" / "HumanSurvey").glob("*.md"))


def discover_topic_refs(root: Path) -> list[TopicRef]:
    return [
        TopicRef(topic_id=f"topic{index:02d}", key=topic_key(index, topic), name=topic)
        for index, topic in enumerate(discover_topics(root), start=1)
    ]


def _topic_number(ref: TopicRef) -> int:
    match = re.search(r"(\d+)$", ref.topic_id)
    return int(match.group(1)) if match else 0


def validate_survey_dir(
    survey_dir: Path,
    *,
    human_dir: Path,
    allow_subset: bool = False,
    expected_topics: Sequence[str] | None = None,
) -> dict[str, Any]:
    survey_dir = survey_dir.resolve()
    if not survey_dir.is_dir():
        raise SystemExit(f"Survey directory not found: {survey_dir}")
    human_topics = sorted(path.stem for path in human_dir.glob("*.md"))
    if expected_topics is not None:
        expected = {str(topic).strip() for topic in expected_topics if str(topic).strip()}
        human_topics = [topic for topic in human_topics if topic in expected]
        missing_expected = sorted(expected - set(human_topics))
        if missing_expected:
            raise SystemExit("Unknown SurveyBench topic(s): " + ", ".join(missing_expected))
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
        title = _strip_heading_number(title.strip())
        if re.match(r"(?i)^references\b", title):
            normalized.append("## References")
            continue
        if _is_non_outline_heading(title):
            normalized.append(f"{hashes} {title}")
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


def _strip_heading_number(title: str) -> str:
    """Remove loose pre-existing numbering before applying SurveyBench numbering.

    SurveyBench extracts outlines with headings like ``## 1 Introduction`` and
    ``### 1.1 Background``. Generated reports sometimes emit headings such as
    ``### 1. Future direction``; if we leave those untouched or prefix another
    number, the native outline parser sees noisy labels like ``9.1 1. ...``.
    """

    return re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", title).strip()


def _is_non_outline_heading(title: str) -> bool:
    return bool(re.match(r"(?i)^(abstract|executive summary|summary|key takeaways?)\b", title.strip()))


def run_native_command(root: Path, command: list[str], *, label: str) -> int:
    meta_dir = native_command_meta_dir(command) or (DEFAULT_RESULTS_ROOT / "_native_commands")
    meta_dir.mkdir(parents=True, exist_ok=True)
    started = utcnow()
    safe_command = sanitize_command(command)
    safe_console_print("$ " + " ".join(safe_command))
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    redactions = command_secret_values(command)
    proc = subprocess.Popen(
        command,
        cwd=root / "src",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        safe_console_print(redact_text(line.rstrip("\n"), redactions))
    returncode = proc.wait()
    row = {
        "schema_version": "survey_bench_native_command.v1",
        "label": label,
        "started_at": started,
        "finished_at": utcnow(),
        "returncode": returncode,
        "cwd": str(root / "src"),
        "command": safe_command,
        "native_judge": True,
    }
    write_json(meta_dir / "native_command.json", row)
    return returncode


def native_command_meta_dir(command: Sequence[str]) -> Path | None:
    for index, item in enumerate(command):
        if item == "--output_dir" and index + 1 < len(command):
            return Path(command[index + 1])
    return None


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


def command_secret_values(command: Sequence[str]) -> list[str]:
    secret_flags = {
        "--api_key",
        "--api-key",
        "--llm_api_key",
        "--emb_api_key",
        "--emb-api-key",
    }
    values: list[str] = []
    capture_next = False
    for item in command:
        if capture_next:
            if item:
                values.append(str(item))
            capture_next = False
            continue
        if item in secret_flags:
            capture_next = True
    return values


def redact_text(text: str, secrets: Sequence[str]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "<redacted>")
    return redacted


def safe_console_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe_text)


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


def summarize_batch_results(
    *,
    score_root: Path,
    fallback_score_root: Path | None = None,
    topic_refs: Sequence[TopicRef],
    from_topic: int = 0,
    to_topic: int = 0,
) -> dict[str, Any]:
    selected = [
        ref
        for ref in topic_refs
        if (not from_topic or _topic_number(ref) >= from_topic)
        and (not to_topic or _topic_number(ref) <= to_topic)
    ]
    rows: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for ref in selected:
        summary_path = score_root / ref.key / "summary.json"
        source_root = score_root
        source_label = "primary"
        if not summary_path.is_file() and fallback_score_root is not None:
            fallback_path = fallback_score_root / ref.key / "summary.json"
            if fallback_path.is_file():
                summary_path = fallback_path
                source_root = fallback_score_root
                source_label = "fallback"
        if not summary_path.is_file():
            missing.append(
                {
                    "topic_id": ref.topic_id,
                    "topic_key": ref.key,
                    "topic": ref.name,
                    "expected_summary": str(summary_path),
                }
            )
            continue
        try:
            summary = read_json(summary_path)
        except Exception as exc:  # noqa: BLE001
            missing.append(
                {
                    "topic_id": ref.topic_id,
                    "topic_key": ref.key,
                    "topic": ref.name,
                    "expected_summary": str(summary_path),
                    "error": str(exc),
                }
            )
            continue
        rows.append(_batch_row_from_summary(ref, summary, summary_path, source_root=source_root, source_label=source_label))
    return {
        "schema_version": "survey_bench_batch_summary.v1",
        "created_at": utcnow(),
        "score_root": str(score_root),
        "fallback_score_root": str(fallback_score_root) if fallback_score_root else "",
        "from_topic": from_topic or None,
        "to_topic": to_topic or None,
        "selected_topic_count": len(selected),
        "completed_topic_count": len(rows),
        "missing_topic_count": len(missing),
        "aggregates": _aggregate_batch_rows(rows),
        "topics": rows,
        "missing": missing,
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


def _batch_row_from_summary(
    ref: TopicRef,
    summary: dict[str, Any],
    summary_path: Path,
    *,
    source_root: Path,
    source_label: str,
) -> dict[str, Any]:
    table = summary.get("content", {}).get("quality_table", {}) if isinstance(summary, dict) else {}
    table = table if isinstance(table, dict) else {}
    outline = table.get("outline_quality") if isinstance(table.get("outline_quality"), dict) else {}
    content = table.get("content_quality") if isinstance(table.get("content_quality"), dict) else {}
    richness = table.get("richness") if isinstance(table.get("richness"), dict) else {}
    quiz = summary.get("quiz", {}) if isinstance(summary, dict) else {}
    quiz = quiz if isinstance(quiz, dict) else {}
    outline_metrics = outline.get("metrics") if isinstance(outline.get("metrics"), dict) else {}
    content_metrics = content.get("metrics") if isinstance(content.get("metrics"), dict) else {}
    row = {
        "topic_id": ref.topic_id,
        "topic_key": ref.key,
        "topic": ref.name,
        "summary_path": str(summary_path),
        "score_source": source_label,
        "score_root": str(source_root),
        "outline_coverage": numeric_or_none(outline_metrics.get("coverage")),
        "outline_relevance": numeric_or_none(outline_metrics.get("relevance")),
        "outline_structure": numeric_or_none(outline_metrics.get("structure")),
        "outline_average": numeric_or_none(outline.get("average")),
        "content_coverage": numeric_or_none(content_metrics.get("coverage")),
        "content_depth": numeric_or_none(content_metrics.get("depth")),
        "content_focus": numeric_or_none(content_metrics.get("focus")),
        "content_coherence": numeric_or_none(content_metrics.get("coherence")),
        "content_fluency": numeric_or_none(content_metrics.get("fluency")),
        "content_average": numeric_or_none(content.get("average")),
        "richness_figures": numeric_or_none(richness.get("figures")),
        "richness_tables": numeric_or_none(richness.get("tables")),
        "richness_total": numeric_or_none(richness.get("total")),
        "quiz_compare_win_rate": numeric_or_none(quiz.get("compare_win_rate_avg")),
        "quiz_specific_score": numeric_or_none(quiz.get("specific_score_avg")),
    }
    row["richness_elements"] = _sum_optional(row["richness_figures"], row["richness_tables"])
    row["richness_estimated_chars"] = _estimated_richness_length(
        elements=row["richness_elements"],
        richness=row["richness_total"],
    )
    row["quality_average"] = mean(
        value
        for value in (row["outline_average"], row["content_average"])
        if isinstance(value, (int, float))
    )
    return row


def _aggregate_batch_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    metric_keys = [
        "outline_coverage",
        "outline_relevance",
        "outline_structure",
        "outline_average",
        "content_coverage",
        "content_depth",
        "content_focus",
        "content_coherence",
        "content_fluency",
        "content_average",
        "quality_average",
        "richness_figures",
        "richness_tables",
        "richness_elements",
        "richness_total",
        "richness_estimated_chars",
        "quiz_compare_win_rate",
        "quiz_specific_score",
    ]
    aggregates: dict[str, Any] = {}
    for key in metric_keys:
        values = [row.get(key) for row in rows if isinstance(row.get(key), (int, float))]
        aggregates[key] = {
            "mean": mean(float(value) for value in values),
            "count": len(values),
        }
    return aggregates


def _sum_optional(left: Any, right: Any) -> float | None:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) + float(right)
    return None


def _estimated_richness_length(*, elements: Any, richness: Any) -> float | None:
    if not isinstance(elements, (int, float)) or not isinstance(richness, (int, float)) or richness <= 0:
        return None
    return float(elements) * 100000.0 / float(richness)


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


def render_batch_summary_markdown(summary: dict[str, Any]) -> str:
    aggregates = summary.get("aggregates", {}) if isinstance(summary.get("aggregates"), dict) else {}
    lines = [
        "# SurveyBench Batch Summary",
        "",
        f"- Selected topics: `{summary.get('selected_topic_count', 0)}`",
        f"- Completed topics: `{summary.get('completed_topic_count', 0)}`",
        f"- Missing topics: `{summary.get('missing_topic_count', 0)}`",
        "",
        "## Paper-Style Result Table",
        "",
    ]
    lines.extend(render_batch_paper_style_table(aggregates, method_label="SimpleAutoResearch"))
    lines.extend(
        [
            "",
        "## Aggregate Means",
        "",
        ]
    )
    lines.extend(render_batch_aggregate_table(aggregates))
    lines.extend(["", "## Per-Topic Scores", ""])
    rows = summary.get("topics", []) if isinstance(summary.get("topics"), list) else []
    source_counts = _score_source_counts(rows)
    if source_counts:
        lines.append(
            "- Score sources: "
            + ", ".join(f"`{key}`={value}" for key, value in sorted(source_counts.items()))
        )
        lines.append("")
    lines.extend(render_batch_topic_table(rows))
    missing = summary.get("missing", []) if isinstance(summary.get("missing"), list) else []
    if missing:
        lines.extend(["", "## Missing or Unreadable Topic Summaries", ""])
        lines.append("| Topic | Expected Summary | Issue |")
        lines.append("| --- | --- | --- |")
        for row in missing:
            topic = f"{row.get('topic_id', '')} {row.get('topic', '')}".strip()
            issue = row.get("error") or "summary.json not found"
            lines.append(f"| {escape_table_text(topic)} | `{row.get('expected_summary', '')}` | {escape_table_text(str(issue))} |")
    return "\n".join(lines).rstrip() + "\n"


def render_batch_paper_style_table(aggregates: dict[str, Any], *, method_label: str) -> list[str]:
    rows = [
        ("**Outline Quality (1-5)**", ""),
        ("Coverage", _aggregate_mean(aggregates, "outline_coverage")),
        ("Relevance", _aggregate_mean(aggregates, "outline_relevance")),
        ("Structure", _aggregate_mean(aggregates, "outline_structure")),
        ("**Average**", _aggregate_mean(aggregates, "outline_average")),
        ("**Content Quality (1-5)**", ""),
        ("Coverage", _aggregate_mean(aggregates, "content_coverage")),
        ("Depth", _aggregate_mean(aggregates, "content_depth")),
        ("Focus", _aggregate_mean(aggregates, "content_focus")),
        ("Coherence", _aggregate_mean(aggregates, "content_coherence")),
        ("Fluency", _aggregate_mean(aggregates, "content_fluency")),
        ("**Average**", _aggregate_mean(aggregates, "content_average")),
        ("**Richness**", ""),
        ("Avg. Fig. Num.", _aggregate_mean(aggregates, "richness_figures")),
        ("Avg. Table Num.", _aggregate_mean(aggregates, "richness_tables")),
        ("Total Avg.", _aggregate_mean(aggregates, "richness_elements")),
        ("Native Richness Density", _aggregate_mean(aggregates, "richness_total")),
    ]
    lines = [
        f"| Dimension | {escape_table_text(method_label)} |",
        "| --- | ---: |",
    ]
    for label, value in rows:
        lines.append(f"| {label} | {fmt(value) if value != '' else ''} |")
    return lines


def _aggregate_mean(aggregates: dict[str, Any], key: str) -> Any:
    row = aggregates.get(key) if isinstance(aggregates.get(key), dict) else {}
    return row.get("mean")


def render_batch_aggregate_table(aggregates: dict[str, Any]) -> list[str]:
    groups = [
        ("Outline Quality (1-5)", [
            ("outline_coverage", "Coverage"),
            ("outline_relevance", "Relevance"),
            ("outline_structure", "Structure"),
            ("outline_average", "Average"),
        ]),
        ("Content Quality (1-5)", [
            ("content_coverage", "Coverage"),
            ("content_depth", "Depth"),
            ("content_focus", "Focus"),
            ("content_coherence", "Coherence"),
            ("content_fluency", "Fluency"),
            ("content_average", "Average"),
        ]),
        ("Combined Quality", [
            ("quality_average", "Outline/Content Avg."),
        ]),
        ("Richness", [
            ("richness_figures", "Avg. Fig. Num."),
            ("richness_tables", "Avg. Table Num."),
            ("richness_elements", "Avg. Fig.+Table Num."),
            ("richness_total", "Native Richness Density"),
            ("richness_estimated_chars", "Estimated Text Chars"),
        ]),
        ("Quiz-Based Evaluation", [
            ("quiz_compare_win_rate", "Compare Win Rate"),
            ("quiz_specific_score", "Specific Score"),
        ]),
    ]
    lines = ["| Dimension | Mean | Count |", "| --- | ---: | ---: |"]
    for title, metrics in groups:
        lines.append(f"| **{title}** |  |  |")
        for key, label in metrics:
            row = aggregates.get(key) if isinstance(aggregates.get(key), dict) else {}
            lines.append(f"| {label} | {fmt(row.get('mean'))} | {row.get('count', 0)} |")
    return lines


def render_batch_topic_table(rows: Sequence[dict[str, Any]]) -> list[str]:
    lines = [
        "_Native richness density follows SurveyBench's original formula: `(figures + tables) / text_length * 1e5`. A short survey with many tables/figures can therefore score very high._",
        "",
        "| Topic | Source | Outline Avg. | Content Avg. | Quality Avg. | Fig. | Table | Elements | Richness Density | Est. Chars |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        topic = f"{row.get('topic_id', '')} {row.get('topic', '')}".strip()
        lines.append(
            "| "
            + " | ".join(
                [
                    escape_table_text(topic),
                    escape_table_text(str(row.get("score_source") or "")),
                    fmt(row.get("outline_average")),
                    fmt(row.get("content_average")),
                    fmt(row.get("quality_average")),
                    fmt(row.get("richness_figures")),
                    fmt(row.get("richness_tables")),
                    fmt(row.get("richness_elements")),
                    fmt(row.get("richness_total")),
                    fmt(row.get("richness_estimated_chars")),
                ]
            )
            + " |"
        )
    return lines


def _score_source_counts(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("score_source") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


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
    lines.append(f"| Native Richness Density | {fmt(richness.get('total'))} |")
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


def topic_key(index: int, topic: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
    return f"topic{index:02d}-{slug or 'topic'}"


def mean(values: Iterable[float]) -> float | None:
    rows = list(values)
    return sum(rows) / len(rows) if rows else None


def fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    if value is None:
        return "-"
    return str(value)


def escape_table_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("|", "\\|")).strip()


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

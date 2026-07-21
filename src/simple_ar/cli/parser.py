from __future__ import annotations

import argparse

from simple_ar.code_task.runtime.config import CodeTaskConfigError, parse_metric_direction_arg


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="simple-ar")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Start a new research run.")
    run_parser.add_argument("--config", default=None, help="Optional TOML config for the 8-stage run.")
    run_parser.add_argument("--topic", default=None)
    run_parser.add_argument("--output-root", default=None)
    run_parser.add_argument("--from-stage", default=None)
    run_parser.add_argument("--to-stage", default=None)
    run_parser.add_argument("--model", default=None)
    run_parser.add_argument("--llm-workers", type=int, default=None)
    run_parser.add_argument("--max-papers", type=int, default=None)
    run_parser.add_argument("--search-query", default=None)
    run_parser.add_argument("--experiment-template", default=None)
    run_parser.add_argument("--experiment-timeout", type=int, default=None)
    _add_pipeline_code_task_args(run_parser)
    run_parser.add_argument("--no-llm", action="store_true", default=None)
    run_parser.add_argument("--offline-search", action="store_true", default=None)
    run_parser.add_argument("--allow-fixture-fallback", action="store_true", default=None)
    run_parser.add_argument("--strict-search", action="store_true", default=None)
    run_parser.add_argument("--no-retrieval", action="store_true", default=None)
    run_parser.add_argument("--retrieval-top-k", type=int, default=None)
    run_parser.add_argument(
        "--report-mode",
        choices=("auto", "research_only", "experiment"),
        default=None,
        help="Report drafting mode: auto (based on results.json), research_only, or experiment.",
    )
    run_parser.add_argument(
        "--report-reviewer",
        choices=("llm", "disabled"),
        default=None,
        help="Override the report reviewer backend. `disabled` skips the reviewer/revision loop.",
    )
    _add_report_output_args(run_parser)
    run_parser.add_argument("--quiet", action="store_true", default=None)
    run_parser.add_argument(
        "--overwrite-stage-artifacts",
        action="store_true",
        default=None,
        help="Do not archive existing code/run stage artifacts before rerunning those stages.",
    )

    resume_parser = subparsers.add_parser("resume", help="Resume an existing run.")
    resume_parser.add_argument("run_dir")
    resume_parser.add_argument("--config", default=None, help="Optional TOML config overrides.")
    resume_parser.add_argument("--from-stage", default=None)
    resume_parser.add_argument("--to-stage", default=None)
    resume_parser.add_argument("--model", default=None)
    resume_parser.add_argument("--llm-workers", type=int, default=None)
    resume_parser.add_argument("--max-papers", type=int, default=None)
    resume_parser.add_argument("--search-query", default=None)
    resume_parser.add_argument("--experiment-template", default=None)
    resume_parser.add_argument("--experiment-timeout", type=int, default=None)
    _add_pipeline_code_task_args(resume_parser)
    resume_parser.add_argument("--no-llm", action="store_true", default=None)
    resume_parser.add_argument("--offline-search", action="store_true", default=None)
    resume_parser.add_argument("--allow-fixture-fallback", action="store_true", default=None)
    resume_parser.add_argument("--strict-search", action="store_true", default=None)
    resume_parser.add_argument("--no-retrieval", action="store_true", default=None)
    resume_parser.add_argument("--retrieval-top-k", type=int, default=None)
    resume_parser.add_argument(
        "--report-mode",
        choices=("auto", "research_only", "experiment"),
        default=None,
        help="Override report drafting mode for a resumed run.",
    )
    resume_parser.add_argument(
        "--report-reviewer",
        choices=("llm", "disabled"),
        default=None,
        help="Override the report reviewer backend. `disabled` skips the reviewer/revision loop.",
    )
    _add_report_output_args(resume_parser)
    resume_parser.add_argument("--quiet", action="store_true", default=None)
    resume_parser.add_argument(
        "--overwrite-stage-artifacts",
        action="store_true",
        default=None,
        help="Do not archive existing code/run stage artifacts before rerunning those stages.",
    )

    status_parser = subparsers.add_parser("status", help="Show run status.")
    status_parser.add_argument("run_dir")

    tools_parser = subparsers.add_parser("tools", help="Inspect or serve run-local SimpleAutoResearch tools.")
    tools_subparsers = tools_parser.add_subparsers(dest="tools_command", required=True)
    tools_schema = tools_subparsers.add_parser("schema", help="Export OpenAI or MCP tool schemas.")
    tools_schema.add_argument("--format", choices=("mcp", "openai"), default="mcp")
    tools_schema.add_argument("--output", default=None)
    tools_call = tools_subparsers.add_parser("call", help="Call one run-local read-only tool.")
    tools_call.add_argument("run_dir")
    tools_call.add_argument("tool_name")
    tools_call.add_argument("--args-json", default="{}")
    tools_call.add_argument("--args-file", default=None, help="Read tool arguments from a JSON file.")
    tools_call.add_argument("--debug-payloads", action="store_true")
    tools_mcp = tools_subparsers.add_parser("serve-mcp", help="Serve read-only run tools over MCP stdio.")
    tools_mcp.add_argument("run_dir")
    tools_mcp.add_argument("--debug-payloads", action="store_true")

    code_task_parser = subparsers.add_parser(
        "code-task",
        help="Work with an existing codebase in an isolated run workspace.",
    )
    code_task_subparsers = code_task_parser.add_subparsers(
        dest="code_task_command",
        required=True,
    )
    code_task_init = code_task_subparsers.add_parser(
        "init",
        help="Prepare a code-task workspace and build a code index.",
    )
    code_task_init.add_argument(
        "--config",
        default=None,
        help="Optional TOML config file for code-task init settings.",
    )
    code_task_init.add_argument("--code-root", default=None)
    code_task_init.add_argument(
        "--kind",
        choices=("existing_project", "greenfield"),
        default=None,
        help="Code-task mode. Use greenfield for from-scratch project generation.",
    )
    code_task_init.add_argument("--task-file", default=None)
    code_task_init.add_argument("--output-root", default=None)
    code_task_init.add_argument("--name", default=None)
    code_task_init.add_argument("--benchmark-command", default=None)
    code_task_init.add_argument(
        "--primary-metric",
        default=None,
        help=(
            "Primary benchmark metric for before/after verdicts, for example "
            "`accuracy` or `macro_f1`."
        ),
    )
    code_task_init.add_argument(
        "--metric-direction",
        action="append",
        default=[],
        type=_metric_direction_arg,
        metavar="METRIC=DIRECTION",
        help=(
            "Metric direction for comparison. Direction aliases include "
            "higher, lower, resource, and ignore. May be repeated."
        ),
    )
    _add_code_task_env_args(code_task_init)
    _add_code_task_workspace_args(code_task_init)
    code_task_init.add_argument(
        "--max-file-bytes",
        type=int,
        default=None,
        help="Maximum file size copied in copy/sparse modes. Use 0 to disable.",
    )
    code_task_probe = code_task_subparsers.add_parser(
        "probe",
        help="Inspect the workspace runtime and project environment.",
    )
    code_task_probe.add_argument("run_dir")
    _add_code_task_env_args(code_task_probe)

    code_task_map = code_task_subparsers.add_parser(
        "map",
        help="Build or refresh layered repo-map artifacts for a code-task run.",
    )
    code_task_map.add_argument("run_dir")
    code_task_map.add_argument(
        "--no-refresh-index",
        action="store_true",
        help="Reuse the existing codebase_index.json instead of scanning the current workspace.",
    )
    code_task_map.add_argument(
        "--show-summary",
        action="store_true",
        help="Print repo_map_summary.md after writing it.",
    )

    code_task_locate = code_task_subparsers.add_parser(
        "locate",
        help="Rank likely editable files and read-only evidence from the repo map.",
    )
    code_task_locate.add_argument("run_dir")
    code_task_locate.add_argument(
        "--query",
        default=None,
        help="Optional locate query. Defaults to code_task/task.md.",
    )
    code_task_locate.add_argument(
        "--top-k",
        type=int,
        default=8,
        help="Maximum candidates kept in each editable/evidence group.",
    )
    code_task_locate.add_argument(
        "--refresh-map",
        action="store_true",
        help="Rebuild codebase_index.json and repo_map.json before locating files.",
    )
    code_task_locate.add_argument(
        "--no-read-only",
        action="store_true",
        help="Omit protected read-only evidence files from locate results.",
    )
    code_task_locate.add_argument(
        "--show-summary",
        action="store_true",
        help="Print locate_results.md after writing it.",
    )

    code_task_context = code_task_subparsers.add_parser(
        "context",
        help="Build a bounded prompt context pack from locate results.",
    )
    code_task_context.add_argument("run_dir")
    code_task_context.add_argument(
        "--query",
        default=None,
        help="Optional locate query. Defaults to code_task/task.md.",
    )
    code_task_context.add_argument(
        "--top-k",
        type=int,
        default=8,
        help="Candidate budget passed to locate for each candidate group.",
    )
    code_task_context.add_argument(
        "--max-files",
        type=int,
        default=8,
        help="Maximum snippets included in the context pack.",
    )
    code_task_context.add_argument(
        "--max-source-chars-per-file",
        type=int,
        default=4000,
        help="Per-file source snippet character budget.",
    )
    code_task_context.add_argument(
        "--max-total-chars",
        type=int,
        default=20000,
        help="Total source snippet character budget.",
    )
    code_task_context.add_argument(
        "--refresh-map",
        action="store_true",
        help="Rebuild codebase_index.json and repo_map.json before locating files.",
    )
    code_task_context.add_argument(
        "--show-prompt",
        action="store_true",
        help="Print prompt_context.md after writing it.",
    )

    code_task_work_plan = code_task_subparsers.add_parser(
        "work-plan",
        help="Generate a batch-oriented implementation work plan for a code-task run.",
    )
    code_task_work_plan.add_argument("run_dir")
    code_task_work_plan.add_argument("--model", default=None)
    code_task_work_plan.add_argument("--no-llm", action="store_true")
    code_task_work_plan.add_argument("--force", action="store_true")
    code_task_work_plan.add_argument(
        "--allow-planning-fallback",
        action="store_true",
        help="Allow deterministic fallback if LLM work planning fails.",
    )
    code_task_work_plan.add_argument(
        "--llm-retry-attempts",
        type=int,
        default=1,
        help="LLM work planning attempts before failing or falling back.",
    )
    code_task_work_plan.add_argument("--max-files", type=int, default=8)
    code_task_work_plan.add_argument("--max-source-chars-per-file", type=int, default=2500)

    code_task_batch = code_task_subparsers.add_parser(
        "batch",
        help="Create an attempt/batch state directory for one work-plan item.",
    )
    code_task_batch.add_argument("run_dir")
    code_task_batch.add_argument(
        "--work-item",
        required=True,
        help="Work-plan item id to execute, for example W1.",
    )
    code_task_batch.add_argument(
        "--attempt-id",
        default=None,
        help="Optional attempt id such as attempt-001. Defaults to the active attempt.",
    )
    code_task_batch.add_argument(
        "--force",
        action="store_true",
        help="Create a new batch even if the active attempt already has one for this item.",
    )

    code_task_plan = code_task_subparsers.add_parser(
        "plan",
        help="Generate a human-reviewable patch plan for a code-task run.",
    )
    code_task_plan.add_argument("run_dir")
    code_task_plan.add_argument("--model", default=None)
    code_task_plan.add_argument("--no-llm", action="store_true")
    code_task_plan.add_argument("--force", action="store_true")
    code_task_plan.add_argument(
        "--allow-planning-fallback",
        action="store_true",
        help="Allow deterministic fallback if LLM patch planning fails.",
    )
    code_task_plan.add_argument(
        "--llm-retry-attempts",
        type=int,
        default=1,
        help="LLM patch planning attempts before failing or falling back.",
    )
    code_task_plan.add_argument("--max-files", type=int, default=8)
    code_task_plan.add_argument("--max-source-chars-per-file", type=int, default=2500)

    code_task_decide = code_task_subparsers.add_parser(
        "decide-plan",
        help="Record a human decision for the current code-task patch plan.",
    )
    code_task_decide.add_argument("run_dir")
    code_task_decide.add_argument(
        "--decision",
        required=True,
        choices=("approve", "reject", "revise"),
    )
    code_task_decide.add_argument("--note", default="")
    code_task_decide.add_argument("--reviewer", default="user")

    code_task_propose = code_task_subparsers.add_parser(
        "propose-edits",
        help="Ask the model to propose controlled old/new text edits.",
    )
    code_task_propose.add_argument("run_dir")
    code_task_propose.add_argument("--model", default=None)
    code_task_propose.add_argument("--no-llm", action="store_true")
    code_task_propose.add_argument("--force", action="store_true")
    code_task_propose.add_argument("--max-files", type=int, default=8)
    code_task_propose.add_argument("--max-source-chars-per-file", type=int, default=4000)
    code_task_propose.add_argument(
        "--allow-large-edits",
        action="store_true",
        help="Accept proposals that exceed the normal budget but fit the large budget.",
    )

    code_task_apply = code_task_subparsers.add_parser(
        "apply-edits",
        help="Safely apply controlled old/new text edits to the workspace.",
    )
    code_task_apply.add_argument("run_dir")
    code_task_apply.add_argument("--edits-file", default=None)
    code_task_apply.add_argument(
        "--allow-unapproved-plan",
        action="store_true",
        help="Bypass the human approval gate. Intended only for local experiments/tests.",
    )
    code_task_apply.add_argument(
        "--allow-large-edits",
        action="store_true",
        help="Apply a reviewed proposal that requires large-edit approval.",
    )

    code_task_validate = code_task_subparsers.add_parser(
        "validate",
        help="Run lightweight static validation over the code-task workspace.",
    )
    code_task_validate.add_argument("run_dir")
    code_task_validate.add_argument("--strict", action="store_true")
    code_task_validate.add_argument("--max-file-bytes", type=int, default=500_000)

    code_task_baseline = code_task_subparsers.add_parser(
        "baseline",
        help="Run the recorded benchmark before applying a patch.",
    )
    code_task_baseline.add_argument("run_dir")
    code_task_baseline.add_argument("--command", dest="benchmark_command", default=None)
    code_task_baseline.add_argument("--timeout", type=int, default=60)
    code_task_baseline.add_argument("--skip-validation", action="store_true")
    _add_code_task_env_args(code_task_baseline)

    code_task_run = code_task_subparsers.add_parser(
        "run",
        help="Run the recorded benchmark command in the code-task workspace.",
    )
    code_task_run.add_argument("run_dir")
    code_task_run.add_argument("--command", dest="benchmark_command", default=None)
    code_task_run.add_argument("--timeout", type=int, default=60)
    code_task_run.add_argument("--skip-validation", action="store_true")
    _add_code_task_env_args(code_task_run)

    code_task_analyze = code_task_subparsers.add_parser(
        "analyze-failure",
        help="Write a deterministic failure analysis from the latest benchmark run.",
    )
    code_task_analyze.add_argument("run_dir")

    code_task_repair = code_task_subparsers.add_parser(
        "repair",
        help="Propose bounded repair edits from the latest failure analysis.",
    )
    code_task_repair.add_argument("run_dir")
    code_task_repair.add_argument("--model", default=None)
    code_task_repair.add_argument("--no-llm", action="store_true")
    code_task_repair.add_argument("--max-files", type=int, default=8)
    code_task_repair.add_argument("--max-source-chars-per-file", type=int, default=4000)

    code_task_execute = code_task_subparsers.add_parser(
        "execute",
        help="Run a conservative state-aware code-task sequence.",
    )
    code_task_execute.add_argument("run_dir")
    code_task_execute.add_argument(
        "--config",
        default=None,
        help="Optional TOML config for execute model, budget, and runtime settings.",
    )
    code_task_execute.add_argument(
        "--to-step",
        choices=(
            "probe",
            "baseline",
            "work-plan",
            "batch",
            "plan",
            "propose-edits",
            "apply-edits",
            "review",
            "validate",
            "run",
            "analyze-failure",
            "repair",
        ),
        default=None,
        help="Last step execute may attempt.",
    )
    code_task_execute.add_argument("--dry-run", action="store_true")
    code_task_execute.add_argument("--model", default=None)
    code_task_execute.add_argument("--no-llm", action="store_true")
    code_task_execute.add_argument("--timeout", type=int, default=60)
    code_task_execute.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Auto-approve inline review gates in normal execute mode; with "
            "--interactive, auto-continue eligible primitive steps."
        ),
    )
    code_task_execute.add_argument(
        "--interactive",
        action="store_true",
        help="Debug mode: ask before each primitive step instead of running to the next review gate.",
    )
    code_task_execute.add_argument(
        "--no-review-inline",
        action="store_true",
        help="Disable inline review prompts and stop at review gates instead.",
    )
    code_task_execute.add_argument("--skip-validation", action="store_true")
    code_task_execute.add_argument("--strict-validation", action="store_true")
    code_task_execute.add_argument("--validation-max-file-bytes", type=int, default=500_000)
    code_task_execute.add_argument(
        "--apply-proposed-edits",
        action="store_true",
        help="Apply reviewed proposed_edits.json after plan approval.",
    )
    code_task_execute.add_argument(
        "--allow-large-edits",
        action="store_true",
        help="Allow execute to accept/apply proposals that exceed the normal edit budget.",
    )
    code_task_execute.add_argument(
        "--allow-planning-fallback",
        action="store_true",
        help=(
            "Allow deterministic offline work/patch plans if LLM planning fails. "
            "By default execute stops so the same command can retry cleanly."
        ),
    )
    code_task_execute.add_argument(
        "--planning-mode",
        choices=("tool_agent", "compact"),
        default=None,
        help="Greenfield planning mode: tool_agent decomposes planning; compact uses the older single architecture call.",
    )
    code_task_execute.add_argument(
        "--llm-retry-attempts",
        type=int,
        default=None,
        help="Stage-level LLM attempts for planning, greenfield architecture/file generation, and patch repair.",
    )
    code_task_execute.add_argument(
        "--planning-review-rounds",
        type=int,
        default=None,
        help="Override greenfield planning reviewer revision rounds for this execute call.",
    )
    code_task_execute.add_argument("--repair-rounds", type=int, default=None)
    code_task_execute.add_argument(
        "--repair-context",
        choices=("full", "raw_logs_only"),
        default=None,
        help=(
            "Ablation control for repair prompts. `full` uses structured failure diagnostics; "
            "`raw_logs_only` omits failure-graph bundle fields from LLM repair context."
        ),
    )
    code_task_execute.add_argument(
        "--no-repair-memory",
        action="store_true",
        help="Ablation control: do not inject previous repair memory into repair prompts.",
    )
    code_task_execute.add_argument(
        "--contract-context",
        choices=("full", "minimal"),
        default=None,
        help=(
            "Ablation control for model prompts. `minimal` keeps durable artifacts but passes "
            "only a task-level contract view to planning/writing/review/repair prompts."
        ),
    )
    code_task_execute.add_argument(
        "--review-gate",
        choices=("strict", "runtime"),
        default=None,
        help=(
            "Greenfield review gate mode. `strict` blocks on all blocking review findings; "
            "`runtime` only blocks safety/direct executability issues and lets judge score packaging/report gaps."
        ),
    )
    code_task_execute.add_argument(
        "--baseline-policy",
        choices=("auto", "run", "skip", "provided", "none"),
        default=None,
        help=(
            "Existing-project baseline behavior: auto/run executes the unchanged "
            "benchmark, skip/none continues without it, provided records metrics "
            "from --baseline-metrics-file."
        ),
    )
    code_task_execute.add_argument(
        "--baseline-metrics-file",
        default=None,
        help="JSON or metric-line file used when --baseline-policy provided is selected.",
    )
    code_task_execute.add_argument("--max-files", type=int, default=8)
    code_task_execute.add_argument("--max-source-chars-per-file", type=int, default=4000)
    _add_code_task_env_args(code_task_execute)

    inspect_parser = subparsers.add_parser("inspect", help="Index and summarize run artifacts.")
    inspect_parser.add_argument("run_dir")

    search_parser = subparsers.add_parser(
        "search-artifacts",
        help="Search indexed run artifacts with lexical retrieval.",
    )
    search_parser.add_argument("run_dir")
    search_parser.add_argument("query")
    search_parser.add_argument("--top-k", type=int, default=8)
    search_parser.add_argument(
        "--include-operational",
        action="store_true",
        help="Also search runner metadata such as manifests and stage_meta.json.",
    )

    clean_parser = subparsers.add_parser(
        "clean",
        help="Review and clean rebuildable caches for one run or shared index store.",
    )
    clean_parser.add_argument(
        "run_dir",
        nargs="?",
        help="Run directory to clean. Omit when using --shared-index or --shared-cache.",
    )
    clean_parser.add_argument(
        "--yes",
        action="store_true",
        help="Delete the displayed cleanup targets without an interactive confirmation.",
    )
    clean_parser.add_argument(
        "--all-caches",
        action="store_true",
        help="Delete every known rebuildable cache/index/context artifact for this run.",
    )
    clean_parser.add_argument(
        "--shared-index",
        action="store_true",
        help=(
            "Dangerous: clear the shared research index store across runs "
            "(SQLite FTS/LanceDB accelerator data)."
        ),
    )
    clean_parser.add_argument(
        "--shared-cache",
        action="store_true",
        help=(
            "Strongest shared cleanup: clear shared research indexes and "
            "literature provider cache under .simple_ar_cache by default."
        ),
    )
    clean_parser.add_argument(
        "--index-root",
        default=None,
        help=(
            "Shared index root for --shared-index/--shared-cache. Defaults to "
            "SIMPLE_AR_RESEARCH_INDEX_ROOT or .simple_ar_cache/research_index."
        ),
    )
    clean_parser.add_argument(
        "--literature-cache-root",
        default=None,
        help=(
            "Literature cache root for --shared-cache. Defaults to "
            ".simple_ar_cache/literature."
        ),
    )
    clean_parser.add_argument(
        "--allow-external-index-root",
        action="store_true",
        help="Allow shared cleanup to touch a cache/index root outside the current workspace.",
    )

    return parser

def _add_code_task_env_args(parser: argparse.ArgumentParser) -> None:
    """Add shared code-task execution environment policy arguments."""
    parser.add_argument(
        "--env-mode",
        choices=("current", "external"),
        default=None,
        help=(
            "Execution environment mode. `current` uses the active "
            "SimpleAutoResearch Python; `external` uses --python. "
            "No dependencies are installed."
        ),
    )
    parser.add_argument(
        "--python",
        dest="python_executable",
        default=None,
        help="Python executable path or command name for --env-mode external.",
    )

def _add_code_task_workspace_args(parser: argparse.ArgumentParser) -> None:
    """Add shared code-task workspace creation arguments."""
    parser.add_argument(
        "--workspace-mode",
        choices=("auto", "copy", "git_worktree", "sparse_copy"),
        default=None,
        help=(
            "Workspace strategy. `auto` prefers git_worktree for Git projects "
            "and falls back to copy; `copy` copies a guarded source tree; "
            "`git_worktree` creates a detached git worktree; "
            "`sparse_copy` is experimental and copies selected patterns."
        ),
    )
    parser.add_argument(
        "--workspace-include",
        action="append",
        default=None,
        help=(
            "POSIX glob copied by --workspace-mode sparse_copy. Repeatable. "
            "Prefer TOML [workspace].include for multiple patterns."
        ),
    )
    parser.add_argument(
        "--workspace-exclude",
        action="append",
        default=None,
        help=(
            "Additional POSIX glob skipped by --workspace-mode sparse_copy. "
            "Repeatable."
        ),
    )
    parser.add_argument(
        "--workspace-reuse-source-venv",
        action="store_true",
        default=None,
        help=(
            "When a source .venv is detected, record and use its Python "
            "interpreter as the initial external execution policy."
        ),
    )
    parser.add_argument(
        "--workspace-setup-hook",
        default=None,
        help=(
            "Record a project setup command for future managed environments. "
            "The hook is not executed during init."
        ),
    )

def _add_pipeline_code_task_args(parser: argparse.ArgumentParser) -> None:
    """Add optional 8-stage code-task experiment configuration arguments."""
    parser.add_argument(
        "--code-task-config",
        default=None,
        help="Optional TOML config for --experiment-template code_task_project.",
    )
    parser.add_argument(
        "--code-root",
        dest="code_task_code_root",
        default=None,
        help="Source project prepared by --experiment-template code_task_project.",
    )
    parser.add_argument(
        "--task-file",
        dest="code_task_task_file",
        default=None,
        help="Markdown task file for --experiment-template code_task_project.",
    )
    parser.add_argument(
        "--benchmark-command",
        dest="code_task_benchmark_command",
        default=None,
        help="Benchmark command run before and after code-task edits.",
    )
    parser.add_argument(
        "--code-task-name",
        default=None,
        help="Optional display name for the embedded code-task experiment.",
    )
    parser.add_argument(
        "--code-task-max-file-bytes",
        type=int,
        default=None,
        help="Maximum source file size copied in embedded copy/sparse modes.",
    )
    parser.add_argument(
        "--code-task-workspace-mode",
        choices=("auto", "copy", "git_worktree", "sparse_copy"),
        default=None,
        help="Embedded code-task workspace strategy.",
    )
    parser.add_argument(
        "--code-task-workspace-reuse-source-venv",
        action="store_true",
        default=None,
        help="Use a detected source .venv Python for the embedded code task.",
    )
    parser.add_argument(
        "--code-task-workspace-setup-hook",
        default=None,
        help="Record a setup command for the embedded code-task workspace.",
    )
    parser.add_argument(
        "--code-task-env-mode",
        choices=("current", "external"),
        default=None,
        help="Embedded code-task execution environment mode.",
    )
    parser.add_argument(
        "--code-task-python",
        dest="code_task_python_executable",
        default=None,
        help="Python executable for --code-task-env-mode external.",
    )
    parser.add_argument(
        "--primary-metric",
        dest="code_task_primary_metric",
        default=None,
        help="Primary benchmark metric for embedded code-task comparison.",
    )
    parser.add_argument(
        "--metric-direction",
        dest="code_task_metric_direction",
        action="append",
        default=None,
        type=_metric_direction_arg,
        metavar="METRIC=DIRECTION",
        help="Metric direction for embedded code-task comparison. May be repeated.",
    )


def _add_report_output_args(parser: argparse.ArgumentParser) -> None:
    """Add shared report output policy arguments for run/resume."""
    parser.add_argument(
        "--report-output-mode",
        choices=("overwrite", "archive", "variant"),
        default=None,
        help=(
            "Report write policy. overwrite replaces 08-report outputs; "
            "archive backs up existing outputs before replacing them; "
            "variant writes a separate 08-report/variants/<label> package "
            "without replacing the current report.md when it already exists."
        ),
    )
    parser.add_argument(
        "--report-output-label",
        default=None,
        help="Optional folder label for --report-output-mode variant/archive.",
    )


def _metric_direction_arg(value: str) -> tuple[str, str]:
    """Parse ``--metric-direction metric=direction`` arguments."""
    try:
        return parse_metric_direction_arg(value)
    except CodeTaskConfigError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from simple_ar.code_task.orchestration.workflow import CodeTaskInitResult
from simple_ar.code_task.orchestration.execute import CodeTaskExecuteResult, ExecuteStepRecord
from simple_ar.core.console import make_console
from simple_ar.core.reporting import style_progress_message


STEP_DESCRIPTIONS = {
    "probe": "inspect OS, Python, tools, GPU, and dependency-file signals",
    "baseline": "run the unchanged benchmark before editing",
    "work-plan": "create a batch-oriented implementation plan",
    "batch": "create attempt/batch state for the selected work item",
    "plan": "draft the human-reviewable patch plan",
    "propose-edits": "ask the model for controlled old/new edit proposals",
    "apply-edits": "apply reviewed edits to the isolated workspace",
    "review": "review the applied patch for scope, interface, tests, and benchmark risk",
    "validate": "run static safety and syntax validation",
    "run": "run the patched benchmark and compare results",
    "analyze-failure": "summarize the latest validation or benchmark failure",
    "repair": "propose a bounded repair edit set",
}


STATUS_STYLES = {
    "done": "bold green",
    "skipped": "cyan",
    "blocked": "bold red",
    "would_run": "yellow",
}


STOP_STYLES = {
    "completed": "bold green",
    "stop_point": "cyan",
    "approval_required": "yellow",
    "proposal_review_required": "yellow",
    "large_edit_approval_required": "bold yellow",
    "patch_apply_failed": "bold red",
    "review_failed": "bold red",
    "validation_failed": "bold red",
    "benchmark_failed": "bold red",
    "baseline_failed": "bold red",
    "llm_planning_failed": "bold red",
}


def render_execute_header(
    run_dir: Path,
    *,
    to_step: str,
    use_llm: bool,
    timeout_sec: int,
    dry_run: bool,
    console: Console | None = None,
) -> None:
    """Render a compact code-task execute header."""

    console = console or make_console()
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(overflow="fold", no_wrap=False)
    table.add_row("Run", escape(str(run_dir)))
    table.add_row("Target", to_step)
    table.add_row("LLM", "enabled" if use_llm else "disabled")
    table.add_row("Timeout", f"{timeout_sec}s")
    if dry_run:
        table.add_row("Mode", "[yellow]dry run[/yellow]")
    console.print(Panel(table, title="[bold cyan]Code Task Execute[/bold cyan]", border_style="cyan"))


def render_step_preview(step: str, *, console: Console | None = None) -> None:
    """Render the next step before asking for confirmation."""

    console = console or make_console()
    description = STEP_DESCRIPTIONS.get(step, "run the next code-task step")
    console.print(
        Panel(
            Text.assemble(("Next step: ", "bold cyan"), (step, "bold white"), ("\n" + description, "dim")),
            border_style="cyan",
        )
    )


def confirm_next_step(step: str, *, console: Console | None = None, assume_yes: bool = False) -> bool:
    """Ask whether to continue with ``step`` unless ``assume_yes`` is set."""

    if assume_yes:
        return True
    console = console or make_console()
    answer = console.input(f"[bold]Continue with {step}? Type yes or no: [/bold]").strip().lower()
    return answer in {"yes", "y"}


def render_review_gate(
    *,
    title: str,
    artifact: Path,
    action: str,
    warning: str,
    console: Console | None = None,
) -> None:
    """Render an inline human-review gate before a sensitive action."""

    console = console or make_console()
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold yellow", no_wrap=True)
    table.add_column(overflow="fold", no_wrap=False)
    table.add_row("Review", escape(str(artifact)))
    table.add_row("Next", escape(action))
    table.add_row("Warning", f"[bold yellow]{escape(warning)}[/bold yellow]")
    console.print(Panel(table, title=f"[bold yellow]{escape(title)}[/bold yellow]", border_style="yellow"))


def confirm_review_gate(
    prompt: str,
    *,
    console: Console | None = None,
    assume_yes: bool = False,
) -> bool:
    """Ask the user whether to continue at an inline review gate."""

    console = console or make_console()
    if assume_yes:
        console.print("[yellow]Auto-approved review gate because --yes was supplied.[/yellow]")
        return True
    try:
        answer = console.input(
            f"[bold yellow]{escape(prompt)} Type yes or no: [/bold yellow]"
        ).strip().lower()
    except EOFError:
        console.print("[yellow]No interactive input was available; review gate was not approved.[/yellow]")
        return False
    return answer in {"yes", "y"}


def render_execute_result(
    result: CodeTaskExecuteResult,
    *,
    console: Console | None = None,
    steps: tuple[ExecuteStepRecord, ...] | None = None,
) -> None:
    """Render execute progress and stop reason with Rich tables."""

    console = console or make_console()
    selected_steps = steps if steps is not None else result.steps
    if selected_steps:
        table = Table(title="Step Progress", show_header=True, header_style="bold cyan", expand=False)
        table.add_column("Step", style="bold")
        table.add_column("Status")
        table.add_column("Detail", overflow="fold")
        for step in selected_steps:
            style = STATUS_STYLES.get(step.status, "white")
            table.add_row(
                escape(step.step),
                f"[{style}]{escape(step.status)}[/{style}]",
                escape(step.detail),
            )
        console.print(table)

    stop_style = STOP_STYLES.get(result.stop_reason, "white")
    summary = Table.grid(padding=(0, 1))
    summary.add_column(style="bold cyan", no_wrap=True)
    summary.add_column(overflow="fold", no_wrap=False)
    summary.add_row("Stop reason", f"[{stop_style}]{escape(result.stop_reason)}[/{stop_style}]")
    summary.add_row("Next action", escape(result.next_action))
    summary.add_row("Summary", escape(str(result.summary_path)))
    console.print(Panel(summary, title="[bold cyan]Execute State[/bold cyan]", border_style="cyan"))


def render_execute_message(message: str, *, console: Console | None = None) -> None:
    """Render one low-noise progress message emitted by the orchestrator."""

    console = console or make_console()
    text = Text("  - ", style="dim")
    text.append(str(message), style=style_progress_message(str(message)))
    console.print(text)


def render_init_result(
    result: CodeTaskInitResult,
    *,
    config_path: str | None = None,
    benchmark_command: str | None = None,
    primary_metric: str | None = None,
    metric_directions: dict[str, str] | None = None,
    console: Console | None = None,
) -> None:
    """Render a code-task initialization summary with stable labels."""

    console = console or make_console()
    project = result.codebase_index.get("project", {})
    files_label = "Files copied:" if result.workspace.mode == "copy" else "Files selected:"
    files_value = (
        f"{result.copy_report.files_copied} ({result.copy_report.skipped_count} skipped)"
        if result.workspace.mode in {"copy", "sparse_copy"}
        else f"workspace created with {result.workspace.mode}; source copy skipped"
    )

    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(overflow="fold", no_wrap=False)
    table.add_row("Code task run:", escape(str(result.run_dir)))
    table.add_row("Mode:", escape(result.kind))
    table.add_row("Workspace:", escape(str(result.workspace.workspace_dir)))
    if result.workspace.project_root != result.workspace.workspace_dir:
        table.add_row("Project root:", escape(str(result.workspace.project_root)))
    table.add_row("Workspace mode:", escape(result.workspace.mode))
    if result.workspace.requested_mode != result.workspace.selected_mode:
        table.add_row(
            "Mode decision:",
            escape(f"{result.workspace.requested_mode} -> {result.workspace.selected_mode}"),
        )
    if result.workspace.fallback_reason:
        table.add_row("Fallback:", escape(result.workspace.fallback_reason.splitlines()[0]))
    table.add_row("Task:", escape(str(result.task_dir / "task.md")))
    table.add_row("Index:", escape(str(result.codebase_index_path)))
    table.add_row("Repo map:", escape(str(result.repo_map_path)))
    table.add_row(files_label, escape(files_value))
    table.add_row(
        "Indexed:",
        (
            f"{project.get('file_count', 0)} file(s), "
            f"{project.get('python_file_count', 0)} Python file(s), "
            f"{project.get('test_file_count', 0)} test file(s)"
        ),
    )
    if config_path:
        table.add_row("Config:", escape(config_path))
    if benchmark_command:
        table.add_row("Benchmark command:", escape(benchmark_command))
    if primary_metric:
        table.add_row("Primary metric:", escape(primary_metric))
    table.add_row("Environment mode:", escape(str(result.environment_policy.get("mode", "current"))))
    table.add_row("Python executable:", escape(str(result.environment_policy.get("python_executable", ""))))
    console.print(Panel(table, title="[bold cyan]Code Task Init[/bold cyan]", border_style="cyan"))

    if result.workspace.warnings or result.workspace.user_next_steps:
        notes = Table(title="Workspace notes", show_header=False, expand=False)
        notes.add_column("Kind", style="bold yellow")
        notes.add_column("Message", overflow="fold")
        for warning in result.workspace.warnings:
            notes.add_row("warning", escape(warning))
        for step in result.workspace.user_next_steps:
            notes.add_row("next", escape(step))
        console.print(notes)

    if metric_directions:
        metrics = Table(title="Metric directions:", show_header=True, header_style="bold cyan")
        metrics.add_column("Metric", style="bold")
        metrics.add_column("Direction")
        for name, direction in metric_directions.items():
            metrics.add_row(escape(name), escape(direction))
        console.print(metrics)

from __future__ import annotations

import sys

from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from simple_ar.core.console import make_console
from simple_ar.core.pipeline import PipelineEvent


def style_progress_message(message: str) -> str:
    """Return the Rich style for one progress line.

    The classifier is intentionally small and shared by the pipeline reporter
    and code-task CLI views so long-running commands feel visually consistent.
    """

    lower = message.lower()
    if "failed" in lower or "error" in lower:
        return "bright_red"
    if "llm usage" in lower:
        return "gold1"
    if "calling llm" in lower:
        return "orchid1"
    if "dependency advice" in lower:
        if "missing" in lower:
            return "bright_yellow"
        if "installed" in lower:
            return "spring_green2"
        return "deep_sky_blue1"
    if "search" in lower or "retriev" in lower or "arxiv" in lower or "openalex" in lower:
        return "deep_sky_blue1"
    if "rate limit" in lower or "fallback" in lower or "warning" in lower or "skipped" in lower:
        return "bright_yellow"
    if "archived" in lower:
        return "bright_yellow"
    if "running" in lower or "generating" in lower or "building" in lower or "writing" in lower:
        return "dodger_blue1"
    return "white"


class ConsoleReporter:
    """Render pipeline progress events to stdout.

    Args:
        enabled: Disable output when callers want quiet command-line runs.
    """

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.console = make_console(file=sys.stdout)

    def __call__(self, event: PipelineEvent) -> None:
        """Print one pipeline event with restrained Rich formatting."""
        if not self.enabled:
            return

        if event.name == "pipeline_start":
            self._print_pipeline_start(event)
            return

        if event.name == "stage_start":
            self._print_stage_start(event)
            return

        if event.name == "stage_message":
            self._print_stage_message(event.message)
            return

        if event.name == "llm_usage":
            self._print_stage_message(event.message)
            return

        if event.name == "stage_done":
            self._print_stage_done(event)
            return

        if event.name == "stage_failed":
            self._print_stage_failed(event)
            return

        if event.name == "pipeline_done":
            completed = event.data.get("completed_stages", 0)
            self.console.print(
                Panel(
                    f"[bold green]Pipeline completed[/bold green]\n"
                    f"[dim]Completed stages:[/dim] {escape(str(completed))}",
                    border_style="green",
                )
            )

    def _print_pipeline_start(self, event: PipelineEvent) -> None:
        table = Table.grid(padding=(0, 1))
        table.add_column(style="bold cyan", no_wrap=True)
        table.add_column()
        table.add_row("Run", escape(str(event.data.get("run_dir", ""))))
        table.add_row("Topic", escape(str(event.data.get("topic", ""))))
        table.add_row(
            "Stages",
            f"{escape(str(event.data.get('from_stage', '')))} -> "
            f"{escape(str(event.data.get('to_stage', '')))}",
        )
        self.console.print(Panel(table, title="SimpleAutoResearch", border_style="cyan"))

    def _print_stage_start(self, event: PipelineEvent) -> None:
        self.console.rule(self._stage_title(event), style="cyan")
        description = str(event.data.get("description", "")).strip()
        if description:
            self.console.print(f"[bold cyan]RUNNING[/bold cyan] [dim]{escape(description)}[/dim]")
            return
        self.console.print("[bold cyan]RUNNING[/bold cyan]")

    def _print_stage_done(self, event: PipelineEvent) -> None:
        outputs = event.data.get("outputs", [])
        output_text = ", ".join(str(item) for item in outputs) if isinstance(outputs, list) else ""
        duration = self._format_duration(event.data.get("duration_sec", 0))
        message = f"[bold green]DONE[/bold green] [dim]in {duration}[/dim]"
        if output_text:
            message += f"\n[dim]Outputs:[/dim] {escape(output_text)}"
        self.console.print(message)

    def _print_stage_failed(self, event: PipelineEvent) -> None:
        duration = self._format_duration(event.data.get("duration_sec", 0))
        error = escape(str(event.data.get("error", "")))
        self.console.print(
            f"[bold red]FAILED[/bold red] [dim]after {duration}[/dim]\n"
            f"[red]{error}[/red]"
        )

    def _print_stage_message(self, message: str) -> None:
        style = style_progress_message(message)
        bullet = Text("  - ", style="dim")
        text = Text(str(message), style=style)
        self.console.print(bullet + text)

    @staticmethod
    def _message_style(message: str) -> str:
        return style_progress_message(message)

    @staticmethod
    def _format_duration(value: object) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if number < 1:
            return f"{number:.3f}s"
        return f"{number:.2f}s"

    @staticmethod
    def _stage_prefix(event: PipelineEvent) -> str:
        stage = event.stage
        stage_name = stage.name.lower() if stage is not None else "pipeline"
        stage_number = int(stage) if stage is not None else 0
        stage_index = ConsoleReporter._format_count(event.data.get("stage_index", stage_number))
        total_stages = ConsoleReporter._format_count(event.data.get("total_stages", "?"))
        return f"[{stage_index}/{total_stages} | {stage_number:02d}-{stage_name}]"

    @staticmethod
    def _stage_title(event: PipelineEvent) -> str:
        stage = event.stage
        stage_name = stage.name.lower() if stage is not None else "pipeline"
        stage_number = int(stage) if stage is not None else 0
        stage_index = ConsoleReporter._format_count(event.data.get("stage_index", stage_number))
        total_stages = ConsoleReporter._format_count(event.data.get("total_stages", "?"))
        return f"{stage_index}/{total_stages}  {stage_number:02d}-{stage_name}"

    @staticmethod
    def _format_count(value: object) -> str:
        try:
            return f"{int(value):02d}"
        except (TypeError, ValueError):
            return str(value)

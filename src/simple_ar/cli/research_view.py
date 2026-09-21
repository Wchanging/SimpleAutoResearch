"""Rich presentation of the canonical research loop; no execution state of its own."""

from contextlib import contextmanager
from time import monotonic

from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from simple_ar.core.console import make_console
from simple_ar.core.reporting import style_progress_message


DESCRIPTIONS = {
    "plan": "Plan research questions and search queries",
    "search": "Search literature providers and select sources",
    "document_ingest": "Fetch and extract available documents",
    "read": "Read documents and collect traceable evidence",
    "synthesize": "Compare evidence and propose candidate ideas",
    "summarize": "Save the research summary",
    "assess_ideas": "Assess candidate evidence and feasibility",
    "research_design": "Design the selected experiment",
    "prepare_execution": "Prepare the isolated project workspace",
    "implement": "Locate code, propose edits, review and validate",
    "analysis": "Compare measured results with the hypothesis",
    "matrix_analysis": "Analyze paired experiment results",
    "report_write": "Draft and review paper sections",
    "report": "Assemble the report and references",
    "report_audit": "Audit citations, metrics and claims",
}


class ResearchConsole:
    def __init__(self, console=None):
        self.console = console or make_console()

    def start(self, view, *, model: str, topic: str):
        table = Table.grid(padding=(0, 2))
        table.add_column(style="cyan", no_wrap=True)
        table.add_column(overflow="fold")
        for label, value in (("Task", topic), ("Session", view.session_root), ("Model", model),
                             ("Status", view.status), ("Next", view.next_action or "none")):
            table.add_row(label, Text(str(value)))
        self.console.print(Panel(table, title="SimpleAutoResearch", border_style="cyan"))
        self.console.print("Elapsed time is shown while an action runs; completion percentages are not estimated.", style="dim")

    def message(self, message: str):
        self.console.print(Text("  - " + message, style=style_progress_message(message)))

    @contextmanager
    def action(self, action: str):
        description = DESCRIPTIONS.get(action, action.replace("_", " "))
        self.console.rule(Text(action, style="bold cyan"))
        self.console.print(Text(description))
        started = monotonic()
        # Redirected logs retain plain milestones without animation or ANSI frames.
        progress = Progress(SpinnerColumn(), TextColumn("{task.description}"), TimeElapsedColumn(),
                            console=self.console, transient=True, refresh_per_second=2,
                            disable=not self.console.is_terminal)
        try:
            with progress:
                progress.add_task("Running — waiting for action result", total=None)
                yield
        except BaseException:
            self.console.print(Text(f"Interrupted/failed after {monotonic() - started:.1f}s", style="bold red"))
            raise
        else:
            self.console.print(Text(f"Action returned in {monotonic() - started:.1f}s", style="dim"))

    def state(self, view):
        style = "green" if view.status == "completed" else "yellow" if view.status in {"paused", "blocked"} else "red" if view.status == "failed" else "cyan"
        self.console.print(Text(f"Status: {view.status}; next: {view.next_action or 'none'}", style=style))
        if view.status_reason:
            self.console.print(Text(view.status_reason))

    def finish(self, view):
        self.state(view)
        table = Table("Artifact", "Location", header_style="bold cyan")
        for name in ("summary", "implementation", "experiment", "matrix_results", "analysis", "report", "report_audit"):
            if name in view.state_refs:
                table.add_row(name, Text(str(view.session_root / view.state_refs[name].path)))
        self.console.print(table)
        self.console.print(Text(f"Artifacts: {len(view.state_refs)}; attempts: {len(view.attempts)}"))
        self.console.print("Completion describes delivered artifacts, not scientific success or paper quality.", style="dim")

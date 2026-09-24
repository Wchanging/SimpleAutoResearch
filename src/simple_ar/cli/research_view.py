"""Rich presentation of the canonical research loop; no execution state of its own."""

from contextlib import contextmanager
import os
import shlex
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
        self._last_decision = None
        self._last_interaction = None

    def start(self, view, *, model: str, topic: str):
        table = Table.grid(padding=(0, 2))
        table.add_column(style="cyan", no_wrap=True)
        table.add_column(overflow="fold")
        interaction = getattr(view, "work_plan", {}).get("interaction", {})
        mode = interaction.get("mode", "legacy") if isinstance(interaction, dict) else "legacy"
        for label, value in (("Task", topic), ("Session", view.session_root), ("Model", model),
                             ("Interaction", mode), ("Status", view.status), ("Next", view.next_action or "none")):
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
        work_plan = getattr(view, "work_plan", {})
        interaction_view = work_plan.get("interaction", {}) if isinstance(work_plan, dict) else {}
        mode = interaction_view.get("mode", "legacy") if isinstance(interaction_view, dict) else "legacy"
        pending = interaction_view.get("decision") if isinstance(interaction_view, dict) else None
        if isinstance(pending, dict):
            key = (pending.get("id"), pending.get("status"))
            if key != self._last_interaction:
                self._last_interaction = key
                rows = [
                    f"Interaction: {mode}",
                    f"Decision: {pending.get('id', 'unknown')} ({pending.get('stage', 'unknown')}; {pending.get('status', 'unknown')})",
                    str(pending.get("question") or "A research decision needs your attention."),
                    str(pending.get("reason") or "No additional rationale recorded."),
                ]
                options = [item for item in pending.get("options", []) if isinstance(item, dict)]
                if options:
                    rows.append("Options: " + "; ".join(
                        f"{item.get('id', 'choice')}: {item.get('label', '')}" for item in options
                    ))
                self.console.print(Panel(Text("\n".join(rows)), title="Research decision", border_style="yellow"))
                topic = str(work_plan.get("task", {}).get("goal", ""))
                if pending.get("status") == "pending":
                    for option in pending.get("options", []):
                        response = option.get("id") if isinstance(option, dict) else None
                        if response not in {"accept", "reject", "revise"}:
                            continue
                        parts = ["simple-ar", "research-session", "--session-root", str(view.session_root),
                                 "--topic", topic, "--decision-id", str(pending.get("id", "")),
                                 "--decision-response", response]
                        if response == "revise":
                            if pending.get("stage") == "delivery":
                                parts.extend(("--report-template", "analysis_report"))
                            else:
                                parts.extend(("--decision-guidance", "REPLACE_WITH_YOUR_GUIDANCE"))
                        command = _shell_join(parts)
                        self.console.print(Text(f"{response}: {command}"))
        elif self._last_interaction is not None:
            self._last_interaction = None
            self.console.print(Text(f"Interaction: {mode}", style="dim"))
        decision = work_plan.get("research_decision", {})
        if decision and decision != self._last_decision:
            self._last_decision = dict(decision)
            rows = [f"Action: {decision.get('action', 'unknown')}"]
            if decision.get("research_iteration") is not None:
                rows.append(f"Research round: {decision['research_iteration']}; remaining authorized rounds: {decision.get('remaining_authorized_rounds', 'unknown')}")
            rows.append(str(decision.get("decision_reason") or "No decision reason recorded."))
            self.console.print(Panel(Text("\n".join(rows)), title="Research decision", border_style="cyan"))
        elif not decision:
            self._last_decision = None
        style = "green" if view.status == "completed" else "yellow" if view.status in {"paused", "blocked"} else "red" if view.status == "failed" else "cyan"
        self.console.print(Text(f"Status: {view.status}; next: {view.next_action or 'none'}", style=style))
        if view.status_reason:
            self.console.print(Text(view.status_reason))

    def finish(self, view):
        self.state(view)
        table = Table("Artifact", "Location", header_style="bold cyan")
        for name, ref_name in _artifact_rows(view):
            table.add_row(name, Text(str(view.session_root / view.state_refs[ref_name].path)))
        self.console.print(table)
        self.console.print(Text(f"Artifacts: {len(view.state_refs)}; attempts: {len(view.attempts)}"))
        self.console.print("Completion describes delivered artifacts, not scientific success or paper quality.", style="dim")


def _artifact_rows(view):
    refs = view.state_refs
    rows = [("summary", "summary")] if "summary" in refs else []
    work_plan = getattr(view, "work_plan", {})
    accepted = work_plan.get("accepted_plan") if isinstance(work_plan, dict) else None
    steps = accepted.get("steps", []) if isinstance(accepted, dict) else []
    by_role = {"baseline": [], "implementation": [], "experiment": [], "analysis": []}
    role_for_capability = {"implement": "implementation", "experiment": "experiment", "analysis": "analysis"}
    for step in steps:
        if not isinstance(step, dict):
            continue
        role = role_for_capability.get(step.get("capability"))
        state_name = step.get("state_name")
        action = str(step.get("action") or "").lower()
        if role == "experiment" and ("baseline" in action or state_name == "baseline"):
            role = "baseline"
        if role is None or not isinstance(state_name, str) or state_name not in refs:
            continue
        if state_name not in by_role[role]:
            by_role[role].append(state_name)

    if not by_role["baseline"] and "baseline" in refs:
        by_role["baseline"].append("baseline")
    if "matrix_results" in refs:
        rows.append(("matrix_results", "matrix_results"))
    for role, names in by_role.items():
        if not names and role in refs:
            names = [role]
        for index, state_name in enumerate(names):
            if index == len(names) - 1 and (state_name != role or len(names) > 1):
                label = f"{role} (latest: {state_name})"
            elif state_name == role and len(names) > 1:
                label = f"{role} (initial)"
            else:
                label = role if len(names) == 1 else f"{role} ({state_name})"
            rows.append((label, state_name))

    rows.extend(
        (("decision (latest)" if name == "decision" else name), name)
        for name in ("decision", "report", "report_audit") if name in refs
    )
    return rows


def _shell_join(parts):
    if os.name == "nt":
        return "& " + " ".join("'" + str(part).replace("'", "''") + "'" for part in parts)
    return shlex.join(str(part) for part in parts)

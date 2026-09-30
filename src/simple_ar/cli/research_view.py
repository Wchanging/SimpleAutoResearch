"""Rich presentation of the canonical research loop; no execution state of its own."""

from contextlib import contextmanager
import os
import shlex
from time import monotonic

from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Column, Table
from rich.text import Text

from simple_ar.core.capabilities import ArtifactRef, ArtifactStore
from simple_ar.core.console import make_console
from simple_ar.core.reporting import style_progress_message
from simple_ar.core.process_output import ProcessMessage


DESCRIPTIONS = {
    "plan": "Plan the next steps from the task, assets and constraints",
    "search": "Search literature providers and select sources",
    "document_ingest": "Fetch and extract available documents",
    "data_ingest": "Freeze supplied data and explicit column settings",
    "data_analysis": "Compute descriptive statistics and editable figures (no API calls)",
    "read": "Read documents and collect traceable evidence",
    "synthesize": "Synthesize source evidence; propose candidates only when requested",
    "summarize": "Save the research summary",
    "assess_ideas": "Assess candidate evidence and feasibility",
    "research_design": "Design the selected experiment",
    "refine_implementation": "Clarify unresolved implementation decisions within the accepted design",
    "prepare_implementation": "Prepare a fresh workspace without repeating valid measurements",
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
        self._progress = None
        self._process_tasks = {}

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
        if isinstance(message, ProcessMessage):
            progress = self._progress
            if message.transient:
                if progress is not None and self.console.is_terminal:
                    task = self._process_tasks.get(message.stream)
                    description = Text.from_ansi(str(message))
                    description.no_wrap = True
                    description.overflow = "ellipsis"
                    if task is None:
                        self._process_tasks[message.stream] = progress.add_task(description, total=None)
                    else:
                        progress.update(task, description=description)
                return
            task = self._process_tasks.pop(message.stream, None)
            if task is not None and progress is not None:
                progress.remove_task(task)
        self.console.print(Text("  - " + message, style=style_progress_message(message)))

    @contextmanager
    def action(self, action: str):
        description = DESCRIPTIONS.get(action.split(":", 1)[0], action.replace("_", " "))
        self.console.rule(Text(action, style="bold cyan"))
        self.console.print(Text(description))
        started = monotonic()
        # Redirected logs retain plain milestones without animation or ANSI frames.
        progress = Progress(SpinnerColumn(), TextColumn("{task.description}", markup=False,
                            table_column=Column(no_wrap=True, overflow="ellipsis")), TimeElapsedColumn(),
                            console=self.console, transient=True, refresh_per_second=2,
                            disable=not self.console.is_terminal)
        try:
            with progress:
                self._progress = progress
                progress.add_task("Running — waiting for action result", total=None)
                yield
        except BaseException:
            self.console.print(Text(f"Interrupted/failed after {monotonic() - started:.1f}s", style="bold red"))
            raise
        else:
            self.console.print(Text(f"Action returned in {monotonic() - started:.1f}s", style="dim"))
        finally:
            self._progress = None
            self._process_tasks.clear()

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
        if "data_analysis" in view.state_refs:
            directory = (view.session_root / view.state_refs["data_analysis"].path).parent
            table.add_row("analysis report", Text(str(directory / "analysis.md")))
            table.add_row("editable figures", Text(str(directory / "figures")))
        if table.row_count:
            self.console.print(table)
        else:
            self.console.print(Text(f"No deliverable yet. Attempt diagnostics: {view.session_root / 'attempts'}", style="dim"))
        self.console.print(Text(f"Artifacts: {len(view.state_refs)}; attempts: {len(view.attempts)}"))
        audit_line = report_audit_line(view)
        if audit_line is not None:
            self.console.print(Text(audit_line))
        method_line = method_validation_line(view)
        if method_line is not None:
            self.console.print(Text(method_line))
        self.console.print("Completion describes delivered artifacts, not scientific success or paper quality.", style="dim")


def report_audit_line(view) -> str | None:
    """Show the persisted quality gate separately from session completion."""
    audit_ref = view.state_refs.get("report_audit")
    if audit_ref is None:
        return None
    try:
        audit = ArtifactStore(view.session_root).read_json(audit_ref)
    except (OSError, ValueError):
        return "Report audit: unavailable (inspect the referenced audit artifact)."
    if not isinstance(audit, dict) or audit.get("status") not in {"passed", "warning", "failed"}:
        return "Report audit: unavailable (invalid audit artifact)."
    status = audit["status"]
    if audit.get("semantic_review_status") == "semantic_unchecked":
        return f"Report audit: {status} (semantic support is not certified)."
    return f"Report audit: {status}."


def method_validation_line(view) -> str | None:
    """Expose the measured candidate's method-evidence boundary at delivery."""
    work_plan = getattr(view, "work_plan", {})
    task = work_plan.get("task", {}) if isinstance(work_plan, dict) else {}
    if isinstance(task, dict) and task.get("kind") in {"measurement", "reproduction"}:
        return None  # A fixed protocol does not promise a new candidate implementation.
    refs = view.state_refs
    latest_experiment = next(
        (name for label, name in reversed(_artifact_rows(view)) if label.startswith("experiment")),
        None,
    )
    matrix_ref = _measured_matrix_ref(view)
    experiment_ref = matrix_ref or refs.get(latest_experiment)
    if experiment_ref is None:
        return None
    store = ArtifactStore(view.session_root)
    try:
        result = store.read_json(experiment_ref)
        if not isinstance(result, dict):
            return "Candidate method evidence: unavailable (invalid experiment artifact)."
        if matrix_ref is not None:
            pairs = result.get("pairs")
            if not isinstance(pairs, list) or not pairs:
                return None
            candidates = [pair.get("candidate") if isinstance(pair, dict) else None for pair in pairs]
            if all(candidate is None for candidate in candidates):
                return None
            measured_refs = []
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    return "Candidate method evidence: unavailable (incomplete candidate lineage)."
                measurement = store.read_json(ArtifactRef.from_dict(candidate))
                measured = measurement.get("implementation_ref") if isinstance(measurement, dict) else None
                if not isinstance(measured, dict):
                    return "Candidate method evidence: unavailable (incomplete candidate lineage)."
                measured_refs.append(measured)
            if any(ref != measured_refs[0] for ref in measured_refs[1:]):
                return "Candidate method evidence: unavailable (mixed candidate implementations)."
            if result.get("implementation_ref") != measured_refs[0]:
                return "Candidate method evidence: unavailable (collection lineage mismatch)."
            implementation_ref = measured_refs[0]
        else:
            implementation_ref = result.get("implementation_ref")
            if not isinstance(implementation_ref, dict):
                return "Candidate method evidence: unavailable (incomplete candidate lineage)."
        implementation = store.read_json(ArtifactRef.from_dict(implementation_ref))
    except (OSError, ValueError, TypeError, KeyError):
        return "Candidate method evidence: unavailable (inspect the experiment artifacts)."
    check = implementation.get("method_validation") if isinstance(implementation, dict) else None
    if not isinstance(check, dict):
        return "Candidate method evidence: not independently checked."
    status = str(check.get("status") or "未检查")
    if status == "未检查":
        return "Candidate method evidence: not independently checked."
    return f"Candidate method evidence: {status}."


def _artifact_rows(view):
    refs = view.state_refs
    rows = [("summary", "summary")] if "summary" in refs else []
    if "data_analysis" in refs:
        rows.append(("data_analysis", "data_analysis"))
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
    if _measured_matrix_ref(view) is not None:
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


def _measured_matrix_ref(view) -> ArtifactRef | None:
    """Hide the session's preallocated matrix until a candidate was measured."""
    ref = view.state_refs.get("matrix_results")
    if ref is None:
        return None
    try:
        payload = ArtifactStore(view.session_root).read_json(ref)
    except (OSError, ValueError, TypeError, KeyError):
        return None
    pairs = payload.get("pairs") if isinstance(payload, dict) else None
    if not isinstance(pairs, list) or not any(
        isinstance(row, dict) and isinstance(row.get("candidate"), dict)
        for row in pairs
    ):
        return None
    return ref


def _shell_join(parts):
    if os.name == "nt":
        return "& " + " ".join("'" + str(part).replace("'", "''") + "'" for part in parts)
    return shlex.join(str(part) for part in parts)

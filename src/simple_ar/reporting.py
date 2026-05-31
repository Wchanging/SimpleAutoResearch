from __future__ import annotations

from simple_ar.console import print_line
from simple_ar.pipeline import PipelineEvent


class ConsoleReporter:
    """Render pipeline progress events to stdout.

    Args:
        enabled: Disable output when callers want quiet command-line runs.
    """

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    def __call__(self, event: PipelineEvent) -> None:
        """Print one pipeline event in a compact, human-readable format."""
        if not self.enabled:
            return

        if event.name == "pipeline_start":
            print_line(f"Run directory: {event.data.get('run_dir', '')}")
            print_line(f"Topic: {event.data.get('topic', '')}")
            print_line(
                "Stages: "
                f"{event.data.get('from_stage', '')} -> {event.data.get('to_stage', '')}",
            )
            return

        if event.name == "stage_start":
            print_line(f"{self._stage_prefix(event)} running")
            return

        if event.name == "stage_message":
            print_line(f"  - {event.message}")
            return

        if event.name == "llm_usage":
            print_line(f"  - {event.message}")
            return

        if event.name == "stage_done":
            outputs = event.data.get("outputs", [])
            output_text = ", ".join(str(item) for item in outputs) if isinstance(outputs, list) else ""
            suffix = f" -> {output_text}" if output_text else ""
            print_line(
                f"{self._stage_prefix(event)} done in {event.data.get('duration_sec', 0)}s{suffix}",
            )
            return

        if event.name == "stage_failed":
            print_line(
                f"{self._stage_prefix(event)} failed: {event.data.get('error', '')}",
            )
            return

        if event.name == "pipeline_done":
            print_line(
                f"Pipeline completed: {event.data.get('completed_stages', 0)} stage(s).",
            )

    @staticmethod
    def _stage_prefix(event: PipelineEvent) -> str:
        stage = event.stage
        stage_name = stage.name.lower() if stage is not None else "pipeline"
        stage_number = int(stage) if stage is not None else 0
        stage_index = ConsoleReporter._format_count(event.data.get("stage_index", stage_number))
        total_stages = ConsoleReporter._format_count(event.data.get("total_stages", "?"))
        return f"[{stage_index}/{total_stages} | {stage_number:02d}-{stage_name}]"

    @staticmethod
    def _format_count(value: object) -> str:
        try:
            return f"{int(value):02d}"
        except (TypeError, ValueError):
            return str(value)

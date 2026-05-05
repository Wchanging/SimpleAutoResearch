from __future__ import annotations

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
            print(f"Run directory: {event.data.get('run_dir', '')}", flush=True)
            print(f"Topic: {event.data.get('topic', '')}", flush=True)
            print(
                "Stages: "
                f"{event.data.get('from_stage', '')} -> {event.data.get('to_stage', '')}",
                flush=True,
            )
            return

        if event.name == "stage_start":
            print(f"{self._stage_prefix(event)} running", flush=True)
            return

        if event.name == "stage_message":
            print(f"  - {event.message}", flush=True)
            return

        if event.name == "stage_done":
            outputs = event.data.get("outputs", [])
            output_text = ", ".join(str(item) for item in outputs) if isinstance(outputs, list) else ""
            suffix = f" -> {output_text}" if output_text else ""
            print(
                f"{self._stage_prefix(event)} done in {event.data.get('duration_sec', 0)}s{suffix}",
                flush=True,
            )
            return

        if event.name == "stage_failed":
            print(
                f"{self._stage_prefix(event)} failed: {event.data.get('error', '')}",
                flush=True,
            )
            return

        if event.name == "pipeline_done":
            print(
                f"Pipeline completed: {event.data.get('completed_stages', 0)} stage(s).",
                flush=True,
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

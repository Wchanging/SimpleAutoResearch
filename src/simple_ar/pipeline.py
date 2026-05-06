from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from simple_ar.artifacts import read_json, write_json, write_text
from simple_ar.contracts import CONTRACTS, StageContract
from simple_ar.stages import STAGE_SEQUENCE, Stage, parse_stage, stage_dir_name, stage_range


class PipelineError(RuntimeError):
    pass


class MissingInputError(PipelineError):
    pass


class MissingOutputError(PipelineError):
    pass


@dataclass(frozen=True)
class PipelineEvent:
    """Progress event emitted by the pipeline runner and stage handlers.

    Args:
        name: Stable event name used by reporters, such as ``stage_start``.
        message: Human-readable summary of the event.
        stage: Pipeline stage related to the event, if any.
        data: Additional structured fields for logs or console rendering.
    """

    name: str
    message: str
    stage: Stage | None = None
    data: dict[str, object] = field(default_factory=dict)


ProgressReporter = Callable[[PipelineEvent], None]
StageHandler = Callable[["Context"], None]


def utcnow_iso() -> str:
    """Return the current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Context:
    """Runtime context for one research run.

    Args:
        run_dir: Directory where stage artifacts and metadata are written.
        topic: User-provided research topic.
        config: JSON-serializable runtime options captured with the run.
        current_stage: Stage currently being executed.
        reporter: Optional callback used to surface progress events.
    """

    run_dir: Path
    topic: str
    config: dict[str, object] = field(default_factory=dict)
    current_stage: Stage = Stage.PLAN
    reporter: ProgressReporter | None = field(default=None, repr=False, compare=False)

    def stage_dir(self, stage: Stage | None = None) -> Path:
        """Get the directory path for a specific stage (or current stage if not provided)."""
        return self.run_dir / stage_dir_name(stage or self.current_stage)

    def artifact_path(self, filename: str, stage: Stage | None = None) -> Path:
        """Get the absolute path to a specific artifact within a stage's directory."""
        return self.stage_dir(stage) / filename

    def find_artifact(self, filename: str) -> Path | None:
        """Search backwards through all completed stages to find a specific artifact."""
        for stage in reversed(STAGE_SEQUENCE):
            candidate = self.artifact_path(filename, stage)
            if candidate.exists():
                return candidate
        return None

    def emit(
        self,
        name: str,
        message: str,
        *,
        stage: Stage | None = None,
        **data: object,
    ) -> None:
        """Send a progress event to the configured reporter.

        Args:
            name: Stable event name for programmatic handling.
            message: Human-readable event summary.
            stage: Stage associated with the event. Defaults to ``current_stage``.
            **data: Additional structured event fields.
        """
        if self.reporter is None:
            return
        self.reporter(
            PipelineEvent(
                name=name,
                message=message,
                stage=stage or self.current_stage,
                data=data,
            )
        )


@dataclass(frozen=True)
class StageExecution:
    stage: Stage
    status: str
    started_at: str
    ended_at: str
    duration_sec: float
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    error: str | None = None


class PipelineRunner:
    """Manages the full lifecycle of pipeline execution, including iteration, validation, and error handling."""

    def __init__(
        self,
        handlers: dict[Stage, StageHandler],
        *,
        reporter: ProgressReporter | None = None,
    ) -> None:
        self.handlers = handlers
        self.reporter = reporter

    def run(
        self,
        ctx: Context,
        *,
        from_stage: Stage | str | int = Stage.PLAN,
        to_stage: Stage | str | int = Stage.REPORT,
    ) -> list[StageExecution]:
        """Execute pipeline stages sequentially, applying I/O contracts and saving progress."""
        if self.reporter is not None:
            ctx.reporter = self.reporter
        start = parse_stage(from_stage)
        end = parse_stage(to_stage)
        stages = stage_range(start, end)
        executions: list[StageExecution] = []

        self._ensure_run_scaffold(ctx)
        ctx.emit(
            "pipeline_start",
            "Pipeline run started.",
            stage=start,
            run_dir=str(ctx.run_dir),
            topic=ctx.topic,
            from_stage=start.name.lower(),
            to_stage=end.name.lower(),
            total_stages=len(stages),
        )

        for stage_index, stage in enumerate(stages, start=1):
            ctx.current_stage = stage
            contract = CONTRACTS[stage]
            stage_dir = ctx.stage_dir(stage)
            stage_dir.mkdir(parents=True, exist_ok=True)
            started_at = utcnow_iso()
            t0 = time.monotonic()
            ctx.emit(
                "stage_start",
                f"{stage.name.lower()} stage started.",
                stage=stage,
                stage_index=stage_index,
                total_stages=len(stages),
                inputs=list(contract.inputs),
                outputs=list(contract.outputs),
            )

            try:
                self._check_inputs(ctx, contract)
            except Exception as exc:
                ctx.emit(
                    "stage_failed",
                    f"{stage.name.lower()} stage failed input validation.",
                    stage=stage,
                    stage_index=stage_index,
                    total_stages=len(stages),
                    duration_sec=round(time.monotonic() - t0, 3),
                    error=str(exc),
                )
                raise

            self._write_stage_meta(
                stage_dir,
                StageExecution(
                    stage=stage,
                    status="running",
                    started_at=started_at,
                    ended_at="",
                    duration_sec=0.0,
                    inputs=contract.inputs,
                    outputs=contract.outputs,
                ),
            )
            ctx.emit(
                "stage_inputs_ok",
                f"{stage.name.lower()} inputs are ready.",
                stage=stage,
                stage_index=stage_index,
                total_stages=len(stages),
            )

            try:
                self.handlers[stage](ctx)
                self._check_outputs(ctx, contract)
            except Exception as exc:
                ended_at = utcnow_iso()
                execution = StageExecution(
                    stage=stage,
                    status="failed",
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_sec=round(time.monotonic() - t0, 3),
                    inputs=contract.inputs,
                    outputs=contract.outputs,
                    error=str(exc),
                )
                self._write_stage_meta(stage_dir, execution)
                self._write_pipeline_state(ctx, stage, "failed")
                ctx.emit(
                    "stage_failed",
                    f"{stage.name.lower()} stage failed.",
                    stage=stage,
                    stage_index=stage_index,
                    total_stages=len(stages),
                    duration_sec=execution.duration_sec,
                    error=str(exc),
                )
                raise

            ended_at = utcnow_iso()
            execution = StageExecution(
                stage=stage,
                status="done",
                started_at=started_at,
                ended_at=ended_at,
                duration_sec=round(time.monotonic() - t0, 3),
                inputs=contract.inputs,
                outputs=contract.outputs,
            )
            self._write_stage_meta(stage_dir, execution)
            self._write_pipeline_state(ctx, stage, "done")
            ctx.emit(
                "stage_done",
                f"{stage.name.lower()} stage completed.",
                stage=stage,
                stage_index=stage_index,
                total_stages=len(stages),
                duration_sec=execution.duration_sec,
                outputs=list(contract.outputs),
            )
            executions.append(execution)

        self._write_manifest(ctx)
        ctx.emit(
            "pipeline_done",
            "Pipeline run completed.",
            stage=end,
            run_dir=str(ctx.run_dir),
            completed_stages=len(executions),
        )
        return executions

    def _ensure_run_scaffold(self, ctx: Context) -> None:
        ctx.run_dir.mkdir(parents=True, exist_ok=True)
        topic_path = ctx.run_dir / "topic.txt"
        if not topic_path.exists():
            write_text(topic_path, ctx.topic + "\n")
        config_path = ctx.run_dir / "config_snapshot.json"
        if not config_path.exists():
            write_json(config_path, ctx.config)
        self._write_manifest(ctx)

    def _check_inputs(self, ctx: Context, contract: StageContract) -> None:
        missing: list[str] = []
        for filename in contract.inputs:
            path = ctx.find_artifact(filename)
            if path is None or not self._artifact_nonempty(path):
                missing.append(filename)
        if missing:
            names = ", ".join(missing)
            raise MissingInputError(f"{contract.stage.name} is missing input(s): {names}")

    def _check_outputs(self, ctx: Context, contract: StageContract) -> None:
        missing: list[str] = []
        for filename in contract.outputs:
            path = ctx.artifact_path(filename, contract.stage)
            if not path.exists() or not self._artifact_nonempty(path):
                missing.append(str(path.relative_to(ctx.run_dir)))
        if missing:
            names = ", ".join(missing)
            raise MissingOutputError(f"{contract.stage.name} did not produce: {names}")

    @staticmethod
    def _artifact_nonempty(path: Path) -> bool:
        if path.is_dir():
            return any(path.iterdir())
        return path.is_file() and path.stat().st_size > 0

    def _write_stage_meta(self, stage_dir: Path, execution: StageExecution) -> None:
        write_json(
            stage_dir / "stage_meta.json",
            {
                "stage": execution.stage.name.lower(),
                "stage_number": int(execution.stage),
                "status": execution.status,
                "started_at": execution.started_at,
                "ended_at": execution.ended_at,
                "duration_sec": execution.duration_sec,
                "inputs": list(execution.inputs),
                "outputs": list(execution.outputs),
                "error": execution.error,
            },
        )

    def _write_pipeline_state(self, ctx: Context, stage: Stage, status: str) -> None:
        next_stage = self._next_stage(stage) if status == "done" else stage
        write_json(
            ctx.run_dir / "pipeline_state.json",
            {
                "status": status,
                "last_stage": stage.name.lower(),
                "last_stage_number": int(stage),
                "next_stage": next_stage.name.lower() if next_stage else None,
                "next_stage_number": int(next_stage) if next_stage else None,
                "updated_at": utcnow_iso(),
            },
        )

    def _write_manifest(self, ctx: Context) -> None:
        stages: list[dict[str, object]] = []
        for stage in STAGE_SEQUENCE:
            meta_path = ctx.artifact_path("stage_meta.json", stage)
            meta = read_json(meta_path) if meta_path.exists() else {}
            status = str(meta.get("status", "pending"))
            contract = CONTRACTS[stage]
            stages.append(
                {
                    "stage": stage.name.lower(),
                    "stage_number": int(stage),
                    "dir": stage_dir_name(stage),
                    "description": contract.description,
                    "status": status,
                    "completed": status == "done",
                    "outputs": list(contract.outputs),
                }
            )
        write_json(
            ctx.run_dir / "manifest.json",
            {
                "schema_version": 1,
                "topic": ctx.topic,
                "run_dir": str(ctx.run_dir),
                "generated_at": utcnow_iso(),
                "stages": stages,
            },
        )

    @staticmethod
    def _next_stage(stage: Stage) -> Stage | None:
        idx = STAGE_SEQUENCE.index(stage)
        if idx + 1 >= len(STAGE_SEQUENCE):
            return None
        return STAGE_SEQUENCE[idx + 1]

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from simple_ar.artifacts import write_json, write_text
from simple_ar.contracts import CONTRACTS, StageContract
from simple_ar.stages import STAGE_SEQUENCE, Stage, parse_stage, stage_dir_name, stage_range


class PipelineError(RuntimeError):
    pass


class MissingInputError(PipelineError):
    pass


class MissingOutputError(PipelineError):
    pass


StageHandler = Callable[["Context"], None]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Context:
    run_dir: Path
    topic: str
    config: dict[str, object] = field(default_factory=dict)
    current_stage: Stage = Stage.PLAN

    def stage_dir(self, stage: Stage | None = None) -> Path:
        return self.run_dir / stage_dir_name(stage or self.current_stage)

    def artifact_path(self, filename: str, stage: Stage | None = None) -> Path:
        return self.stage_dir(stage) / filename

    def find_artifact(self, filename: str) -> Path | None:
        for stage in reversed(STAGE_SEQUENCE):
            candidate = self.artifact_path(filename, stage)
            if candidate.exists():
                return candidate
        return None


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
    def __init__(self, handlers: dict[Stage, StageHandler]) -> None:
        self.handlers = handlers

    def run(
        self,
        ctx: Context,
        *,
        from_stage: Stage | str | int = Stage.PLAN,
        to_stage: Stage | str | int = Stage.REPORT,
    ) -> list[StageExecution]:
        start = parse_stage(from_stage)
        end = parse_stage(to_stage)
        executions: list[StageExecution] = []

        self._ensure_run_scaffold(ctx)
        for stage in stage_range(start, end):
            ctx.current_stage = stage
            contract = CONTRACTS[stage]
            self._check_inputs(ctx, contract)

            stage_dir = ctx.stage_dir(stage)
            stage_dir.mkdir(parents=True, exist_ok=True)
            started_at = utcnow_iso()
            t0 = time.monotonic()
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
            executions.append(execution)

        self._write_manifest(ctx)
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
            stages.append(
                {
                    "stage": stage.name.lower(),
                    "stage_number": int(stage),
                    "dir": stage_dir_name(stage),
                    "completed": meta_path.exists(),
                }
            )
        write_json(
            ctx.run_dir / "manifest.json",
            {
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

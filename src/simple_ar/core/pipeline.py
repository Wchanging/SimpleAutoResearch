from __future__ import annotations

import time
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from simple_ar.app.state import WorkspaceState
from simple_ar.app.state import SearchState
from simple_ar.core.artifacts import (
    load_workspace_state,
    read_json,
    save_workspace_state,
    write_json,
    write_stage_contract,
    write_stage_report,
    write_text,
)
from simple_ar.core.capabilities import ArtifactStore
from simple_ar.core.contracts import CONTRACTS, StageContract
from simple_ar.core.stage_results import collect_stage_result
from simple_ar.core.stages import STAGE_SEQUENCE, Stage, parse_stage, stage_dir_name, stage_range


class PipelineError(RuntimeError):
    pass


class MissingInputError(PipelineError):
    pass


class MissingOutputError(PipelineError):
    pass


@dataclass(frozen=True)
class PipelineEvent:
    """Progress event emitted by the pipeline runner and stage handlers."""

    name: str
    message: str
    stage: Stage | None = None
    data: dict[str, object] = field(default_factory=dict)


ProgressReporter = Callable[[PipelineEvent], None]
StageHandler = Callable[["Context"], None]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Context:
    """Runtime context for one research run."""

    run_dir: Path
    topic: str
    config: dict[str, object] = field(default_factory=dict)
    current_stage: Stage = Stage.PLAN
    reporter: ProgressReporter | None = field(default=None, repr=False, compare=False)
    state: WorkspaceState | None = field(default=None, repr=False, compare=False)

    def stage_dir(self, stage: Stage | None = None) -> Path:
        return self.run_dir / stage_dir_name(stage or self.current_stage)

    def artifact_path(self, filename: str, stage: Stage | None = None) -> Path:
        return self.stage_dir(stage) / filename

    @property
    def artifact_store(self) -> ArtifactStore:
        """Return the lightweight run-relative store for new capabilities."""
        return ArtifactStore(self.run_dir)

    def resolve_artifact(self, relative_path: str | None) -> Path | None:
        if not relative_path:
            return None
        path = Path(relative_path)
        return path if path.is_absolute() else self.run_dir / path

    def find_artifact(self, filename: str) -> Path | None:
        if self.state is not None:
            known = self.state.resolve_artifact(filename)
            if known:
                path = self.resolve_artifact(known)
                if path is not None and path.exists():
                    return path
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
        if self.reporter is None:
            return
        try:
            self.reporter(
                PipelineEvent(
                    name=name,
                    message=message,
                    stage=stage or self.current_stage,
                    data=data,
                )
            )
        except (OSError, UnicodeError):
            return


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
    """Manage pipeline execution, checkpoints, and state-backed stage validation."""

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
                inputs=[item.name.lower() for item in contract.requires],
                outputs=list(contract.outputs),
                description=contract.description,
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

            if ctx.state is not None:
                ctx.state.mark_stage_running(stage)
                save_workspace_state(ctx.run_dir, ctx.state)
            self._write_stage_meta(
                stage_dir,
                StageExecution(
                    stage=stage,
                    status="running",
                    started_at=started_at,
                    ended_at="",
                    duration_sec=0.0,
                    inputs=tuple(item.name.lower() for item in contract.requires),
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
                self._finalize_stage(ctx, stage)
                self._check_outputs(ctx, contract)
            except Exception as exc:
                ended_at = utcnow_iso()
                if ctx.state is not None:
                    ctx.state.mark_stage_failed(stage, str(exc))
                    save_workspace_state(ctx.run_dir, ctx.state)
                execution = StageExecution(
                    stage=stage,
                    status="failed",
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_sec=round(time.monotonic() - t0, 3),
                    inputs=tuple(item.name.lower() for item in contract.requires),
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
                inputs=tuple(item.name.lower() for item in contract.requires),
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
        state = load_workspace_state(ctx.run_dir)
        if state is None:
            state = WorkspaceState.create(run_dir=ctx.run_dir, topic=ctx.topic, config=ctx.config)
        else:
            if ctx.config:
                state.config.values.update(ctx.config)
            ctx.topic = state.topic
        ctx.state = state
        save_workspace_state(ctx.run_dir, state)
        self._write_manifest(ctx)

    def _check_inputs(self, ctx: Context, contract: StageContract) -> None:
        if ctx.state is None:
            raise MissingInputError("Workspace state is not initialized.")
        missing = [stage.name.lower() for stage in contract.requires if not ctx.state.has_completed(stage)]
        if missing:
            names = ", ".join(missing)
            raise MissingInputError(f"{contract.stage.name} requires completed stage(s): {names}")

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

    def _finalize_stage(self, ctx: Context, stage: Stage) -> None:
        if ctx.state is None:
            raise MissingOutputError("Workspace state is not initialized.")
        running_state = ctx.state.stage_state(stage)
        result = collect_stage_result(ctx, stage)
        stage_dir = ctx.stage_dir(stage)
        if stage == Stage.SEARCH and not self._keep_verbose_stage_artifacts(ctx):
            self._compact_search_artifacts(ctx, result.state)
        contract_path = write_stage_contract(stage_dir, result.contract)
        report_path = write_stage_report(stage_dir, result.report_markdown)
        result.state.started_at = running_state.started_at
        result.state.contract_path = str(contract_path.relative_to(ctx.run_dir)).replace("\\", "/")
        result.state.report_path = str(report_path.relative_to(ctx.run_dir)).replace("\\", "/")
        ctx.state.mark_stage_completed(stage, result.state)
        save_workspace_state(ctx.run_dir, ctx.state)

    @staticmethod
    def _keep_verbose_stage_artifacts(ctx: Context) -> bool:
        value = ctx.config.get("debug_artifacts", False)
        return value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _compact_search_artifacts(self, ctx: Context, state: object) -> None:
        if not isinstance(state, SearchState):
            return
        stage_dir = ctx.stage_dir(Stage.SEARCH)
        for dirname in ("planning", "traces", "review"):
            path = stage_dir / dirname
            if path.exists():
                shutil.rmtree(path)
        for relpath in ("documents/sections.jsonl",):
            path = stage_dir / relpath
            if path.exists():
                path.unlink()
        search_meta_path = stage_dir / "search_meta.json"
        if search_meta_path.exists():
            search_meta = read_json(search_meta_path)
            for key in (
                "research_plan",
                "retrieval_rounds",
                "retrieval_selection",
                "coverage_report",
                "sections",
            ):
                search_meta.pop(key, None)
            search_meta["compact_artifacts"] = True
            search_meta["debug_artifacts_retained"] = False
            write_json(search_meta_path, search_meta)
        state.research_plan_path = None
        state.coverage_path = None
        state.coverage_json_path = None
        state.legacy_outputs = {
            key: value
            for key, value in state.legacy_outputs.items()
            if not key.startswith(("planning/", "traces/", "review/"))
            and key != "documents/sections.jsonl"
        }

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
            state_stage = ctx.state.stage_state(stage) if ctx.state is not None else None
            stages.append(
                {
                    "stage": stage.name.lower(),
                    "stage_number": int(stage),
                    "dir": stage_dir_name(stage),
                    "description": contract.description,
                    "status": status,
                    "completed": status == "done",
                    "requires": [item.name.lower() for item in contract.requires],
                    "outputs": list(contract.outputs),
                    "contract_path": state_stage.contract_path if state_stage is not None else None,
                    "report_path": state_stage.report_path if state_stage is not None else None,
                }
            )
        write_json(
            ctx.run_dir / "manifest.json",
            {
                "schema_version": 2,
                "topic": ctx.topic,
                "run_dir": str(ctx.run_dir),
                "generated_at": utcnow_iso(),
                "state_path": "state.json",
                "stages": stages,
            },
        )

    @staticmethod
    def _next_stage(stage: Stage) -> Stage | None:
        idx = STAGE_SEQUENCE.index(stage)
        if idx + 1 >= len(STAGE_SEQUENCE):
            return None
        return STAGE_SEQUENCE[idx + 1]

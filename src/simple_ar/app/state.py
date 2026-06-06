from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from simple_ar.app.config import AppConfigSnapshot
from simple_ar.core.stages import STAGE_SLUGS, Stage


StageStatus = Literal["pending", "running", "failed", "completed"]
RunStatus = Literal["created", "running", "failed", "completed"]


def utcnow_iso() -> str:
    """Return the current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StageRuntime(BaseModel):
    """Shared metadata for one pipeline stage."""

    model_config = ConfigDict(extra="allow")

    status: StageStatus = "pending"
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    contract_path: str | None = None
    report_path: str | None = None
    legacy_outputs: dict[str, str] = Field(default_factory=dict)


class PlanState(StageRuntime):
    goal_markdown: str = ""
    problem_markdown: str = ""


class SearchState(StageRuntime):
    query: str = ""
    queries: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    planner: str = ""
    research_questions: list[str] = Field(default_factory=list)
    research_plan_path: str | None = None
    search_meta_path: str | None = None
    papers_path: str | None = None
    documents_path: str | None = None
    coverage_path: str | None = None
    coverage_json_path: str | None = None
    selected_paper_ids: list[str] = Field(default_factory=list)
    selected_document_ids: list[str] = Field(default_factory=list)
    document_count: int = 0
    chunk_count: int = 0
    paper_card_count: int = 0
    claim_card_count: int = 0
    store_paths: dict[str, str] = Field(default_factory=dict)


class ReadState(StageRuntime):
    notes_path: str | None = None
    paper_notes_path: str | None = None
    screening_decisions_path: str | None = None
    shortlist_path: str | None = None
    reading_table_path: str | None = None
    shortlist_count: int = 0
    paper_note_count: int = 0
    debug_card_paths: dict[str, str] = Field(default_factory=dict)


class SynthesisState(StageRuntime):
    synthesis_markdown: str = ""
    hypothesis_markdown: str = ""
    synthesis_path: str | None = None
    hypothesis_path: str | None = None
    synthesis_brief_path: str | None = None
    evidence_pack_path: str | None = None
    gap_summary_path: str | None = None
    idea_candidates_path: str | None = None
    novelty_checks_path: str | None = None
    idea_candidate_count: int = 0


class DesignState(StageRuntime):
    experiment_plan_path: str | None = None
    experiment_contract_path: str | None = None
    experiment_name: str = ""
    experiment_template: str = ""
    experiment_mode: str = ""


class CodeState(StageRuntime):
    experiment_path: str | None = None
    code_task_meta_path: str | None = None
    changed_files: list[str] = Field(default_factory=list)


class RunStageState(StageRuntime):
    results_path: str | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class ReportState(StageRuntime):
    report_path: str | None = None
    references_path: str | None = None
    quality_path: str | None = None
    memory_path: str | None = None
    audit_path: str | None = None
    manifest_path: str | None = None
    report_mode: str = ""
    template_name: str = ""
    audit_status: str = ""
    cited_paper_ids: list[str] = Field(default_factory=list)


class WorkspaceState(BaseModel):
    """Checkpointable state for one pipeline run."""

    model_config = ConfigDict(extra="allow")

    schema_version: str = "workspace_state.v1"
    run_id: str
    topic: str
    current_stage: str = "plan"
    status: RunStatus = "created"
    config: AppConfigSnapshot = Field(default_factory=AppConfigSnapshot)
    artifact_aliases: dict[str, str] = Field(default_factory=dict)
    plan: PlanState = Field(default_factory=PlanState)
    search: SearchState = Field(default_factory=SearchState)
    read: ReadState = Field(default_factory=ReadState)
    synthesize: SynthesisState = Field(default_factory=SynthesisState)
    design: DesignState = Field(default_factory=DesignState)
    code: CodeState = Field(default_factory=CodeState)
    run: RunStageState = Field(default_factory=RunStageState)
    report: ReportState = Field(default_factory=ReportState)
    updated_at: str = Field(default_factory=utcnow_iso)

    @classmethod
    def create(
        cls,
        *,
        run_dir: Path,
        topic: str,
        config: dict[str, object],
    ) -> "WorkspaceState":
        return cls(
            run_id=run_dir.name,
            topic=topic,
            config=AppConfigSnapshot.from_runtime_config(config),
        )

    @classmethod
    def load(cls, path: Path) -> "WorkspaceState":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = utcnow_iso()
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    def stage_state(self, stage: Stage) -> StageRuntime:
        slug = STAGE_SLUGS[stage]
        return getattr(self, slug)

    def has_completed(self, stage: Stage) -> bool:
        return self.stage_state(stage).status == "completed"

    def mark_stage_running(self, stage: Stage) -> None:
        state = self.stage_state(stage)
        state.status = "running"
        state.started_at = utcnow_iso()
        state.completed_at = None
        state.error = None
        self.current_stage = STAGE_SLUGS[stage]
        self.status = "running"

    def mark_stage_completed(
        self,
        stage: Stage,
        stage_state: StageRuntime,
    ) -> None:
        stage_state.status = "completed"
        if not stage_state.started_at:
            stage_state.started_at = utcnow_iso()
        stage_state.completed_at = utcnow_iso()
        setattr(self, STAGE_SLUGS[stage], stage_state)
        self.current_stage = STAGE_SLUGS[stage]
        if stage == Stage.REPORT:
            self.status = "completed"
        else:
            self.status = "running"
        for name, relative_path in stage_state.legacy_outputs.items():
            self.artifact_aliases[name] = relative_path

    def mark_stage_failed(self, stage: Stage, error: str) -> None:
        state = self.stage_state(stage)
        state.status = "failed"
        state.completed_at = utcnow_iso()
        state.error = error
        self.current_stage = STAGE_SLUGS[stage]
        self.status = "failed"

    def resolve_artifact(self, filename: str) -> str | None:
        return self.artifact_aliases.get(filename)

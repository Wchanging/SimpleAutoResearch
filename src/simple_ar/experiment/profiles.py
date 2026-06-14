"""Domain profiles for experiment and coding tasks."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class DomainProfile:
    profile_id: str
    display_name: str
    task_kinds: list[str] = field(default_factory=list)
    expected_entrypoints: list[str] = field(default_factory=list)
    preferred_metrics: list[str] = field(default_factory=list)
    result_requirements: list[str] = field(default_factory=list)
    resource_notes: list[str] = field(default_factory=list)
    planning_guidance: list[str] = field(default_factory=list)
    schema_version: str = "2.5"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def resolve_domain_profile(name: str, *, task_kind: str = "auto") -> DomainProfile:
    requested = (name or "auto").strip().lower()
    if requested == "auto":
        if task_kind in {"existing_project", "benchmark_solution", "code_task"}:
            requested = "code_experiment"
        else:
            requested = "generic_research_experiment"
    return _PROFILES.get(requested, _PROFILES["generic_research_experiment"])


_PROFILES: dict[str, DomainProfile] = {
    "generic_research_experiment": DomainProfile(
        profile_id="generic_research_experiment",
        display_name="Generic research experiment",
        task_kinds=["auto", "fixed_template", "greenfield"],
        expected_entrypoints=["experiment.py", "main.py", "benchmark.py"],
        preferred_metrics=["accuracy", "macro_f1", "runtime_sec"],
        result_requirements=[
            "Write machine-readable metrics as JSON when possible.",
            "Keep stdout useful for humans but do not rely on stdout as the only result store.",
        ],
        resource_notes=[
            "Prefer CPU-friendly defaults unless GPU use is explicitly allowed.",
            "Keep dependency installation explicit and reviewable.",
        ],
        planning_guidance=[
            "Start with a minimal reproducible baseline before adding complexity.",
            "Separate data preparation, model or method code, and evaluation output.",
        ],
    ),
    "code_experiment": DomainProfile(
        profile_id="code_experiment",
        display_name="Existing codebase experiment",
        task_kinds=["existing_project", "code_task"],
        expected_entrypoints=["benchmark.py", "main.py", "pytest", "uv run"],
        preferred_metrics=["primary_metric", "runtime_sec", "parameter_count"],
        result_requirements=[
            "Preserve the original project boundary and edit only allowed paths.",
            "Run baseline before patched evaluation whenever a benchmark command exists.",
            "Record patch diff, validation result, benchmark stdout/stderr, and metric comparison.",
        ],
        resource_notes=[
            "Use copy or git worktree sandbox modes instead of editing the source project directly.",
            "Avoid dependency installation unless the task configuration allows it.",
        ],
        planning_guidance=[
            "Locate the smallest set of files that explains the target behavior.",
            "Prefer incremental patches and validation-driven repair over broad rewrites.",
            "Treat config files, secrets, and generated artifacts as protected unless explicitly allowed.",
        ],
    ),
    "ml_experiment": DomainProfile(
        profile_id="ml_experiment",
        display_name="Machine learning experiment",
        task_kinds=["greenfield", "existing_project", "benchmark_solution"],
        expected_entrypoints=["train.py", "main.py", "benchmark.py"],
        preferred_metrics=["accuracy", "macro_f1", "loss", "train_time_sec", "inference_time_ms"],
        result_requirements=[
            "Use deterministic seeds where possible.",
            "Report both task quality and resource cost.",
            "Persist model-free metrics; do not require inspecting console logs to judge success.",
        ],
        resource_notes=[
            "Default to small datasets and CPU-friendly runs unless the resource plan allows more.",
            "Surface expected memory, runtime, and optional GPU assumptions before implementation.",
        ],
        planning_guidance=[
            "Define dataset split, metric, baseline, and acceptance threshold before editing code.",
            "Keep training loops observable with concise progress output.",
        ],
    ),
    "code_agent_eval": DomainProfile(
        profile_id="code_agent_eval",
        display_name="Code agent evaluation",
        task_kinds=["greenfield", "existing_project", "benchmark_solution"],
        expected_entrypoints=["benchmark.py", "main.py", "pytest"],
        preferred_metrics=["pass_rate", "repair_success_rate", "runtime_sec", "token_cost"],
        result_requirements=[
            "Separate benchmark tasks, agent outputs, validation logs, and aggregate metrics.",
            "Preserve task prompts and environment assumptions for audit.",
        ],
        resource_notes=[
            "Bound the number of agent attempts and tool calls.",
            "Keep retry and repair loops observable and resumable.",
        ],
        planning_guidance=[
            "Model the task as plan -> implement -> validate -> repair.",
            "Use benchmark-driven acceptance instead of subjective output quality alone.",
        ],
    ),
}

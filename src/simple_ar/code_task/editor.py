from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from simple_ar.code_task.budget import EditBudget


MessageCallback = Callable[[str], None]


@dataclass(frozen=True)
class EditorSafetyPolicy:
    """Safety policy that every editor backend must respect.

    Args:
        protected_patterns: Workspace-relative glob patterns that may be read as
            evidence but must not be modified by automated edits.
        blocked_read_patterns: Workspace-relative or shell-style patterns that
            external tool backends must not read. Controlled in-process backends
            may ignore this field when they already read only bounded context
            packs.
        allow_command_execution: Whether an editor backend may execute shell
            commands while preparing a proposal.
        allow_network: Whether an editor backend may use network access.
        allow_large_edits: Whether the caller explicitly approved a proposal
            that exceeds the normal edit budget.
        allow_unapproved_plan: Whether the backend may bypass the human plan
            approval gate. This is reserved for tests and controlled local
            experiments.
    """

    protected_patterns: tuple[str, ...]
    blocked_read_patterns: tuple[str, ...] = ()
    allow_command_execution: bool = False
    allow_network: bool = False
    allow_large_edits: bool = False
    allow_unapproved_plan: bool = False


@dataclass(frozen=True)
class EditorContext:
    """Run-local context passed to an editor backend.

    The context describes where the backend may work and which artifacts shaped
    the current request. Backends should not infer paths outside this contract.
    Benchmark execution, validation, approval, and reporting remain owned by the
    code-task orchestrator rather than by the backend.
    """

    run_dir: Path
    task_dir: Path
    workspace_dir: Path
    meta_dir: Path
    manifest: dict[str, Any]
    task_text: str = ""
    patch_plan: str = ""
    codebase_index: dict[str, Any] = field(default_factory=dict)
    batch: dict[str, Any] | None = None
    context_pack: dict[str, Any] | None = None


@dataclass(frozen=True)
class EditRequest:
    """Request to generate a reviewable edit proposal."""

    context: EditorContext
    safety: EditorSafetyPolicy
    model: str | None = None
    use_llm: bool = True
    force: bool = False
    max_files: int = 8
    max_source_chars_per_file: int = 4000
    budget: EditBudget | None = None
    budget_profile: str | None = None
    edit_budget_overrides: dict[str, Any] | None = None
    message_callback: MessageCallback | None = None


@dataclass(frozen=True)
class EditResult:
    """Result returned by an editor backend after proposal generation."""

    backend: str
    run_dir: Path
    proposal_path: Path
    mode: str
    edit_count: int
    selected_files: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ApplyEditRequest:
    """Request to apply a reviewed proposal inside the editable workspace."""

    context: EditorContext
    safety: EditorSafetyPolicy
    proposal_path: Path | None = None


@dataclass(frozen=True)
class ApplyEditResult:
    """Result returned by an editor backend after applying reviewed edits."""

    backend: str
    run_dir: Path
    applied_edits_path: Path
    patch_diff_path: Path
    changed_files: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


class EditorBackend(Protocol):
    """Protocol implemented by code-task editor backends."""

    name: str

    def propose(self, request: EditRequest) -> EditResult:
        """Generate a reviewable edit proposal without modifying files."""

    def apply(self, request: ApplyEditRequest) -> ApplyEditResult:
        """Apply a reviewed proposal inside the prepared workspace."""


def editor_metadata(
    *,
    backend: str,
    request: EditRequest | ApplyEditRequest | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return normalized metadata written to editor artifacts."""

    metadata: dict[str, Any] = {
        "schema_version": 1,
        "backend": backend,
    }
    if request is not None:
        metadata["run_dir"] = str(request.context.run_dir)
        metadata["workspace_dir"] = "code_task/workspace"
        if request.context.batch:
            metadata["batch"] = request.context.batch
        if request.context.context_pack:
            metadata["context_pack"] = request.context.context_pack
    if extra:
        metadata.update(extra)
    return metadata

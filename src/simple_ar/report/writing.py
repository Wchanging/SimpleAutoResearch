"""Controller-owned Writer execution using the existing report agent."""

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable

from simple_ar.core.capabilities import ArtifactRef, CapabilityContext, CapabilityResult
from simple_ar.report.agent import run_report_agent
from simple_ar.report.memory import initialize_report_memory
from simple_ar.report.schema import ReportContext, ReportMemory, ReportRuntimeConfig, ReportTemplateBundle
from simple_ar.report.tool_gateway import ReportToolGateway


@dataclass(frozen=True)
class ReportWritingRequest:
    report_context: ReportContext
    memory: ReportMemory
    config: ReportRuntimeConfig
    template: ReportTemplateBundle
    llm_client: Any
    resume_ref: ArtifactRef | None = None
    emit: Callable[[str], None] | None = None


def run_report_writing_capability(*, context: CapabilityContext, request: ReportWritingRequest) -> CapabilityResult:
    if request.llm_client is None:
        raise ValueError("Report writing requires an LLM client; no offline paper is invented.")
    memory = request.memory.model_copy(deep=True)
    if not memory.section_plan:
        memory.section_plan = initialize_report_memory(context=request.report_context, template=request.template).section_plan
    snapshot = {"context": request.report_context.model_dump(mode="json"),
                "memory": memory.model_dump(mode="json"), "config": request.config.model_dump(mode="json"),
                "template": request.template.model_dump(mode="json"),
                "sources": [ref.to_dict() for ref in context.inputs if ref != request.resume_ref]}
    # Locations are provenance, not writing input: identical installed and
    # checkout templates must share a checkpoint identity.
    identity = {**snapshot, "template": request.template.model_dump(
        mode="json", exclude={"template_path", "criteria_path"},
    )}
    snapshot["snapshot_id"] = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    source = context.store.write_json("report_inputs.json", snapshot, kind="report_snapshot", schema="report_snapshot.v1")
    completed = None
    reused_ref = None
    if request.resume_ref is not None:
        previous = context.read_input_json(request.resume_ref)
        if previous["snapshot_id"] == snapshot["snapshot_id"]:
            completed = previous["completed"]
            reused_ref = request.resume_ref
    checkpoint_ref = context.store.ref("sections.json", kind="report_checkpoint", schema="report_checkpoint.v1")

    def save_checkpoint(completed: dict[str, Any]) -> None:
        context.store.write_json(checkpoint_ref.path, {"snapshot_id": snapshot["snapshot_id"], "completed": completed,
            "source_attempt": context.attempt.attempt_id, "resume_ref": reused_ref.to_dict() if reused_ref else None},
            kind=checkpoint_ref.kind, schema=checkpoint_ref.schema)
    result = run_report_agent(client=request.llm_client, context=request.report_context, memory=memory,
                              config=request.config, template=request.template,
                              gateway=ReportToolGateway(request.report_context),
                              emit=request.emit, completed_checkpoint=completed,
                              checkpoint_sink=save_checkpoint)
    artifacts = (source, checkpoint_ref) if context.store.resolve(checkpoint_ref).is_file() else (source,)
    if result is None or not any(section.draft_markdown.strip() for section in result.sections):
        return CapabilityResult(status="failed", artifacts=artifacts, diagnostics=("Writer returned no usable sections.",))
    payload = result.model_dump(mode="json")
    payload.pop("report_body", None)
    payload.update(schema_version="report_agent_result.v1", input_snapshot=source.to_dict(), snapshot_id=snapshot["snapshot_id"])
    writer = context.store.write_json("writer.json", payload, kind="report_writer_result", schema="report_agent_result.v1")
    return CapabilityResult(status="completed", artifacts=(*artifacts, writer))

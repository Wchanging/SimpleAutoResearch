"""Controller-owned Writer execution using the existing report agent."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from simple_ar.core.capabilities import ArtifactRef, CapabilityContext, CapabilityResult
from simple_ar.report.agent import run_report_agent
from simple_ar.report.memory import initialize_report_memory
from simple_ar.report.schema import ReportContext, ReportMemory, ReportRuntimeConfig, ReportTemplateBundle, finding_requires_resolution
from simple_ar.report.tool_gateway import ReportToolGateway
from simple_ar.research.documents.ingest import DocumentBundle


@dataclass(frozen=True)
class ReportWritingRequest:
    report_context: ReportContext
    memory: ReportMemory
    config: ReportRuntimeConfig
    template: ReportTemplateBundle
    llm_client: Any
    resume_ref: ArtifactRef | None = None
    emit: Callable[[str], None] | None = None


def _writing_identity(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Content owns recovery; repeated configuration locations do not.

    Brief/assets/runtime contents are already projected into context, memory
    and config. Opaque source/measurement refs remain part of the identity.
    """
    identity = {key: value for key, value in snapshot.items() if key != 'snapshot_id'}
    identity['template'] = {key: value for key, value in identity['template'].items()
                            if key not in {'template_path', 'criteria_path'}}
    identity['config'] = {key: value for key, value in identity['config'].items()
                          if not (key in {'review_scope', 'draft_scope'} and value == 'section')}
    identity['sources'] = [row for row in identity['sources'] if row['kind'] not in {
        'research_brief', 'research_brief_markdown', 'research_assets', 'runtime_config'}]
    return identity


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
    identity = _writing_identity(snapshot)
    snapshot["snapshot_id"] = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    source = context.store.write_json("report_inputs.json", snapshot, kind="report_snapshot", schema="report_snapshot.v1")
    completed = None
    reused_ref = None
    if request.resume_ref is not None:
        previous = context.read_input_json(request.resume_ref)
        compatible = previous['snapshot_id'] == snapshot['snapshot_id']
        if not compatible and previous.get('source_attempt'):
            # Old checkpoints include redundant revision-path refs in their
            # fingerprint. Compare their full retained inputs, not a guessed
            # match or just the unchanged goal text.
            input_store = context.input_store or context.store
            prior_path = Path(request.resume_ref.path).with_name('report_inputs.json')
            prior_inputs = input_store.read_json(prior_path) if input_store.resolve(prior_path).is_file() else None
            compatible = prior_inputs is not None and _writing_identity(prior_inputs) == identity
        if compatible:
            completed = previous["completed"]
            reused_ref = request.resume_ref
    checkpoint_ref = context.store.ref("sections.json", kind="report_checkpoint", schema="report_checkpoint.v1")

    def save_checkpoint(completed: dict[str, Any]) -> None:
        context.store.write_json(checkpoint_ref.path, {"snapshot_id": snapshot["snapshot_id"], "completed": completed,
            "source_attempt": context.attempt.attempt_id, "resume_ref": reused_ref.to_dict() if reused_ref else None},
            kind=checkpoint_ref.kind, schema=checkpoint_ref.schema)
    document_refs = [ref for ref in context.inputs if ref.schema == "document_bundle.v1"]
    if len(document_refs) > 1:
        raise ValueError("Report backtracking requires one unambiguous document bundle.")
    documents = DocumentBundle.from_handoff_dict(context.read_input_json(document_refs[0])) if document_refs else None
    output_refs = {ref.path: ref for ref in context.inputs if ref.kind == "experiment_output"}

    def read_output(path: str, offset: int, limit: int, query: str = "", record_match: dict | None = None) -> dict:
        from simple_ar.experiment.execution.outputs import read_output_window
        if path not in output_refs:
            raise ValueError("Output is not a registered input of this writing attempt.")
        file = context.require_input(output_refs[path])
        return read_output_window((context.input_store or context.store).root, file, offset=offset, limit=limit,
                                  query=query, record_match=record_match)

    result = run_report_agent(client=request.llm_client, context=request.report_context, memory=memory,
                              config=request.config, template=request.template,
                              gateway=ReportToolGateway(request.report_context, documents=documents, output_reader=read_output),
                              emit=request.emit, completed_checkpoint=completed,
                              checkpoint_sink=save_checkpoint)
    artifacts = (source, checkpoint_ref) if context.store.resolve(checkpoint_ref).is_file() else (source,)
    if result is None or not any(section.draft_markdown.strip() for section in result.sections):
        return CapabilityResult(status="failed", artifacts=artifacts, diagnostics=("Writer returned no usable sections.",))
    payload = result.model_dump(mode="json")
    payload.pop("report_body", None)
    payload.update(schema_version="report_agent_result.v1", input_snapshot=source.to_dict(), snapshot_id=snapshot["snapshot_id"])
    writer = context.store.write_json("writer.json", payload, kind="report_writer_result", schema="report_agent_result.v1")
    pending = sum(finding_requires_resolution(row) for row in result.memory.reviewer_findings)
    diagnostics = (f"Draft delivered with {pending} unresolved writing review finding(s); inspect {writer.path} before relying on it.",) if pending else ()
    if diagnostics and request.emit is not None:
        request.emit(diagnostics[0])
    if any(row.type in {"document_review_unavailable", "document_revision_unavailable"}
           for row in result.memory.reviewer_findings):
        # Preserve usable drafts and candidates, but let the controller pause
        # and resume this same checkpoint rather than finalize a failed check.
        return CapabilityResult(status="failed", artifacts=(*artifacts, writer),
            diagnostics=(*diagnostics, "Writing inspection was unavailable; resume the saved checkpoint after resolving capacity or transport, without redrafting."))
    return CapabilityResult(status="completed", artifacts=(*artifacts, writer), diagnostics=diagnostics)

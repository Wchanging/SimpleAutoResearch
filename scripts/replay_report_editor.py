"""Replay final report editing on saved evidence, never rerun experiments.

Diagnostic acceptance tool, not a replacement user workflow. Writes only a new
output directory; the original report/checkpoint is read-only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from simple_ar.core.capabilities import ArtifactStore, AttemptManifest, CapabilityContext
from simple_ar.integrations.llm import LLMClient
from simple_ar.report.agent import run_report_agent
from simple_ar.report.audit import build_report_audit
from simple_ar.report.capability import ReportAssemblyRequest, run_report_capability
from simple_ar.report.projection import _append_verified_experiment_evidence
from simple_ar.report.schema import ReportContext, ReportMemory, ReportRuntimeConfig, ReportTemplateBundle
from simple_ar.report.tool_gateway import ReportToolGateway
from simple_ar.research.documents.ingest import DocumentBundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attempt", type=Path, help="Writer attempt with report_inputs.json and sections.json")
    parser.add_argument("output", type=Path, help="New directory for diagnostic outputs")
    parser.add_argument("--model", default=None)
    parser.add_argument("--env-file", type=Path, default=None, help="Private global API settings; never copied")
    parser.add_argument("--documents", type=Path, default=None, help="Explicit saved document bundle for original-passage backtracking")
    args = parser.parse_args()
    inputs = json.loads((args.attempt / "report_inputs.json").read_text(encoding="utf-8"))
    completed = json.loads((args.attempt / "sections.json").read_text(encoding="utf-8"))["completed"]
    completed["document_review_done"] = False
    context = ReportContext.model_validate(inputs["context"])
    memory = ReportMemory.model_validate(inputs["memory"])
    config = ReportRuntimeConfig.model_validate(inputs["config"]).model_copy(update={"document_review": True})
    template = ReportTemplateBundle.model_validate(inputs["template"])
    documents = DocumentBundle.from_handoff_dict(json.loads(args.documents.read_text(encoding="utf-8"))) if args.documents else None
    if args.env_file:
        load_dotenv(args.env_file, override=False)
    args.output.mkdir(parents=True, exist_ok=False)
    store = ArtifactStore(args.output)
    store.write_json("replay.json", {"source_attempt": str(args.attempt.resolve()),
        "scope": "final-editor component replay; not fresh CLI end-to-end acceptance", "experiment_rerun": False})
    result = run_report_agent(client=LLMClient.from_env(model=args.model), context=context,
        memory=memory, config=config, template=template, gateway=ReportToolGateway(context, documents=documents),
        completed_checkpoint=completed, emit=lambda text: print(text, flush=True),
        checkpoint_sink=lambda checkpoint: store.write_json("editor_checkpoint.json", checkpoint))
    if result is None:
        raise RuntimeError("Editor returned no usable result; retained diagnostic checkpoint.")
    store.write_json("writer.json", result.model_dump(mode="json"))
    run_report_capability(context=CapabilityContext(store=store, attempt=AttemptManifest("editor-replay")),
        request=ReportAssemblyRequest(title=context.topic,
            sections=_append_verified_experiment_evidence(tuple(result.sections), context), config=config,
            document_plan=result.memory.document_plan, template_name=template.name,
            papers=tuple(context.papers), citation_key_map=context.citation_key_map))
    audit = build_report_audit(report=(args.output / "report.md").read_text(encoding="utf-8"),
        report_body=(args.output / "report_body.md").read_text(encoding="utf-8"), context=context, memory=result.memory)
    store.write_json("audit.json", audit.model_dump(mode="json"))
    print("Replay audit:", audit.status, flush=True)
    print("Remaining:", [(row.section_id, row.severity, row.type) for row in audit.reviewer_findings], flush=True)
    return 0 if audit.status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

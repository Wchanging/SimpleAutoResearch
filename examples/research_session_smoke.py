"""Run the smallest complete research-session path locally.

This smoke intentionally uses a local fixture and a one-line experiment so it
is safe on a laptop. It still exercises the persisted session, artifact,
experiment, analysis, report, and audit boundaries in one run.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from simple_ar.app.research_brief import ResearchBriefSessionRequest
from simple_ar.app.research_report import (
    ResearchReportSessionRequest,
    build_research_session_report_inputs,
    run_research_report_session,
)
from simple_ar.app.research_session import ResearchSessionRequest, run_research_session
from simple_ar.app.session_roots import new_research_session_root
from simple_ar.report.schema import ReportSectionDraft


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("runs/research-session-smoke"),
        help="Directory under which the unique session directory is created.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    fixture = repo_root / "examples" / "research_brief" / "fixtures" / "reliable_agents.md"
    topic = "reliable agents"
    session_root = new_research_session_root(args.output_root, topic)
    session = run_research_session(
        ResearchSessionRequest(
            brief=ResearchBriefSessionRequest(
                topic=topic,
                session_root=session_root,
                local_documents=(fixture,),
                max_results=2,
                max_chunks=20,
            ),
            command=(sys.executable, "-c", "print('accuracy: 0.75')"),
            cwd=repo_root,
            timeout_sec=10,
            result_schema={
                "primary_metric": "accuracy",
                "required_metrics": ["accuracy"],
                "metric_directions": {"accuracy": "higher"},
            },
        )
    )
    context, memory = build_research_session_report_inputs(session)
    selected_papers = session.search.selected_papers
    paper_id = selected_papers[0].id
    report = run_research_report_session(
        ResearchReportSessionRequest(
            session_root=session.session_root,
            title="Reliable agents",
            sections=(
                ReportSectionDraft(
                    section_id="findings",
                    heading="Findings",
                    draft_markdown=(
                        "The smoke experiment produced accuracy 0.75 "
                        f"from the prepared execution [@{paper_id}]."
                    ),
                    used_sources=(paper_id,),
                ),
            ),
            context=context,
            memory=memory,
            source_refs=(session.analysis_ref,),
        )
    )

    print(f"Session: {session.session_root}")
    print(f"Research status: {session.status}")
    print(f"Report status: {report.status}")
    print(f"Report: {session.session_root / report.report_ref.path}")
    print(f"Audit: {session.session_root / report.audit_ref.path}")
    required = (
        "session_manifest.json",
        "attempts/plan-001/research_plan.json",
        "attempts/search-001/search_result.json",
        "attempts/document-001/document_bundle.json",
        "attempts/read-001/read_result.json",
        "attempts/synthesize-001/synthesis_result.json",
        "attempts/design-001/research_design.json",
        "attempts/experiment-001/results.json",
        "attempts/analysis-001/analysis.json",
        "attempts/report-001/report.md",
        "attempts/report-audit-001/report_audit.json",
    )
    missing = [path for path in required if not (session.session_root / path).is_file()]
    if missing:
        print("Missing canonical artifacts: " + ", ".join(missing))
        return 1
    try:
        manifest = json.loads(
            (session.session_root / "session_manifest.json").read_text(encoding="utf-8")
        )
        search_handoff = json.loads(
            (session.session_root / "attempts" / "search-001" / "search_result.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read final session manifest: {exc}")
        return 1
    if not isinstance(manifest, dict) or manifest.get("status") != "completed":
        print("Final session manifest is not completed.")
        return 1
    if (
        not isinstance(search_handoff, dict)
        or not search_handoff.get("selected_paper_ids")
        or not search_handoff.get("selection")
        or not isinstance(search_handoff.get("coverage"), dict)
    ):
        print("Search handoff is missing canonical selection or coverage evidence.")
        return 1
    return 0 if report.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

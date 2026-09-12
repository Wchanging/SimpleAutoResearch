"""Budgeted live search/LLM/report check of the new application (not GPU acceptance).

Optionally supply a small text,label,split CSV to exercise real CPU training.
Requires the user's configured provider; no synthetic model answers or metrics.
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

from simple_ar.app.research_application import ResearchApplicationServices, create_session, load_session
from simple_ar.integrations.llm import LLMClient
from simple_ar.research.workflow_contracts import ResearchBrief


def medium_review_execution() -> dict:
    """Reuse the bundled multi-file project; freeze its data and metric code."""
    root = Path(__file__).resolve().parent / "code_task_medium_review" / "project"
    command = [sys.executable, "main.py", "--config", "configs/experiment.json", "--show-progress"]
    return {
        "command": command, "baseline": {"command": command}, "cwd": str(root), "timeout_sec": 20,
        "code_task": {"code_root": str(root), "approval_note": "Modify only the isolated example copy; preserve evaluation assets.", "max_repairs": 1,
                      "budget_profile": "large", "allow_large_edits": True},
        "result_schema": {"primary_metric": "accuracy", "direction": "higher", "required_metrics": ["accuracy", "macro_f1"]},
        "protocol": {"contract_id": "medium-review-phrase-v1",
            "hypothesis": "Phrase-aware features can improve the fixed review-classification baseline.",
            "dataset_refs": [{"asset_id": "bundled-reviews", "revision": "1"}],
            "split_spec": {"source": "review_pipeline/data.py", "rule": "Existing train/eval labels; no resplitting"},
            "metric_specs": [{"name": metric, "unit": "fraction", "direction": "higher"} for metric in ("accuracy", "macro_f1")],
            "comparison_conditions": {"rounds": 4, "threshold": 0.0},
            "protected_assets": [{"asset_id": name, "path": name} for name in
                ("main.py", "review_pipeline/data.py", "review_pipeline/metrics.py", "review_pipeline/experiment.py", "tests/test_review_pipeline.py")]},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--topic")
    task = parser.add_mutually_exclusive_group()
    task.add_argument("--dataset", type=Path)
    task.add_argument("--medium-review", action="store_true", help="Use the bundled isolated multi-file CodeTask baseline/candidate check.")
    task.add_argument("--cifar10", action="store_true", help="Use the prepared CIFAR-10 three-seed application configuration.")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry", action="store_true", help="Explicitly retry a paused session; requires --resume.")
    parser.add_argument("--deterministic-plan", action="store_true",
                        help="Use deterministic planning; later network/LLM stages still run normally.")
    parser.add_argument("--max-actions", type=int, default=16)
    parser.add_argument("--request-timeout", type=int, default=45, help="Provider timeout in seconds; does not increase session request/token limits.")
    parser.add_argument("--max-output-tokens", type=int, default=2400,
                        help="Per-request LLM output cap for this live check.")
    parser.add_argument("--python", type=Path, help="Prepared Python interpreter for --cifar10.")
    parser.add_argument("--data-root", type=Path, help="Prepared CIFAR data root for --cifar10.")
    parser.add_argument("--epochs", type=int, help="Frozen epoch limit for a new --cifar10 session.")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu", help="CIFAR execution device.")
    args = parser.parse_args()
    if args.retry and not args.resume:
        parser.error("--retry requires --resume")
    if args.max_actions < 1:
        parser.error("--max-actions must be positive")
    if args.request_timeout < 1:
        parser.error("--request-timeout must be positive")
    if args.max_output_tokens < 1:
        parser.error("--max-output-tokens must be positive")
    if args.cifar10 and not args.resume:
        if args.python is None or args.data_root is None or args.epochs is None:
            parser.error("a new --cifar10 session requires --python, --data-root and --epochs")
    load_dotenv()
    os.environ["SIMPLE_AR_LLM_TIMEOUT_SEC"] = str(args.request_timeout)
    os.environ["SIMPLE_AR_LLM_RETRY_ATTEMPTS"] = "2"
    os.environ["SIMPLE_AR_MAX_OUTPUT_TOKENS"] = str(args.max_output_tokens)
    client = LLMClient.from_env()
    if args.resume:
        app = load_session(args.session, services=ResearchApplicationServices(llm_client=client))
        if args.retry:
            app.continue_session(reason=f"Explicit retry of the live application check; provider timeout {args.request_timeout}s, unchanged session budget.")
    elif args.cifar10:
        from cifar10_calibration.application import create_cifar_session
        app = create_cifar_session(root=args.session, python=args.python, data_root=args.data_root,
                                   epochs=args.epochs, device=args.device, llm_client=client)
    else:
        config = {"research_sources": ["openalex", "semantic_scholar", "arxiv"], "research_max_documents": 3,
                  "research_allow_pdf_download": False,
                  "research_planning_max_output_tokens": 900,
                  "report": {"max_review_iterations": 0, "max_section_tokens": 1200}}
        if args.deterministic_plan:
            config["research_plan_mode"] = "deterministic"
        if args.dataset:
            config["execution"] = {"dataset": str(args.dataset.resolve()), "timeout_sec": 20}
        if args.medium_review:
            config["execution"] = medium_review_execution()
        topic = args.topic or ("Phrase-aware features and negation in lightweight sentiment classification" if args.medium_review
                               else "Data augmentation and calibration for low-data image classification")
        request = topic
        if args.medium_review:
            request += "\n\n" + (Path(__file__).resolve().parent / "code_task_medium_review/task.md").read_text(encoding="utf-8")
        token_limit = 320000 if args.medium_review else 160000
        app = create_session(ResearchBrief(request_text=request, objective=topic,
            requested_outputs=("experiments", "report") if args.dataset or args.medium_review else ("report",)),
            root=args.session, services=ResearchApplicationServices(llm_client=client,
                max_results=3, max_chunks=20, idea_limit=2, config=config,
                budget_limits={"llm_requests": 40, "total_tokens": token_limit,
                               "process_invocations": 8 if args.medium_review else (1 if args.dataset else 0),
                               "process_wall_seconds": 120 if args.medium_review else 20}))
    console = Console()
    console.print(f"Session: {args.session}", markup=False)
    for _ in range(args.max_actions):
        console.print(f"Starting: {app.view().next_action}", markup=False)
        view = app.advance()
        console.print(f"Status: {view.status}; next: {view.next_action}; {view.status_reason}", markup=False)
        if view.status in {"completed", "paused", "blocked", "failed"}:
            break
    app.export_session()
    return 0 if view.status == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())

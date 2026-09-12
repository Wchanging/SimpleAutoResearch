"""Construct the CIFAR application inputs; CLI prints config and never executes it."""

import argparse
import json
from pathlib import Path


def execution_config(*, python: Path, data_root: Path, epochs: int, device: str = "cpu") -> dict:
    """Use prepared external assets and a copied project with one editable method."""
    python, data_root = python.resolve(), data_root.resolve()
    if not python.is_file() or not (data_root / "low-data-v1.json").is_file():
        raise ValueError("Prepare the interpreter and dataset manifest before creating the execution config.")
    if not 1 <= epochs <= 30 or device not in {"cpu", "cuda"}:
        raise ValueError("Choose frozen epochs in 1..30 and cpu/cuda explicitly.")
    project = Path(__file__).resolve().parent
    command = [str(python), "train.py", "--data-root", str(data_root), "--epochs", str(epochs),
               "--device", device, "--batch-size", "128"]
    pairs = [{"seed": seed, "baseline_command": [*command, "--seed", str(seed)],
              "candidate_command": [*command, "--seed", str(seed)]} for seed in (0, 1, 2)]
    return {
        "cwd": str(project), "timeout_sec": 1200, "pairs": pairs,
        "code_task": {"code_root": str(project), "max_repairs": 1, "allowed_patterns": ["method.py"],
                      "approval_note": "Modify method.py in the isolated project only; preserve data, evaluator and schedule."},
        "result_schema": {"primary_metric": "accuracy", "direction": "higher",
            "required_metrics": ["accuracy", "nll", "ece"],
            "metric_directions": {"accuracy": "higher", "nll": "lower", "ece": "lower", "wall_seconds": "resource"}},
        "protocol": {"contract_id": "cifar10-low-data-calibration-v1",
            "hypothesis": "A literature-supported augmentation change can improve low-data classification; assess calibration tradeoffs.",
            "dataset_refs": [{"asset_id": "cifar10-official-train", "revision": "low-data-v1"}],
            "split_spec": {"manifest": str(data_root / "low-data-v1.json"), "train": 10000, "validation": 5000,
                           "split_seed": 1729, "official_test_used": False},
            "metric_specs": [{"name": name, "unit": unit} for name, unit in
                             (("accuracy", "fraction"), ("nll", "nats"), ("ece", "fraction"), ("wall_seconds", "seconds"))],
            "comparison_conditions": {"epochs": epochs, "batch_size": 128, "checkpoint": "last_epoch",
                "optimizer": "SGD", "learning_rate": 0.1, "momentum": 0.9, "weight_decay": 0.0005,
                "schedule": "cosine", "ece_bins": 15, "device": device},
            "protected_assets": [{"asset_id": name, "path": name} for name in
                ("train.py", "protocol.py", "prepare.py", "preflight.py", "application.py", "requirements-cpu.txt")]
                + [{"asset_id": "split-manifest", "path": str(data_root / "low-data-v1.json")}],
        },
    }


def create_cifar_session(*, root: Path, python: Path, data_root: Path, epochs: int, device: str = "cpu", llm_client=None):
    """Create only; callers explicitly advance the normal ResearchApplication."""
    from simple_ar.app.research_application import ResearchApplicationServices, create_session
    from simple_ar.research.workflow_contracts import ResearchBrief

    execution = execution_config(python=python, data_root=data_root, epochs=epochs, device=device)
    return create_session(ResearchBrief(
        request_text="Study augmentation and calibration for low-data CIFAR-10. Compare 2-3 feasible approaches from literature, "
            "choose one, change only method.py, compare paired seeds, and write a full Markdown paper with accurate citations. "
            "Report negative results and calibration tradeoffs honestly; do not assume novelty or significance.",
        requested_outputs=("experiments", "report")), root=root,
        services=ResearchApplicationServices(llm_client=llm_client, max_results=50, max_chunks=300, idea_limit=3,
            max_attempts=40, config={"execution": execution,
                "research_sources": ["openalex", "semantic_scholar", "arxiv"],
                "research_max_documents": 20, "research_allow_pdf_download": True,
                "report": {"max_review_iterations": 1, "max_section_tokens": 1800,
                           "figures": {"enabled": True, "max_figures": 3}}},
            budget_limits={"llm_requests": 80, "total_tokens": 300000,
                           "process_invocations": 9, "process_wall_seconds": 7200}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--epochs", type=int, required=True, help="Freeze after throughput probing; identical for all conditions.")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    args = parser.parse_args()
    print(json.dumps(execution_config(**vars(args)), indent=2))


if __name__ == "__main__":
    main()

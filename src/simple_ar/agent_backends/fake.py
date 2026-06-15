"""Deterministic backend used for integration tests and dry runs."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .base import AgentBackend, AgentRunRequest, AgentRunResult


class FakeAgentBackend(AgentBackend):
    """A small backend that writes realistic agent artifacts without LLM calls."""

    name = "fake"

    def __init__(self, *, status: str = "passed") -> None:
        self.status = status

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        started = time.perf_counter()
        request.handoff_dir.mkdir(parents=True, exist_ok=True)
        mode = str(request.metadata.get("mode") or "review")
        artifacts: list[Path] = []

        review_path = request.handoff_dir / "review.md"
        review_path.write_text(
            "# Fake Agent Review\n\n"
            f"- Provider: `{request.provider}`\n"
            f"- Mode: `{mode}`\n"
            f"- Status: `{self.status}`\n",
            encoding="utf-8",
        )
        artifacts.append(review_path)

        if mode == "greenfield":
            generated_dir = request.handoff_dir / "generated_files"
            generated_dir.mkdir(parents=True, exist_ok=True)
            main_path = generated_dir / "main.py"
            main_path.write_text(_fake_greenfield_main(), encoding="utf-8")
            artifacts.append(main_path)

        result_path = request.handoff_dir / "agent_result.json"
        result_path.write_text(
            json.dumps(
                {
                    "provider": request.provider,
                    "backend": self.name,
                    "status": self.status,
                    "mode": mode,
                    "artifacts": [str(path.relative_to(request.handoff_dir)) for path in artifacts],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        artifacts.append(result_path)

        return AgentRunResult(
            provider=request.provider,
            status=self.status,
            message="Fake backend completed deterministic dry-run.",
            artifacts=[str(path) for path in artifacts],
            result_path=str(result_path),
            review_path=str(review_path),
            elapsed_sec=round(time.perf_counter() - started, 6),
        )


def _fake_greenfield_main() -> str:
    return '''"""Deterministic generated project used by the fake agent backend."""

from __future__ import annotations

import json
from pathlib import Path


def run_experiment() -> dict:
    return {
        "accuracy": 0.75,
        "macro_f1": 0.72,
        "train_time_sec": 0.01,
        "inference_time_ms": 0.1,
        "parameter_count": 12,
    }


def main() -> None:
    metrics = run_experiment()
    Path("metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
'''

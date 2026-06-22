"""LLM-backed local agent backend."""

from __future__ import annotations

import json
import time
from pathlib import Path

from simple_ar.integrations.llm import LLMClient

from .base import AgentBackend, AgentRunRequest, AgentRunResult


class LocalLlmAgentBackend(AgentBackend):
    """Use the configured LLM as a constrained local agent reviewer/planner."""

    name = "local_llm"

    def __init__(self, client: LLMClient | None) -> None:
        self.client = client

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        started = time.perf_counter()
        request.handoff_dir.mkdir(parents=True, exist_ok=True)
        result_path = request.handoff_dir / "agent_result.json"
        review_path = request.handoff_dir / "review.md"

        if self.client is None:
            payload = {
                "provider": request.provider,
                "backend": self.name,
                "status": "failed",
                "message": "No LLM client is configured for local_llm backend.",
            }
            result_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            return AgentRunResult(
                provider=request.provider,
                status="failed",
                message=payload["message"],
                artifacts=[str(result_path)],
                result_path=str(result_path),
                elapsed_sec=round(time.perf_counter() - started, 6),
            )

        instructions = _read_optional(request.handoff_dir / "instructions.md")
        manifest = _read_optional(request.handoff_dir / "handoff_manifest.json")
        system = (
            "You are a constrained local agent working inside SimpleAutoResearch. "
            "Do not claim to edit files unless the requested artifact is explicitly produced. "
            "Return a concise Markdown review with risks, missing evidence, and next steps."
        )
        user = (
            "Review this handoff package and produce a practical agent review.\n\n"
            "# Instructions\n"
            f"{instructions}\n\n"
            "# Handoff Manifest\n"
            f"{manifest}\n"
        )
        response = self.client.ask(system, user, label=f"agent-backend-{request.provider}")
        review_path.write_text(response.strip() + "\n", encoding="utf-8")
        payload = {
            "provider": request.provider,
            "backend": self.name,
            "status": "passed",
            "message": "Local LLM backend produced review artifact.",
            "artifacts": ["review.md"],
        }
        result_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return AgentRunResult(
            provider=request.provider,
            status="passed",
            message=payload["message"],
            artifacts=[str(review_path), str(result_path)],
            result_path=str(result_path),
            review_path=str(review_path),
            elapsed_sec=round(time.perf_counter() - started, 6),
        )


def _read_optional(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")

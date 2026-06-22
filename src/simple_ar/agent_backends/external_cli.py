"""Subprocess-backed external agent backends."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .base import AgentBackend, AgentRunRequest, AgentRunResult


DEFAULT_ENV_ALLOWLIST = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
)


@dataclass(frozen=True)
class ExternalCliBackendConfig:
    provider: str
    binary: str
    enabled: bool = False
    model: str | None = None
    timeout_sec: int = 600
    extra_args: tuple[str, ...] = ()
    env_allowlist: tuple[str, ...] = DEFAULT_ENV_ALLOWLIST
    command_template: tuple[str, ...] = field(default_factory=tuple)


class ExternalCliBackend(AgentBackend):
    """Run an external coding/research agent CLI against a handoff directory."""

    def __init__(self, config: ExternalCliBackendConfig) -> None:
        self.config = config

    @property
    def name(self) -> str:
        return self.config.provider

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        started = time.perf_counter()
        request.handoff_dir.mkdir(parents=True, exist_ok=True)
        run_meta = request.handoff_dir / "agent_run.json"
        stdout_path = request.handoff_dir / "stdout.txt"
        stderr_path = request.handoff_dir / "stderr.txt"

        if not self.config.enabled:
            payload = self._run_payload(
                request,
                status="blocked",
                command=self.preview_command(request),
                message="External agent backend is disabled by configuration.",
                returncode=None,
                timed_out=False,
                elapsed_sec=round(time.perf_counter() - started, 6),
            )
            run_meta.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            return AgentRunResult(
                provider=request.provider,
                status="blocked",
                message=payload["message"],
                artifacts=[str(run_meta)],
                result_path=str(run_meta),
                elapsed_sec=payload["elapsed_sec"],
            )

        command = self.build_command(request)
        timed_out = False
        returncode: int | None = None
        message = "External agent completed."
        try:
            completed = subprocess.run(
                command,
                cwd=str(request.handoff_dir.resolve()),
                env=self._safe_env(request),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(1, self.config.timeout_sec),
                check=False,
            )
            returncode = completed.returncode
            stdout_path.write_text(completed.stdout or "", encoding="utf-8", errors="replace")
            stderr_path.write_text(completed.stderr or "", encoding="utf-8", errors="replace")
            if completed.returncode != 0:
                message = _failure_message(
                    completed.returncode,
                    stderr=completed.stderr or "",
                    stdout=completed.stdout or "",
                    provider=self.name,
                )
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            message = f"External agent timed out after {self.config.timeout_sec}s."
            stdout_path.write_text(exc.stdout or "", encoding="utf-8", errors="replace")
            stderr_path.write_text(exc.stderr or "", encoding="utf-8", errors="replace")
        except OSError as exc:
            message = f"External agent could not be started: {exc}"

        elapsed = round(time.perf_counter() - started, 6)
        status = "passed" if returncode == 0 and not timed_out else "failed"
        artifacts = _collect_agent_artifacts(request.handoff_dir)
        artifacts.extend(path for path in (stdout_path, stderr_path) if path.exists())
        payload = self._run_payload(
            request,
            status=status,
            command=self.preview_command(request),
            message=message,
            returncode=returncode,
            timed_out=timed_out,
            elapsed_sec=elapsed,
        )
        payload["artifacts"] = [str(path.relative_to(request.handoff_dir)) for path in artifacts]
        run_meta.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        artifacts.append(run_meta)

        return AgentRunResult(
            provider=request.provider,
            status=status,
            message=message,
            artifacts=[str(path) for path in artifacts],
            result_path=str(run_meta),
            review_path=str(request.handoff_dir / "review.md") if (request.handoff_dir / "review.md").exists() else "",
            elapsed_sec=elapsed,
        )

    def build_command(self, request: AgentRunRequest) -> list[str]:
        if self.config.command_template:
            return _render_command_template(self.config.command_template, request, self.config)
        prompt = _instruction_prompt(request.handoff_dir)
        command = [_resolve_binary(self.config.binary)]
        command.extend(self.config.extra_args)
        command.append(prompt)
        return command

    def preview_command(self, request: AgentRunRequest) -> list[str]:
        command = self.build_command(request)
        return [_redact_arg(arg) for arg in command]

    def _safe_env(self, request: AgentRunRequest) -> dict[str, str]:
        allowed = set(self.config.env_allowlist) | set(request.env_allowlist)
        return {key: value for key, value in os.environ.items() if key in allowed}

    def _run_payload(
        self,
        request: AgentRunRequest,
        *,
        status: str,
        command: Sequence[str],
        message: str,
        returncode: int | None,
        timed_out: bool,
        elapsed_sec: float,
    ) -> dict:
        return {
            "provider": request.provider,
            "backend": self.name,
            "status": status,
            "message": message,
            "cwd": str(request.handoff_dir.resolve()),
            "workspace_dir": str(request.workspace_dir) if request.workspace_dir else None,
            "command": list(command),
            "returncode": returncode,
            "timed_out": timed_out,
            "elapsed_sec": elapsed_sec,
        }


class CodexCliBackend(ExternalCliBackend):
    def __init__(
        self,
        *,
        binary: str = "codex",
        model: str = "",
        enabled: bool = False,
        timeout_sec: int = 600,
        extra_args: tuple[str, ...] = (),
    ) -> None:
        super().__init__(
            ExternalCliBackendConfig(
                provider="codex",
                binary=binary,
                enabled=enabled,
                model=model or None,
                timeout_sec=timeout_sec,
                extra_args=extra_args,
            )
        )

    def build_command(self, request: AgentRunRequest) -> list[str]:
        handoff_root = str(request.handoff_dir.resolve())
        command = [
            _resolve_binary(self.config.binary),
            "exec",
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "-C",
            handoff_root,
        ]
        if self.config.model:
            command.extend(["-m", self.config.model])
        command.extend(self.config.extra_args)
        command.append(_instruction_prompt(request.handoff_dir))
        return command


class ClaudeCodeCliBackend(ExternalCliBackend):
    def __init__(
        self,
        *,
        binary: str = "claude",
        model: str = "",
        enabled: bool = False,
        timeout_sec: int = 600,
        extra_args: tuple[str, ...] = (),
    ) -> None:
        super().__init__(
            ExternalCliBackendConfig(
                provider="claude_code",
                binary=binary,
                enabled=enabled,
                model=model or None,
                timeout_sec=timeout_sec,
                extra_args=extra_args,
            )
        )

    def build_command(self, request: AgentRunRequest) -> list[str]:
        command = [_resolve_binary(self.config.binary), "-p", _instruction_prompt(request.handoff_dir)]
        if self.config.model:
            command.extend(["--model", self.config.model])
        command.extend(self.config.extra_args)
        return command


class OpenCodeCliBackend(ExternalCliBackend):
    def __init__(
        self,
        *,
        binary: str = "opencode",
        model: str = "",
        enabled: bool = False,
        timeout_sec: int = 600,
        extra_args: tuple[str, ...] = (),
    ) -> None:
        super().__init__(
            ExternalCliBackendConfig(
                provider="opencode",
                binary=binary,
                enabled=enabled,
                model=model or None,
                timeout_sec=timeout_sec,
                extra_args=extra_args,
            )
        )

    def build_command(self, request: AgentRunRequest) -> list[str]:
        command = [_resolve_binary(self.config.binary), "run"]
        if self.config.model:
            command.extend(["-m", self.config.model])
        command.extend(self.config.extra_args)
        command.append(_instruction_prompt(request.handoff_dir))
        return command


def _instruction_prompt(handoff_dir: Path) -> str:
    return (
        "Follow the instructions in instructions.md. Write all outputs inside this handoff "
        "directory. For generated code, place files under generated_files/."
    )


def _render_command_template(
    template: Sequence[str], request: AgentRunRequest, config: ExternalCliBackendConfig
) -> list[str]:
    prompt = _instruction_prompt(request.handoff_dir)
    values = {
        "binary": _resolve_binary(config.binary),
        "handoff_dir": str(request.handoff_dir.resolve()),
        "workspace_dir": str(request.workspace_dir or ""),
        "instructions": str(request.handoff_dir / "instructions.md"),
        "prompt": prompt,
        "model": config.model or "",
    }
    rendered = [part.format(**values) for part in template]
    if config.extra_args:
        rendered.extend(config.extra_args)
    return rendered


def _resolve_binary(binary: str) -> str:
    """Resolve CLI shims for subprocess without shell=True.

    PowerShell can find npm-installed ``*.ps1`` shims, but Python subprocess
    with ``shell=False`` needs a directly executable file. On Windows, prefer
    the matching ``.cmd``/``.exe``/``.bat`` wrapper before falling back to
    ``shutil.which``.
    """

    raw = binary.strip()
    if not raw:
        return binary
    path = Path(raw)
    if path.is_absolute() or path.parent != Path("."):
        return str(path)
    if os.name == "nt" and not path.suffix:
        for suffix in (".cmd", ".exe", ".bat", ".com"):
            found = shutil.which(raw + suffix)
            if found:
                return found
    found = shutil.which(raw)
    return found or raw


def _collect_agent_artifacts(handoff_dir: Path) -> list[Path]:
    candidates = [
        handoff_dir / "agent_result.json",
        handoff_dir / "review.md",
        handoff_dir / "patch.diff",
        handoff_dir / "proposed_edits.json",
        handoff_dir / "results.json",
    ]
    artifacts = [path for path in candidates if path.exists()]
    generated = handoff_dir / "generated_files"
    if generated.exists():
        artifacts.extend(path for path in generated.rglob("*") if path.is_file())
    return artifacts


def _redact_arg(arg: str) -> str:
    lowered = arg.lower()
    if "api_key" in lowered or "token" in lowered or "secret" in lowered:
        return "<redacted>"
    return arg


def _failure_message(returncode: int, *, stderr: str, stdout: str, provider: str) -> str:
    text = (stderr or stdout or "").strip()
    if not text:
        return f"External agent exited with code {returncode}."
    tail = _tail_text(text)
    hint = ""
    lowered = tail.lower()
    if "model is not supported" in lowered or "unsupported model" in lowered:
        hint = (
            " Hint: check [implementation].agent_model; leave it empty to use "
            f"the {provider} CLI default model, or set a model supported by your account."
        )
    elif "not found" in lowered or "is not recognized" in lowered:
        hint = " Hint: check [implementation].agent_binary and PATH."
    return f"External agent exited with code {returncode}. {tail}{hint}"


def _tail_text(text: str, *, max_chars: int = 900) -> str:
    lines = [line.strip() for line in text.replace("\r", "\n").splitlines() if line.strip()]
    if not lines:
        compact = " ".join(text.split())
    else:
        compact = " ".join(lines[-6:])
    if len(compact) <= max_chars:
        return compact
    return "..." + compact[-max_chars:]

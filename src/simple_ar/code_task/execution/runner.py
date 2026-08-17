from __future__ import annotations

import codecs
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from simple_ar.core.artifacts import write_json, write_text
from simple_ar.code_task.editing.attempts import (
    load_latest_code_task_batch,
    update_code_task_batch_state,
)
from simple_ar.code_task.execution.comparison import CodeTaskComparisonResult, compare_code_task_runs
from simple_ar.code_task.execution.environment import ensure_code_task_environment_policy
from simple_ar.code_task.execution.artifact_contract import (
    compact_artifact_scan,
    expected_artifact_row,
    has_artifact_path_mismatch,
    scan_required_artifacts,
)
from simple_ar.code_task.execution.run_history import archive_run_attempt
from simple_ar.code_task.runtime.state import (
    code_task_paths,
    load_code_task_manifest,
    manifest_section,
    save_code_task_manifest,
    utcnow_iso,
)
from simple_ar.code_task.execution.summary import write_code_task_summary
from simple_ar.code_task.execution.validation import validate_code_task
from simple_ar.experiment.metrics import parse_metric_lines


CONTROL_TOKENS = {"|", "||", "&&", ";", "<", ">", ">>", "2>", "2>>"}
OUTPUT_CHAR_LIMIT = 200_000
WATCHDOG_OUTPUT_CHAR_LIMIT = 1_000_000
WATCHDOG_WARNING_LINE_LIMIT = 300
WATCHDOG_REPEATED_LINE_LIMIT = 160
WATCHDOG_MIN_ELAPSED_SEC = 1.0
WATCHDOG_WARNING_TOKENS = (
    "warning",
    "convergencewarning",
    "runtimewarning",
    "total no. of iterations",
    "failed to converge",
    "maximum iterations",
)
OutputCallback = Callable[[str, str], None]
StreamOutputMode = str
STREAM_OUTPUT_ALIASES = {
    "0": "off",
    "1": "auto",
    "false": "off",
    "no": "off",
    "off": "off",
    "none": "off",
    "true": "auto",
    "yes": "auto",
    "on": "auto",
    "line": "line",
    "lines": "line",
    "auto": "auto",
    "tqdm": "auto",
    "progress": "auto",
    "summary": "summary",
    "tail": "summary",
}
PROGRESS_RELAY_MIN_INTERVAL_SEC = 0.5
SUMMARY_TAIL_LINES = 12


class CodeTaskRunError(RuntimeError):
    """Raised when a code-task benchmark cannot be launched safely."""


VALID_RUN_LABELS = {"baseline", "patched"}


class _OutputWatchdog:
    """Detect obvious output floods while preserving normal long runs."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self.total_chars = 0
        self.line_count = 0
        self.warning_line_count = 0
        self.repeated_lines: dict[str, int] = {}
        self.samples: list[str] = []
        self.triggered: dict[str, Any] | None = None

    def observe(self, stream_name: str, text: str) -> None:
        if self.triggered is not None:
            return
        display = _display_stream_text(text)
        if not display:
            return
        self.total_chars += len(text)
        self.line_count += 1
        if len(self.samples) < 12:
            self.samples.append(f"{stream_name}: {display[:240]}")
        normalized = _normalize_watchdog_line(display)
        if normalized:
            self.repeated_lines[normalized] = self.repeated_lines.get(normalized, 0) + 1
        lowered = display.lower()
        if any(token in lowered for token in WATCHDOG_WARNING_TOKENS):
            self.warning_line_count += 1
        elapsed = time.monotonic() - self.started
        if elapsed < WATCHDOG_MIN_ELAPSED_SEC:
            return
        if self.warning_line_count >= WATCHDOG_WARNING_LINE_LIMIT:
            self._trigger(
                "warning_flood",
                stream_name=stream_name,
                elapsed_sec=elapsed,
                detail=f"{self.warning_line_count} warning-like lines observed.",
            )
        elif normalized and self.repeated_lines.get(normalized, 0) >= WATCHDOG_REPEATED_LINE_LIMIT:
            self._trigger(
                "repeated_output_flood",
                stream_name=stream_name,
                elapsed_sec=elapsed,
                detail=f"Line repeated {self.repeated_lines[normalized]} times.",
            )
        elif self.total_chars >= WATCHDOG_OUTPUT_CHAR_LIMIT:
            self._trigger(
                "output_volume_limit",
                stream_name=stream_name,
                elapsed_sec=elapsed,
                detail=f"Captured output exceeded {WATCHDOG_OUTPUT_CHAR_LIMIT} characters.",
            )

    def _trigger(self, reason: str, *, stream_name: str, elapsed_sec: float, detail: str) -> None:
        self.triggered = {
            "schema_version": "code_task_runtime_watchdog.v1",
            "reason": reason,
            "stream": stream_name,
            "elapsed_sec": round(elapsed_sec, 3),
            "line_count": self.line_count,
            "warning_line_count": self.warning_line_count,
            "total_chars": self.total_chars,
            "detail": detail,
            "sample_lines": self.samples[-12:],
        }


def _normalize_watchdog_line(text: str) -> str:
    text = " ".join(text.strip().split())
    if not text:
        return ""
    # Keep the semantic shape while ignoring counters and timings.
    text = re.sub(r"\d+(?:\.\d+)?", "<num>", text.lower())
    return text[:240]


@dataclass(frozen=True)
class CodeTaskRunResult:
    """Captured result from running a code-task benchmark.

    Args:
        run_dir: Code-task run directory.
        label: Execution label, usually ``baseline`` or ``patched``.
        report_path: Structured execution report.
        stdout_path: Captured stdout path.
        stderr_path: Captured stderr path.
        metrics_path: Parsed metrics path.
        status: ``passed``, ``failed``, ``timed_out``, or
            ``blocked_by_validation``.
        returncode: Subprocess return code, or ``None`` for timeout/blocking.
        timed_out: Whether execution timed out.
        metrics: Parsed ``name: value`` metric lines from stdout.
    """

    run_dir: Path
    label: str
    report_path: Path
    stdout_path: Path
    stderr_path: Path
    metrics_path: Path
    status: str
    returncode: int | None
    timed_out: bool
    metrics: dict[str, float] = field(default_factory=dict)


def run_code_task_benchmark(
    run_dir: Path,
    *,
    command: str | None = None,
    timeout_sec: int = 60,
    skip_validation: bool = False,
    run_label: str = "patched",
    env_mode: str | None = None,
    python_executable: str | Path | None = None,
    stream_output: bool | str = False,
    output_callback: OutputCallback | None = None,
) -> CodeTaskRunResult:
    """Run the recorded benchmark command inside the copied workspace.

    Args:
        run_dir: Code-task run directory.
        command: Optional command override. When omitted, the command recorded
            during ``code-task init`` is used.
        timeout_sec: Maximum runtime in seconds.
        skip_validation: Run even when static validation has not passed.
        run_label: Result slot under ``code_task/run``. Use ``baseline`` before
            patching and ``patched`` after edits have been applied.
        env_mode: Optional execution environment mode override.
        python_executable: External interpreter path or executable name when
            ``env_mode`` is ``external``.
        stream_output: Whether and how to relay subprocess output while it
            runs. ``True`` is treated as ``"auto"`` for progress-bar friendly
            output; supported string modes are ``off``, ``line``, ``auto``,
            and ``summary``.
        output_callback: Callback receiving ``("stdout"|"stderr", line)`` when
            streaming is enabled.

    Returns:
        Execution artifacts and status.

    Raises:
        CodeTaskRunError: If the command is missing, unsafe, or timeout is
            invalid.
    """
    if timeout_sec < 1:
        raise CodeTaskRunError("timeout_sec must be at least 1")
    label = _normalize_run_label(run_label)

    manifest = load_code_task_manifest(run_dir)
    paths = code_task_paths(run_dir)
    if not paths.workspace_dir.is_dir():
        raise FileNotFoundError(f"Missing code-task workspace: {paths.workspace_dir}")
    paths.run_artifact_dir.mkdir(parents=True, exist_ok=True)

    command_text = command or _benchmark_command(manifest)
    if not command_text:
        raise CodeTaskRunError(
            "No benchmark command recorded. Pass --command or rerun code-task init "
            "with --benchmark-command."
        )
    environment_policy = ensure_code_task_environment_policy(
        run_dir,
        manifest,
        env_mode=env_mode,
        python_executable=python_executable,
    )
    command_args = _split_command(command_text, environment_policy=environment_policy)

    if not skip_validation:
        validation = validate_code_task(run_dir)
        if validation.error_count:
            return _write_blocked_result(
                run_dir,
                command_text=command_text,
                command_args=command_args,
                environment_policy=environment_policy,
                timeout_sec=timeout_sec,
                reason="Static validation reported errors.",
                run_label=label,
            )
        manifest = load_code_task_manifest(run_dir)

    started = time.monotonic()
    try:
        completed = _run_command(
            command_args,
            cwd=paths.workspace_dir,
            timeout_sec=timeout_sec,
            stream_output=stream_output,
            output_callback=output_callback,
        )
        duration_sec = round(time.monotonic() - started, 3)
        stdout, stdout_truncated = _clip_output(completed.stdout)
        stderr, stderr_truncated = _clip_output(completed.stderr)
        metrics = parse_metric_lines(stdout)
        status = "passed" if completed.returncode == 0 else "failed"
        report_metadata: dict[str, Any] = {}
        run_metadata: dict[str, Any] = {}
        artifact_scan: dict[str, Any] | None = None
        if _is_greenfield_manifest(manifest):
            artifact_scan = scan_required_artifacts(run_dir, manifest)
            artifact_scan_path = _write_runtime_artifact_scan(run_dir, label, artifact_scan)
            artifact_scan_rel = _relative_to_run(run_dir, artifact_scan_path)
            artifact_scan_summary = compact_artifact_scan(artifact_scan)
            report_metadata.update(
                {
                    "artifact_scan": artifact_scan_rel,
                    "artifact_scan_summary": artifact_scan_summary,
                }
            )
            run_metadata.update(
                {
                    "artifact_scan": artifact_scan_rel,
                    "artifact_scan_summary": artifact_scan_summary,
                }
            )
        watchdog_record = getattr(completed, "simple_ar_watchdog", None)
        if isinstance(watchdog_record, dict):
            report_metadata["runtime_watchdog"] = watchdog_record
            run_metadata["runtime_watchdog"] = watchdog_record
        quality_guard = _greenfield_result_quality_guard(
            run_dir,
            manifest,
            status=status,
            metrics=metrics,
            artifact_scan=artifact_scan,
        )
        if quality_guard is not None:
            status = "failed"
            guard_message = str(quality_guard.get("message") or "Generated benchmark quality guard failed.")
            stderr_prefix = stderr.rstrip()
            stderr_text = f"{stderr_prefix}\n{guard_message}\n" if stderr_prefix else guard_message + "\n"
            stderr, stderr_truncated = _clip_output(stderr_text)
            report_metadata["quality_guard"] = quality_guard
            run_metadata["quality_guard"] = quality_guard
        return _write_execution_result(
            run_dir,
            manifest=manifest,
            command_text=command_text,
            command_args=command_args,
            environment_policy=environment_policy,
            timeout_sec=timeout_sec,
            duration_sec=duration_sec,
            status=status,
            returncode=completed.returncode,
            timed_out=False,
            stdout=stdout,
            stderr=stderr,
            metrics=metrics,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            run_label=label,
            report_metadata=report_metadata or None,
            run_metadata=run_metadata or None,
        )
    except subprocess.TimeoutExpired as exc:
        duration_sec = round(time.monotonic() - started, 3)
        stdout, stdout_truncated = _clip_output(_output_text(exc.stdout))
        stderr_text = _output_text(exc.stderr)
        if stderr_text:
            stderr_text += "\n"
        stderr_text += f"Timed out after {timeout_sec} seconds."
        stderr, stderr_truncated = _clip_output(stderr_text)
        metrics = parse_metric_lines(stdout)
        return _write_execution_result(
            run_dir,
            manifest=manifest,
            command_text=command_text,
            command_args=command_args,
            environment_policy=environment_policy,
            timeout_sec=timeout_sec,
            duration_sec=duration_sec,
            status="timed_out",
            returncode=None,
            timed_out=True,
            stdout=stdout,
            stderr=stderr,
            metrics=metrics,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            run_label=label,
        )


def run_code_task_baseline(
    run_dir: Path,
    *,
    command: str | None = None,
    timeout_sec: int = 60,
    skip_validation: bool = False,
    env_mode: str | None = None,
    python_executable: str | Path | None = None,
    stream_output: bool | str = False,
    output_callback: OutputCallback | None = None,
) -> CodeTaskRunResult:
    """Run the recorded benchmark as the pre-patch baseline.

    Args:
        run_dir: Code-task run directory.
        command: Optional command override.
        timeout_sec: Maximum runtime in seconds.
        skip_validation: Run even when static validation has not passed.
        env_mode: Optional execution environment mode override.
        python_executable: External interpreter path or executable name when
            ``env_mode`` is ``external``.
        stream_output: Whether and how to relay subprocess output while it
            runs. ``True`` is treated as ``"auto"``.
        output_callback: Callback receiving ``("stdout"|"stderr", line)`` when
            streaming is enabled.

    Returns:
        Baseline execution artifacts and status.
    """
    return run_code_task_benchmark(
        run_dir,
        command=command,
        timeout_sec=timeout_sec,
        skip_validation=skip_validation,
        run_label="baseline",
        env_mode=env_mode,
        python_executable=python_executable,
        stream_output=stream_output,
        output_callback=output_callback,
    )


def record_provided_code_task_baseline(
    run_dir: Path,
    *,
    metrics: dict[str, float],
    source_path: str,
    env_mode: str | None = None,
    python_executable: str | Path | None = None,
) -> CodeTaskRunResult:
    """Record user-provided baseline metrics without running the benchmark.

    This is for expensive existing-project tasks where the unchanged baseline
    has already been measured elsewhere. The artifact is deliberately explicit:
    reports and manifests mark the baseline as provided so downstream summaries
    do not confuse it with a fresh subprocess run.
    """

    numeric_metrics = {
        str(name): float(value)
        for name, value in metrics.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    if not numeric_metrics:
        raise CodeTaskRunError("Provided baseline metrics must include at least one numeric metric.")

    manifest = load_code_task_manifest(run_dir)
    environment_policy = ensure_code_task_environment_policy(
        run_dir,
        manifest,
        env_mode=env_mode,
        python_executable=python_executable,
    )
    return _write_execution_result(
        run_dir,
        manifest=manifest,
        command_text="provided-baseline-metrics",
        command_args=[],
        environment_policy=environment_policy,
        timeout_sec=0,
        duration_sec=0.0,
        status="passed",
        returncode=None,
        timed_out=False,
        stdout=f"Baseline metrics were provided by the user from {source_path}.\n",
        stderr="",
        metrics=numeric_metrics,
        stdout_truncated=False,
        stderr_truncated=False,
        run_label="baseline",
        report_metadata={
            "provided_baseline": True,
            "provided_source": source_path,
        },
        run_metadata={
            "provided": True,
            "provided_source": source_path,
            "baseline_policy": "provided",
        },
    )


def _run_command(
    command_args: list[str],
    *,
    cwd: Path,
    timeout_sec: int,
    stream_output: bool | str,
    output_callback: OutputCallback | None,
) -> subprocess.CompletedProcess[str]:
    mode = normalize_stream_output_mode(stream_output)
    watchdog = _OutputWatchdog()
    callback = output_callback if mode not in {"off", "summary"} else None
    process = subprocess.Popen(
        command_args,
        cwd=cwd,
        env=_safe_env(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    stop_event = threading.Event()
    stdout_thread = threading.Thread(
        target=_read_stream,
        args=(process.stdout, "stdout", stdout_chunks, callback, mode, watchdog, stop_event),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_read_stream,
        args=(process.stderr, "stderr", stderr_chunks, callback, mode, watchdog, stop_event),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    deadline = time.monotonic() + timeout_sec
    killed_by_watchdog = False
    returncode: int | None = None
    while True:
        returncode = process.poll()
        if returncode is not None:
            break
        if watchdog.triggered is not None:
            killed_by_watchdog = True
            stop_event.set()
            process.kill()
            try:
                returncode = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                returncode = 124
            break
        if time.monotonic() >= deadline:
            stop_event.set()
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
            raise subprocess.TimeoutExpired(
                command_args,
                timeout_sec,
                output="".join(stdout_chunks),
                stderr="".join(stderr_chunks),
            )
        time.sleep(0.05)
    if killed_by_watchdog:
        stop_event.set()
        process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)
    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)
    if watchdog.triggered is not None:
        detail = str(watchdog.triggered.get("detail") or watchdog.triggered.get("reason") or "output flood")
        stderr = stderr.rstrip() + f"\nRuntime output watchdog aborted benchmark: {detail}\n"
    completed = subprocess.CompletedProcess(
        command_args,
        int(returncode if returncode is not None else 124),
        stdout=stdout,
        stderr=stderr,
    )
    if watchdog.triggered is not None:
        setattr(completed, "simple_ar_watchdog", watchdog.triggered)
    if mode == "summary" and output_callback is not None:
        _emit_output_summary(completed.stdout, completed.stderr, output_callback)
    return completed


def normalize_stream_output_mode(value: bool | str) -> StreamOutputMode:
    """Normalize benchmark output relay mode.

    ``True`` maps to ``auto`` so existing configs keep working while gaining
    carriage-return progress support for tools such as tqdm.
    """

    if isinstance(value, bool):
        return "auto" if value else "off"
    text = str(value).strip().lower()
    if text in STREAM_OUTPUT_ALIASES:
        return STREAM_OUTPUT_ALIASES[text]
    raise ValueError(
        "stream_output must be one of: false, true, off, line, auto, summary"
    )


def _read_stream(
    stream: Any,
    stream_name: str,
    chunks: list[str],
    callback: OutputCallback | None,
    mode: StreamOutputMode,
    watchdog: _OutputWatchdog | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    if stream is None:
        return
    buffer: list[str] = []
    last_progress = ""
    last_progress_time = 0.0
    pending_progress = ""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def emit(text: str, *, progress: bool = False, force: bool = False) -> bool:
        nonlocal last_progress, last_progress_time
        if callback is None:
            return False
        display = _display_stream_text(text)
        if not display:
            return False
        if progress:
            now = time.monotonic()
            if not force:
                if display == last_progress:
                    return False
                if now - last_progress_time < PROGRESS_RELAY_MIN_INTERVAL_SEC:
                    return False
            last_progress = display
            last_progress_time = now
        try:
            callback(stream_name, display)
        except Exception:
            pass
        return True

    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            raw = stream.read(1)
            if not raw:
                break
            char = decoder.decode(raw)
            if not char:
                continue
            chunks.append(char)
            if char == "\n":
                line = "".join(buffer)
                if watchdog is not None:
                    watchdog.observe(stream_name, line)
                    if watchdog.triggered is not None and stop_event is not None:
                        stop_event.set()
                if pending_progress:
                    if _display_stream_text(pending_progress) != _display_stream_text(line):
                        emit(pending_progress, progress=True, force=True)
                    pending_progress = ""
                emit(line, force=True)
                buffer.clear()
            elif char == "\r":
                if mode == "auto":
                    text = "".join(buffer)
                    if watchdog is not None:
                        watchdog.observe(stream_name, text)
                        if watchdog.triggered is not None and stop_event is not None:
                            stop_event.set()
                    if not emit(text, progress=True):
                        pending_progress = text
                    buffer.clear()
                else:
                    buffer.append(char)
            else:
                buffer.append(char)
        tail = decoder.decode(b"", final=True)
        if tail:
            chunks.append(tail)
            buffer.append(tail)
        if buffer:
            if watchdog is not None:
                watchdog.observe(stream_name, "".join(buffer))
            if pending_progress:
                if _display_stream_text(pending_progress) != _display_stream_text("".join(buffer)):
                    emit(pending_progress, progress=True, force=True)
                pending_progress = ""
            emit("".join(buffer), force=True)
        elif pending_progress:
            emit(pending_progress, progress=True, force=True)
    finally:
        stream.close()


def _display_stream_text(text: str) -> str:
    """Return a terminal-friendly representation of captured stream text."""

    return text.replace("\r", "").strip()


def _emit_output_summary(
    stdout: str,
    stderr: str,
    callback: OutputCallback,
) -> None:
    for stream_name, text in (("stdout", stdout), ("stderr", stderr)):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            continue
        omitted = max(0, len(lines) - SUMMARY_TAIL_LINES)
        if omitted:
            callback(stream_name, f"... {omitted} earlier line(s) omitted ...")
        for line in lines[-SUMMARY_TAIL_LINES:]:
            callback(stream_name, line)


def _write_blocked_result(
    run_dir: Path,
    *,
    command_text: str,
    command_args: list[str],
    environment_policy: dict[str, Any],
    timeout_sec: int,
    reason: str,
    run_label: str,
) -> CodeTaskRunResult:
    manifest = load_code_task_manifest(run_dir)
    return _write_execution_result(
        run_dir,
        manifest=manifest,
        command_text=command_text,
        command_args=command_args,
        environment_policy=environment_policy,
        timeout_sec=timeout_sec,
        duration_sec=0.0,
        status="blocked_by_validation",
        returncode=None,
        timed_out=False,
        stdout="",
        stderr=reason + "\n",
        metrics={},
        stdout_truncated=False,
        stderr_truncated=False,
        run_label=run_label,
    )


def _write_execution_result(
    run_dir: Path,
    *,
    manifest: dict[str, Any],
    command_text: str,
    command_args: list[str],
    environment_policy: dict[str, Any],
    timeout_sec: int,
    duration_sec: float,
    status: str,
    returncode: int | None,
    timed_out: bool,
    stdout: str,
    stderr: str,
    metrics: dict[str, float],
    stdout_truncated: bool,
    stderr_truncated: bool,
    run_label: str,
    report_metadata: dict[str, Any] | None = None,
    run_metadata: dict[str, Any] | None = None,
) -> CodeTaskRunResult:
    paths = code_task_paths(run_dir)
    run_dir_for_label = _run_label_dir(paths.run_artifact_dir, run_label)
    run_dir_for_label.mkdir(parents=True, exist_ok=True)
    stdout_path = run_dir_for_label / "stdout.txt"
    stderr_path = run_dir_for_label / "stderr.txt"
    metrics_path = run_dir_for_label / "metrics.json"
    report_path = run_dir_for_label / "execution_report.json"
    rel_base = f"code_task/run/{run_label}"

    write_text(stdout_path, stdout or "")
    write_text(stderr_path, stderr or "")
    write_json(metrics_path, metrics)
    report = {
        "schema_version": 1,
        "generated_at": utcnow_iso(),
        "status": status,
        "label": run_label,
        "command_text": command_text,
        "command": command_args,
        "cwd": str(paths.workspace_dir),
        "environment": _execution_environment_record(environment_policy),
        "timeout_sec": timeout_sec,
        "duration_sec": duration_sec,
        "returncode": returncode,
        "timed_out": timed_out,
        "stdout": f"{rel_base}/stdout.txt",
        "stderr": f"{rel_base}/stderr.txt",
        "metrics": f"{rel_base}/metrics.json",
        "metric_values": metrics,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }
    if report_metadata:
        report.update(report_metadata)
    history_record = archive_run_attempt(
        run_dir,
        run_label=run_label,
        stdout=stdout or "",
        stderr=stderr or "",
        metrics=metrics,
        execution_report=report,
    )
    report["history_attempt"] = history_record["id"]
    report["history_report"] = history_record["execution_report"]
    write_json(report_path, report)
    _update_manifest_after_run(
        run_dir,
        manifest,
        status=status,
        command_text=command_text,
        environment_policy=environment_policy,
        returncode=returncode,
        timed_out=timed_out,
        metric_values=metrics,
        run_label=run_label,
        run_metadata=run_metadata,
        history_record=history_record,
    )
    comparison = _maybe_compare_runs(run_dir)
    if run_label == "patched":
        _update_latest_batch_after_benchmark(run_dir, report_path, status)
        _update_manifest_after_patched_outcome(
            run_dir,
            status=status,
            comparison_verdict=comparison.verdict if comparison is not None else "",
        )
    write_code_task_summary(run_dir)
    return CodeTaskRunResult(
        run_dir=paths.run_dir,
        label=run_label,
        report_path=report_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        metrics_path=metrics_path,
        status=status,
        returncode=returncode,
        timed_out=timed_out,
        metrics=metrics,
    )


def _maybe_compare_runs(run_dir: Path) -> CodeTaskComparisonResult | None:
    paths = code_task_paths(run_dir)
    baseline_report = paths.run_artifact_dir / "baseline" / "execution_report.json"
    patched_report = paths.run_artifact_dir / "patched" / "execution_report.json"
    if baseline_report.exists() and patched_report.exists():
        return compare_code_task_runs(run_dir)
    return None


def _update_latest_batch_after_benchmark(run_dir: Path, report_path: Path, status: str) -> None:
    batch = load_latest_code_task_batch(run_dir)
    if batch is None:
        return
    update_code_task_batch_state(
        run_dir,
        batch.batch_state_path,
        state="completed" if status == "passed" else "failed",
        artifacts={"benchmark_run": _relative_to_run(run_dir, report_path)},
        detail=f"Patched benchmark {status}.",
        extra={"benchmark_status": status},
    )


def _update_manifest_after_patched_outcome(
    run_dir: Path,
    *,
    status: str,
    comparison_verdict: str,
) -> None:
    manifest = load_code_task_manifest(run_dir)
    if status == "passed":
        if comparison_verdict:
            manifest["objective"] = {
                "status": comparison_verdict,
                "source": "code_task/run/comparison.json",
                "updated_at": utcnow_iso(),
            }
            if comparison_verdict == "improved":
                manifest["status"] = "objective_improved"
            elif comparison_verdict in {"regressed", "mixed"}:
                manifest["status"] = "objective_" + comparison_verdict
            else:
                manifest["status"] = "objective_inconclusive"
        _mark_failure_resolved(manifest)
        _mark_repair_benchmark_resolved(manifest)
    save_code_task_manifest(run_dir, manifest)


def _mark_failure_resolved(manifest: dict[str, Any]) -> None:
    failure = manifest.get("failure_analysis")
    if not isinstance(failure, dict) or not failure:
        return
    if failure.get("status") != "no_failure":
        failure["status"] = "resolved"
        failure["resolved_at"] = utcnow_iso()
        manifest["failure_analysis"] = failure


def _mark_repair_benchmark_resolved(manifest: dict[str, Any]) -> None:
    repair = manifest.get("repair")
    if not isinstance(repair, dict) or not repair:
        return
    if str(repair.get("status", "")).startswith("repair_"):
        repair["status"] = "benchmark_passed"
        repair["resolved_at"] = utcnow_iso()
        manifest["repair"] = repair


def _relative_to_run(run_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(Path(run_dir).resolve()).as_posix()
    except ValueError:
        return str(path)


def _normalize_run_label(value: str) -> str:
    label = value.strip().lower().replace("_", "-")
    if label not in VALID_RUN_LABELS:
        raise CodeTaskRunError(
            "run_label must be one of: " + ", ".join(sorted(VALID_RUN_LABELS))
        )
    return label


def _run_label_dir(run_artifact_dir: Path, label: str) -> Path:
    return run_artifact_dir / label


def _split_command(command_text: str, *, environment_policy: dict[str, Any]) -> list[str]:
    try:
        args = shlex.split(command_text, posix=os.name != "nt")
    except ValueError as exc:
        raise CodeTaskRunError(f"Could not parse benchmark command: {exc}") from exc
    if not args:
        raise CodeTaskRunError("Benchmark command is empty")
    if any(token in CONTROL_TOKENS for token in args):
        raise CodeTaskRunError(
            "Shell control operators are not supported in benchmark commands. "
            "Use a direct command such as `python -m unittest discover -s tests`."
        )
    if args[0] in {"python", "python3"}:
        args[0] = _policy_python_executable(environment_policy)
    return args


def _policy_python_executable(environment_policy: dict[str, Any]) -> str:
    executable = environment_policy.get("python_executable")
    if isinstance(executable, str) and executable:
        return executable
    return sys.executable


def _execution_environment_record(environment_policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": environment_policy.get("mode", "current"),
        "python_executable": _policy_python_executable(environment_policy),
        "python_version": environment_policy.get("python_version"),
        "allow_dependency_install": bool(
            environment_policy.get("allow_dependency_install", False)
        ),
        "dependency_install": environment_policy.get("dependency_install", "disabled"),
    }


def _safe_env(workspace_dir: Path) -> dict[str, str]:
    allowed = {
        "APPDATA",
        "COMSPEC",
        "HOME",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    python_paths = [str(workspace_dir), str(workspace_dir / "src")]
    existing = os.environ.get("PYTHONPATH")
    if existing:
        python_paths.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["SIMPLE_AR_CODE_TASK"] = "1"
    return env


def _clip_output(text: str) -> tuple[str, bool]:
    if len(text) <= OUTPUT_CHAR_LIMIT:
        return text, False
    suffix = "\n... [truncated by SimpleAutoResearch]\n"
    return text[: OUTPUT_CHAR_LIMIT - len(suffix)] + suffix, True


def _output_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _benchmark_command(manifest: dict[str, Any]) -> str:
    benchmark = manifest.get("benchmark", {})
    if isinstance(benchmark, dict):
        command = benchmark.get("command")
        return str(command) if command else ""
    return ""


def _is_greenfield_manifest(manifest: dict[str, Any]) -> bool:
    code_task = manifest.get("code_task")
    return isinstance(code_task, dict) and str(code_task.get("kind") or "") == "greenfield"


def _write_runtime_artifact_scan(run_dir: Path, label: str, scan: dict[str, Any]) -> Path:
    paths = code_task_paths(run_dir)
    run_dir_for_label = _run_label_dir(paths.run_artifact_dir, label)
    run_dir_for_label.mkdir(parents=True, exist_ok=True)
    path = run_dir_for_label / "artifact_scan.json"
    write_json(path, scan)
    return path


def _greenfield_result_quality_guard(
    run_dir: Path,
    manifest: dict[str, Any],
    *,
    status: str,
    metrics: dict[str, float],
    artifact_scan: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if status != "passed":
        return None
    if not _is_greenfield_manifest(manifest):
        return None
    paths = code_task_paths(run_dir)
    if artifact_scan and has_artifact_path_mismatch(artifact_scan):
        return {
            "status": "failed",
            "reason": "artifact_path_mismatch",
            "message": (
                "Generated benchmark quality guard failed: a required artifact was "
                "written to a same-name path outside the expected generated-project "
                "artifact location. See the run artifact_scan.json."
            ),
            "artifact_scan": compact_artifact_scan(artifact_scan),
        }
    results_row = expected_artifact_row(artifact_scan or {}, "artifacts/results.json")
    results_rel = (
        str(results_row.get("expected_path", "generated_project/artifacts/results.json"))
        if isinstance(results_row, dict)
        else "generated_project/artifacts/results.json"
    )
    results_path = paths.workspace_dir / results_rel
    results = None
    if isinstance(results_row, dict):
        parse_status = str(results_row.get("parse_status") or "missing")
        if parse_status in {"missing", "empty", "invalid_json", "unreadable"}:
            reason = "missing_results_artifact" if parse_status == "missing" else f"{parse_status}_results_artifact"
            return {
                "status": "failed",
                "reason": reason,
                "message": (
                    "Generated benchmark quality guard failed: expected "
                    f"{results_rel} is {parse_status}. Successful completion requires "
                    "all configured runtime artifacts."
                ),
                "artifact": _relative_to_run(run_dir, results_path),
                "artifact_scan": compact_artifact_scan(artifact_scan or {}),
            }
        try:
            results = json.loads(results_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "status": "failed",
                "reason": "unreadable_results_artifact",
                "message": (
                    "Generated benchmark quality guard failed: "
                    f"could not read {results_rel} ({exc})."
                ),
                "artifact": _relative_to_run(run_dir, results_path),
                "artifact_scan": compact_artifact_scan(artifact_scan or {}),
            }

    if isinstance(results, dict):
        record_count = _count_result_records(results.get("raw_records"))
        condition_summary_count = _mapping_size(results.get("condition_summaries"))
        dataset_summary_count = _mapping_size(results.get("dataset_comparisons"))
        non_resource_metrics = _non_resource_metric_names(manifest, metrics)
        all_non_resource_zero = bool(non_resource_metrics) and all(
            abs(float(metrics.get(name, 0.0))) <= 1e-12 for name in non_resource_metrics
        )
        no_structured_evidence = (
            record_count == 0 and condition_summary_count == 0 and dataset_summary_count == 0
        )
        if no_structured_evidence and all_non_resource_zero:
            return {
                "status": "failed",
                "reason": "empty_greenfield_evidence",
                "message": (
                    "Generated benchmark quality guard failed: artifacts/results.json contains "
                    "no condition-level records or summaries, and all non-resource metrics are "
                    "zero. Treating this run as failed instead of benchmark_passed."
                ),
                "artifact": _relative_to_run(run_dir, results_path),
                "record_count": record_count,
                "condition_summary_count": condition_summary_count,
                "dataset_summary_count": dataset_summary_count,
                "non_resource_metrics": non_resource_metrics,
            }

    unusable_artifacts = [
        row
        for row in (artifact_scan or {}).get("artifacts", [])
        if isinstance(row, dict)
        and str(row.get("parse_status", "")) in {"missing", "empty", "invalid_json", "unreadable"}
        and not str(row.get("expected_path", "")).replace("\\", "/").endswith("artifacts/results.json")
    ]
    if unusable_artifacts:
        row = unusable_artifacts[0]
        parse_status = str(row.get("parse_status") or "missing")
        expected_path = str(row.get("expected_path") or row.get("contract_path") or "required artifact")
        return {
            "status": "failed",
            "reason": f"{parse_status}_required_artifact",
            "message": (
                "Generated benchmark quality guard failed: expected "
                f"{expected_path} is {parse_status}. Successful completion requires "
                "all configured runtime artifacts."
            ),
            "artifact": _relative_to_run(run_dir, paths.workspace_dir / expected_path),
            "artifact_scan": compact_artifact_scan(artifact_scan or {}),
        }
    return None


def _count_result_records(value: object) -> int:
    if isinstance(value, list):
        total = 0
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("records"), list):
                total += len(item["records"])
            elif isinstance(item, dict):
                total += 1
            else:
                total += _count_result_records(item)
        return total
    if isinstance(value, dict):
        records = value.get("records")
        if isinstance(records, list):
            return len(records)
        return sum(_count_result_records(item) for item in value.values())
    return 0


def _mapping_size(value: object) -> int:
    return len(value) if isinstance(value, dict) else 0


def _non_resource_metric_names(manifest: dict[str, Any], metrics: dict[str, float]) -> list[str]:
    benchmark = manifest.get("benchmark")
    directions = benchmark.get("metric_directions") if isinstance(benchmark, dict) else {}
    directions = directions if isinstance(directions, dict) else {}
    names: list[str] = []
    for name in metrics:
        direction = str(directions.get(name, "")).strip().lower()
        lowered = name.lower()
        if direction == "resource" or "runtime" in lowered or lowered.endswith("_sec"):
            continue
        names.append(name)
    return names


def _greenfield_results_artifact_path(workspace_dir: Path) -> Path:
    candidates = [
        workspace_dir / "generated_project" / "artifacts" / "results.json",
        workspace_dir / "artifacts" / "results.json",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]


def _update_manifest_after_run(
    run_dir: Path,
    manifest: dict[str, Any],
    *,
    status: str,
    command_text: str,
    environment_policy: dict[str, Any],
    returncode: int | None,
    timed_out: bool,
    metric_values: dict[str, float],
    run_label: str,
    run_metadata: dict[str, Any] | None = None,
    history_record: dict[str, Any] | None = None,
) -> None:
    layout = manifest_section(manifest, "layout")
    layout["run"] = "code_task/run"
    layout[f"{run_label}_execution_report"] = f"code_task/run/{run_label}/execution_report.json"
    layout["latest_execution_report"] = f"code_task/run/{run_label}/execution_report.json"
    benchmark = manifest_section(manifest, "benchmark")
    runs = benchmark.get("runs", {})
    if not isinstance(runs, dict):
        runs = {}
    run_record = {
        "label": run_label,
        "status": status,
        "run_at": utcnow_iso(),
        "execution_report": f"code_task/run/{run_label}/execution_report.json",
        "stdout": f"code_task/run/{run_label}/stdout.txt",
        "stderr": f"code_task/run/{run_label}/stderr.txt",
        "metrics": f"code_task/run/{run_label}/metrics.json",
        "returncode": returncode,
        "timed_out": timed_out,
        "metric_values": metric_values,
        "environment": _execution_environment_record(environment_policy),
    }
    existing_record = runs.get(run_label)
    previous_attempts = (
        existing_record.get("attempts", []) if isinstance(existing_record, dict) else []
    )
    previous_attempt_count = (
        int(existing_record.get("attempt_count", 0) or 0) if isinstance(existing_record, dict) else 0
    )
    attempts = [row for row in previous_attempts if isinstance(row, dict)]
    if history_record:
        attempts.append(dict(history_record))
        run_record["latest_attempt"] = history_record["id"]
        run_record["latest_attempt_dir"] = str(history_record["execution_report"]).rsplit("/", 1)[0]
    if attempts:
        run_record["attempts"] = attempts[-50:]
        run_record["attempt_count"] = max(previous_attempt_count + (1 if history_record else 0), len(attempts))
    if run_metadata:
        run_record.update(run_metadata)
    runs[run_label] = run_record
    benchmark.update(
        {
            "command": command_text,
            "executed": True,
            "latest_label": run_label,
            "last_status": status,
            "last_run_at": run_record["run_at"],
            "execution_report": run_record["execution_report"],
            "stdout": run_record["stdout"],
            "stderr": run_record["stderr"],
            "metrics": run_record["metrics"],
            "returncode": returncode,
            "timed_out": timed_out,
            "metric_values": metric_values,
            "runs": runs,
        }
    )
    if run_label == "baseline":
        policy = "provided" if run_record.get("provided") else "run"
        benchmark["baseline_policy"] = {
            "policy": policy,
            "status": "recorded",
            "source": run_record.get("provided_source") or "benchmark_run",
            "updated_at": run_record["run_at"],
        }
    manifest["layout"] = layout
    manifest["benchmark"] = benchmark
    if run_label == "baseline":
        if status == "passed":
            manifest["status"] = "baseline_passed"
        elif status == "blocked_by_validation":
            manifest["status"] = "baseline_blocked"
        else:
            manifest["status"] = "baseline_failed"
    elif status == "passed":
        manifest["status"] = "benchmark_passed"
    elif status == "blocked_by_validation":
        manifest["status"] = "benchmark_blocked"
    else:
        manifest["status"] = "benchmark_failed"
    save_code_task_manifest(run_dir, manifest)

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from simple_ar.integrations.llm import LLMClient, LLMError, LLMSettings


DEFAULT_PROMPT = (
    "Return a compact JSON object with keys status, summary, and checks. "
    "Use status='ok', summary as one short sentence, and checks as a list of three strings."
)


@dataclass(frozen=True)
class ProbeResult:
    mode: str
    attempt: int
    ok: bool
    elapsed_sec: float
    output_chars: int
    error_type: str
    error: str
    preview: str


def main() -> int:
    load_dotenv()
    args = _parse_args()
    console = Console()

    api_key = args.api_key or os.environ.get("OPENAI_API_KEY", "")
    base_url = args.base_url or os.environ.get("OPENAI_BASE_URL", "")
    model = args.model or os.environ.get("SIMPLE_AR_MODEL", "gpt-4o-mini")
    if not api_key:
        console.print("[red]OPENAI_API_KEY is required.[/red]")
        return 2

    prompt = _build_prompt(args)
    modes = _selected_modes(args.modes)
    output_path = Path(args.output).resolve() if args.output else None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("", encoding="utf-8")

    console.rule("[bold cyan]LLM Transport Probe")
    console.print(f"Model: [bold]{model}[/bold]")
    console.print(f"Base URL: [bold]{base_url or '(default provider)'}[/bold]")
    console.print(f"Prompt chars: [bold]{len(prompt)}[/bold]")
    console.print(f"Modes: [bold]{', '.join(modes)}[/bold]")

    rows: list[ProbeResult] = []
    runners = _build_runners(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout=args.timeout,
        max_output_tokens=args.max_output_tokens,
    )
    for attempt in range(1, args.repeat + 1):
        for mode in modes:
            runner = runners[mode]
            result = _run_one(mode, attempt, runner, prompt, expect_json=args.expect_json)
            rows.append(result)
            _append_jsonl(output_path, result)
            _print_result_line(console, result)
            if args.sleep_sec > 0:
                time.sleep(args.sleep_sec)

    _print_summary(console, rows)
    if output_path is not None:
        console.print(f"\nJSONL: [cyan]{output_path}[/cyan]")
    return 0 if any(row.ok for row in rows) else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare Responses API and Chat Completions transport stability.",
    )
    parser.add_argument("--model", default="", help="Model name. Defaults to SIMPLE_AR_MODEL.")
    parser.add_argument("--base-url", default="", help="OpenAI-compatible /v1 base URL.")
    parser.add_argument("--api-key", default="", help="API key. Defaults to OPENAI_API_KEY.")
    parser.add_argument("--repeat", type=int, default=3, help="Number of requests per mode.")
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-request timeout seconds.")
    parser.add_argument("--max-output-tokens", type=int, default=512, help="Output token cap.")
    parser.add_argument("--sleep-sec", type=float, default=0.0, help="Sleep between requests.")
    parser.add_argument(
        "--modes",
        default="litellm-responses,litellm-chat,simple-responses,simple-chat,openai-responses,openai-chat",
        help=(
            "Comma-separated modes: litellm-responses,litellm-chat,"
            "simple-responses,simple-chat,openai-responses,openai-chat,"
            "litellm-responses-stream,litellm-chat-stream,openai-responses-stream,openai-chat-stream."
        ),
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Prompt text.")
    parser.add_argument("--prompt-repeat", type=int, default=1, help="Repeat prompt to simulate larger inputs.")
    parser.add_argument("--prompt-file", default="", help="Optional UTF-8 prompt file.")
    parser.add_argument(
        "--preset",
        choices=("compact", "arc-planning", "code-json"),
        default="compact",
        help="Built-in workload prompt. Ignored when --prompt-file or a custom --prompt is supplied.",
    )
    parser.add_argument("--expect-json", action="store_true", help="Fail a call that returns non-JSON text.")
    parser.add_argument("--output", default="runs/llm_transport_probe/probe.jsonl", help="JSONL output path.")
    return parser.parse_args()


def _selected_modes(raw: str) -> list[str]:
    allowed = {
        "litellm-responses",
        "litellm-chat",
        "simple-responses",
        "simple-chat",
        "openai-responses",
        "openai-chat",
        "litellm-responses-stream",
        "litellm-chat-stream",
        "openai-responses-stream",
        "openai-chat-stream",
    }
    modes = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = [mode for mode in modes if mode not in allowed]
    if unknown:
        raise SystemExit(f"Unknown mode(s): {', '.join(unknown)}")
    return modes or sorted(allowed)


def _load_prompt(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _build_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        return _load_prompt(args.prompt_file)
    prompt = args.prompt
    if prompt == DEFAULT_PROMPT:
        prompt = _preset_prompt(args.preset)
    return _sized_prompt(prompt, args.prompt_repeat)


def _sized_prompt(prompt: str, repeat: int) -> str:
    repeat = max(1, repeat)
    return "\n\n".join(prompt for _ in range(repeat))


def _preset_prompt(name: str) -> str:
    if name == "arc-planning":
        return (
            "Return valid JSON only. Do not include Markdown. Build a realistic greenfield "
            "experiment architecture plan for a medium machine-learning benchmark. The JSON "
            "object must contain keys: objective, assumptions, shared_schemas, files, "
            "execution_plan, validation_plan, risks. Include exactly 12 files. Each file must "
            "be an object with path, purpose, dependencies, public_api, acceptance_criteria, "
            "and entrypoint. Use paths relative to the generated project root, with main.py "
            "as the only entrypoint. Define shared schemas for DatasetSpec, ConditionRecord, "
            "MetricSummary, and ArtifactPaths. Make dependencies internally consistent: every "
            "cross-file call must refer to a public_api entry declared by the dependency file. "
            "The experiment must compare multiple preprocessing or modeling conditions, write "
            "artifacts/results.json and artifacts/report.md, include deterministic seeds, and "
            "validate that result records are non-empty. The response should be detailed enough "
            "to guide one-file-at-a-time code generation."
        )
    if name == "code-json":
        return (
            "Return valid JSON only. Do not include Markdown. Generate one Python module as a "
            "JSON object with keys path, role, imports, public_api, content, and self_checks. "
            "The content value must be a complete Python source file of roughly 180 to 260 "
            "lines implementing dataset loading, condition expansion, metric computation, "
            "artifact writing, and CLI-friendly error messages. Avoid placeholders and keep "
            "function names consistent with public_api."
        )
    return DEFAULT_PROMPT


def _build_runners(
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float,
    max_output_tokens: int,
) -> dict[str, Callable[[str], str]]:
    return {
        "litellm-responses": lambda prompt: _litellm_responses_call(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            max_output_tokens=max_output_tokens,
            prompt=prompt,
            stream=False,
        ),
        "litellm-chat": lambda prompt: _litellm_chat_call(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            max_output_tokens=max_output_tokens,
            prompt=prompt,
            stream=False,
        ),
        "litellm-responses-stream": lambda prompt: _litellm_responses_call(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            max_output_tokens=max_output_tokens,
            prompt=prompt,
            stream=True,
        ),
        "litellm-chat-stream": lambda prompt: _litellm_chat_call(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            max_output_tokens=max_output_tokens,
            prompt=prompt,
            stream=True,
        ),
        "simple-responses": lambda prompt: _simple_ar_call(
            api_key=api_key,
            base_url=base_url,
            model=model,
            api_mode="responses",
            timeout=timeout,
            max_output_tokens=max_output_tokens,
            prompt=prompt,
        ),
        "simple-chat": lambda prompt: _simple_ar_call(
            api_key=api_key,
            base_url=base_url,
            model=model,
            api_mode="chat",
            timeout=timeout,
            max_output_tokens=max_output_tokens,
            prompt=prompt,
        ),
        "openai-responses": lambda prompt: _openai_responses_call(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            max_output_tokens=max_output_tokens,
            prompt=prompt,
            stream=False,
        ),
        "openai-chat": lambda prompt: _openai_chat_call(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            max_output_tokens=max_output_tokens,
            prompt=prompt,
            stream=False,
        ),
        "openai-responses-stream": lambda prompt: _openai_responses_call(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            max_output_tokens=max_output_tokens,
            prompt=prompt,
            stream=True,
        ),
        "openai-chat-stream": lambda prompt: _openai_chat_call(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            max_output_tokens=max_output_tokens,
            prompt=prompt,
            stream=True,
        ),
    }


def _simple_ar_call(
    *,
    api_key: str,
    base_url: str,
    model: str,
    api_mode: str,
    timeout: float,
    max_output_tokens: int,
    prompt: str,
) -> str:
    client = LLMClient(
        LLMSettings(
            model=model,
            api_key=api_key,
            base_url=base_url,
            request_timeout_sec=timeout,
            max_output_tokens=max_output_tokens,
            retry_attempts=1,
            api_mode=api_mode,
            json_response_format="off",
        )
    )
    return client.ask(_system_prompt(), prompt, max_output_tokens=max_output_tokens, label=f"probe-{api_mode}")


def _litellm_model(model: str, base_url: str) -> str:
    return f"openai/{model}" if base_url and "/" not in model else model


def _litellm_responses_call(
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float,
    max_output_tokens: int,
    prompt: str,
    stream: bool,
) -> str:
    import litellm

    request = {
        "model": _litellm_model(model, base_url),
        "instructions": _system_prompt(),
        "input": [{"role": "user", "content": prompt}],
        "api_key": api_key,
        "timeout": timeout,
        "max_output_tokens": max_output_tokens,
        "stream": stream,
    }
    if base_url:
        request["api_base"] = base_url
        request["base_url"] = base_url
    response = litellm.responses(**request)
    return _extract_stream_text(response) if stream else _extract_response_text(response)


def _litellm_chat_call(
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float,
    max_output_tokens: int,
    prompt: str,
    stream: bool,
) -> str:
    import litellm

    request = {
        "model": _litellm_model(model, base_url),
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": prompt},
        ],
        "api_key": api_key,
        "timeout": timeout,
        "max_tokens": max_output_tokens,
        "stream": stream,
    }
    if base_url:
        request["api_base"] = base_url
        request["base_url"] = base_url
    response = litellm.completion(**request)
    return _extract_stream_text(response) if stream else _extract_response_text(response)


def _openai_responses_call(
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float,
    max_output_tokens: int,
    prompt: str,
    stream: bool,
) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=timeout)
    response = client.responses.create(
        model=model,
        instructions=_system_prompt(),
        input=[{"role": "user", "content": prompt}],
        max_output_tokens=max_output_tokens,
        stream=stream,
    )
    return _extract_stream_text(response) if stream else str(getattr(response, "output_text", "") or "")


def _openai_chat_call(
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float,
    max_output_tokens: int,
    prompt: str,
    stream: bool,
) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=timeout)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_output_tokens,
        stream=stream,
    )
    if stream:
        return _extract_stream_text(response)
    choice = response.choices[0] if response.choices else None
    message = getattr(choice, "message", None)
    return str(getattr(message, "content", "") or "")


def _extract_response_text(response: object) -> str:
    choices = _get_value(response, "choices")
    if isinstance(choices, list) and choices:
        message = _get_value(choices[0], "message")
        content = _get_value(message, "content") if message is not None else None
        if content is not None:
            return str(content)
    output_text = _get_value(response, "output_text")
    if output_text is not None:
        return str(output_text)
    output = _get_value(response, "output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            content = _get_value(item, "content")
            if isinstance(content, list):
                for chunk in content:
                    text = _get_value(chunk, "text")
                    if text is None:
                        text = _get_value(chunk, "content")
                    if text is not None:
                        parts.append(str(text))
        if parts:
            return "\n".join(parts)
    return ""


def _extract_stream_text(stream: object) -> str:
    parts: list[str] = []
    for chunk in stream:  # type: ignore[operator]
        text = _stream_chunk_text(chunk)
        if text:
            parts.append(text)
    return "".join(parts)


def _stream_chunk_text(chunk: object) -> str:
    event_type = str(_get_value(chunk, "type") or "")
    if event_type.endswith(".delta") or event_type in {"response.output_text.delta", "output_text.delta"}:
        delta = _get_value(chunk, "delta")
        if delta is not None:
            return str(delta)

    choices = _get_value(chunk, "choices")
    if isinstance(choices, list) and choices:
        choice = choices[0]
        delta_obj = _get_value(choice, "delta")
        delta_text = _get_value(delta_obj, "content") if delta_obj is not None else None
        if delta_text is not None:
            return str(delta_text)
        text = _get_value(choice, "text")
        if text is not None:
            return str(text)

    content = _get_value(chunk, "content")
    if isinstance(content, list):
        return "".join(_message_content_text(item) for item in content)
    if content is not None and not isinstance(content, (dict, list)):
        return str(content)

    output_text = _get_value(chunk, "output_text")
    if output_text is not None:
        return str(output_text)
    return ""


def _get_value(obj: object, name: str) -> object | None:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _system_prompt() -> str:
    return "You are a transport probe. Return concise plain text or JSON only."


def _run_one(
    mode: str,
    attempt: int,
    runner: Callable[[str], str],
    prompt: str,
    *,
    expect_json: bool,
) -> ProbeResult:
    start = time.perf_counter()
    try:
        output = runner(prompt)
    except Exception as exc:
        return ProbeResult(
            mode=mode,
            attempt=attempt,
            ok=False,
            elapsed_sec=round(time.perf_counter() - start, 3),
            output_chars=0,
            error_type=type(exc).__name__,
            error=_compact_error(exc),
            preview="",
        )
    output = output.strip()
    if expect_json:
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError as exc:
            return ProbeResult(
                mode=mode,
                attempt=attempt,
                ok=False,
                elapsed_sec=round(time.perf_counter() - start, 3),
                output_chars=len(output),
                error_type="InvalidJSON",
                error=_compact_error(exc),
                preview=output.replace("\n", " ")[:180],
            )
        if not isinstance(parsed, dict):
            return ProbeResult(
                mode=mode,
                attempt=attempt,
                ok=False,
                elapsed_sec=round(time.perf_counter() - start, 3),
                output_chars=len(output),
                error_type="NonObjectJSON",
                error="Response parsed as JSON but was not an object.",
                preview=output.replace("\n", " ")[:180],
            )
    return ProbeResult(
        mode=mode,
        attempt=attempt,
        ok=bool(output),
        elapsed_sec=round(time.perf_counter() - start, 3),
        output_chars=len(output),
        error_type="" if output else "EmptyOutput",
        error="" if output else "The call returned an empty response body.",
        preview=output.replace("\n", " ")[:180],
    )


def _compact_error(exc: Exception) -> str:
    text = str(exc).replace("\n", " ")
    return " ".join(text.split())[:500]


def _append_jsonl(path: Path | None, result: ProbeResult) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")


def _print_result_line(console: Console, result: ProbeResult) -> None:
    status = "[green]OK[/green]" if result.ok else "[red]FAIL[/red]"
    detail = result.preview if result.ok else f"{result.error_type}: {result.error}"
    console.print(
        f"{status} [cyan]{result.mode}[/cyan] #{result.attempt} "
        f"{result.elapsed_sec:.2f}s chars={result.output_chars} {detail}"
    )


def _print_summary(console: Console, rows: list[ProbeResult]) -> None:
    table = Table(title="Transport Summary")
    table.add_column("Mode", style="cyan")
    table.add_column("OK", justify="right", style="green")
    table.add_column("Fail", justify="right", style="red")
    table.add_column("Avg sec", justify="right")
    table.add_column("Common error")
    for mode in sorted({row.mode for row in rows}):
        items = [row for row in rows if row.mode == mode]
        ok_count = sum(1 for row in items if row.ok)
        fail_count = len(items) - ok_count
        avg = sum(row.elapsed_sec for row in items) / len(items)
        errors = [row.error for row in items if row.error]
        table.add_row(mode, str(ok_count), str(fail_count), f"{avg:.2f}", errors[0] if errors else "-")
    console.print()
    console.print(table)


if __name__ == "__main__":
    raise SystemExit(main())

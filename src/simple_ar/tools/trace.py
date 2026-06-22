from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from simple_ar.core.artifacts import append_jsonl
from simple_ar.tools.specs import CommonToolSpec, ToolCall, ToolResult


@dataclass
class ToolTraceWriter:
    """Append compact tool-call traces without storing large payloads by default."""

    path: Path
    debug_payloads: bool = False

    def append(
        self,
        *,
        call: ToolCall,
        result: ToolResult,
        spec: CommonToolSpec | None = None,
        duration_sec: float | None = None,
    ) -> None:
        row: dict[str, Any] = {
            "schema_version": "tool_trace.v2",
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tool": call.tool_name,
            "caller": call.caller,
            "trace_id": call.trace_id,
            "domain": spec.domain if spec else "",
            "permission": spec.permission_level if spec else "",
            "risk": spec.risk_level if spec else "",
            "status": result.status,
            "duration_sec": round(duration_sec, 6) if duration_sec is not None else None,
            "input_summary": _summary(call.arguments),
            "output_summary": _summary(result.data),
            "artifacts": list(result.artifacts),
            "error": result.error,
        }
        if self.debug_payloads:
            row["debug"] = {"arguments": call.arguments, "data": result.data}
        append_jsonl(self.path, row)


class tool_timer:
    """Small context helper for measuring local tool calls."""

    def __enter__(self) -> "tool_timer":
        self.started = time.monotonic()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.duration_sec = time.monotonic() - self.started


def _summary(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        keys = sorted(str(key) for key in value.keys())
        return {"type": "dict", "keys": keys[:20], "key_count": len(keys)}
    if isinstance(value, list):
        return {"type": "list", "length": len(value)}
    if value is None:
        return {"type": "none"}
    return {"type": type(value).__name__}

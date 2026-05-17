from __future__ import annotations

import re


METRIC_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9_.-]*)\s*:\s*(-?(?:\d+(?:\.\d*)?|\.\d+))\s*$"
)


def parse_metric_lines(text: str) -> dict[str, float]:
    """Parse ``name: value`` metric lines from captured stdout.

    Args:
        text: Captured stdout from an experiment or benchmark script.

    Returns:
        Mapping of metric names to numeric values. Non-numeric lines are
        ignored so scripts can also print human-readable status messages.
    """
    metrics: dict[str, float] = {}
    for line in text.splitlines():
        match = METRIC_RE.match(line)
        if match is None:
            continue
        metrics[match.group(1)] = float(match.group(2))
    return metrics

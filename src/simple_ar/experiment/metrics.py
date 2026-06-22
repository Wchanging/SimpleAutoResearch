from __future__ import annotations

import re


NUMBER_PATTERN = r"-?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
METRIC_RE = re.compile(
    rf"^\s*(?:METRIC\s+)?([A-Za-z][A-Za-z0-9_.-]*)\s*(?::|=)\s*({NUMBER_PATTERN})\s*$"
)


def parse_metric_lines(text: str) -> dict[str, float]:
    """Parse stable numeric metric lines from captured stdout.

    Supported formats are ``name: value`` and ``METRIC name=value``.

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

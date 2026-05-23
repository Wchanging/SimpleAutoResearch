from __future__ import annotations


def progress_line(step: int, total: int, *, width: int, message: str) -> str:
    """Render a simple newline progress bar that works in captured logs."""

    if total < 1:
        total = 1
    step = max(0, min(step, total))
    filled = int(width * step / total)
    bar = "#" * filled + "." * (width - filled)
    percent = 100 * step / total
    return f"round {step}/{total} [{bar}] {percent:5.1f}% {message}"

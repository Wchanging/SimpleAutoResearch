from __future__ import annotations

def style_progress_message(message: str) -> str:
    """Return the Rich style for one progress line.

    Keep CodeTask CLI progress readable without a separate event framework.
    """

    lower = message.lower()
    if "failed" in lower or "error" in lower:
        return "bright_red"
    if "llm usage" in lower:
        return "gold1"
    if "calling llm" in lower:
        return "orchid1"
    if "dependency advice" in lower:
        if "missing" in lower:
            return "bright_yellow"
        if "installed" in lower:
            return "spring_green2"
        return "deep_sky_blue1"
    if "search" in lower or "retriev" in lower or "arxiv" in lower or "openalex" in lower:
        return "deep_sky_blue1"
    if "rate limit" in lower or "fallback" in lower or "warning" in lower or "skipped" in lower:
        return "bright_yellow"
    if "archived" in lower:
        return "bright_yellow"
    if "running" in lower or "generating" in lower or "building" in lower or "writing" in lower:
        return "dodger_blue1"
    return "white"

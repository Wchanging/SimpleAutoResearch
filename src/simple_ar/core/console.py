from __future__ import annotations

import sys
from typing import Any

from rich.console import Console


def make_console(*, file: Any | None = None) -> Console:
    """Return a Windows-safe Rich console used by CLI output paths."""

    return Console(
        file=file or sys.stdout,
        highlight=False,
        soft_wrap=True,
        legacy_windows=False,
        emoji=False,
    )


def print_line(message: object = "", **kwargs: Any) -> None:
    """Print one CLI line through the shared Rich console.

    The wrapper keeps Rich adoption incremental: call sites get better Unicode
    handling and future styling hooks without requiring a full CLI/TUI
    migration in one refactor.
    """

    console = make_console(file=sys.stdout)
    console.print(str(message), markup=False, highlight=False, **kwargs)

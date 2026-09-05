from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["main"]


def _entrypoint(*args: Any, **kwargs: Any) -> Any:
    """Dispatch to the CLI implementation without importing it at package load."""

    module = import_module("simple_ar.cli.main")
    return module.main(*args, **kwargs)


main = _entrypoint


def __getattr__(name: str) -> Any:
    """Keep the console-script export without eagerly importing ``main``.

    Eagerly importing the module here makes ``python -m simple_ar.cli.main``
    execute a module that is already present in ``sys.modules`` and emits a
    runpy warning.  Lazy lookup preserves the historical package import while
    keeping the module entry point clean.
    """

    module = import_module("simple_ar.cli.main")
    # Importing a child module assigns it to the parent package under the
    # same name. Restore the console-script callable for compatibility
    # with ``from simple_ar.cli import main``.
    globals()["main"] = _entrypoint
    return getattr(module, name)

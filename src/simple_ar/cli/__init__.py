"""CLI package. The callable entry point is ``simple_ar.cli.main:main``.

Keeping the package free of same-name re-exports avoids import-order collisions
with the ``main`` submodule, and keeps ``python -m simple_ar.cli.main`` lazy.
"""

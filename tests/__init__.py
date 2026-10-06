"""Offline test package; shared fixtures resolve without PYTHONPATH overrides."""

from contextlib import contextmanager


@contextmanager
def captured_data_figures():
    """Inspect public plotted artists while retaining real file exports."""
    from unittest.mock import patch
    from matplotlib.figure import Figure
    from pathlib import Path
    saved = []
    savefig = Figure.savefig

    def capture(figure, path, *args, **kwargs):
        if kwargs.get("format") == "svg" or (isinstance(path, (str, Path)) and Path(path).suffix == ".svg"):
            saved.append(figure)
        return savefig(figure, path, *args, **kwargs)

    with patch.object(Figure, "savefig", capture):
        yield saved

# Paired observations (no API)

This complete case uses artificial matched timings, not a benchmark or scientific
result. Run from the repository root:

```bash
uv run simple-ar research-session --config examples/data-paired/research.toml \
  --interaction autonomous
```

Outputs under `runs/data-paired/` include copied input, `analysis.json`, a readable
report, editable SVGs, vector PDFs and PNG previews. Differences are candidate minus baseline: A has three
pairs, mean −5/3 seconds and SE 1/3; B has two pairs, mean 2 seconds and SE 1.
Two B rows are jointly omitted. Subtracting separately filtered means would be wrong.
The difference figure shows ±1 SE assuming independent pairs, not a confidence
interval or significance test. Smaller timings alone do not establish a valid method.
Set `plot = "box"` in `[analysis]` to show each method's distribution while
retaining the separate matched-difference panel; default `bar` shows means only.

Use the completed `analysis.json` as `start --kind writing --material PATH`; keep
its copied input beside it. That separate writing task requires a model. Resume
an existing session with its printed `--session-root`, not by rerunning this case.

[中文](README_zh.md) · [Analysis settings](../../docs/CONFIG_REFERENCE.md#existing-data-descriptive-analysis)

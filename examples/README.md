# Examples

## Research Brief

Build a small evidence-backed brief from a topic and local Markdown/TXT input:

```bash
uv run simple-ar research-brief --topic "reliable agents" \
  --local-document examples/research_brief/fixtures/reliable_agents.md \
  --output-root runs/research-brief
```

The session keeps plan, search, document-ingest, and brief handoffs in separate
attempt directories. The fixture is intentionally small and offline.

SimpleAutoResearch keeps a small set of public example entrypoints. Each one mirrors
a common user workflow and keeps its config next to the project or task it
drives.

```text
examples/
  research_brief/
    fixtures/reliable_agents.md       offline input for the brief example

  research_report/
    configs/research_report.toml    search -> read -> synthesize -> report

  code_task_medium_review/
    configs/code_task.toml          standalone code-task workflow
    project/                        editable example repository
    task.md                         requested code change

  full_pipeline_tiny_mlp/
    configs/pipeline.toml           full 8-stage pipeline with embedded code-task
    project/                        editable example repository
    task.md                         embedded code-task request

  greenfield_lightweight_training/
    configs/greenfield_training.toml full 8-stage greenfield generation workflow
    task.md                         from-scratch local training task

  code_task_greenfield_ml_suite/
    configs/code_task.toml          larger standalone greenfield code-task
    task.md                         server-oriented ML workbench task

  capability_package_minimal/
    README.md                       smallest replaceable capability boundary
    capability.py                   context -> artifact -> result example
```

Use `research_report` when you want a research-only survey, `code_task_medium_review`
when you want to test automated code edits in an isolated workspace, and
`full_pipeline_tiny_mlp` when you want the whole pipeline around an existing
project. Use `greenfield_lightweight_training` when you want a bounded from-zero
implementation task that exercises a medium-light CPU-only experiment suite with
multiple model conditions, parseable metrics, review, and run diagnosis.
Use `code_task_greenfield_ml_suite` when you want a larger pure code-task
greenfield acceptance run on a stronger local machine or server.

Use `capability_package_minimal` when adding a replaceable V2.8 capability. It
is offline, has no domain-specific schema, and demonstrates the expected
`CapabilityContext` -> `ArtifactStore` -> `CapabilityResult` handoff. Its
contract test is included in `uv run simple-ar-checks core`.

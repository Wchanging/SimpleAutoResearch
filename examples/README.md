# Examples

SimpleAutoResearch keeps a small set of public example entrypoints. Each one mirrors
a common user workflow and keeps its config next to the project or task it
drives.

```text
examples/
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
```

Use `research_report` when you want a research-only survey, `code_task_medium_review`
when you want to test automated code edits in an isolated workspace, and
`full_pipeline_tiny_mlp` when you want the whole pipeline around an existing
project. Use `greenfield_lightweight_training` when you want a bounded from-zero
implementation task that exercises a medium-light CPU-only experiment suite with
multiple model conditions, parseable metrics, review, and run diagnosis.

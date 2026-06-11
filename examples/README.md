# Examples

SimpleAutoResearch keeps only three public example entrypoints. Each one mirrors
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
```

Use `research_report` when you want a research-only survey, `code_task_medium_review`
when you want to test automated code edits in an isolated workspace, and
`full_pipeline_tiny_mlp` when you want the whole pipeline from plan to report.

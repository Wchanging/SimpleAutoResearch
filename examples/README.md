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

After a `research-session` reaches `ready_for_report`, continue it through the
existing report Writer/Reviewer and audit boundary:

```bash
uv run simple-ar research-report \
  --session-root runs/research-session/<session> \
  --model gpt-5.4
```

For a new session, `research-session --with-report` is the equivalent explicit
one-command continuation; it still uses the same persisted attempts and does
not introduce an automatic retry loop.

The same session can use the existing Code-Task backend for its experiment
attempt. Omit `--command`, pass a Code-Task TOML, and provide `--model`; the
session keeps the Code-Task workspace and canonical result under
`experiment-001` while reusing the normal Analysis handoff:

```bash
uv run simple-ar research-session \
  --topic "reliable agents" \
  --local-document examples/research_brief/fixtures/reliable_agents.md \
  --code-task-config examples/code_task_medium_review/configs/code_task.toml \
  --model gpt-5.4 \
  --output-root runs/research-session
```

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

For the first bounded research-to-code consumer, run `research-brief` first and
pass its `research_brief.json` to `research-code-task` together with an existing
Code-Task TOML. The latter reuses the normal isolated Code-Task backend and keeps
execution and analysis artifacts in a new session. Use `--max-candidates N` only
for an explicit bounded comparison of several synthesis ideas; add `--with-report`
to pass only the selected successful candidate to the existing report/audit path.

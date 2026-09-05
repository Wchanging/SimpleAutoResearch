# Examples

## Research Brief

Build a small evidence-backed brief from a topic and local Markdown/TXT input:

```bash
uv run simple-ar research-brief --topic "reliable agents" \
  --local-document examples/research_brief/fixtures/reliable_agents.md \
  --output-root runs/research-brief
```

The session keeps plan, search, document-ingest, read, and synthesis handoffs in
separate attempt directories. The fixture is intentionally small and offline.

After a `research-session` reaches `ready_for_report`, continue it through the
existing report Writer/Reviewer and audit boundary:

```bash
uv run simple-ar research-report \
  --session-root runs/research-session/<session> \
  --model "$SIMPLE_AR_MODEL"
```

For a new model-backed session, `research-session` now appends the report and
audit by default, so the command below is the explicit equivalent. Use
`--no-report` when inspecting only the research and experiment handoff; it
does not introduce an automatic retry loop.

For a laptop-safe complete smoke, run the checked-in example below. It uses
the local fixture and a one-line experiment, but still writes the complete
session through report and audit:

```bash
uv run python examples/research_session_smoke.py
```

```bash
SIMPLE_AR_LLM_RETRY_ATTEMPTS=1 SIMPLE_AR_LLM_TIMEOUT_SEC=90 \
uv run simple-ar research-session \
  --topic "reliable agents" \
  --local-document examples/research_brief/fixtures/reliable_agents.md \
  --model "$SIMPLE_AR_MODEL" \
  --with-report \
  --output-root runs/research-session \
  --command python -c "print('accuracy: 0.75')"
```

For a low-budget online smoke, replace the local-document option with one
provider result and keep the experiment command deterministic:

```bash
SIMPLE_AR_LLM_RETRY_ATTEMPTS=1 SIMPLE_AR_LLM_TIMEOUT_SEC=90 \
uv run simple-ar research-session \
  --topic "lightweight language model agents" \
  --query "large language model agents" \
  --provider arxiv \
  --max-results 1 \
  --max-chunks 5 \
  --idea-limit 1 \
  --timeout-sec 90 \
  --model "$SIMPLE_AR_MODEL" \
  --report-reviewer disabled \
  --max-review-iterations 0 \
  --output-root runs/research-session-online-smoke \
  --command python -c "print('accuracy: 0.75')"
```

The model name must be available from the configured OpenAI-compatible
gateway. This command makes a bounded sequence of planning, reading,
synthesis, design, analysis, and report calls; a gateway/model error is a failed smoke,
not permission to silently switch to fixture output.

The same session can use the existing Code-Task backend for its experiment
attempt. Omit `--command`, pass a Code-Task TOML, and provide `--model`; the
session keeps the Code-Task workspace and canonical result under
`experiment-001` while reusing the normal Analysis handoff:

```bash
uv run simple-ar research-session \
  --topic "reliable agents" \
  --local-document examples/research_brief/fixtures/reliable_agents.md \
  --code-task-config examples/code_task_medium_review/configs/code_task.toml \
  --model "$SIMPLE_AR_MODEL" \
  --output-root runs/research-session
```

The embedded bridge merges a strict dependent work-plan chain into one bounded
batch when the implementation, wiring, and configuration must land together.
That can require the `large` edit budget. For a trusted isolated project, set
`[execute].allow_large_edits = true` in the Code-Task TOML only after reviewing
the task scope; otherwise the session preserves its Code-Task artifacts and
stops at the explicit large-edit approval boundary.

## AutoDL / 3090 low-resource acceptance

When a GPU server is available, validate in this order; do not start with
long training runs or candidate batches:

1. Record `nvidia-smi`, Python/uv versions, the repository commit, and the
   dataset/project paths.
2. Run `research_session_smoke.py` first to verify the environment, artifacts,
   and report writes.
3. Run the low-budget online smoke: one provider, one result, at most 5
   chunks, one idea, one LLM retry, a 90-second timeout, and no reviewer
   iterations.
4. Use the prepared `code_task_medium_review` project for one CodeTask
   direction, and verify that baseline, constrained edits, validation,
   experiment, analysis, and report/audit all land in the session.
5. Only after that path is stable, connect real data/models with a small batch
   and a few epochs; keep the complete session directory as the reproduction
   record.

V2.8 does not request GPUs, manage training queues, or schedule parallel
candidates. GPU use is only for validating a real user project and a
low-resource experiment. Model calls, timeouts, repair rounds, and output
roots must remain explicit. If online smoke fails at `plan`, preserve the
failed artifacts and fix the model/gateway configuration; never substitute
fixture output for a real closed loop.

For a Linux/AutoDL server, the checked-in helper records non-secret
environment information and runs the local fixture by default:

```bash
uv sync --frozen
bash examples/autodl_low_resource_smoke.sh
```

Enable the bounded network/LLM run only after setting a valid model and
`OPENAI_API_KEY`:

```bash
SIMPLE_AR_RUN_ONLINE=1 \
SIMPLE_AR_AUTODL_OUTPUT_ROOT=runs/autodl-online \
bash examples/autodl_low_resource_smoke.sh
```

Add `SIMPLE_AR_RUN_CODE_TASK=1` to also run the prepared single-project
research-to-CodeTask path. The helper never stores the API key and exits
before any LLM call when the model or key is missing.

SimpleAutoResearch keeps a small set of public example entrypoints. Each one mirrors
a common user workflow and keeps its config next to the project or task it
drives.

```text
examples/
  autodl_low_resource_smoke.sh          Linux/AutoDL bounded acceptance helper

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
pass its `synthesis_result.json` to `research-code-task` together with an existing
Code-Task TOML. The latter reuses the normal isolated Code-Task backend and keeps
execution and analysis artifacts in a new session. The V2.8 path intentionally
runs one selected direction; multi-candidate comparison is deferred until this
single-direction path is validated on a real prepared project. Add `--with-report`
to pass the successful session to the existing report/audit path.

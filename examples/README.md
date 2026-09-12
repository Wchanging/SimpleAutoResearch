# Examples

V2.8 的正式用户主线是 `research-session`，它把检索、阅读、研究设计、代码/实验、分析、
报告和审计保留在同一个 session 中。下面的 `research-brief`
主要用于分段开发、诊断。旧 `research-experiment`、`research-code-task` 和 `simple-ar run/resume`
命令已退出；尚存的旧八阶段配置只供历史参考，不再是可运行示例，后续清理随消费者一起完成。

## New ResearchApplication live check (development)

The new application can be checked separately from the compatibility CLI:

```bash
uv run --no-sync python examples/research_application_live.py --session runs/application-live-check
```

This uses the configured `.env` provider and real OpenAlex, Semantic Scholar,
and arXiv search with provider failures retained, retaining up to three papers.
It spends API budget: at most 40 provider requests / 160k tokens,
45 seconds per request and two attempts. No GPU or training is started by default.
If the provider is temporarily slow during planning, add
`--deterministic-plan` to skip only that LLM call; later network retrieval,
reading, synthesis, and report stages still use the configured LLM. The
session records this explicit planning mode in its runtime configuration.
`--dataset PATH` adds the small `text,label,split` CSV baseline (20 seconds, one
process); use a matching `--topic`. It does not exercise CodeTask or replace GPU
acceptance. Sources may be abstract-only; the report must disclose that limitation.

`--medium-review` instead reuses `code_task_medium_review/project` and its task:
the application copies it, measures a baseline, invokes CodeTask, measures the
candidate and permits at most one repair/retest before analysis and report.
The low-resource report recipe uses one Reviewer pass per section; increase the
report review setting only when the session budget is deliberately raised.
Its budget is eight process invocations / 120 process-wall seconds (20 seconds
per experiment); the API cap is 40 requests / 320k tokens for the combined
CodeTask and full report. This is a tiny
weighted-feature engineering check (6 train / 14 evaluation examples), not a
realistic research dataset or a demonstrated full-loop success. Data, evaluator,
entrypoint and tests are protected; only the isolated implementation/config can
change. The existing task forbids changing evaluation examples to inflate scores.

`--cifar10` starts the prepared three-seed CIFAR-10 application after the data
and interpreter are prepared. It uses the same ResearchApplication runner and
Rich progress output; it does not download data or install packages:

```bash
uv run --no-sync python examples/research_application_live.py --cifar10 \
  --session runs/cifar10-live --python runs/cifar-cuda-env/bin/python \
  --data-root runs/assets/cifar10 --epochs 1 --device cuda --max-actions 1
```

Use `--resume --session runs/cifar10-live` to continue the persisted session.
Freeze the epoch count only after the short device probe and keep `--max-actions`
small while checking the plan; the configured process budget is consumed only
when experiment actions actually start.

Use `--max-actions 1` for a single application action. Resume the same session
with `--resume`; add `--retry` only when explicitly retrying a paused action.
For slow providers, `--request-timeout 90` adjusts the per-request deadline;
it does not reset or increase the session's request/token budget. The default
remains 45 seconds. Longer deadlines do not remedy invalid evidence references.
`--max-output-tokens 1200` can lower the per-request response cap for a slow or
reasoning-heavy gateway; it changes neither the session budget nor the evidence
validation rules. The default remains 2400.
Do not change the framework mid-run and call the result a clean acceptance run.

## Research Brief

Build a small evidence-backed brief from a topic and local Markdown/TXT input:

```bash
uv run simple-ar research-brief --topic "reliable agents" \
  --local-document examples/research_brief/fixtures/reliable_agents.md \
  --output-root runs/research-brief
```

The session keeps plan, search, document-ingest, read, and synthesis handoffs in
separate attempt directories. The fixture is intentionally small and offline.

For a session created with `--no-report`, continue it through the canonical
report Writer/Reviewer and audit boundary without rerunning research or the
experiment:

```bash
uv run simple-ar research-report \
  --session-root runs/research-session/<session> \
  --model "$SIMPLE_AR_MODEL"
```

For a new model-backed session, `research-session` includes the report and
audit by default, so the command below is the explicit one-command flow. Use
`--no-report` when inspecting only the research and experiment handoff; it
does not introduce an automatic retry loop.

For literature-only use, omit both `--command` and `--code-task-config`. Without
`--model` this writes the evidence-backed summary and exits without launching a
process; with `--model` it writes the research-only report path.

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
session keeps the Code-Task workspace under its preparation/implementation
attempts while reusing the normal canonical Analysis handoff:

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

The prepared [CIFAR-10 calibration baseline](cifar10_calibration/README.md) defines
the planned user-scale GPU task's shared data, protected evaluator and editable
method. Its new scripts still require CUDA/Linux dependency locking, real CPU
training confirmation and live three-seed matrix acceptance; they are not a
completed GPU acceptance.

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

For the current V2.8 normal-user-scale acceptance, use the prepared
[CIFAR-10 calibration baseline](cifar10_calibration/README.md): a fixed low-data
image-classification task with an editable method, protected evaluator and
paired three-seed measurements. The target is approximately 30--50 raw
literature records, 10--20 bounded Read candidates, one constrained method
change, the baseline/candidate matrix and the full Markdown `experiment`
report profile. Start with the CPU/data path; use the 3090 only for the bounded
CUDA probe and frozen matrix after that path is known to work.

The `code_task_digits_mlp` / `load_digits` project is a cheap standalone CPU
coding example, not the GPU acceptance direction or an eight-stage pipeline.

This scale acceptance has been completed once on AutoDL with the prepared
project: v13 retained 60 raw records and 10 selected documents, used the real
`gpt-5.4-mini + chat` path, ran the bounded Code-Task baseline/modified
experiment and analysis, and produced a `completed` session with a full
Markdown report and passing citation/metric/claim audit. The provider returned
60 raw records for `--max-results 10`; the exact count is kept in the session
artifacts rather than silently clipped to the approximate target.

The prepared-project path keeps research scope, data, dependencies, and code
permissions explicit. If a real task lacks one of those inputs, prepare or
update the project/configuration before continuing from its persisted boundary;
this is an input contract, not a separate human-handoff subsystem. Do not ask
the LLM to silently download or install arbitrary resources.

V2.8 does not request GPUs, manage training queues, or schedule parallel
candidates. GPU use is only for validating a real user project and a
low-resource experiment. Model calls, timeouts, repair rounds, and output
roots must remain explicit. If online smoke fails at `plan`, preserve the
failed artifacts and fix the model/gateway configuration; never substitute
fixture output for a real closed loop.

For a Linux/AutoDL server, the checked-in helper records non-secret
environment information (including untracked-code dirty state) and runs a focused
CPU preflight by default: real process accounting, new-application report,
baseline recovery, code modification and bounded repair. Model responses in this
preflight are fixtures; passing it is not live research acceptance. CUDA is hidden
and numerical-library threads are limited to two. No GPU rental is needed.

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

`SIMPLE_AR_RUN_ONLINE=1` runs the new application's literature/report path,
without synthetic experiment metrics. Set `SIMPLE_AR_RUN_CODE_TASK=1` for the
isolated medium-review baseline/CodeTask/candidate/report path instead (or both
flags to request two separate sessions). Export the model and API key in the
shell; the helper never stores the key and stops before model calls if absent.
The general online session retains the example's 40-request/160k-token ceiling;
the bundled medium-review CodeTask path uses a separate 320k-token ceiling for
its full implementation plus sectioned experiment report. Neither ceiling is
a guarantee that an arbitrary full paper fits. A pause or budget stop is not acceptance.
Do not rerun the helper to erase a failed run: inspect its artifacts and explicitly
resume with `research_application_live.py --session <path> --resume --retry`.
The medium-review data is a small engineering fixture, not user-scale GPU research.

SimpleAutoResearch keeps a small set of public example entrypoints. Each one mirrors
a common user workflow and keeps its config next to the project or task it
drives.

```text
examples/
  autodl_low_resource_smoke.sh          Linux/AutoDL bounded acceptance helper

  research_brief/
    fixtures/reliable_agents.md       offline input for the brief example

  code_task_medium_review/
    configs/code_task.toml          standalone code-task workflow
    project/                        editable example repository
    task.md                         requested code change

  code_task_digits_mlp/
    configs/code_task.toml          standalone CPU code-task
    project/                        editable example repository
    task.md                         requested MLP improvement

  greenfield_lightweight_training/
    configs/code_task.toml          standalone bounded greenfield generation
    task.md                         from-scratch local training task

  code_task_greenfield_ml_suite/
    configs/code_task.toml          larger standalone greenfield code-task
    task.md                         server-oriented ML workbench task

  capability_package_minimal/
    README.md                       smallest replaceable capability boundary
    capability.py                   context -> artifact -> result example
```

Use `research_session_smoke.py` or the `research-session` commands above for the
formal V2.8 mainline, including literature-only reports. Use
`code_task_medium_review` when you want to test automated code edits in an
isolated workspace, and `code_task_digits_mlp` for a small CPU training baseline.
Use `greenfield_lightweight_training` when you want a bounded from-zero
implementation task that exercises a medium-light CPU-only experiment suite with
multiple model conditions, parseable metrics, review, and run diagnosis.
Use `code_task_greenfield_ml_suite` when you want a larger pure code-task
greenfield acceptance run on a stronger local machine or server.

The small CPU examples use the same standalone commands (run from the repository root):

```bash
uv run simple-ar code-task init --config examples/greenfield_lightweight_training/configs/code_task.toml
uv run simple-ar code-task execute runs/<run-id> --config examples/greenfield_lightweight_training/configs/code_task.toml
```

Use `examples/code_task_digits_mlp/configs/code_task.toml` for the prepared digits
project instead. These coding examples do not themselves claim search-to-report
acceptance. They require the active Python environment and configured LLM access;
no automatic dependency installation is enabled.

Use `capability_package_minimal` when adding a replaceable V2.8 capability. It
is offline, has no domain-specific schema, and demonstrates the expected
`CapabilityContext` -> `ArtifactStore` -> `CapabilityResult` handoff. Its
contract test is included in `uv run simple-ar-checks core`.

Use `research-session --code-task-config` for a complete research-to-code experiment.
The former segmented `research-code-task` creator has been retired; historical
results remain readable. For coding-only work, use the standalone `code-task` flow.

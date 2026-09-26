# SimpleAutoResearch

**A lightweight research framework. From a task to inspectable evidence, code, experiments, and reports.**

[中文](README_zh.md) · [Quick start](#quick-start) · [Examples](#choose-a-case) · [Documentation](#documentation) · [Changelog](CHANGELOG.md)

Give SimpleAutoResearch a research goal, the materials you already have, and the
conditions it can work within. It connects literature research, scoped code
changes, experiment execution, and reporting in a saved, resumable session.
Not every task needs every step: a survey does not need training, and a code
repair does not need a paper.

The emphasis is on **visible decisions, real execution, and reusable results**—
with a codebase that remains understandable and straightforward to extend.

> **Actively developed.** Literature and code-task workflows are available;
> prepared-project research is still being validated end to end. A completed
> run means artifacts were delivered, not that a hypothesis is correct or a
> paper is ready for publication.

## What can you do with it?

| Your task | What you provide | What to inspect afterward |
| --- | --- | --- |
| Explore a research direction | A question, scope, and model access | Selected sources, reading notes, synthesis, and a Markdown report |
| Improve or repair a codebase | A project, task, and validation commands | An isolated edited workspace, change records, reviews, and validation results |
| Investigate a research improvement | Prepared code, data, environment, evaluation conditions, and resource limits | Candidate changes, actual measurements, analysis, and an evidence-based report or draft |

### Why SimpleAutoResearch?

- **Task-driven, not one mandatory sequence.** An accepted plan connects the
  capabilities needed for the task. Bounded research follow-ups can supplement
  measurements or revise a candidate when the evidence calls for it.
- **Work you can inspect.** Plans, sources, patches, measurements, usage, and
  reports are saved as files, rather than only appearing in a conversation.
- **Resume without throwing everything away.** Sessions retain attempt history
  and can reuse unaffected evidence and compatible measurements.
- **Choose where to participate.** Use assisted decisions, key checkpoints, or
  autonomous execution within the supplied scope.
- **Keep the machinery understandable.** File-based state, TOML configuration,
  Rich terminal output, and focused modules instead of another infrastructure
  platform to operate.

## Quick start

Start with a real literature survey: **no dataset, training environment, or GPU
required**. It does need network access and a working model API.

### 1. Install

Requirements: **Python 3.12+**, Git, and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Wchanging/SimpleAutoResearch.git
cd SimpleAutoResearch
uv sync
```

### 2. Configure model access

Copy `.env.example` to `.env` if you do not already have one:

```bash
cp .env.example .env
```

On PowerShell, use `Copy-Item .env.example .env` instead. Edit the local file
with your provider's credentials, API base URL, and model identifier:

```dotenv
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://your-provider.example/v1
SIMPLE_AR_MODEL=your_model_id
SIMPLE_AR_LLM_API=chat
SIMPLE_AR_LLM_STREAM=true
```

Replace the placeholders. This example uses a Chat Completions-compatible
endpoint with streaming; choose `responses` instead if that is the API your
provider supports. Do not commit credentials. Advanced transport, retry, and
timeout options belong in the [configuration reference](docs/CONFIG_REFERENCE.md).

### 3. Run a survey

```bash
uv run simple-ar research-session --config examples/survey/research.toml
```

The included task surveys small-memory continual learning. To try your own
question, change `[task].goal` in the config—for example:

```toml
goal = "Compare replay-based approaches to small-memory continual learning. Explain their tradeoffs, evidence limitations, and open questions."
```

This case searches literature providers, reads available abstracts and metadata,
and produces a Markdown report with references and an audit. It **does not
download full texts or run training**. See the [survey case](examples/survey/README.md)
for its exact scope.

**Where are the results?** The terminal prints the session directory and
deliverable paths. For this case, look under `runs/survey/<session>/`; open the
reported `report` and `report_audit` files. Keep the session path for continuation.

> **Cost:** model calls are real and billable. Cumulative API request/token
> budgets are uncapped unless you set them. Optional caps, process limits, and
> research-round limits are separate settings; see [configuration](docs/CONFIG_REFERENCE.md).
> Runtime depends on the provider and task, so there is no fixed completion-time promise.

## Choose a case

Each example directory contains one case's inputs, configuration, and run guide.
Generated outputs stay in `runs/`.

| Case | Best for | Preparation |
| --- | --- | --- |
| [Literature survey](examples/survey/README.md) | A first research session without training | Model API and internet access |
| [Continual learning](examples/continual_learning/README.md) | Research improvement on Mammoth / CIFAR-100 | Project, dataset, fixed split, training environment, and GPU budget |
| [Digits MLP](examples/code_task_digits_mlp/README.md) | A small model-code improvement task | Included project; NumPy and scikit-learn |
| [Multi-file code review](examples/code_task_medium_review/README.md) | Trying scoped edits and validation | Included project; Python standard library |

The last two use standalone `code-task`, not the complete research loop.
For the classical-ML example, install `uv sync --extra examples` and retain that
extra when running with `uv run --extra examples ...`.

The continual-learning case runs from its checked-in config after you prepare
the project, data and fixed split at the paths declared in its task TOML. No
TOML copy is required; see its [run guide](examples/continual_learning/README.md).
The [example index](examples/README.md) explains the shared directory conventions.

## Bring your own task

Start with the closest case and describe **the outcome you want**, rather than
writing a sequence of internal stages. A useful task supplies:

1. **Goal and deliverable:** a survey, a verified code change, experiment analysis,
   or a research draft where evidence supports it.
2. **Available assets:** papers, project paths, datasets, or prior results.
3. **Execution conditions:** the prepared interpreter, validation/measurement
   command, comparable evaluation conditions, and permitted edit scope.
4. **Limits and participation:** compute limits, research rounds, optional API
   caps, and the decisions you want to review.

For example:

> Improve forgetting under a small replay-memory budget in this prepared
> continual-learning project. Keep the dataset split and evaluation unchanged.
> Compare against a suitable baseline, explain the measured tradeoffs, and
> deliver an analysis if the evidence does not support an improvement.

Natural language expresses the goal; configuration still supplies execution
permissions and resource constraints. The system does not automatically
provision a training environment. For your own project, keep the research and
CodeTask TOMLs beside the task files, use `{config_dir}` for case-local paths
and explicit absolute paths for machine assets; `.env` is for global model/API
settings, not task resources. Run the research TOML directly.

### Decide how involved to be

Select a policy with `--interaction` or `[research].interaction`:

| Mode | Participation |
| --- | --- |
| `assisted` | Review the execution protocol, research choices, and key delivery decisions |
| `checkpoints` | Review key protocol/direction/delivery decisions; allow bounded same-protocol follow-ups |
| `autonomous` | Let the system choose valid next actions within the configured scope and limits |

New CLI sessions default to `checkpoints`; an example config may choose a
different mode. The survey example uses `autonomous`. **All modes can pause for
missing critical facts, assets, or permissions**—automatic approval cannot supply them.

## Inspect and resume

Rich output shows the current action, elapsed time, progress messages, and final
artifact paths. Read saved measurements and validation results alongside the
report: generated prose is not a substitute for execution evidence.

To continue the survey after resolving a provider error or another blocker,
replace `SESSION_PATH` below with the exact directory printed by your run:

```bash
uv run simple-ar research-session --config examples/survey/research.toml --session-root "SESSION_PATH"
```

Use the original task's config for other cases. Omitting `--session-root` starts
a new session. Continuation preserves history and accounting; it does not reset
an exhausted budget. For a pending decision, follow the terminal's decision ID
and reply instructions. See [CLI reference](docs/CLI_REFERENCE.md) for explicit
revisions and allowance changes.

## How it fits together

```text
Task + materials + constraints
             ↓
      Plan the next work ←──────────────┐
             ↓                         │
  Research / CodeTask / Experiment     │
             ↓                         │
     Save evidence and results ── Reassess
                                       │
                                Deliver or pause
```

These are shared capabilities, not separate end-to-end pipelines for every
task. CodeTask handles scoped implementation and validation; experiments
record measurements; reporting organizes available evidence. The session
coordinates their handoffs and bounded follow-ups. Technical repair, research
revision, and writing revision serve different purposes.

## Current boundaries

- **Research quality needs review.** A run can finish with negative or inconclusive
  results. Report audits do not certify scientific correctness or publication quality.
- **Reading depth depends on the inputs.** Abstract-only runs are not full-paper
  reviews; complex PDFs and incomplete source material remain limitations.
- **Code changes are scoped.** Controlled edits and isolated workspaces do not
  make this a general-purpose unattended refactoring agent or a security sandbox.
- **Prepare the execution environment.** Project dependencies, datasets, and
  GPU/server setup remain your responsibility. Do not run unfamiliar project
  code with access to sensitive files or credentials.
- **Providers can interrupt work.** Network failures, rate limits, and model
  output errors can pause a session. Resume after addressing the cause.

## Documentation

| Guide | Use it for |
| --- | --- |
| [Usage](docs/USAGE.md) | Workflows, outputs, and troubleshooting |
| [Configuration](docs/CONFIG_REFERENCE.md) | Model settings, task inputs, budgets, and execution options |
| [CLI reference](docs/CLI_REFERENCE.md) | Commands, continuation, and decision replies |
| [Workflows and artifacts](docs/WORKFLOWS.md) | Execution boundaries and saved results |
| [Development](docs/DEVELOPMENT.md) | Extending capabilities and understanding internal interfaces |
| [Changelog](CHANGELOG.md) | Changes and migration notes |

## Contributing and acknowledgements

Issues, reproducible bug reports, case studies, and focused pull requests are
welcome. Include the command, relevant config, and diagnostics—**remove API
keys, credentials, and private data first**. Report-quality improvements and
clearer examples are as valuable as code changes.

The project is inspired by [AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw),
while pursuing a compact, inspectable implementation. Our priorities are
lightweight execution, robustness, clear structure, stable operation, and easy
maintenance and extension.

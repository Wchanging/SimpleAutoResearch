# SimpleAutoResearch

**A lightweight research assistant: investigate, edit, analyse, and write with inspectable results.**

[中文](README_zh.md) · [Quick start](#quick-start) · [Examples](#choose-a-case) · [Documentation](#documentation) · [Changelog](CHANGELOG.md)

Choose a function in the guided CLI, describe a task and provide the material you already have. SimpleAutoResearch
organises the necessary work in a saved session and delivers files you can
inspect, edit, rebuild, and continue using.

Use individual functions or connect them: **existing data → analysis and plots →
report → optional ACM export**. A code repair need not search papers, and writing from
existing results need not repeat an experiment.

Material writing defaults to a supplied-evidence report, not a failed-experiment
analysis. Ask for a paper-style draft explicitly when that is the intended delivery;
neither format turns unverified material into a validated scientific result.

> **Source-visible preview.** Structured setup and bounded native workflows are
> available. Free-form conversation, general autonomous reproduction preparation,
> and integrated external coding Agents are not yet offered. A completed session
> is not a scientific-correctness or publication-quality certificate. Invalid model
> reviews can leave required work unresolved; inspect the audit before using a draft.
>
> A project license has not yet been selected. Public repository access is not
> an open-source license.

## What can you do with it?

| Task | Minimum input | Delivery and current scope |
| --- | --- | --- |
| Research a direction | Question, scope, model API; optional local papers | Sources, reading notes, comparisons and Markdown report. Reading depth depends on available material. |
| Repair a codebase | Project, allowed edit scope, validation command, model API | Isolated edited workspace, patch/review records and actual validation. Intended for bounded tasks, not arbitrary unattended engineering. |
| Analyse and plot data | CSV/TSV or JSON records, columns and row/unit meanings | Descriptive statistics or supplied values, editable bar/line/scatter SVGs and rebuilding inputs. No API needed; no inferred uncertainty or automatic scientific validation. |
| Write from material | Notes, draft or descriptive-analysis package; model API | Reviewed report or evidence-limited paper-style draft, references and reusable figures. No new experiment or online research is implied. |
| Reproduce a specified conclusion | Local paper, ready project/environment, fixed command and evaluation protocol | Actual measurements and a bounded reproduction report. Preparation is required; this is not arbitrary whole-paper reproduction. |
| Export a saved report | Report artifacts and Pandoc; SVG conversion tools when needed; TeX for optional compilation | Editable ACM acmart demonstration project, bibliography and figures, through separate `report-export`. Compilation and content quality are separate checks. |

The first five tasks have guided `start` entries. Export consumes an existing
report; it is not a sixth setup choice. Research improvement remains experimental
and is described with the prepared cases below.

### Why use an assistant around these tasks?

- **Less material handoff.** Saved analysis packages and report artifacts can
  feed the next task instead of becoming disconnected chat answers.
- **Inspect actual work.** Plans, source excerpts, patches, measurements,
  usage and rejected revisions remain available as files.
- **Continue saved work.** Compatible completed results and supported writing
  checkpoints can be reused; continuation retains accounting.
- **Keep control.** Choose checkpoints or autonomous actions within the accepted
  scope. Missing critical inputs and permissions still require participation.

These are intended workflow benefits, not evidence that the assistant outperforms
a general coding Agent. Cross-task quality and first-time user experience remain
under validation.

Guided writing from existing evidence uses adaptive article planning with built-in
templates. Expert TOML tasks can opt in with `report.outline_strategy = "adaptive"`.
Custom templates and saved tasks keep their structure. See the
[writing configuration](docs/CONFIG_REFERENCE.md#writing-from-supplied-material).
This organizes the draft; it does not certify claims or create missing experiments.
Writing keeps imported analysis packages complete but links full row records by
default, instead of filling the prose with raw tables. Set `report.data_tables = "full"`
if row-by-row tables are part of the requested report.
Experiment reports likewise keep full recorded evidence in a linked, portable
package by default. This reduces duplicate provenance tables, not required results
or review; recording evidence does not independently verify scientific claims.
With adaptive planning, existing data figures can have an explicit owning section
even when several sections cite the same material; assembly supplies the actual
registered images rather than relying on model-created file links.

For line/scatter data, axes remain separate by default. Select
`analysis.series_layout = "shared"` (or `start --series-layout shared`) when the
columns have a common declared unit. Shared plots preserve every supplied value;
they do not normalize, sample or infer uncertainty. Analysis packages can be
moved, rebuilt and supplied directly to writing.

## Quick start

Requirements: **Python 3.12+**, Git and [uv](https://docs.astral.sh/uv/).

### 1. Install

The current preview is on `feat/v2.9-task-driven-research`, not the default branch:

```bash
git clone --branch feat/v2.9-task-driven-research https://github.com/Wchanging/SimpleAutoResearch.git
cd SimpleAutoResearch
uv sync
```

### 2. Try a task without an API

The included demonstration data can be plotted without credentials, a GPU or
a training environment:

```bash
uv run simple-ar research-session --config examples/data-curves/research.toml
```

Inspect the printed analysis package and SVG paths under `runs/data-curves/`.
This case plots supplied demonstration coordinates; it is not an experiment
or benchmark result. See the [case guide](examples/data-curves/README.md).

### 3. Configure a model for research, code or writing

Copy `.env.example` to `.env` **only if you do not already have one**:
`cp .env.example .env` on a shell, or `Copy-Item .env.example .env` on PowerShell.
Edit it with your provider's actual values:

```dotenv
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://your-provider.example/v1
SIMPLE_AR_MODEL=your_model_id
SIMPLE_AR_LLM_API=chat
SIMPLE_AR_LLM_STREAM=true
```

Use an API format supported by your provider; `responses` is also supported.
Keep secrets out of Git. `.env` holds global model/transport settings; project
paths, interpreters, data and task limits belong in task configuration.
[Advanced settings](docs/CONFIG_REFERENCE.md) are optional.

### 4. Start your own task

```bash
uv run simple-ar start
```

The numbered menu explains each function and its preparation requirements.
Select a number or `survey`, `bug_fix`, `reproduction`, `writing` or `data_analysis`.
The structured guide collects relevant inputs, saves ordinary TOML under
`runs/assistant/`, and starts the shared session. You do not have to write a task
file first. `--prepare-only` saves configuration without execution or model calls.
It is not yet a free-form conversation.

Or try the supplied model-backed survey:

```bash
uv run simple-ar research-session --config examples/survey/research.toml
```

This example surveys small-memory continual learning using available abstracts
and metadata. It **does not download full text or train models**. Change
`[task].goal` for another question; use the guide/configuration reference when
you need supplied papers or best-effort full-text retrieval.

> **Cost:** model calls are billable. Cumulative API request/token caps are
> optional and otherwise uncapped. Process time, resources and research-round
> limits are separate. There is no fixed completion-time promise.

## Choose a case

One directory contains one case's inputs, configuration and instructions;
generated work goes to `runs/`. Run the checked-in config directly—do not copy
templates into `runs/` first.

| Case | Purpose | Preparation |
| --- | --- | --- |
| [Data curves](examples/data-curves/README.md) | Supplied line/scatter coordinates | No API or training; included demonstration data |
| [Descriptive analysis](examples/data-analysis/README.md) | Tables, descriptive bars and rebuilding | No API; explicit data/row semantics |
| [Literature survey](examples/survey/README.md) | Research report without training | Model API and internet |
| [Conformal reproduction](examples/conformal_reproduction/README.md) | Bounded, adapted numerical reproduction | Follow the case environment/protocol; not whole-paper or benchmark acceptance |
| [ICML 2025 RCP subset](examples/rcp_reproduction/README.md) | Fixed author-code-assisted real-data comparison | Prepare pinned code, data, paper and scientific Python; two methods, ten paired seeds. Not whole-paper reproduction; writing quality needs separate review. |
| [Multi-file code review](examples/code_task_medium_review/README.md) | Scoped editing and validation | Model API for generated edits; Python standard-library project |
| [Digits MLP](examples/code_task_digits_mlp/README.md) | Small model-code task | NumPy/scikit-learn; `uv sync --extra examples`, retain the extra in `uv run` |
| [Continual learning](examples/continual_learning/README.md) | Experimental research improvement | Prepared Mammoth/CIFAR-100 project, fixed split, environment and GPU budget |

The two code examples use standalone `code-task`, not a full research loop.
[TabM](examples/tabm_research/README.md) is a prepared-server diagnostic, not a
fresh-checkout quick start. See the [example index](examples/README.md) for scope.

## What you receive

| Delivery | Files to inspect | Reuse |
| --- | --- | --- |
| Data analysis | `analysis.json`, `analysis.md`, copied input and editable SVGs | Rebuild the package or use its JSON as writing material |
| Report | `report.md`, `report_body.md`, `references.bib` and the separately printed audit | Review claims alongside sources; export the report attempt directory |
| ACM export | `main.tex`, `body.tex`, bibliography, figures and `export.json` | Edit/move the project; inspect `build.log` when compilation is requested |
| Code/measurement | Edited workspace, actual validation/run logs and saved results | Inspect the actual change and evaluation conditions, not only the final prose |

The terminal prints exact session/attempt paths. Keep the whole analysis directory so
its copied data remains available after moving it.

For example, connect an analysis package to writing without repeating its task:

```bash
uv run simple-ar start --kind writing --goal "Explain these results and their limitations" --material "PATH_TO_ANALYSIS/analysis.json"
```

Replace `PATH_TO_ANALYSIS` with the printed analysis directory; model access is
needed for writing. Export that report separately if desired:

```bash
uv run simple-ar report-export --report-dir "PATH_TO_REPORT_ATTEMPT" --output runs/acm-draft
```

Replace the report path; choose an unused output directory. Pandoc is required;
SVG conversion requires `rsvg-convert`. Add `--compile` only when pdfLaTeX,
BibTeX and acmart are installed. An uncompiled source project is not a verified
PDF or a conference submission. See the [export guide](docs/CLI_REFERENCE.md#simple-ar-report-export).

## Bring your own material

Start with the function and desired delivery, not an internal stage sequence:

- **Survey:** question, scope, relevant papers, whether online search is allowed.
- **Code:** repository, problem, allowed files and an actual validation command.
- **Data:** input file, selected columns, what each row/value represents and units.
- **Writing:** source material, report or paper-style draft, optional reference papers.
- **Reproduction:** specified paper conclusion, prepared execution conditions,
  fixed evaluation and resource limits.

For writing, provide the completed descriptive-analysis package as material,
rather than presenting arbitrary JSON as validated experimental results.
Prepared evaluators can explicitly retain raw text results for report review
through task-local [`execution.output_files`](docs/CONFIG_REFERENCE.md#research-toml-sections-and-defaults);
recorded attachments are not automatically certified scientific evidence.
For a prepared project, put configuration beside the case, use `{config_dir}`
where supported for case-local paths and explicit paths for machine assets.
The assistant does not automatically provision environments or download datasets.
See [usage](docs/USAGE.md) and [guided configuration](docs/CONFIG_REFERENCE.md#guided-setup).

## Inspect and resume

The terminal prints the session directory, deliverables and current action.
Read actual measurements and source evidence alongside the report.

To resume unchanged model-backed inputs, use the saved session path:

```bash
uv run simple-ar research-session --session-root "SESSION_PATH" --model env
```

Do not rerun `start` to continue a session. For the model-free data example,
omit `--model env`. A new run without `--session-root` creates a new session.
Continuation does not reset an exhausted budget or imply background execution
after disconnect. Confirm an interrupted worker has stopped before recovery;
see the [CLI reference](docs/CLI_REFERENCE.md).

New CLI sessions default to `checkpoints`; use `--interaction assisted`,
`checkpoints` or `autonomous` to choose participation. Examples can specify a
different policy. No mode supplies missing facts, assets or permissions.

## How it fits together

```text
Goal + material + constraints
             ↓
      Select necessary work ←──────────┐
             ↓                         │
 Research / Code / Data / Run / Write   │
             ↓                         │
 Save evidence and artifacts ─── Reassess
                                       ↓
                              Deliver or request input
```

One shared session owns attempts, accounting, artifacts and recovery.
Domain modules own their evidence and execution. Technical repair, scientific
revision and prose revision are different actions—not one generic retry.

## Current boundaries

- **Reading:** saved-text retrieval and one bounded Reader follow-up can find
  missed passages. Lexical matches and selected excerpts are not whole-paper
  semantic verification. Unknowns and unresolved queries must remain visible.
- **Writing:** review, source backtracking and revision are model-assisted.
  Citation/metric audits and a compiled PDF do not certify correct claims,
  complete bibliography, academic style or publication readiness.
  Check both the final audit and the reader-facing report: a section-level
  `pass` does not resolve an outstanding whole-document requirement.
- **Data and figures:** descriptive bars and numeric-coordinate line/scatter
  plots are supported. Arbitrary statistical inference, heatmaps and free-form
  scientific illustration are not covered by this guided data path.
- **Execution:** isolated copies and scoped edits are not an OS sandbox.
  Prepare dependencies/data; do not expose sensitive files to unfamiliar code.
- **Services and scope:** provider errors can pause work. Integrated external
  Agents and general autonomous reproduction preparation remain deferred.
  No formal PaperBench or ScienceAgentBench result is claimed.

## Documentation

| Guide | Purpose |
| --- | --- |
| [Usage](docs/USAGE.md) | Choose a function, run it, inspect outputs and troubleshoot |
| [Configuration](docs/CONFIG_REFERENCE.md) | Global connections, task inputs and optional expert settings |
| [CLI reference](docs/CLI_REFERENCE.md) | Commands, continuation and explicit revisions |
| [Workflows and artifacts](docs/WORKFLOWS.md) | Evidence, execution and saved-result boundaries |
| [Development](docs/DEVELOPMENT.md) | Module ownership and engineering contracts |
| [Changelog](CHANGELOG.md) | Implemented changes and migration notes |

## Contributing and acknowledgements

Issues and reproducible cases are welcome. Discuss code contributions before
opening a PR while licensing is undecided. Include commands, relevant
configuration and diagnostics; **remove credentials and private data**.

Workflow and presentation inspiration includes
[AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw),
[OpenResearch](https://github.com/alphaXiv/OpenResearch) and
[ARIS](https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep).
This project keeps its own compact, file-based runtime and scoped native workflows.
Reports, examples and usability improvements matter alongside code.

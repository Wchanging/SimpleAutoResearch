# Continual-learning research case

This is a prepared-project research example, not a claim of completed acceptance.
It uses the official Mammoth `tpami2023` commit
`ad4d39068a07cc339fdfd3b8bad30067c591d046` with CIFAR-100 class-incremental
learning (ten tasks of ten classes). Repository selection and this adapter are
engineering-provided inputs; do not describe them as autonomous discovery.

## Case inputs and normal entrypoint

- `research.toml`: natural-language goal, delivery and research/process limits.
- `code_task.toml`: edit scope, measurement command, and the case-declared project/data paths.
- `task.md`: implementation and comparison requirements.
- `run_mammoth.py`: measurement adapter, not a separate research orchestrator.

Run the checked-in `research.toml` directly. Its paired `code_task.toml` declares
the expected project, dataset and frozen split under `runs/assets` relative to
the case file; those paths must exist before the session starts. The CLI checks
required paths before creating a session or calling the model. `research.toml`
resolves its `code_task_config` and output directory from its own location, and
`code_task.toml` resolves case files through `{config_dir}`.

The default `[environment] mode = "current"` uses the Python running
SimpleAutoResearch. If the project requires another installed environment,
edit the case TOML explicitly (without putting the path in `.env`):

```toml
[environment]
mode = "external"
python_executable = "{config_dir}/../../runs/envs/mammoth-probe/bin/python"
```

The benchmark begins with `python`; the existing environment policy resolves it
to the selected interpreter. No dependency installation or data download is
automatic, and the TOML is not a substitute for verifying the assets.

After preparing the data, fixed split, Python environment and GPU budget:

```bash
uv run --no-sync simple-ar research-session --config examples/continual_learning/research.toml
```

The template uses autonomous interaction and no cumulative API request/token cap.
It bounds research follow-ups to two and processes to eight invocations, 7200
seconds total and 1800 seconds each. These are ceilings, not runtime estimates.
Its one epoch per task is a low-cost engineering acceptance condition. The ten
tasks are ten sequential groups of CIFAR-100 classes, not ten framework stages.
Baseline policy is `auto`; no three-seed matrix or mandatory revision is preset.
The literature input is abstract-level; this is not full-text writing validation.
API credentials belong in the environment. Outputs belong in `runs`, not here.

## Measurement and comparison

The example declares dataset, split and comparison settings in
`execution.protocol`. Keep them aligned with the measurement command when
changing the experiment. `seed_flag` identifies the literal seed argument even
for a single run; it does not request additional seeds. These are declared
conditions, not proof that data files or an old run were independently verified.
Updating this configuration does not retroactively repair historical results.

When configured with `mode = "external"`, CodeTask uses the selected training
Python; otherwise it uses the current framework Python. Static dependency checks use the selected interpreter, without importing the
training project or installing packages. A passed static check is not a runtime
test or evidence of scientific effectiveness.

`run_mammoth.py` runs from an isolated Mammoth project working directory. It
uses the official model, training loop, task splitting, transforms and evaluator.
It only maps the shared dataset location, limits loader workers/torch threads
to two, and replaces the final logger output with standard metric lines plus
`continual_results.json`. The JSON retains the measured seen-task accuracy
matrix, execution arguments and elapsed time. Accuracy units are fractions;
forgetting and backward transfer exclude the final newly learned task.
Unseen-task padding is not reported as a measurement.
For Torch 2.8 compatibility, the adapter discards the upstream scheduler's
removed `verbose` logging argument; milestones, gamma and stepping are unchanged.

Prepare and verify `DATA_ROOT/CIFAR100/cifar-100-python` before execution.
Use the existing framework executor with a finite timeout and an explicitly
authorized GPU budget. It sets `SIMPLE_AR_OUTPUT_DIR` per invocation; standalone
diagnostics must instead pass a unique `--output` directory.

The literal experiment argv lives in `code_task.toml`; `{config_dir}` paths are
expanded before it is parsed into process arguments. Keep quoted path references
in that command when paths may contain spaces. For your own project, put its
research and CodeTask TOMLs beside the task description, use `{config_dir}` for
case-owned files or explicit absolute paths for machine assets, and declare
required input paths under `[environment].required_paths`. Existing CodeTask
TOMLs without `{config_dir}` retain their current working-directory-relative
behavior; task resource paths are not read from `.env`.

One epoch is a reduced-training protocol, not the paper's
original 50-epoch setting or evidence of scientific effectiveness. Measure
real-data throughput before setting the final matrix. Keep seeds and evaluation
conditions identical for baseline/candidate. Never tune on the held-out test set.
Random-input GPU probes and `--debug_mode 1` runs are diagnostics only.

Before preparing isolated baseline/candidate workspaces, freeze the upstream
`datasets/val_permutations/seq-cifar100.pt` split as an explicit project asset.
The upstream validation loader otherwise generates it on first use, consuming
the current random state; independently generated splits are not a controlled
comparison. Verify that it is a complete permutation of the training indices
and include it among the protocol's protected assets. Keep the same split
across seeds; only training randomness should vary. Do not place it in Git.

The framework preserves the JSON under its existing `experiment_outputs`
artifact. Each measured cell also emits a standard metric named
`accuracy_after_task_I_on_task_J` (one-based task indices, fraction units).
The existing report metric tool can retrieve these values with their result
artifact provenance; it need not read arbitrary files. Only seen tasks are
emitted. A fixture verifies this route locally; actual training and the Writer's
use of these values still need server acceptance.

The current server environment reuses Torch 2.8/CUDA rather than the original
Torch 1.12.1 dependency lock; compatibility and scientific reproduction are
different claims. This adapter does not provide intra-training checkpointing:
failed training is an incomplete run, while completed experiments can be reused
by the existing research session. Do not invent task results after interruption.

Local tests verify measurement conversion and report-tool provenance. Keep
dated runtime measurements and acceptance outcomes in run records, not this
reusable case definition. A completed process is not by itself evidence of
method effectiveness, report quality or original-paper reproduction.

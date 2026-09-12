# CIFAR-10 low-data augmentation and calibration

Prepared baseline for the new application's planned single-3090 acceptance.
This is **not an accepted end-to-end run**. No candidate method or claimed
improvement is supplied. The application must search, assess alternatives,
edit a method, compare measurements and write its own evidence-backed report.

Windows CPU preflight passed on 2026-09-12 with the official dataset: preparation
66.125s, two-step training process 7.203s (including imports/data setup). Both
process records settled in the shared ledger. The probe used CPU, batch 2, two
threads, and emitted no accuracy or checkpoint. This verifies real data loading
and optimization, **not** validation accuracy, a full epoch, Linux or GPU throughput.

## Boundaries

- `method.py`: research-editable training transforms and loss.
- `train.py`, `protocol.py`: protected training schedule, partition and evaluator.
- `prepare.py`: explicit shared-data preparation, never part of candidate training.
- Shared data stays outside the copied code project, for example under `runs/assets`.
  Protect the generated `low-data-v1.json` in the application's execution protocol too.

The baseline calls [torchvision ResNet-18](https://docs.pytorch.org/vision/stable/models/generated/torchvision.models.resnet18.html)
with no pretrained weights, replacing its stem by a 3x3 stride-1 convolution
and removing the initial maxpool. It imports the installed implementation rather
than vendoring it; see [torchvision's BSD license](https://github.com/pytorch/vision/blob/main/LICENSE).
[CIFAR10](https://docs.pytorch.org/vision/stable/generated/torchvision.datasets.CIFAR10.html)
performs upstream archive integrity checks. Preparation additionally hashes training
batch contents and saves fixed split indices. The official test set is not read.

## Preparation and execution

Torch and torchvision are **example-only dependencies**, not additions to the
framework dependency set. `requirements-cpu.txt` records the installed Windows /
Python 3.12 CPU environment. CUDA and Linux installation remain unverified: select
the appropriate official wheel index for the eventual server and freeze its actual
environment before acceptance. Do not rent a GPU merely to debug this script.
Each measured run records installed versions.

For an isolated CPU validation environment, the currently selected pair is
`torch==2.9.1` / `torchvision==0.24.1`, from the
[official CPU wheel index](https://pytorch.org/get-started/previous-versions/).
Do not install CUDA packages into the framework environment for this check:

```bash
uv venv runs/cifar-cpu-env --python 3.12
uv pip install --python runs/cifar-cpu-env/bin/python \
  -r examples/cifar10_calibration/requirements-cpu.txt
uv run --no-sync python examples/cifar10_calibration/preflight.py \
  --python runs/cifar-cpu-env/bin/python --data-root runs/assets/cifar10 \
  --output runs/cifar-cpu-preflight --download
```

On Windows use `runs/cifar-cpu-env/Scripts/python.exe`. The preflight uses the
existing process executor with a persisted ledger: one asset-preparation process
(180s), then one two-step CPU probe (60s). Logs and exit/timeout evidence remain
under the output directory; failures are not retried. Remove `--download` when
using prepared data. This is not a second research orchestrator.

From the repository root, in that prepared environment:

```bash
# Explicit download permission; omit --download to use already prepared data.
python examples/cifar10_calibration/prepare.py --data-root runs/assets/cifar10 --download

# Short CPU code/environment check, not a scientific measurement.
python examples/cifar10_calibration/train.py --data-root runs/assets/cifar10 \
  --output runs/cifar-cpu-probe --seed 0 --device cpu --batch-size 2 --probe-steps 2

# Only after GPU approval: estimate throughput/memory before freezing epochs.
python examples/cifar10_calibration/train.py --data-root runs/assets/cifar10 \
  --output runs/cifar-gpu-probe --seed 0 --device cuda --probe-steps 100

# Example single condition; real runs must be launched through the framework's
# execution boundary so its process timeout and shared budget apply.
python examples/cifar10_calibration/train.py --data-root runs/assets/cifar10 \
  --output runs/cifar-baseline-seed0 --seed 0 --device cuda --epochs 30
```

Output directories must be new: an interrupted run is not silently overwritten.
When launched by the canonical experiment executor, omit `--output`: the executor
sets `SIMPLE_AR_OUTPUT_DIR` to a unique directory inside that invocation. This keeps
seeds and repair revisions separate without command-string substitution. The
canonical result registers the directory as `experiment_outputs` when created.
An explicit `--output` remains available for direct manual runs and overrides the
environment default. Windows two-step CPU probing of this boundary has passed;
this does not establish full training or GPU acceptance.
The direct script has no wall-clock watchdog or automatic resume; the canonical
executor must provide those lifecycle decisions. A probe emits only `probe.json`,
not accuracy or a checkpoint. GPU memory figures measure allocated tensor memory,
not all driver/process memory. CUDA requests never fall back to CPU.

## Frozen comparison rules

`application.py` constructs the three-seed `ResearchApplication` execution config.
Its CLI only prints config; it does not create a session, download, call a model or train:

```bash
uv run --no-sync python examples/cifar10_calibration/application.py \
  --python runs/cifar-cpu-env/bin/python --data-root runs/assets/cifar10 --epochs 1
```

Use the Windows interpreter path shown above on Windows. `--epochs 1` is a config
check, not the frozen GPU protocol. Freeze epochs after the approved GPU probe.
`create_cifar_session(...)` creates the normal application without advancing it;
pass the shared LLM client when preparing a live run. It proposes ceilings of
80 requests/300k tokens, nine physical runs and 7200 process-wall seconds. Creating
this session does not authorize renting a GPU or spend those limits. Actual API
cost and the frozen-code live acceptance must be reviewed before advancing it.

For the actual user-facing runner, prepare the interpreter and data first, then
start one bounded action at a time so the persisted session remains inspectable:

```bash
uv run --no-sync python examples/research_application_live.py --cifar10 \
  --session runs/cifar10-live --python runs/cifar-cuda-env/bin/python \
  --data-root runs/assets/cifar10 --epochs 1 --device cuda --max-actions 1
```

Continue the same session with `--resume --session runs/cifar10-live`; the saved
runtime configuration supplies the execution protocol on resume. This runner
does not install dependencies or download data, and `--max-actions` limits
application actions rather than silently shrinking the declared experiment.

The config passes `allowed_patterns=["method.py"]` through the existing CodeTask
workspace initializer. This is enforced by its edit-scope machinery, not merely
an instruction in the prompt. Evaluation files and the shared split manifest are
also declared protected assets. Workspace preparation was checked locally without
training; the dataset remains external to the copied project.

Split seed 1729; 1000 train and 500 validation samples per class. Training seeds
0/1/2, baseline and one candidate paired. SGD (lr .1, momentum .9, weight decay
.0005), cosine schedule, batch 128, at most 30 epochs. Two CPU threads, zero loader
workers. Last epoch selection, no cherry-picking the best validation epoch.
The GPU probe determines one common epoch limit before the matrix begins.

Accuracy is a fraction; NLL uses natural logs and true-class probability clipped
at 1e-12. ECE uses 15 equal-width bins, left-closed/right-open except the last bin
includes 1. Empty-bin values are null, not measured zero. `history.json` contains
epoch metrics and reliability bins; `metrics.json` contains final measurements;
`last.pt` is the sole saved checkpoint. Conditions include method and asset hashes.

The session-level matrix (six main runs, at most three justified extra runs),
two-GPU-hour ceiling, negative-result stopping, figure rendering, full paper and
user-scale live acceptance remain to be verified. The new application config and
isolated preparation are connected; statistical plots and scientific iteration
beyond execution repair are still incomplete. These scripts do
not introduce a second research loop or claim to enforce the matrix budget.

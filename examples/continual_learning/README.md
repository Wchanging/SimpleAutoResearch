# Continual-learning acceptance candidate

This example is under development, not a completed V2.8 acceptance result.
It uses the official Mammoth `tpami2023` commit
`ad4d39068a07cc339fdfd3b8bad30067c591d046` with CIFAR-100 class-incremental
learning (ten tasks of ten classes). Repository selection and this adapter are
engineering-provided inputs; do not describe them as autonomous discovery.

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

Example experiment argv, executed with the isolated project as cwd:

```bash
TORCH_PYTHON /path/to/SimpleAutoResearch/examples/continual_learning/run_mammoth.py \
  --data-root /path/to/shared/datasets --model er --dataset seq-cifar100 \
  --lr 0.03 --buffer_size 200 --minibatch_size 32 --batch_size 32 \
  --n_epochs 5 --seed 0 --validation 1 --nowand 1 --disable_log 0
```

Five epochs are an initial reduced-training candidate protocol, not the paper's
original 50-epoch setting and not yet a frozen scientific comparison. Measure
real-data throughput before setting the final matrix. Keep seeds and evaluation
conditions identical for baseline/candidate. Never tune on the held-out test set.
Random-input GPU probes and `--debug_mode 1` runs are diagnostics only.

Before preparing isolated baseline/candidate workspaces, freeze the upstream
`datasets/val_permutations/seq-cifar100.pt` split as an explicit project asset.
The upstream validation loader otherwise generates it on first use, consuming
the current random state; independently generated splits are not a controlled
comparison. Record its hash and include it among the existing protocol's
protected assets. Keep the same split across seeds; only training randomness
should vary. This preparation has not yet been validated on the server.

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

Local tests verify measurement conversion and report-tool provenance. Server
diagnostics on a single RTX 3090 completed the ten-task sequence: 26.83 seconds
in debug mode, then 89.02 seconds for one full epoch per task. These establish
runtime compatibility, not method effectiveness or original-paper reproduction.
Frozen-version research-to-paper acceptance remains pending.

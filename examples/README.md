# Examples

Each directory is one complete case with its own inputs, configuration, entry
command and expected outputs. Run commands from the repository root.

| Case | Goal | Entry and preparation |
| --- | --- | --- |
| [Survey](survey/README.md) | Literature survey, no training | `research-session --config examples/survey/research.toml`; configure model access |
| [Continual learning](continual_learning/README.md) | Mammoth/CIFAR-100 research improvement | Set three machine paths in `.env`, prepare data and split, then run the checked-in research config |
| [Digits MLP](code_task_digits_mlp/README.md) | Small CPU model-code improvement | Included project/task/config; requires NumPy and scikit-learn |
| [Medium review](code_task_medium_review/README.md) | Small multi-file code improvement | Included project/task/config; Python standard library |

The coding cases use standalone `code-task`; they are not full autonomous
research or paper-quality acceptance. Research cases use `research-session`.
A runnable case is not a claim of scientific or writing-quality acceptance.

## Files and outputs

- `examples/<case>/`: reusable case inputs only, never generated runs or credentials.
- `.env`: ignored machine-specific credentials and optional paths; checked-in cases
  refer to those paths by name, without local TOML copies.
- `runs/`: generated state, workspaces, measurements and reports.
- `tests/fixtures/`: internal regression material, not additional user examples.
- `scripts/research_session_smoke.py`: offline developer wiring check, not an acceptance case.

Keep machine paths out of committed templates and credentials in the environment.
Do not start GPU examples just to validate configuration. Removed historical cases
remain recoverable from Git; do not copy dated outputs back into examples.

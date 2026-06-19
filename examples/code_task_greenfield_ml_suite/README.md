# Greenfield ML Suite Code Task

This is a larger standalone `code-task` acceptance scenario for testing
from-scratch code generation. It intentionally skips the 8-stage research
pipeline and exercises the unified greenfield code-task runtime directly.

The task asks the model or external agent to create a medium-scale local ML
experiment workbench with packaged/local open datasets when available,
deterministic synthetic fallback only when necessary, multiple model families,
registries, presets, self-checks, ablations, resource-aware execution,
structured metrics, and a terminal-friendly report. It is designed for a
stronger local machine or a server; keep the lightweight
`greenfield_lightweight_training` example for quick laptop smoke tests.

The example deliberately does not require runtime dataset downloads. A good
generated solution should prefer locally packaged open datasets, such as
scikit-learn datasets when `scikit-learn` is installed, or user-provided files
under a local data directory. Synthetic data should be reported as a fallback,
not treated as the main evidence path.

Before implementation planning, greenfield execute writes
`code_task/meta/dependency_advice.json` / `.md` and prints a Rich-friendly
summary of installed and missing recommended packages. If the advice suggests
installing packages such as `scikit-learn`, install them explicitly and rerun
execute; SimpleAutoResearch will not mutate the environment automatically.

The requested implementation is intentionally larger than a single script: it
should look like a small open-source project with reusable modules, tests,
configuration, CLI modes, JSON/Markdown artifacts, and extension points. The
configured generation budget leaves room for a few-thousand-line implementation
without requiring padded boilerplate.

```bash
uv run simple-ar code-task init --config examples/code_task_greenfield_ml_suite/configs/code_task.toml
uv run simple-ar code-task execute runs/code-task-greenfield-ml-suite/<run-id> --config examples/code_task_greenfield_ml_suite/configs/code_task.toml --yes
```

To test an external agent backend, edit `[implementation]` in the TOML and set
`provider`, `agent_mode`, and `allow_external_agent` explicitly.

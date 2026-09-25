# Examples

Run commands from the repository root. Use `research-session` for task-driven
research; standalone `code-task` examples exercise coding only.

## Choose a case

| Case | Purpose | Inputs / entry |
| --- | --- | --- |
| [Continual learning](continual_learning/README.md) | Prepared Mammoth/CIFAR-100 research, GPU server | Research config, CodeTask config, task and measurement adapter |
| [Research config](research_config/minimal.toml) | General research-session configuration | Minimal and advanced TOML templates |
| Research brief | Small local evidence input | `research_brief/fixtures/reliable_agents.md` |
| [Digits MLP](code_task_digits_mlp/README.md) | Small CPU code modification | `configs/code_task.toml`, `project/`, `task.md` |
| Medium review | Tiny code-review fixture, not scientific evidence | `code_task_medium_review/configs/code_task.toml` |
| Lightweight training | Bounded from-scratch coding | `greenfield_lightweight_training/configs/code_task.toml` |
| [Greenfield ML suite](code_task_greenfield_ml_suite/README.md) | Larger coding suite, use a server | Config and task |
| [CIFAR-10 calibration](cifar10_calibration/README.md) | Separate prepared GPU diagnostic | Data preparation, preflight and adapter |
| [Capability package](capability_package_minimal/README.md) | Offline extension example | Capability contract |
| [MCP coding agent](tool_mcp_codex_agent/README.md) | Optional external coding backend | Backend config template and task |

A template is not an acceptance result. See each case's resource and preparation
requirements. Do not run training examples on a laptop merely to check config syntax.

## Directory responsibilities

- `examples/<case>/`: versioned, reusable input, small fixture project, configuration
  templates, adapter and usage instructions. No credentials, downloaded datasets,
  virtual environments or generated reports.
- `.local/cases/<case>/`: ignored machine-specific copies of configuration. Use
  absolute project, interpreter, data, adapter and task paths where needed.
- `.local/assets/`, `.local/envs/`: optional ignored shared assets/environments.
  Existing assets can remain elsewhere; their location is an explicit input.
- `runs/<case>/<session>/`: generated session state, attempts, isolated workspaces,
  measurements, logs and reports. Not the canonical home of case definitions.
- `tests/`: automated regression checks; not a directory of user acceptance runs.

Keep one directory per reusable case; do not create a new version/date-specific
case for every run. Preserve useful evidence before removing old runs. Moving a
historical session may break absolute references; an archive is not a resumable
session at its new location. Restore its original path before attempting resume.

## Small entrypoints

Local-material brief:

```bash
uv run simple-ar research-brief --topic "reliable agents" \
  --local-document examples/research_brief/fixtures/reliable_agents.md \
  --output-root runs/research-brief
```

Standalone coding (requires configured model access):

```bash
uv run simple-ar code-task init --config examples/code_task_medium_review/configs/code_task.toml
uv run simple-ar code-task execute runs/<run-id> --config examples/code_task_medium_review/configs/code_task.toml
```

For task-driven research, prepare a case config and use:

```bash
uv run simple-ar research-session --config /absolute/path/to/research.toml
```

Credentials and provider selection belong in the environment, never a case file.
Research-session relative paths resolve against its TOML directory; do not assume
that command arguments or standalone CodeTask paths receive the same rebasing.

## Developer diagnostics

`research_session_smoke.py` is a small wiring smoke with fixture measurements.
`research_application_live.py` and `autodl_low_resource_smoke.sh` are bounded
development helpers with their own options and budgets, not the normal user
acceptance entrypoint. Consult their source/help before use; do not confuse a
fixed diagnostic matrix or synthetic metric with autonomous research evidence.
Retain these scripts while tests/docs still consume them, rather than moving
entrypoints and breaking callers merely to make the directory look uniform.

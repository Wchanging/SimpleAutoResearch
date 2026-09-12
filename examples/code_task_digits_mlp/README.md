# CPU digits CodeTask

Improve a small NumPy MLP on scikit-learn's bundled digits dataset. No download
or GPU is needed. NumPy and scikit-learn must be available in the active Python
environment. The original project and its tests remain unchanged; edits are
limited to `digits_mlp/**` in an isolated copy.

From the repository root:

```bash
uv run --extra examples simple-ar code-task init --config examples/code_task_digits_mlp/configs/code_task.toml
uv run --extra examples simple-ar code-task execute runs/<run-id> --config examples/code_task_digits_mlp/configs/code_task.toml
```

Execution pauses for normal plan/proposal approval. Model-backed edits require
the usual LLM configuration. The benchmark reports measured accuracy, macro-F1,
training/inference time and parameter count; this example is not a full research
or paper-generation acceptance run. Its config can also be supplied to the formal
`research-session --code-task-config` entrypoint.

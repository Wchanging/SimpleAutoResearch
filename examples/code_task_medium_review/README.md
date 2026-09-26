# Multi-file review-classifier improvement

Improve phrase-aware classification in the included small Python project. The
task, editable source, fixed evaluation examples and tests belong to this case:

- `task.md`: requested behavior and constraints.
- `project/`: runnable standard-library project and its tests.
- `configs/code_task.toml`: execution, edit scope and review settings.

From the repository root, with model access configured:

```bash
uv run --no-sync simple-ar code-task init --config examples/code_task_medium_review/configs/code_task.toml
uv run --no-sync simple-ar code-task execute runs/<run-id> --config examples/code_task_medium_review/configs/code_task.toml
```

Use the run directory printed by `init`. Follow the normal plan/proposal approval
prompts. Edits run in an isolated workspace; do not modify the original fixture to
make its measured score improve. Results include patch, validation and benchmark
metrics under the run directory. This is a coding case, not autonomous research.

# Codex Agent Greenfield Experiment Task

Build a small but non-trivial CPU-only Python experiment project for lightweight
text classification. The project should be generated from scratch and must be
safe to run locally.

Required behavior:

- implement a deterministic synthetic text classification dataset;
- compare at least three model or feature conditions, including one simple
  baseline and one stronger n-gram or feature-based condition;
- keep code modular enough to inspect with read-only tools after generation;
- print parseable metric lines from the entrypoint;
- avoid network access, external datasets, GPU requirements, shell calls, and
  dependency installation;
- keep runtime under the configured local timeout.

Required metrics:

- `accuracy`
- `macro_f1`
- `majority_accuracy`
- `keyword_accuracy`
- `unigram_accuracy`
- `bigram_accuracy`
- `ablation_gain`
- `condition_count`
- `data_size`
- `train_time_sec`
- `inference_time_ms`
- `parameter_count`

Implementation note:

SimpleAutoResearch will pass this task through an external Codex CLI backend.
Codex should write candidate project files only under the handoff
`generated_files/` directory. SimpleAutoResearch will then copy, review, run,
and validate the project.

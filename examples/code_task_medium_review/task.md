# Code Task: Improve The Medium Review Pipeline

## Objective

Improve the review-classification baseline in `project/` by adding phrase-aware sentiment handling. The current model only uses unigram word features, so it misclassifies common phrases such as `not good`, `not bad`, `not slow`, `hardly useful`, and `good ... broken`.

## Scope

- Keep the command-line entrypoint as `python main.py --config configs/experiment.json --show-progress`.
- Preserve the existing `review_pipeline` module layout.
- Prefer a small, reviewable patch across the relevant source/config files rather than a whole-file rewrite.
- You may update `configs/experiment.json` when the change only enables the new feature family required by the implementation.
- Do not change evaluation examples, labels, tests, or metric computation to inflate the score.

## Expected Implementation Direction

- Add phrase or bigram feature extraction in `review_pipeline/features.py`.
- Wire phrase features into `review_pipeline/model.py` with explicit, readable weights or scoring logic.
- Enable the new feature family in `configs/experiment.json` when phrase features are implemented.
- Keep progress output visible during runs.
- Keep metric lines in `name: value` format so SimpleAutoResearch can parse them.

## Success Criteria

- `python main.py --config configs/experiment.json --show-progress` exits with code 0.
- `accuracy` and `macro_f1` improve compared with the recorded baseline.
- `train_time_sec` remains lightweight for local execution.
- The patch changes implementation/configuration files only, not tests or evaluation data.

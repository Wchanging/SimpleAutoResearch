# Task

Improve the toy spam baseline so it detects obvious lottery/prize spam while keeping the public API stable.

## Constraints

- Prefer a minimal change to existing keyword rules.
- Do not change the test expectations.
- Keep `classify(text: str) -> str` and `score_message(text: str) -> float`.
- Validate with `python -m unittest discover -s tests`.

## Expected direction

The current baseline already detects `free`, `winner`, and `win`, but it misses short messages such as `urgent lottery prize waiting`.

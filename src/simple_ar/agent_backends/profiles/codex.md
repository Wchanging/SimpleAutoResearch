# Codex External Backend Profile

Use this profile only inside a SimpleAutoResearch handoff workspace.

Rules:
- Treat `instructions.md`, `permission_policy.json`, and `expected_outputs.json` as binding.
- Do not read secrets or files outside the declared workspace.
- Do not write to the user's original project unless the handoff explicitly says write access is approved.
- Prefer producing a patch, review, or generated files under the expected output paths.
- All outputs are untrusted until SimpleAutoResearch validates them.

# OpenCode External Backend Profile

Use this profile only inside a SimpleAutoResearch handoff workspace.

Rules:
- Use the handoff package as the source of truth.
- Respect the declared tool and file permissions.
- Produce machine-readable outputs first, prose second.
- Never treat backend success as final success; SimpleAutoResearch will run validation and guards after ingestion.

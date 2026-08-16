from __future__ import annotations

from simple_ar.core import CapabilityContext, CapabilityRegistry, CapabilityResult


CAPABILITY_NAME = "minimal-copy"


def register(registry: CapabilityRegistry) -> None:
    """Register this package without changing global core state."""
    registry.register(CAPABILITY_NAME, run)


def run(*, context: CapabilityContext) -> CapabilityResult:
    """Copy one registered JSON input into the current attempt.

    This deliberately demonstrates a small capability contract: it owns one
    input shape, one output artifact, and explicit failure diagnostics.
    """
    if len(context.inputs) != 1:
        return CapabilityResult(
            status="failed",
            diagnostics=("minimal-copy requires exactly one JSON input.",),
            provenance={"capability": CAPABILITY_NAME},
        )

    source = context.inputs[0]
    try:
        payload = context.read_input_json(source)
    except (OSError, ValueError) as exc:
        return CapabilityResult(
            status="failed",
            diagnostics=(f"Could not read input {source.path}: {exc}",),
            provenance={"capability": CAPABILITY_NAME},
        )

    output = context.store.write_json(
        "result.json",
        {"input": payload},
        kind="result",
        schema="minimal_copy.result.v1",
        producer=CAPABILITY_NAME,
    )
    return CapabilityResult(
        status="completed",
        artifacts=(output,),
        usage={"inputs": 1, "outputs": 1},
        provenance={"capability": CAPABILITY_NAME},
    )

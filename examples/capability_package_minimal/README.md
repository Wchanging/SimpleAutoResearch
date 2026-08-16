# Minimal Capability Package

This is the smallest complete V2.8 capability example. It shows the frozen
boundary a feature package should use:

1. register one named handler;
2. receive `CapabilityContext`;
3. read only registered inputs;
4. write outputs through the attempt-local `ArtifactStore`;
5. return `CapabilityResult` for success or failure.

The example does not modify the old eight-stage pipeline and does not add a
new global state object. A package owner can copy this shape, replace the
input/output contract, and add normal, missing-input and degraded fixtures.

The corresponding test is `tests/test_capability_package_example.py`.

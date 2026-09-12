"""Measured-run identity and declared comparison-protocol compatibility."""

import hashlib
import json
from collections.abc import Mapping
from typing import Any
from pathlib import Path


def snapshot_protocol_assets(contract: Mapping[str, Any] | None, cwd: Path) -> dict[str, dict[str, str]]:
    """Hash only explicitly named files; never recurse through a data directory."""
    snapshots = {}
    for asset in (contract or {}).get("protected_assets", []):
        asset_id, locator = asset.get("asset_id"), asset.get("path")
        if not isinstance(asset_id, str) or not asset_id or asset_id in snapshots:
            raise ValueError("Protected assets need unique non-empty asset_id values.")
        if not isinstance(locator, str) or not locator:
            raise ValueError(f"Protected asset {asset_id} requires a file path.")
        path = Path(locator)
        # Keep the named path so a symlink retarget is observed on the second
        # read, rather than checking only its original resolved target.
        path = (cwd / path).absolute() if not path.is_absolute() else path
        snapshots[asset_id] = {"path": str(path), "sha256": _file_hash(path)}
    return snapshots


def reconcile_protocol_assets(before: dict[str, dict[str, str]]) -> dict[str, Any]:
    after, errors = {}, {}
    for asset_id, snapshot in before.items():
        try:
            after[asset_id] = _file_hash(Path(snapshot["path"]))
        except OSError as exc:
            errors[asset_id] = str(exc)
    changed = [asset_id for asset_id, snapshot in before.items()
               if after.get(asset_id) != snapshot["sha256"]]
    fingerprint = hashlib.sha256(json.dumps(
        {key: value["sha256"] for key, value in before.items()}, sort_keys=True,
    ).encode()).hexdigest() if before else None
    return {"status": "changed" if changed else "observed_unchanged" if before else "not_checked",
            "before": before, "after": after, "errors": errors,
            "changed_assets": changed, "content_fingerprint": fingerprint}


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def measurement_record(
    run: Any, contract: Mapping[str, Any] | None, result_schema: Mapping[str, Any],
) -> dict[str, Any]:
    process = getattr(run, "process_record", {})
    protocol = dict(contract or {})
    # A legacy hypothesis alone does not specify a comparable experiment.
    complete = all(protocol.get(key) for key in (
        "dataset_refs", "split_spec", "metric_specs", "comparison_conditions",
    ))
    fingerprint = hashlib.sha256(json.dumps(
        {"protocol": protocol, "result_schema": dict(result_schema)},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")).hexdigest() if complete else None
    return {
        "schema_version": "experiment_measurement.v1",
        "measurement_id": process.get("invocation_id"),
        "condition_id": getattr(run, "label", "experiment"),
        "source_kind": "measured" if process.get("invocation_id") else "unverified_backend",
        "protocol_id": protocol.get("contract_id"),
        "protocol_revision": protocol.get("protocol_revision"),
        "protocol_fingerprint": fingerprint,
        "protocol_status": "declared" if fingerprint else "incomplete",
        "limitations": ["Protocol identity reflects declared settings, not verified dataset or evaluator contents."],
    }


def comparison_compatibility(baseline: Mapping, candidate: Mapping) -> tuple[str, str]:
    first, second = baseline.get("measurement"), candidate.get("measurement")
    if first is None and second is None:
        return "legacy_unverified", "Historical results have no measurement protocol metadata."
    if not isinstance(first, Mapping) or not isinstance(second, Mapping):
        return "unknown", "One result lacks measurement protocol metadata."
    a, b = first.get("protocol_fingerprint"), second.get("protocol_fingerprint")
    if not a or not b:
        return "unknown", "Comparison protocol is incomplete."
    if a != b:
        return "mismatched", "Results use different declared protocols; deltas are descriptive only."
    if first.get("source_kind") != "measured" or second.get("source_kind") != "measured":
        return "unknown", "At least one result is not executor-observed measurement."
    if first.get("measurement_id") == second.get("measurement_id"):
        return "unknown", "The same physical measurement cannot be its own independent control."
    x, y = first.get("asset_integrity"), second.get("asset_integrity")
    if x or y:
        if not isinstance(x, Mapping) or not isinstance(y, Mapping):
            return "unknown", "Only one measurement has asset-integrity observations."
        if x["status"] == "changed" or y["status"] == "changed":
            return "mismatched", "A declared protected asset changed during execution."
        if x["content_fingerprint"] != y["content_fingerprint"]:
            return "mismatched", "Declared protected files differ between measurements."
        if x["status"] == y["status"] == "observed_unchanged":
            return "declared_match", "Declared protocols and explicitly checked file contents match at the observed boundaries."
    return "declared_match", "Declared protocols match; independent asset integrity is not yet verified."

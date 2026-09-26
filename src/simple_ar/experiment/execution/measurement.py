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


def comparable_protocol(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Project comparison conditions, excluding candidate identity and research prose."""
    return {key: contract.get(key) for key in (
        "protocol_revision", "dataset_refs", "split_spec", "metric_specs",
        "comparison_conditions", "protected_assets",
    )}


def _protocol_fingerprint(contract: Mapping[str, Any], result_schema: Mapping[str, Any]) -> str | None:
    protocol = comparable_protocol(contract)
    if not all(protocol.get(key) for key in (
        "dataset_refs", "split_spec", "metric_specs", "comparison_conditions",
    )):
        return None
    return hashlib.sha256(json.dumps(
        {"protocol": protocol, "result_schema": dict(result_schema)},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")).hexdigest()


def measurement_record(
    run: Any, contract: Mapping[str, Any] | None, result_schema: Mapping[str, Any],
) -> dict[str, Any]:
    process = getattr(run, "process_record", {})
    protocol = dict(contract or {})
    fingerprint = _protocol_fingerprint(protocol, result_schema)
    return {
        "schema_version": "experiment_measurement.v1",
        "measurement_id": process.get("invocation_id"),
        "condition_id": getattr(run, "label", "experiment"),
        "source_kind": "measured" if process.get("invocation_id") else "unverified_backend",
        "protocol_id": protocol.get("contract_id"),
        "protocol_revision": protocol.get("protocol_revision"),
        "protocol_fingerprint": fingerprint,
        "protocol_status": "declared" if fingerprint else "incomplete",
        "seed": protocol.get("comparison_conditions", {}).get("seed"),
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
    # Re-project persisted contracts so old whole-contract fingerprints and new
    # condition-only fingerprints can be compared without rewriting history.
    contracts = (baseline.get("experiment_contract"), candidate.get("experiment_contract"))
    if any(isinstance(contract, Mapping) for contract in contracts):
        if not all(isinstance(contract, Mapping) for contract in contracts):
            return "unknown", "One result lacks its persisted comparison contract."
        a = _protocol_fingerprint(contracts[0], baseline.get("result_schema") or {})
        b = _protocol_fingerprint(contracts[1], candidate.get("result_schema") or {})
        if not a or not b:
            return "unknown", "Comparison protocol is incomplete."
    if a != b:
        return "mismatched", "Results use different declared protocols; deltas are descriptive only."
    placeholder_reason = _unverified_protocol_reason(baseline, candidate)
    if placeholder_reason:
        return "unknown", placeholder_reason
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


def _unverified_protocol_reason(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any],
) -> str | None:
    """Reject framework placeholders as proof that two measurements are comparable.

    Prepared execution can supply a command boundary without declaring the
    dataset or split used by that command.  The resulting fingerprint is
    useful for lineage, but it cannot establish a scientific comparison.
    Explicit contracts with concrete split/dataset facts remain eligible even
    when no protected file was named.
    """

    for result in (baseline, candidate):
        contract = result.get("experiment_contract")
        if not isinstance(contract, Mapping):
            continue
        split = contract.get("split_spec")
        if isinstance(split, Mapping) and str(split.get("status") or "").strip().lower() == "not_declared_by_framework":
            return "Comparison protocol contains an execution-boundary placeholder split; comparability is unverified."
    return None

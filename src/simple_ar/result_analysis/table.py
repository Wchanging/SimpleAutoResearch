"""Explicit, descriptive table analysis; no model, experiment or code execution.

The input capability freezes the supplied bytes and settings once. The analysis
capability consumes that registered snapshot, so recovery never rereads a user's
changed file. Aggregated values and row-level observations remain distinct.
"""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
import io
import json
import math
from pathlib import Path
import statistics

from simple_ar.core.capabilities import ArtifactRef, CapabilityContext, CapabilityResult


@dataclass(frozen=True)
class TableSpec:
    value_columns: tuple[str, ...]
    observation_unit: str
    group_column: str = ""
    value_unit: str = ""
    mode: str = "observations"
    missing: str = "reject"
    width: str = "wide"
    max_mb: int = 20
    max_figures: int = 100

    def __post_init__(self):
        if not self.value_columns or any(not isinstance(v, str) or not v.strip() for v in self.value_columns):
            raise ValueError("Select at least one nonempty numeric value column; columns are not guessed.")
        if len(set(self.value_columns)) != len(self.value_columns) or self.group_column in self.value_columns:
            raise ValueError("Value columns must be distinct and cannot be the grouping column.")
        if not isinstance(self.observation_unit, str) or not self.observation_unit.strip():
            raise ValueError("State what one row represents (observation_unit).")
        if not isinstance(self.group_column, str) or not isinstance(self.value_unit, str):
            raise ValueError("group_column and value_unit must be strings.")
        if self.mode not in {"observations", "values"} or self.missing not in {"reject", "omit"}:
            raise ValueError("mode must be observations/values; missing must be reject/omit.")
        if self.width not in {"column", "wide"} or type(self.max_mb) is not int or self.max_mb < 1:
            raise ValueError("width must be column/wide; max_mb must be a positive integer.")
        if type(self.max_figures) is not int or self.max_figures < 1:
            raise ValueError("max_figures must be a positive physical output limit.")
        if self.mode == "values" and not self.group_column:
            raise ValueError("Already aggregated values require a unique row-label group_column; no re-aggregation is inferred.")

    @classmethod
    def from_config(cls, config):
        values = config.get("value_columns", [])
        if not isinstance(values, (list, tuple)):
            raise ValueError("value_columns must be a list of explicit column names.")
        return cls(tuple(values), config.get("observation_unit", ""),
                   config.get("group_column", ""), config.get("value_unit", ""),
                   config.get("mode", "observations"), config.get("missing", "reject"),
                   config.get("width", "wide"), config.get("max_mb", 20), config.get("max_figures", 100))


def parse_table(text: str, suffix: str) -> list[dict]:
    """Read one homogeneous records array or a rectangular UTF-8 CSV/TSV."""
    if suffix == ".json":
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError(f"Duplicate JSON key: {key!r}")
                result[key] = value
            return result
        rows = json.loads(text, object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Nonfinite JSON number: {value}")))
        if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
            raise ValueError("JSON input must be a nonempty array of homogeneous records, not a nested object.")
        keys = set(rows[0])
        if not keys or any(set(row) != keys for row in rows):
            raise ValueError("JSON records must have the same nonempty column set.")
        if any(isinstance(value, (dict, list)) for row in rows for value in row.values()):
            raise ValueError("Nested JSON cells are not supported.")
        return rows
    if suffix not in {".csv", ".tsv"}:
        raise ValueError("Data input must be UTF-8 CSV, TSV or a JSON records array.")
    reader = csv.reader(io.StringIO(text), delimiter="\t" if suffix == ".tsv" else ",", strict=True)
    header = next(reader, [])
    if not header or any(not name.strip() for name in header) or len(set(header)) != len(header):
        raise ValueError("Table header must contain distinct nonempty column names.")
    rows = []
    for index, row in enumerate(reader, start=2):
        if not row:
            continue
        if len(row) != len(header):
            raise ValueError(f"Row {index} has {len(row)} cells; expected {len(header)}.")
        rows.append(dict(zip(header, row)))
    if not rows:
        raise ValueError("Data table has no observations.")
    return rows


def describe_table(rows: list[dict], spec: TableSpec) -> dict:
    required = [*spec.value_columns, *([spec.group_column] if spec.group_column else [])]
    unknown = set(required) - set(rows[0])
    if unknown:
        raise ValueError(f"Selected columns not found: {sorted(unknown)!r}")
    groups = {}
    for index, row in enumerate(rows, start=1):
        raw_group = row.get(spec.group_column) if spec.group_column else "All observations"
        if raw_group is None or str(raw_group).strip() == "":
            raise ValueError(f"Missing grouping value at data row {index}.")
        group = str(raw_group)
        if spec.mode == "values" and group in groups:
            raise ValueError("Already aggregated values require unique labels; refusing to average repeated summaries.")
        bucket = groups.setdefault(group, {name: [] for name in spec.value_columns})
        for name in spec.value_columns:
            value = row[name]
            if value is None or (isinstance(value, str) and not value.strip()):
                if spec.missing == "reject":
                    raise ValueError(f"Missing {name!r} at data row {index}; choose omit explicitly, not zero imputation.")
                bucket[name].append(None)
                continue
            if isinstance(value, bool):
                raise ValueError(f"Boolean is not a numeric observation: {name!r}, row {index}.")
            try:
                number = float(value)
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError(f"Nonnumeric {name!r} at data row {index}.") from exc
            if not math.isfinite(number):
                raise ValueError(f"Nonfinite {name!r} at data row {index}.")
            bucket[name].append(number)
    records = []
    for group, columns in groups.items():
        for name, cells in columns.items():
            values = [v for v in cells if v is not None]
            if not values:
                raise ValueError(f"No numeric values remain for {name!r}, group {group!r}.")
            record = {"group": group, "column": name, "count": len(values), "missing": len(cells) - len(values)}
            if spec.mode == "observations":
                mean = statistics.mean(values)
                std = statistics.stdev(values) if len(values) > 1 else None
                if not math.isfinite(mean) or (std is not None and not math.isfinite(std)):
                    raise ValueError("Computed statistic exceeds finite numeric range.")
                record.update(mean=mean, sample_std=std, min=min(values), max=max(values))
            else:
                record["value"] = values[0]
            records.append(record)
    return {"schema_version": "table_analysis.v1", "status": "completed", "evidence_role": "computed_from_user_supplied_data",
            "row_count": len(rows), "spec": asdict(spec), "records": records,
            "limitations": ["Input collection and column semantics are user-declared, not independently verified.",
                            "Descriptive only: no significance, causal, paired-comparison or replication claim.",
                            "No uncertainty bars are inferred; sample standard deviation describes rows, not confidence in a method."]}


def snapshot_table_capability(context: CapabilityContext, request: dict) -> CapabilityResult:
    path = Path(request.get("file", "")).expanduser().resolve()
    spec = TableSpec.from_config(request)
    if path.suffix.lower() not in {".csv", ".tsv", ".json"} or not path.is_file():
        raise ValueError("Provide an existing UTF-8 CSV/TSV or JSON records file.")
    with path.open("rb") as handle:
        raw = handle.read(spec.max_mb * 1024 * 1024 + 1)
    if len(raw) > spec.max_mb * 1024 * 1024:
        raise ValueError(f"Data exceeds the configured {spec.max_mb} MiB input limit.")
    text = raw.decode("utf-8-sig")
    # Validate before committing a usable snapshot; settings are frozen with it.
    describe_table(parse_table(text, path.suffix.lower()), spec)
    source = context.store.ref("input" + path.suffix.lower(), kind="user_data")
    context.store.resolve(source).write_bytes(raw)
    ref = context.store.write_json("table_input.json", {"schema_version": "table_input.v1", "source_name": path.name,
        "source": source.to_dict(), "spec": asdict(spec), "input_bytes": len(raw)}, kind="table_input", schema="table_input.v1")
    return CapabilityResult("completed", (ref, source))


def analyze_table_capability(context: CapabilityContext, request: ArtifactRef) -> CapabilityResult:
    from simple_ar.report.figures import render_table_figures
    payload = json.loads(context.require_input(request).read_text(encoding="utf-8"))
    if payload.get("schema_version") != "table_input.v1":
        raise ValueError("Expected a table_input.v1 snapshot.")
    spec = TableSpec.from_config(payload["spec"])
    # The source is a sibling of this registered snapshot, never the original locator.
    source_ref = ArtifactRef.from_dict(payload["source"])
    source = context.require_input(request).parent / source_ref.path
    raw = source.read_bytes()
    text = raw.decode("utf-8-sig")
    result = describe_table(parse_table(text, source.suffix.lower()), spec)
    copied = context.store.ref(source.name, kind="user_data")
    context.store.resolve(copied).write_bytes(raw)
    result["source"] = copied.to_dict()
    result["source_name"] = payload["source_name"]
    figures = render_table_figures(result, context.store.root)
    result["figures"] = figures
    ref = context.store.write_json("analysis.json", result, kind="table_analysis", schema="table_analysis.v1")
    lines = ["# Descriptive data analysis", "", f"Input: `{payload['source_name']}`; rows: {result['row_count']}.",
             f"Observation unit (user-declared): {spec.observation_unit}. Value unit: {spec.value_unit or 'not supplied'}.",
             f"Mode: {spec.mode}; missing policy: {spec.missing}.", ""]
    for row in result["records"]:
        metric = f"mean={row['mean']:.12g}, sample_std={row['sample_std']}" if spec.mode == "observations" else f"value={row['value']:.12g}"
        lines.append(f"- {row['group']!r} / {row['column']!r}: {metric}; n={row['count']}; missing={row['missing']}.")
    lines.extend(["", *[f"![Descriptive values]({item['path']})\n\n{item['caption']}\n" for item in figures],
                  "## Limits", "", *result["limitations"], "", "Rebuild from this directory (no model/API calls):", "", "```sh",
                  "python -m simple_ar.result_analysis.table analysis.json", "```", "",
                  "The copied input can contain sensitive data; review before sharing this directory."])
    report = context.store.write_text("analysis.md", "\n".join(lines) + "\n", kind="table_report")
    outputs = (ref, copied, report, *[context.store.ref(item["path"], kind="figure") for item in figures])
    return CapabilityResult("completed", tuple(outputs))


def rebuild(path: Path) -> None:
    from simple_ar.report.figures import render_table_figures
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "table_analysis.v1":
        raise ValueError("Expected a table_analysis.v1 result.")
    source = path.parent / ArtifactRef.from_dict(payload["source"]).path
    result = describe_table(parse_table(source.read_bytes().decode("utf-8-sig"), source.suffix.lower()), TableSpec.from_config(payload["spec"]))
    if result["records"] != payload["records"]:
        raise ValueError("Copied data no longer matches the saved results; not overwriting the original analysis.")
    render_table_figures(payload, path.parent)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Rebuild figures from a saved descriptive analysis and its copied data.")
    parser.add_argument("result", type=Path)
    rebuild(parser.parse_args().result.resolve())

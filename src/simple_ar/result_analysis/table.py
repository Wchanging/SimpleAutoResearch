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
    plot: str = "bar"
    x_column: str = ""
    x_unit: str = ""
    max_points: int = 10000

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
        if self.plot not in {"bar", "line", "scatter"}:
            raise ValueError("plot must be bar/line/scatter.")
        if not isinstance(self.x_column, str) or not isinstance(self.x_unit, str):
            raise ValueError("x_column and x_unit must be strings.")
        if type(self.max_points) is not int or self.max_points < 1:
            raise ValueError("max_points must be a positive physical SVG point limit.")
        if self.plot != "bar":
            if self.mode != "values" or self.group_column or not self.x_column.strip():
                raise ValueError("line/scatter require mode=values and an explicit numeric x_column, without group_column; each value column is plotted separately.")
            if self.x_column in self.value_columns:
                raise ValueError("x_column must differ from value_columns.")
        elif self.x_column or self.x_unit:
            raise ValueError("x_column/x_unit require a line or scatter plot.")
        if self.mode == "values" and self.plot == "bar" and not self.group_column:
            raise ValueError("Already aggregated values require a unique row-label group_column; no re-aggregation is inferred.")

    @classmethod
    def from_config(cls, config):
        values = config.get("value_columns", [])
        if not isinstance(values, (list, tuple)):
            raise ValueError("value_columns must be a list of explicit column names.")
        return cls(tuple(values), config.get("observation_unit", ""),
                   config.get("group_column", ""), config.get("value_unit", ""),
                   config.get("mode", "observations"), config.get("missing", "reject"),
                   config.get("width", "wide"), config.get("max_mb", 20), config.get("max_figures", 100),
                   config.get("plot", "bar"), config.get("x_column", ""), config.get("x_unit", ""),
                   config.get("max_points", 10000))


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
    validate_table_columns(rows, spec)
    if spec.plot != "bar":
        return _coordinate_values(rows, spec)
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
            bucket[name].append(_number(value, name, index))
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


def _number(value, name: str, index: int) -> float:
    if isinstance(value, bool):
        raise ValueError(f"Boolean is not a numeric observation: {name!r}, row {index}.")
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"Nonnumeric {name!r} at data row {index}.") from exc
    if not math.isfinite(number):
        raise ValueError(f"Nonfinite {name!r} at data row {index}.")
    return number


def _coordinate_values(rows: list[dict], spec: TableSpec) -> dict:
    """Preserve supplied coordinates and missing y values; never aggregate them."""
    if len(rows) > spec.max_points:
        raise ValueError(f"Data has {len(rows)} rows; exceeds physical max_points={spec.max_points}. Explicitly increase the limit or supply a smaller table; no points were sampled.")
    records, seen = [], set()
    valid_counts = dict.fromkeys(spec.value_columns, 0)
    for index, row in enumerate(rows, start=1):
        raw_x = row[spec.x_column]
        if raw_x is None or (isinstance(raw_x, str) and not raw_x.strip()):
            raise ValueError(f"Missing x coordinate at data row {index}; provide x for every row. omit applies only to y values.")
        x = _number(raw_x, spec.x_column, index)
        if spec.plot == "line" and x in seen:
            raise ValueError("Line plots require unique numeric x coordinates; no replicate aggregation is inferred. Use scatter or supply an explicit summary.")
        seen.add(x)
        for column in spec.value_columns:
            raw = row[column]
            missing = raw is None or (isinstance(raw, str) and not raw.strip())
            if missing and spec.missing == "reject":
                raise ValueError(f"Missing {column!r} at data row {index}; choose omit explicitly, not zero imputation.")
            records.append({"group": f"Row {index}", "column": column, "x": x,
                            "value": None if missing else _number(raw, column, index),
                            "count": 0 if missing else 1, "missing": int(missing)})
            valid_counts[column] += not missing
    if any(count == 0 for count in valid_counts.values()):
        raise ValueError("No numeric y values remain for at least one selected column.")
    return {"schema_version": "table_analysis.v1", "status": "completed",
            "evidence_role": "computed_from_user_supplied_data", "row_count": len(rows),
            "spec": asdict(spec), "records": records,
            "limitations": ["Input collection, coordinate semantics and units are user-declared, not independently verified.",
                            "Supplied coordinates only: no aggregation, smoothing, fitted trend, significance or uncertainty is inferred.",
                            "Line points are ordered by numeric x, not input order; missing y points remain explicit gaps."]}


def read_table_source(path: Path, *, max_mb: int) -> tuple[bytes, list[dict]]:
    """One bounded decoding/parser owner for setup and actual ingestion."""
    if type(max_mb) is not int or max_mb < 1:
        raise ValueError("max_mb must be a positive integer.")
    if path.suffix.lower() not in {".csv", ".tsv", ".json"} or not path.is_file():
        raise ValueError("Provide an existing UTF-8 CSV/TSV or JSON records file.")
    with path.open("rb") as handle:
        raw = handle.read(max_mb * 1024 * 1024 + 1)
    if len(raw) > max_mb * 1024 * 1024:
        raise ValueError(f"Data exceeds the configured {max_mb} MiB input limit.")
    try:
        return raw, parse_table(raw.decode("utf-8-sig"), path.suffix.lower())
    except UnicodeDecodeError as exc:
        raise ValueError("Data must be UTF-8 text; export a CSV/TSV or JSON records file in UTF-8.") from exc
    except csv.Error as exc:
        raise ValueError(f"Invalid delimited table: {exc}") from exc


def validate_table_columns(rows: list[dict], spec: TableSpec) -> None:
    """Report exact available names, without guessing column meaning or types."""
    if not rows:
        raise ValueError("Data table has no observations.")
    required = [*spec.value_columns, *([spec.group_column] if spec.group_column else []),
                *([spec.x_column] if spec.x_column else [])]
    available = list(rows[0])
    unknown = set(required) - set(available)
    if unknown:
        shown = available[:20]
        omitted = len(available) - len(shown)
        raise ValueError(f"Selected columns not found: {sorted(unknown)!r}. Available columns: {shown!r}"
                         + (f" ({omitted} more)" if omitted else "")
                         + ". Choose explicit column names; no field is inferred.")


def snapshot_table_capability(context: CapabilityContext, request: dict) -> CapabilityResult:
    path = Path(request.get("file", "")).expanduser().resolve()
    spec = TableSpec.from_config(request)
    raw, rows = read_table_source(path, max_mb=spec.max_mb)
    # Validate before committing a usable snapshot; settings are frozen with it.
    describe_table(rows, spec)
    source = context.store.ref("input" + path.suffix.lower(), kind="user_data")
    context.store.resolve(source).write_bytes(raw)
    ref = context.store.write_json("table_input.json", {"schema_version": "table_input.v1", "source_name": path.name,
        "source": source.to_dict(), "spec": asdict(spec), "input_bytes": len(raw)}, kind="table_input", schema="table_input.v1")
    return CapabilityResult("completed", (ref, source))


def analyze_table_capability(context: CapabilityContext, request: ArtifactRef) -> CapabilityResult:
    from simple_ar.result_analysis.figures import render_table_figures
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
    report = context.store.write_text("analysis.md", table_markdown(result), kind="table_report")
    outputs = (ref, copied, report, *[context.store.ref(item["path"], kind="figure") for item in figures])
    return CapabilityResult("completed", tuple(outputs))


def table_markdown(result: dict) -> str:
    """Describe computed values without changing their evidence role."""
    spec = TableSpec.from_config(result["spec"])
    lines = ["# Descriptive data analysis", "", f"Input: `{result['source_name']}`; rows: {result['row_count']}.",
             f"Observation unit (user-declared): {spec.observation_unit}. Value unit: {spec.value_unit or 'not supplied'}.",
             f"Mode: {spec.mode}; missing policy: {spec.missing}.", ""]
    for row in result["records"]:
        if spec.plot != "bar":
            metric = f"x={row['x']:.12g}, value={row['value'] if row['value'] is not None else 'missing'}"
        else:
            metric = f"mean={row['mean']:.12g}, sample_std={row['sample_std']}" if spec.mode == "observations" else f"value={row['value']:.12g}"
        lines.append(f"- {row['group']!r} / {row['column']!r}: {metric}; n={row['count']}; missing={row['missing']}.")
    lines.extend(["", *[f"![Descriptive values]({item['path']})\n\n{item['caption']}\n" for item in result["figures"]],
                  "## Limits", "", *result["limitations"], "", "Rebuild from this directory (no model/API calls):", "", "```sh",
                  "python -m simple_ar.result_analysis.table analysis.json", "```", "",
                  "The copied input can contain sensitive data; review before sharing this directory."])
    return "\n".join(lines) + "\n"


def load_analysis_package(path: Path, *, max_mb: int | None = 20) -> tuple[dict, bytes, str]:
    """Recheck a v1 result against its package-local input, not its prose/SVG.

    Imported packages are external input, not trusted just because they name
    our schema. Limit reads and reject paths/symlinks outside the selected pack.
    Never execute a supplied script or follow its figure/resource links.
    """
    path = path.resolve()
    limit = max_mb * 1024 * 1024 if max_mb is not None else None
    with path.open("rb") as handle:
        raw_result = handle.read(limit + 1) if limit is not None else handle.read()
    if limit is not None and len(raw_result) > limit:
        raise ValueError(f"Analysis package result exceeds the {max_mb} MiB writing import limit.")
    payload = json.loads(raw_result.decode("utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "table_analysis.v1" or payload.get("status") != "completed":
        raise ValueError("Writing JSON material must be a completed table_analysis.v1 package with its copied input.")
    if not isinstance(payload.get("spec"), dict) or not isinstance(payload.get("source"), dict):
        raise ValueError("Analysis package must include its column settings and copied input reference.")
    if not isinstance(payload["source"].get("path"), str) or not payload["source"]["path"]:
        raise ValueError("Analysis package must name its copied input with a relative path.")
    spec = TableSpec.from_config(payload["spec"])
    source_ref = ArtifactRef.from_dict(payload["source"])
    source = (path.parent / source_ref.path).resolve()
    if not source.is_relative_to(path.parent) or source == path:
        raise ValueError("Analysis input must stay inside the selected package directory.")
    input_limit = spec.max_mb * 1024 * 1024
    if limit is not None:
        input_limit = min(limit, input_limit)
    with source.open("rb") as handle:
        raw = handle.read(input_limit + 1)
    if len(raw) > input_limit:
        raise ValueError("Analysis input exceeds the configured size limit.")
    result = describe_table(parse_table(raw.decode("utf-8-sig"), source.suffix.lower()), spec)
    if result["records"] != payload.get("records") or result["row_count"] != payload.get("row_count"):
        raise ValueError("Copied data no longer matches the saved analysis; refusing stale or altered results.")
    result["source_name"] = Path(str(payload.get("source_name") or source.name)).name
    return result, raw, source.suffix.lower()


def copy_analysis_package(path: Path, output_dir: Path) -> dict:
    """Freeze rechecked data and rebuild native figures for a new consumer."""
    from simple_ar.core.capabilities import ArtifactStore
    from simple_ar.result_analysis.figures import render_table_figures
    result, raw, suffix = load_analysis_package(path)
    store = ArtifactStore(output_dir)
    source = store.ref("input" + suffix, kind="user_data")
    output_dir.mkdir(parents=True, exist_ok=True)
    store.resolve(source).write_bytes(raw)
    result["source"] = source.to_dict()
    result["figures"] = render_table_figures(result, output_dir)
    store.write_json("analysis.json", result, kind="table_analysis", schema="table_analysis.v1")
    store.write_text("analysis.md", table_markdown(result), kind="table_report")
    return result


def table_values_markdown(result: dict) -> str:
    """Deterministic numerical evidence for a report, without inferred tests."""
    from html import escape
    def cell(value):
        return escape(str(value)).replace("|", "&#124;").replace("\n", " ").replace("\r", " ")
    fields = ("mean", "sample_std", "min", "max") if result["spec"]["mode"] == "observations" else ("value",)
    if result["spec"].get("plot", "bar") != "bar":
        fields = ("x", *fields)
    lines = ["| Group | Column | Count | Missing | " + " | ".join(fields) + " |",
             "| --- | --- | ---: | ---: | " + " | ".join("---:" for _ in fields) + " |"]
    for row in result["records"]:
        numbers = ["not defined" if row[field] is None else f"{row[field]:.12g}" for field in fields]
        lines.append("| " + " | ".join([cell(row["group"]), cell(row["column"]), str(row["count"]), str(row["missing"]), *numbers]) + " |")
    spec = result["spec"]
    return "\n".join([f"Row unit (user-declared): {cell(spec['observation_unit'])}. Value unit: {cell(spec['value_unit'] or 'not supplied')}.",
                      f"Mode: {spec['mode']}; missing policy: {spec['missing']}.", "", *lines])


def rebuild(path: Path) -> None:
    from simple_ar.result_analysis.figures import render_table_figures
    # Rebuilding one's own package keeps its explicitly configured input limit.
    # The narrower external-writing limit must not restrict this expert entry.
    result, _, _ = load_analysis_package(path, max_mb=None)
    render_table_figures(result, path.parent)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Rebuild figures from a saved descriptive analysis and its copied data.")
    parser.add_argument("result", type=Path)
    rebuild(parser.parse_args().result.resolve())

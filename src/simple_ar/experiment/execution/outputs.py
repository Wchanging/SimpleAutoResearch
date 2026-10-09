"""Explicit process-output attachments; no cwd scan or stdout path inference."""
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath
import os
import re
import stat
import csv
import io
import json
import math

from simple_ar.core.capabilities import ArtifactStore

MAX_OUTPUT_BYTES = 2 * 1024 * 1024
PREVIEW_CHARACTERS = 1200
WINDOW_CHARACTERS = 2400


def output_files(schema: Mapping) -> dict[str, str]:
    files = schema.get("output_files", {})
    if not isinstance(files, Mapping):
        raise ValueError("result_schema.output_files must map names to relative files.")
    for name, value in files.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError("Output names must be non-empty strings of at most 80 characters.")
        if (not isinstance(value, str) or not value or "\\" in value
                or Path(value).is_absolute() or PureWindowsPath(value).drive
                or any(part in {".", "..", ""} for part in value.split("/"))):
            raise ValueError("Output files must be relative POSIX paths inside SIMPLE_AR_OUTPUT_DIR.")
    return dict(files)


def _read_output_text(root: Path, path: Path) -> tuple[str, int]:
    """Shared bounded read for previews and declared metric extraction."""
    relative = path.absolute().relative_to(root.absolute())
    if ".." in relative.parts:
        raise ValueError("Output attachment cannot escape its store.")
    current = root.absolute()
    if current.is_symlink():
        raise ValueError("Output root cannot be a symlink.")
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError("Output attachments cannot traverse symlinks.")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Output attachment must be a regular file.")
    with path.open("rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_OUTPUT_BYTES:
            raise ValueError("Output must be a regular text file within 2 MiB.")
        raw = stream.read(MAX_OUTPUT_BYTES + 1)
    if len(raw) > MAX_OUTPUT_BYTES:
        raise ValueError("Output grew beyond the text attachment limit.")
    text = raw.decode("utf-8-sig")
    if "\x00" in text:
        raise ValueError("Output attachment is not UTF-8 text.")
    return text, len(raw)


def metric_sources(schema: Mapping) -> dict:
    """Validate metric -> {output: alias, path: [str/int]} or column + match.

    CSV/TSV match values are exact cell strings (scalar values stringify).
    An empty match is valid only when the file contains exactly one data row.
    """
    files = output_files(schema)
    sources = schema.get("metric_sources", {})
    if not isinstance(sources, Mapping):
        raise ValueError("metric_sources must map metric names to explicit file selectors.")
    for name, source in sources.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or not isinstance(source, Mapping):
            raise ValueError("Metric sources need named mapping selectors.")
        alias = source.get("output")
        if not isinstance(alias, str) or alias not in files:
            raise ValueError("Metric source output must name a registered output_files alias.")
        suffix = Path(files[alias]).suffix.lower()
        if set(source) == {"output", "path"} and suffix == ".json":
            path = source["path"]
            if not isinstance(path, list) or not path or any(type(key) not in (str, int) or
                    (type(key) is int and key < 0) for key in path):
                raise ValueError("JSON metric path must be a nonempty list of string keys/nonnegative integer indices.")
        elif set(source) == {"output", "column", "match"} and suffix in {".csv", ".tsv"}:
            match = source["match"]
            if not isinstance(source["column"], str) or not source["column"] or not isinstance(match, Mapping):
                raise ValueError("CSV metric sources require a column and exact match mapping.")
            if len(match) > 4 or any(not isinstance(key, str) or not key or
                    type(value) not in (str, int, float, bool) or
                    (type(value) is float and not math.isfinite(value)) for key, value in match.items()):
                raise ValueError("CSV match accepts at most four finite scalar conditions.")
        else:
            raise ValueError("Metric selector must be JSON path or CSV/TSV column + match, without extra fields.")
    return {name: dict(source) for name, source in sources.items()}


def extract_file_metrics(store: ArtifactStore, evidence: list[dict], sources: Mapping,
                         stdout_metrics: Mapping) -> tuple[dict, dict, list[dict]]:
    """Read only available registered attempt outputs; return values, locations, errors."""
    available = {row["name"]: row for row in evidence if row.get("status") == "available"}
    metrics, locations, issues = {}, {}, []
    def unique_object(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("Duplicate JSON keys are not valid metric evidence.")
        return result
    def reject_constant(value):
        raise ValueError("Nonfinite JSON constants are not valid metric evidence.")
    for name, source in sources.items():
        location = dict(source)
        try:
            row = available.get(source["output"])
            if row is None:
                raise ValueError("Registered metric output is unavailable for this attempt.")
            location["artifact"] = row["artifact"]
            text, _ = _read_output_text(store.root, store.resolve(row["artifact"]))
            if "path" in source:
                value = json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)
                for key in source["path"]:
                    if not (type(key) is str and isinstance(value, dict) or
                            type(key) is int and isinstance(value, list)):
                        raise ValueError("JSON metric path does not match the value structure.")
                    value = value[key]
                if type(value) not in (int, float):
                    raise ValueError("JSON metric must be a number, not boolean or text.")
            else:
                reader = csv.DictReader(io.StringIO(text), delimiter="\t" if
                    Path(row["declared_file"]).suffix.lower() == ".tsv" else ",", strict=True)
                headers = reader.fieldnames or []
                if not headers or len(set(headers)) != len(headers) or any(not h for h in headers):
                    raise ValueError("CSV metric requires unique nonempty headers.")
                if not {source["column"], *source["match"]}.issubset(headers):
                    raise ValueError("CSV metric column or match field is missing.")
                chosen = []
                for index, record in enumerate(reader, 1):
                    if None in record or any(value is None for value in record.values()):
                        raise ValueError("CSV metric rows must match the header width.")
                    if all(record[key] == str(value) for key, value in source["match"].items()):
                        chosen.append((index, record[source["column"]]))
                if len(chosen) != 1:
                    raise ValueError("CSV metric match must select exactly one data row.")
                location["data_row"] = chosen[0][0]
                value = float(chosen[0][1])
            value = float(value)
            if not math.isfinite(value):
                raise ValueError("File metric must be finite.")
            if name in stdout_metrics and (type(stdout_metrics[name]) not in (int, float) or
                    stdout_metrics[name] != value):
                raise ValueError("File metric conflicts with stdout metric; stdout was not overwritten.")
        except (OSError, ValueError, UnicodeError, KeyError, IndexError, OverflowError, RecursionError, csv.Error) as exc:
            locations[name] = {**location, "status": "failed", "reason": str(exc)}
            issues.append({"severity": "error", "code": "file_metric_invalid",
                           "message": f"Metric `{name}`: {exc}"})
        else:
            metrics[name] = value
            locations[name] = {**location, "status": "extracted",
                               "stdout_agrees": name in stdout_metrics,
                               "verification": "producer_output_not_independently_verified"}
    return metrics, locations, issues


def read_output_window(root: Path, path: Path, *, offset: int = 0,
                       limit: int = WINDOW_CHARACTERS, query: str = "",
                       record_match: Mapping | None = None, overview: bool = False) -> dict:
    """Bounded UTF-8 read of an explicitly registered file, with coverage."""
    if type(offset) is not int or not 0 <= offset <= MAX_OUTPUT_BYTES:
        raise ValueError("Output offset is outside the bounded character range.")
    if type(limit) is not int or not 1 <= limit <= WINDOW_CHARACTERS:
        raise ValueError("Output window must contain 1..2400 characters.")
    if not isinstance(query, str) or len(query) > 200:
        raise ValueError("Output query must be a literal phrase of at most 200 characters.")
    if record_match is not None and not isinstance(record_match, Mapping):
        raise ValueError("Record selection must map field names to scalar values.")
    selector = dict(record_match or {})
    if len(selector) > 4 or any(not isinstance(key, str) or not key or len(key) > 120
                              or not isinstance(value, (str, int, float, bool, type(None)))
                              for key, value in selector.items()):
        raise ValueError("Record selection accepts at most four scalar field/value conditions.")
    if query and selector:
        raise ValueError("Choose literal query or exact record selection, not both.")
    text, byte_count = _read_output_text(root, path)
    overview_structure = {}
    if overview and not offset and not query and not selector and path.suffix.lower() == ".json":
        # Small producer objects often place runtime/protocol after a long
        # pretty-printed metrics block. Show the entire object when lossless
        # whitespace removal fits one existing tool window, not selected keys.
        def unique_object(pairs):
            result = dict(pairs)
            if len(result) != len(pairs):
                raise ValueError("Duplicate JSON keys cannot be compacted losslessly.")
            return result
        try:
            value = json.loads(text, object_pairs_hook=unique_object)
            compact = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (ValueError, RecursionError):
            pass  # Malformed/ambiguous JSON remains an ordinary raw preview.
        else:
            if isinstance(value, dict):
                keys = list(value)
                overview_structure = {"object_fields": keys[:64], "object_fields_omitted": max(0, len(keys) - 64),
                    "structure_scope": "top_level_keys_only_not_field_contents"}
            if isinstance(value, dict) and len(compact) <= WINDOW_CHARACTERS:
                return {"text": compact, "offset": 0, "next_offset": len(text),
                        "total_characters": len(text), "bytes": byte_count,
                        "rendered_characters": len(compact), "truncated": False, "has_more": False,
                        "query": "", "query_matched": None, "search_scope": "registered_utf8_file",
                        "view_kind": "complete_json_object_whitespace_compacted",
                        "verification": "producer_output_not_independently_verified"}
    selection = {}
    if selector:
        csv_source = path.suffix.lower() in {".csv", ".tsv"}
        if csv_source:
            records = list(csv.DictReader(io.StringIO(text), delimiter="\t" if path.suffix.lower() == ".tsv" else ","))
        elif path.suffix.lower() == ".json":
            records = json.loads(text)
        else:
            raise ValueError("Exact record selection supports JSON record arrays and headered CSV/TSV only.")
        if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
            raise ValueError("Output is not a record array; use a literal text query instead.")
        def matches(row):
            return all(key in row and (row[key] == str(value) if csv_source else
                       row[key] == value and isinstance(row[key], bool) == isinstance(value, bool))
                       for key, value in selector.items())
        chosen = [row for row in records if matches(row)]
        # This is a projection of producer rows, never a calculation or sample
        # presented as the full file. Offsets refer to the selected JSON view.
        text = json.dumps(chosen, ensure_ascii=False, indent=2)
        selection = {"record_match": selector, "source_records": len(records),
                     "matched_records": len(chosen), "view_kind": "selected_producer_records"}
    match = re.search(re.escape(query), text, re.IGNORECASE) if query else None
    if query and match:
        offset = max(0, match.start() - limit // 5)
    end = min(len(text), offset + limit)
    return {"text": "" if query and not match else text[offset:end], "offset": offset, "next_offset": end,
            "total_characters": len(text), "bytes": byte_count,
            "truncated": offset > 0 or end < len(text),
            "has_more": end < len(text), "query": query,
            "query_matched": bool(match) if query else None,
            "search_scope": "registered_record_array" if selector else "registered_utf8_file" if query else "character_window",
            "verification": "producer_output_not_independently_verified", **overview_structure, **selection}


def capture_outputs(store: ArtifactStore, directory: Path | None, files: Mapping[str, str]):
    rows, refs = [], []
    for name, filename in files.items():
        row = {"name": name, "declared_file": filename,
               "verification": "producer_output_not_independently_verified"}
        if directory is None:
            rows.append({**row, "status": "not_available", "reason": "No local process output directory."})
            continue
        path = directory / filename
        try:
            preview = read_output_window(store.root, path, limit=PREVIEW_CHARACTERS, overview=True)
            ref = store.ref(path, kind="experiment_output", schema="text.v1", producer="research.experiment")
        except (OSError, ValueError, UnicodeError) as exc:
            rows.append({**row, "status": "missing" if isinstance(exc, FileNotFoundError) else "unreadable",
                         "reason": str(exc)})
        else:
            rows.append({**row, "status": "available", "artifact": ref.path, "preview": preview})
            refs.append(ref)
    return rows, refs

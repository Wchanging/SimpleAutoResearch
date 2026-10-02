"""Explicit process-output attachments; no cwd scan or stdout path inference."""
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath
import os
import re
import stat
import csv
import io
import json

from simple_ar.core.capabilities import ArtifactStore

MAX_OUTPUT_BYTES = 2 * 1024 * 1024
PREVIEW_CHARACTERS = 1200
WINDOW_CHARACTERS = 2400


def output_files(schema: Mapping) -> dict[str, str]:
    files = schema.get("output_files", {})
    if not isinstance(files, Mapping) or len(files) > 8:
        raise ValueError("result_schema.output_files must map at most eight names to relative files.")
    for name, value in files.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError("Output names must be non-empty strings of at most 80 characters.")
        if (not isinstance(value, str) or not value or "\\" in value
                or Path(value).is_absolute() or PureWindowsPath(value).drive
                or any(part in {".", "..", ""} for part in value.split("/"))):
            raise ValueError("Output files must be relative POSIX paths inside SIMPLE_AR_OUTPUT_DIR.")
    return dict(files)


def read_output_window(root: Path, path: Path, *, offset: int = 0,
                       limit: int = WINDOW_CHARACTERS, query: str = "",
                       record_match: Mapping | None = None) -> dict:
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
    relative = path.absolute().relative_to(root.absolute())
    if ".." in relative.parts:
        raise ValueError("Output attachment cannot escape its store.")
    current = root.absolute()
    # Reject links in the authorized subtree, including its root. The outer
    # workspace may itself live on a mount or symlink chosen by the user.
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
            "total_characters": len(text), "bytes": len(raw),
            "truncated": offset > 0 or end < len(text),
            "has_more": end < len(text), "query": query,
            "query_matched": bool(match) if query else None,
            "search_scope": "registered_record_array" if selector else "registered_utf8_file" if query else "character_window",
            "verification": "producer_output_not_independently_verified", **selection}


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
            preview = read_output_window(store.root, path, limit=PREVIEW_CHARACTERS)
            ref = store.ref(path, kind="experiment_output", schema="text.v1", producer="research.experiment")
        except (OSError, ValueError, UnicodeError) as exc:
            rows.append({**row, "status": "missing" if isinstance(exc, FileNotFoundError) else "unreadable",
                         "reason": str(exc)})
        else:
            rows.append({**row, "status": "available", "artifact": ref.path, "preview": preview})
            refs.append(ref)
    return rows, refs

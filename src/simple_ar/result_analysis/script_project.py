"""Prepare a tiny existing-project CodeTask; never generate or execute code here.

Main owns authorization, shared clients/ledger, and the 60/300-second execution
limit. Pass PROTECTED_PATTERNS and data_inputs to CodeTask initialization.
An existing or partially prepared root must not be reused as a new project.
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
from shutil import which

from simple_ar.core.artifacts import write_text
from simple_ar.result_analysis.table import preview_table_source


MAX_DATA_BYTES = 20 * 1024 * 1024
PROTECTED_PATTERNS = ("data/**", "tests/**")
SCRIPT_EDIT_PATTERNS = ("analysis.py", "src/**", "outputs/**")
VALIDATION_COMMAND = ("python", "tests/verify_delivery.py")


def copy_code_analysis_package(source: Path, destination: Path | None, *, workspace: bool = False) -> dict:
    """Copy declared script deliverables without executing or certifying results.

    Initial publication follows a successful CodeTask validation. Later copies
    use only the saved relative inventory, so relocation needs no old workspace.
    """
    from shutil import copy2
    from simple_ar.core.artifacts import read_json, write_json

    root = source.resolve() if workspace else source.resolve().parent
    if workspace:
        files = ["analysis.py", "README.md", "outputs/report.md", "outputs/results.json"]
        files.extend(path.relative_to(root).as_posix() for folder in ("data", "tests", "src", "outputs")
                     for path in (root / folder).rglob("*") if path.is_file() and "__pycache__" not in path.parts)
        files.extend(path.relative_to(root).as_posix() for path in root.glob("*")
                     if path.is_file() and path.suffix in {".py", ".dot"} and path.name != "analysis.py")
        figure_groups = {}
        for name in sorted(set(files)):
            path = Path(name)
            if path.parts[0] == "outputs" and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".svg", ".pdf"}:
                figure_groups.setdefault(path.with_suffix("").as_posix(), {})[path.suffix.lower()[1:]] = name
        figures = []
        for stem, exports in figure_groups.items():
            if set(exports) == {"pdf"}:
                continue  # A standalone PDF may be a document, not a figure.
            primary = next(exports[ext] for ext in ("png", "jpg", "jpeg", "svg", "pdf") if ext in exports)
            figures.append({"path": primary, "caption": f"Figure: {Path(stem).name}.",
                            "exports": {ext: name for ext, name in exports.items() if name != primary}})
        result = {"schema_version": "code_analysis.v1", "files": list(dict.fromkeys(files)),
                  "figures": figures,
                  "evidence_role": "validated_script_output_not_independently_recomputed"}
    else:
        result = read_json(source)
        if not isinstance(result, dict) or result.get("schema_version") != "code_analysis.v1":
            raise ValueError("Expected a code_analysis.v1 package.")
    files = result.get("files")
    required = {"analysis.py", "outputs/report.md", "outputs/results.json"}
    if not isinstance(files, list) or not all(isinstance(path, str) for path in files) or not required.issubset(files):
        raise ValueError("Code analysis package is missing its source or delivery inventory.")
    resolved = []
    for name in files:
        relative = Path(name)
        path = (root / relative).resolve()
        if relative.is_absolute() or ".." in relative.parts or not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Code analysis attachments must be existing package-local files.")
        resolved.append((relative, path))
    if sum(path.stat().st_size for _, path in resolved) > 4 * MAX_DATA_BYTES:
        raise ValueError("Code analysis attachment package exceeds 80 MiB.")
    figures = result.get("figures")
    if not isinstance(figures, list):
        raise ValueError("Code analysis figures must be a list (empty for results without figures).")
    for figure in figures:
        if (not isinstance(figure, dict) or not isinstance(figure.get("caption"), str)
                or not isinstance(figure.get("exports", {}), dict)
                or any(path not in files for path in [figure.get("path"), *figure.get("exports", {}).values()])):
            raise ValueError("Code analysis figures must refer to inventoried attachments.")
    recorded = read_json(root / "outputs/results.json")
    if not isinstance(recorded, (dict, list)) or not recorded:
        raise ValueError("Code analysis results must be a nonempty JSON object or array.")
    result = {**result, "results": recorded}
    if destination is None:
        return result
    # Validate the entire inventory before copying; never follow package paths
    # into another project, even if the manifest says the project was validated.
    for relative, path in resolved:
        target = destination / relative
        if not target.resolve().is_relative_to(destination.resolve()):
            raise ValueError("Code analysis destination must stay inside its package.")
        target.parent.mkdir(parents=True, exist_ok=True)
        copy2(path, target)
    write_json(destination / "analysis.json", result)
    return result

_ANALYSIS = '''"""Implement the complete analysis/diagram goal and input paths in README.md.

Replace this placeholder with a complete runnable module, not just a main body.
"""
raise SystemExit("Analysis is not implemented; implement README.md first.")
'''

_VERIFY = '''"""Check delivery structure, not statistical or scientific correctness."""
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def main():
    subprocess.run([sys.executable, str(ROOT / "analysis.py")], cwd=ROOT, check=True)
    outputs = ROOT / "outputs"
    results = json.loads((outputs / "results.json").read_text(encoding="utf-8"))
    if not isinstance(results, (dict, list)) or not results:
        raise ValueError("outputs/results.json must be a nonempty JSON object or array")
    if not (outputs / "report.md").read_text(encoding="utf-8").strip():
        raise ValueError("outputs/report.md must contain an explanation")
    for path in outputs.rglob("*"):
        if not path.resolve().is_relative_to(outputs.resolve()):
            raise ValueError("Output files must stay inside the delivery directory")
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                image.load()
        elif path.suffix.lower() == ".svg":
            import xml.etree.ElementTree as ET
            if ET.parse(path).getroot().tag != "{http://www.w3.org/2000/svg}svg":
                raise ValueError(f"Invalid SVG document: {path.name}")
__MODE_CHECK__
    print("Delivery files are readable; statistical or diagram correctness was not verified.")


if __name__ == "__main__":
    main()
'''


_DIAGRAM_CHECK = '''    import xml.etree.ElementTree as ET
    with Image.open(outputs / "figure.png") as image:
        if image.format != "PNG":
            raise ValueError("Diagram preview must be outputs/figure.png")
    svg = ET.parse(outputs / "figure.svg").getroot()
    if svg.tag != "{http://www.w3.org/2000/svg}svg":
        raise ValueError("outputs/figure.svg must be an SVG document")
    elements = list(svg.iter())
    vectors = [e for e in elements if e.tag.startswith("{http://www.w3.org/2000/svg}")]
    if any(e.tag.rsplit("}", 1)[-1] in {"image", "feImage", "foreignObject", "script"}
           or any("data:image" in v.lower() for v in e.attrib.values()) for e in elements):
        raise ValueError("Diagram SVG must contain editable vectors, not embedded raster content")
    if not any((e.tag.rsplit("}", 1)[-1] == "text" and "".join(e.itertext()).strip())
               or (e.tag.rsplit("}", 1)[-1] == "path" and e.get("d", "").strip())
               or (e.tag.rsplit("}", 1)[-1] in {"rect", "circle", "ellipse", "line", "polyline", "polygon"}
                   and any(e.get(a, "").strip() for a in ("width", "r", "rx", "x2", "y2", "points")))
               for e in vectors):
        raise ValueError("Diagram SVG must contain text, paths or shapes")
    if not isinstance(results, dict) or not isinstance(results.get("components"), list) or not results["components"]:
        raise ValueError("Diagram results must record nonempty components")
    if not isinstance(results.get("relationships"), list) or not isinstance(results.get("input_sources"), list) or not results["input_sources"]:
        raise ValueError("Diagram results must record relationships and input_sources")'''


def prepare_script_project(root: Path, data: Path | None, goal: str, *,
                           diagram: bool = False) -> tuple[Path, tuple[str, ...]]:
    """Return a new project root and argv for its delivery validation command.

    Use kind='existing_project', workspace_mode='copy', task_file=root/'README.md',
    data_inputs=('data/input<suffix>',) when supplied, otherwise (), and protected
    patterns data/**, tests/**. diagram=True permits no data and requires vector SVG.
    Implement with baseline skipped before running the returned command through
    the existing runner. Analysis figures follow the user goal; their presence
    and scientific meaning require task-level review, not a fixed file count.
    Preparation only snapshots a regular file of at most 20 MiB and writes source.
    """
    root = Path(root).absolute()
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("Supply a nonempty analysis goal")
    if root.exists() or root.is_symlink():
        raise FileExistsError("Script project root must be new, not an existing directory")
    if data is None and not diagram:
        raise ValueError("Analysis requires data; omit data only for diagram=True")
    content, preview, input_name = b"", None, None
    if data is not None:
        data = Path(data).absolute()
        if data.name.lower() == ".env" or data.name.lower().startswith(".env."):
            raise ValueError("Environment/credential files are not analysis data")
        if data.is_symlink() or not data.is_file():
            raise ValueError("Analysis data must be a regular local file, not a symlink")
        if data.stat().st_size > MAX_DATA_BYTES:
            raise ValueError("Analysis data exceeds the 20 MiB limit")
        with data.open("rb") as stream:
            content = stream.read(MAX_DATA_BYTES + 1)
        if len(content) > MAX_DATA_BYTES:
            raise ValueError("Analysis data exceeds the 20 MiB limit")
        preview = preview_table_source(data, max_mb=20)
        input_name = "data/input" + data.suffix
    facts = []
    for package in ("matplotlib", "numpy", "pandas", "scipy", "Pillow"):
        try:
            facts.append(f"- {package}: installed distribution version {version(package)}")
        except PackageNotFoundError:
            facts.append(f"- {package}: distribution not found in the preparing interpreter")
    facts.append(f"- Graphviz dot executable: {which('dot') or 'not found in the preparing environment'}")
    mode_requirements = '''- Create a conceptual method diagram, optionally combined with supplied data.
  Choose a mature graph-layout renderer when available for node/edge structure;
  do not hand-place every node and arrow by default. Keep feedback and other
  non-forward edges outside node interiors. If the supplied environment lacks
  such a renderer, use available vector/layout libraries without installing tools.
  Retain editable analysis.py and the graph/layout source when applicable;
  prefer SVG text labels (Matplotlib svg.fonttype='none') and vector paths/shapes.
- Write outputs/figure.svg as parseable SVG with actual editable text, paths or
  shapes, never a PNG/JPEG embedded in an SVG wrapper. PNG remains required.
- Write outputs/results.json as an object with components, relationships and
  input_sources lists describing what was drawn and its sources (including the
  user goal). Do not invent measured values, observations or performance claims.
  Mark proposed/conceptual relationships as such; distinguish supplied evidence.
''' if diagram else '''- Write outputs/results.json as a nonempty object/array of actually computed
  values and their provenance; never fabricate observations, scores or p-values.
'''
    readme = f'''{goal}

## Implementation requirements

- Implement analysis.py as the runnable entry point for the complete goal above.
  Put reusable helper modules and editable layout sources under src/ when useful;
  they are included in the portable delivery. Small tasks may use analysis.py alone.
  {f'Supplied input: {input_name} ({len(content)} bytes).' if input_name else 'No dataset was supplied; draw the conceptual method, without dummy data or numerical measurements.'}
- Choose available mature analysis and rendering tools appropriate to the goal;
  Matplotlib is a default for data panels, not a restriction for method diagrams.
  Methods, plot types and multi-panel compositions are not restricted to presets.
- When data is supplied, inspect structure, column meanings, units and observation units first.
  Never infer pairing from row order or similar labels. If pairing, independence,
  sampling or another required meaning is unknown, state the missing information;
  do not claim a paired test or unsupported inferential conclusion.
- Document statistical assumptions, missing-value handling, excluded rows, sample
  sizes, transformations, uncertainty definitions and random seeds where relevant.
- Read data/** without modifying it. Never edit tests/** or bypass validation.
{mode_requirements.rstrip()}
- Write outputs/report.md explaining methods, results, assumptions and limitations.
- {'Write outputs/figure.png and outputs/figure.svg for the requested diagram.' if diagram else 'Create figures only when requested or useful to answer the goal; table-only analysis needs no placeholder plot.'}
  Use descriptive filenames under outputs/ for each figure/table, and link every
  delivered figure/table from outputs/report.md with its meaning and source.
  Additional figures and SVG/PDF exports are supported. Use readable labels and units.
  When using Matplotlib, select a noninteractive backend for unattended runs.
- Keep all generated files in this project, with deliverables under outputs/.
  No dependency installation, network access or destructive actions are authorized.
- Validation command: python tests/verify_delivery.py. It runs analysis.py and
  checks JSON, explanation, supplied image readability{' and required editable SVG structure' if diagram else ''}; it does not verify
  statistical correctness, diagram semantics or the truth of proposed relations.
- Generation/editing and execution authorization and time limits belong to main.
  Workspace/edit-scope restrictions are not an OS sandbox.

## Available dependency facts

These are distribution metadata from the preparing interpreter, not import or
compatibility tests. If main selects another interpreter, recheck its environment
through the existing CodeTask environment flow. No packages were installed.

{chr(10).join(facts)}

## Observed input shape (not inferred scientific semantics)

{json.dumps(preview, ensure_ascii=False) if preview is not None else 'No data input; no table preview was performed.'}
'''
    root.mkdir(parents=True, exist_ok=False)
    (root / "data").mkdir()
    if input_name is not None:
        (root / input_name).write_bytes(content)
    write_text(root / "analysis.py", _ANALYSIS)
    write_text(root / "README.md", readme)
    write_text(root / "tests" / "verify_delivery.py", _VERIFY.replace("__MODE_CHECK__", _DIAGRAM_CHECK if diagram else ""))
    return root, VALIDATION_COMMAND

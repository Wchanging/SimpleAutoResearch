"""Export existing report artifacts through Pandoc, without running research."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any
from urllib.parse import unquote, urlsplit


class ReportExportError(ValueError):
    pass


def _run(argv: list[str], *, cwd: Path, text: str | None = None, timeout: int = 60,
         env: dict[str, str] | None = None) -> str:
    result = subprocess.run(argv, cwd=cwd, input=text, text=True, encoding="utf-8",
                            errors="replace", capture_output=True, timeout=timeout, env=env)
    if result.returncode:
        raise ReportExportError(f"{Path(argv[0]).name} failed: {(result.stderr or result.stdout)[-2500:]}")
    return result.stdout


def _nodes(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _nodes(item)


def _asset_path(root: Path, target: str) -> Path:
    url = urlsplit(target)
    if url.scheme or url.netloc or url.query or url.fragment:
        raise ReportExportError(f"Export requires a local image inside the report directory: {target}")
    path = (root / unquote(url.path)).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ReportExportError(f"Missing or out-of-scope report image: {target}")
    if path.suffix.lower() not in {".svg", ".png", ".jpg", ".jpeg", ".pdf"}:
        raise ReportExportError(f"Unsupported report image format: {path.suffix}")
    return path


def _copy_image(source: Path, output: Path, index: int) -> str:
    assets = output / "figures"
    assets.mkdir(exist_ok=True)
    target = assets / f"figure-{index}{source.suffix.lower()}"
    shutil.copyfile(source, target)
    if source.suffix.lower() == ".svg":
        # Framework figures are self-contained. Do not follow external image or
        # stylesheet references while converting a supplied SVG.
        text = source.read_text(encoding="utf-8")
        references = re.findall(r"(?:href\s*=\s*['\"]([^'\"]*)['\"])|(?:url\(([^)]*)\))", text, re.I)
        if re.search(r"<!DOCTYPE|<!ENTITY|@import", text, re.I) or any(
            not (href or css).strip().strip("'\"").startswith("#") for href, css in references
        ):
            raise ReportExportError("SVG contains external resource references; use a self-contained figure.")
        converter = shutil.which("rsvg-convert")
        if not converter:
            raise ReportExportError("SVG export needs rsvg-convert (librsvg); the original report is unchanged.")
        pdf = target.with_suffix(".pdf")
        _run([converter, "--format=pdf", "--output", str(pdf), str(target)], cwd=output)
        target = pdf
    return target.relative_to(output).as_posix()


def _plain_inlines(inlines: list[dict]) -> str:
    return " ".join(str(node.get("c", "")) for node in _nodes(inlines) if node.get("t") == "Str")


def _bind_figure_captions(ast: dict, captions: dict[str, list[dict]]) -> list[str]:
    """Bind recorded figure captions, for both legacy and modern Pandoc ASTs.

    Only a standalone image with a manifest path is changed. Remove its exact
    adjacent caption paragraph, not nearby discussion or arbitrary prose.
    """
    bound, blocks = [], []
    pending_caption = None
    for block in ast["blocks"]:
        if pending_caption is not None and block.get("t") == "Para":
            inlines = block["c"]
            if len(inlines) == 1 and inlines[0].get("t") == "Emph":
                inlines = inlines[0]["c"]
            if inlines == pending_caption:
                pending_caption = None
                continue
        pending_caption = None
        standalone = block.get("t") == "Figure" or (block.get("t") == "Para"
            and len(block["c"]) == 1 and block["c"][0].get("t") == "Image")
        images = [node for node in _nodes(block) if node.get("t") == "Image"] if standalone else []
        if len(images) == 1:
            image = images[0]
            path = image["c"][-1][0]
            if path in captions:
                caption = deepcopy(captions[path])
                image["c"][1] = caption
                if block["t"] == "Figure":
                    block["c"][1] = [None, [{"t": "Plain", "c": deepcopy(caption)}]]
                else:
                    image["c"][-1][1] = "fig:"  # legacy implicit-figure marker
                pending_caption = caption
                bound.append(path)
        blocks.append(block)
    ast["blocks"] = blocks
    return bound


def _breakable_code(text: str) -> str:
    """Escape literal code and allow breaks at identifier/path separators.

    Constructed TeX only: input Markdown still cannot supply raw TeX.
    """
    escapes = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$",
               "&": r"\&", "#": r"\#", "%": r"\%", "_": r"\_",
               "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    content = "".join(escapes.get(char, char) + (r"\allowbreak{}" if char in "_/-.:" else "")
                      for char in text)
    return r"\texttt{" + content + "}"


def _scientific_unicode_preamble(*fragments: str) -> str:
    """Declare scientific glyphs for pdfLaTeX without rewriting source prose.

    Pandoc preserves bare Greek letters and many mathematical symbols. Their
    fixed TeX equivalents work in prose, math and escaped code alike; this is
    not a general multilingual font system or permission to inject a preamble.
    """
    greek = dict(zip("αβγδεζηθικλμνξοπρστυφχψω", (
        "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi o pi rho sigma tau upsilon phi chi psi omega"
    ).split(), strict=True))
    greek.update(dict(zip("ΓΔΘΛΞΠΣΥΦΨΩ", "Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega".split(), strict=True)))
    symbols = {char: "\\" + name if name != "o" else "o" for char, name in greek.items()}
    symbols.update({"ς": r"\varsigma", "ϑ": r"\vartheta", "ϕ": r"\varphi", "ϵ": r"\varepsilon",
                    "∞": r"\infty", "≤": r"\leq", "≥": r"\geq", "≠": r"\neq",
                    "≈": r"\approx", "±": r"\pm", "∓": r"\mp", "×": r"\times",
                    "÷": r"\div", "∈": r"\in", "∉": r"\notin", "∂": r"\partial",
                    "∇": r"\nabla", "∑": r"\sum", "∏": r"\prod", "∫": r"\int",
                    "√": r"\surd", "µ": r"\mu", "−": "-"})
    present = set("".join(fragments)) & symbols.keys()
    return "".join(f"\\DeclareUnicodeCharacter{{{ord(char):04X}}}{{\\ensuremath{{{symbols[char]}}}}}\n"
                   for char in sorted(present))


def export_acm_report(report_dir: Path, output_dir: Path, *, title: str | None = None,
                      compile_pdf: bool = False) -> dict[str, Any]:
    """Create a new editable acmart manuscript from the canonical citation body.

    Pandoc and optional TeX/librsvg are external rendering dependencies. No
    model is called and no upstream evidence or session state is changed.
    """
    root, output = report_dir.resolve(), output_dir.resolve()
    body_path = root / "report_body.md"
    if not body_path.is_file():
        raise ReportExportError("Expected report_body.md from report assembly; display-only report.md loses citation keys.")
    if output.exists():
        raise ReportExportError("Choose a new output directory; existing exports are retained.")
    pandoc = shutil.which("pandoc")
    if not pandoc:
        raise ReportExportError("Install Pandoc to export an ACM project. No model or experiment is required.")
    source = body_path.read_text(encoding="utf-8")
    ast = json.loads(_run([pandoc, "--from=markdown-raw_tex-raw_html+tex_math_single_backslash", "--to=json"], cwd=root, text=source))
    ast["meta"] = {}  # Input metadata must not supply a TeX preamble or includes.
    captions = {}
    figure_manifest = root / "figures/figures_manifest.json"
    if figure_manifest.is_file():
        for row in json.loads(figure_manifest.read_text(encoding="utf-8")).get("figures", []):
            if not row.get("caption"):
                continue
            parsed = json.loads(_run([pandoc, "--from=markdown-raw_tex-raw_html+tex_math_single_backslash", "--to=json"],
                                     cwd=root, text=row["caption"]))["blocks"]
            if len(parsed) != 1 or parsed[0].get("t") not in {"Para", "Plain"}:
                raise ReportExportError("Recorded figure caption must be one Markdown paragraph.")
            captions[row["path"]] = parsed[0]["c"]
    bound_captions = _bind_figure_captions(ast, captions)
    source_ast = deepcopy(ast)
    blocks = ast["blocks"]
    title_inlines = [{"t": "Str", "c": title}] if title else []
    if blocks and blocks[0].get("t") == "Header" and blocks[0]["c"][0] == 1:
        header = blocks.pop(0)
        if not title_inlines:
            title_inlines = header["c"][2]
    if not title_inlines:
        title_inlines = [{"t": "Str", "c": "Research report"}]
    abstract: list[dict] = []
    for index, block in enumerate(blocks):
        if block.get("t") == "Header" and _plain_inlines(block["c"][2]).lower() in {
            "abstract", "abstract / executive summary", "summary", "executive summary",
        }:
            end = index + 1
            while end < len(blocks) and blocks[end].get("t") != "Header":
                end += 1
            abstract = blocks[index + 1:end]
            del blocks[index:end]
            break
    # Markdown reports use H2 for top-level sections; LaTeX starts at section.
    for node in _nodes(blocks):
        if node.get("t") == "Header":
            node["c"][0] = max(1, node["c"][0] - 1)
    citations = {citation["citationId"] for node in _nodes([blocks, abstract])
                 if node.get("t") == "Cite" for citation in node["c"][0]}
    bibliography_path = root / "references.bib"
    bibliography = bibliography_path.read_text(encoding="utf-8") if bibliography_path.is_file() else ""
    keys = set(re.findall(r"@\w+\s*\{\s*([^,\s]+)\s*,", bibliography))
    if citations - keys:
        raise ReportExportError("Unresolved bibliography keys: " + ", ".join(sorted(citations - keys)))
    image_sources = {node["c"][-1][0]: _asset_path(root, node["c"][-1][0])
                     for node in _nodes([blocks, abstract]) if node.get("t") == "Image"}
    output.mkdir(parents=True)
    manifest: dict[str, Any] = {"schema_version": "report_export.v1", "format": "acmart-manuscript",
                               "source_report": str(body_path), "status": "exporting",
                               "compiled": False, "quality_checked": False,
                               "bound_figure_captions": bound_captions}
    try:
        image_targets = {url: _copy_image(path, output, index)
                         for index, (url, path) in enumerate(image_sources.items(), start=1)}
        external_sources: list[str] = []
        for node in _nodes([blocks, abstract, source_ast]):
            if node.get("t") == "Image":
                node["c"][-1][0] = image_targets[node["c"][-1][0]]
            elif node.get("t") == "Link":
                target = node["c"][-1][0]
                url = urlsplit(target)
                if target in image_targets:
                    node["c"][-1][0] = image_targets[target]
                elif not url.scheme and not url.netloc and url.path and target != "references.bib":
                    # Source evidence may live outside this report. Do not
                    # silently copy data or ship dangling filesystem links.
                    external_sources.append(target)
                    node.update(t="Span", c=[node["c"][0], node["c"][1]])
        manifest["external_source_references"] = list(dict.fromkeys(external_sources))
        manifest["external_sources_included"] = False
        for node in _nodes([blocks, abstract]):
            if node.get("t") == "Code":
                node.update(t="RawInline", c=["latex", _breakable_code(node["c"][1])])

        def latex(fragment: list[dict]) -> str:
            document = {**ast, "blocks": fragment}
            return _run([pandoc, "--from=json", "--to=latex", "--natbib", "--no-highlight"],
                        cwd=output, text=json.dumps(document)).strip()

        heading = latex([{"t": "Plain", "c": title_inlines}])
        body_tex = latex(blocks)
        # Pandoc's bare figure environment defaults to tbp, often pushing
        # figures away from their owning prose. Prefer near-source placement
        # while retaining TeX's pagination/floating decisions; do not force H.
        body_tex = body_tex.replace("\\begin{figure}\n", "\\begin{figure}[htbp]\n")
        (output / "body.tex").write_text(body_tex + "\n", encoding="utf-8")
        abstract_tex = latex(abstract) if abstract else ""
        (output / "references.bib").write_text(bibliography, encoding="utf-8")
        markdown = _run([pandoc, "--from=json", "--to=markdown", "--wrap=none"],
                        cwd=output, text=json.dumps(source_ast))
        (output / "source.md").write_text(markdown, encoding="utf-8")
        main = (
            "% acmart manuscript demonstration; no conference submission metadata is inferred.\n"
            "\\documentclass[manuscript,screen,nonacm]{acmart}\n"
            "\\usepackage{longtable,booktabs,array,calc}\n"
            + _scientific_unicode_preamble(heading, body_tex, abstract_tex, bibliography)
            +
            "\\providecommand{\\tightlist}{\\setlength{\\itemsep}{0pt}\\setlength{\\parskip}{0pt}}\n"
            "\\providecommand{\\passthrough}[1]{#1}\n"
            "\\providecommand{\\pandocbounded}[1]{#1}\n"
            # Preserve intrinsic figure sizing (e.g. column-width SVG/PDF),
            # only shrinking oversized assets to the current text width.
            "\\makeatletter\n"
            "\\def\\sarmaxwidth{\\ifdim\\Gin@nat@width>\\linewidth\\linewidth\\else\\Gin@nat@width\\fi}\n"
            "\\makeatother\n"
            "\\setkeys{Gin}{width=\\sarmaxwidth,keepaspectratio}\n"
            "\\settopmatter{printacmref=false}\n"
            "\\citestyle{acmnumeric}\n"
            # No author metadata was supplied. Suppress acmart's empty default
            # address footnote; users can add real authors/addresses themselves.
            "\\authorsaddresses{}\n"
            "\\setlength{\\emergencystretch}{1em}\n"
            f"\\title[Research report]{{{heading}}}\n\\begin{{document}}\n"
            + (f"\\begin{{abstract}}\n{abstract_tex}\n\\end{{abstract}}\n" if abstract_tex else "")
            + "\\maketitle\n\\input{body.tex}\n"
            + ("\\bibliographystyle{ACM-Reference-Format}\n\\bibliography{references}\n" if citations else "")
            + "\\end{document}\n"
        )
        (output / "main.tex").write_text(main, encoding="utf-8")
        (output / "README.txt").write_text(
            "Editable ACM acmart manuscript demonstration, not a conference-specific submission.\n"
            "Edit main.tex/body.tex/references.bib and the figures. The export does not certify research claims.\n"
            "Class and bibliography style: https://ctan.org/pkg/acmart (install via your TeX distribution).\n"
            "Dependencies: pdflatex, bibtex, acmart and its packages; Pandoc for regeneration; librsvg for SVG conversion.\n"
            "Compile in this directory: pdflatex -no-shell-escape -halt-on-error main.tex; bibtex main; "
            "pdflatex -no-shell-escape -halt-on-error main.tex (twice).\n"
            "source.md uses the exported figure paths, not the original report directory.\n"
            "Local source-evidence links are retained as labels; their original targets are listed in export.json.\n"
            "External datasets and source artifacts are not copied or redistributed automatically.\n"
            "The built-in compile option restricts file access and records logs. Add author/venue metadata yourself.\n",
            encoding="utf-8",
        )
        manifest["status"] = "exported"
        if compile_pdf:
            manifest.update(_compile(output, bool(citations)))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        manifest.update(status="failed", error=str(exc))
        raise ReportExportError(f"Export failed; retained files and export.json at {output}: {exc}") from exc
    finally:
        (output / "export.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _compile(output: Path, has_citations: bool) -> dict[str, Any]:
    latex, bibtex = shutil.which("pdflatex"), shutil.which("bibtex")
    if not latex or (has_citations and not bibtex):
        return {"status": "exported", "compiled": False, "compile_note": "TeX unavailable; editable source retained."}
    env = {**os.environ, "openin_any": "p", "openout_any": "p", "shell_escape": "f"}
    command = [latex, "-no-shell-escape", "-halt-on-error", "-interaction=nonstopmode", "main.tex"]
    commands = [command, *([[bibtex, "main"]] if has_citations else []), command, command]
    logs: list[str] = []
    try:
        for argv in commands:
            logs.append(_run(argv, cwd=output, timeout=90, env=env))
            if has_citations and argv[0] == bibtex:
                # ACM's style may emit nested [] for explicitly unknown dates.
                # Protect generated optional labels for natbib; preserve the
                # bibliography's facts and the canonical input unchanged.
                bbl = output / "main.bbl"
                if bbl.is_file():
                    text = bbl.read_text(encoding="utf-8")
                    text = re.sub(r"\\bibitem\[(.*?)\](\s*%\s*\n\s*|\s*)\{",
                                  lambda match: r"\bibitem[{" + match[1] + "}]" + match[2] + "{",
                                  text, flags=re.S)
                    bbl.write_text(text, encoding="utf-8")
    except (OSError, subprocess.SubprocessError, ReportExportError) as exc:
        logs.append(str(exc))
        return {"status": "compile_failed", "compiled": False, "compile_note": str(exc)}
    finally:
        (output / "build.log").write_text("\n".join(logs), encoding="utf-8")
    compiled = (output / "main.pdf").is_file()
    final_log = (output / "main.log").read_text(encoding="utf-8", errors="replace") if (output / "main.log").is_file() else ""
    warnings = re.findall(r"(?:Class \w+ Warning|LaTeX Warning|Package \w+ Warning):[^\n]*|Overfull \\[hv]box[^\n]*", final_log)
    return {"status": "compiled" if compiled else "compile_failed", "compiled": compiled,
            "compile_warnings": list(dict.fromkeys(warnings))}

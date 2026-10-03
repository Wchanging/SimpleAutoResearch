from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simple_ar.report.export import ReportExportError, _compile, _copy_image, export_acm_report


def _ast(image: str | None = None, citation: str = "p1") -> dict:
    blocks = [
        {"t": "Header", "c": [1, ["", [], []], [{"t": "Str", "c": "Evidence report"}]]},
        {"t": "Header", "c": [2, ["", [], []], [{"t": "Str", "c": "Abstract"}]]},
        {"t": "Para", "c": [{"t": "Str", "c": "A bounded finding."}]},
        {"t": "Header", "c": [2, ["", [], []], [{"t": "Str", "c": "Results"}]]},
        {"t": "Para", "c": [{"t": "Cite", "c": [[{"citationId": citation}], [{"t": "Str", "c": "result"}]]}]},
    ]
    if image:
        blocks.append({"t": "Para", "c": [{"t": "Image", "c": [["", [], []], [], [image, ""]]}]})
    return {"pandoc-api-version": [1, 22], "meta": {"header-includes": "untrusted"}, "blocks": blocks}


class ReportExportTests(unittest.TestCase):
    def test_manifest_caption_binding_handles_both_pandoc_shapes_without_deleting_discussion(self):
        from copy import deepcopy
        from simple_ar.report.export import _bind_figure_captions
        caption = [{"t": "Str", "c": "Recorded"}, {"t": "Space"}, {"t": "Str", "c": "observations."}]
        image = {"t": "Image", "c": [["", [], []], [{"t": "Str", "c": "Generic"}], ["figures/raw.svg", ""]]}
        for modern in (False, True):
            for italic in (False, True):
                with self.subTest(modern=modern, italic=italic):
                    para = {"t": "Para", "c": [deepcopy(image)]}
                    figure = {"t": "Figure", "c": [["", [], []], [None, []], [para]]} if modern else para
                    duplicate = {"t": "Para", "c": [{"t": "Emph", "c": deepcopy(caption)}] if italic else deepcopy(caption)}
                    discussion = {"t": "Para", "c": [{"t": "Str", "c": "Interpretation remains limited."}]}
                    ast = {"blocks": [figure, duplicate, discussion]}
                    bound = _bind_figure_captions(ast, {"figures/raw.svg": caption})
                    self.assertEqual(bound, ["figures/raw.svg"])
                    self.assertEqual(ast["blocks"], [figure, discussion])
                    rendered_image = figure["c"][2][0]["c"][0] if modern else figure["c"][0]
                    self.assertEqual(rendered_image["c"][1], caption)
                    if modern:
                        self.assertEqual(figure["c"][1], [None, [{"t": "Plain", "c": caption}]])
                    else:
                        self.assertEqual(rendered_image["c"][-1][1], "fig:")

    def test_caption_binding_leaves_unregistered_and_inline_images_unchanged(self):
        from copy import deepcopy
        from simple_ar.report.export import _bind_figure_captions
        ast = _ast("unknown.png")
        ast["blocks"].append({"t": "Para", "c": [{"t": "Str", "c": "Inline"},
            {"t": "Image", "c": [["", [], []], [], ["registered.png", ""]]}]})
        before = deepcopy(ast)
        self.assertEqual(_bind_figure_captions(ast, {"registered.png": [{"t": "Str", "c": "Caption"}]}), [])
        self.assertEqual(ast, before)

    def test_scientific_unicode_support_is_fixed_and_only_for_present_symbols(self):
        from simple_ar.report.export import _scientific_unicode_preamble

        preamble = _scientific_unicode_preamble("α ≤ β, Γ ≠ ∞; µm", r"\input{secret}")
        for code in ("03B1", "03B2", "0393", "2264", "2260", "221E", "00B5"):
            self.assertIn("\\DeclareUnicodeCharacter{" + code + "}", preamble)
        self.assertNotIn("03B3", preamble)
        self.assertIn(r"\ensuremath{\alpha}", preamble)
        self.assertNotIn(r"\input", preamble)
        self.assertEqual(_scientific_unicode_preamble("Plain ASCII and unknown 中文"), "")

    def test_breakable_code_escapes_commands_and_keeps_separators(self):
        from simple_ar.report.export import _breakable_code
        rendered = _breakable_code(r"weighted_mc_error\input{secret}%")
        self.assertIn(r"\_\allowbreak{}", rendered)
        self.assertIn(r"\textbackslash{}input\{secret\}\%", rendered)
        self.assertNotIn(r"\input{", rendered)

    def test_slash_prose_breaks_without_font_change_or_rewriting_math_and_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._source(root)
            document = _ast()
            document["blocks"].append({"t": "Para", "c": [
                {"t": "Str", "c": "train/validation/calibration/test"},
                {"t": "Space"}, {"t": "Str", "c": "10,123/3,639/2,048/5,803"},
                {"t": "Space"}, {"t": "Str", "c": r"x/\input{secret}%"},
                {"t": "Math", "c": [{"t": "InlineMath"}, "a/b"]},
                {"t": "Link", "c": [["", [], []], [{"t": "Str", "c": "source"}],
                    ["https://example.test/a/b", ""]]},
            ]})
            converted = []
            def convert(argv, **kwargs):
                if "--to=json" in argv:
                    return json.dumps(document)
                data = json.loads(kwargs["text"])
                if "--to=latex" in argv:
                    converted.append(data)
                return "converted text"
            with patch("simple_ar.report.export.shutil.which", return_value="pandoc"), patch(
                    "simple_ar.report.export._run", side_effect=convert):
                export_acm_report(source, root / "acm")
            para = converted[1]["blocks"][-1]["c"]
            self.assertEqual(para[0], {"t": "RawInline", "c": ["latex",
                r"train/\allowbreak{}validation/\allowbreak{}calibration/\allowbreak{}test"]})
            self.assertEqual(para[2]["c"][1], r"10,123/\allowbreak{}3,639/\allowbreak{}2,048/\allowbreak{}5,803")
            self.assertNotIn(r"\texttt", para[0]["c"][1])
            self.assertNotIn(r"\input{", para[4]["c"][1])
            self.assertIn(r"\textbackslash{}input\{secret\}\%", para[4]["c"][1])
            self.assertEqual(para[5], document["blocks"][-1]["c"][5])
            self.assertEqual(para[6]["c"][-1][0], "https://example.test/a/b")
            self.assertIn("incomplete bibliography", (root / "acm/README.txt").read_text())

    def _source(self, root: Path) -> Path:
        report = root / "report"
        report.mkdir()
        (report / "report_body.md").write_text("# Evidence report\n\nBody [@p1].\n", encoding="utf-8")
        (report / "references.bib").write_text("@misc{p1, title={Evidence}}\n", encoding="utf-8")
        return report

    def test_export_preserves_canonical_sources_and_separates_abstract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._source(root)
            calls: list[dict] = []
            def convert(argv, **kwargs):
                if "--to=json" in argv:
                    return json.dumps(_ast())
                document = json.loads(kwargs["text"])
                calls.append(document)
                return "converted text"
            with patch("simple_ar.report.export.shutil.which", return_value="pandoc"), patch("simple_ar.report.export._run", side_effect=convert):
                result = export_acm_report(source, root / "acm")
            self.assertEqual(result["status"], "exported")
            self.assertFalse(result["compiled"])
            self.assertFalse(result["quality_checked"])
            self.assertTrue(all(document["meta"] == {} for document in calls))
            body_headers = [block["c"][0] for block in calls[1]["blocks"] if block["t"] == "Header"]
            self.assertEqual(body_headers, [1])
            self.assertEqual((root / "acm/source.md").read_text(), "converted text")
            self.assertEqual((source / "report_body.md").read_text(), "# Evidence report\n\nBody [@p1].\n")
            self.assertEqual((root / "acm/references.bib").read_text(), (source / "references.bib").read_text())
            main = (root / "acm/main.tex").read_text()
            self.assertLess(main.index("\\begin{abstract}"), main.index("\\maketitle"))
            self.assertIn("\\authorsaddresses{}", main)
            self.assertIn("\\Gin@nat@width>\\linewidth", main)
            self.assertIn("\\setkeys{Gin}{width=\\sarmaxwidth,keepaspectratio}", main)
            self.assertNotIn("\\setkeys{Gin}{width=\\linewidth,keepaspectratio}", main)
            self.assertTrue((root / "acm/export.json").is_file())

    def test_exported_markdown_and_figures_move_together_without_external_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._source(root)
            (source / "original.png").write_bytes(b"image")
            document = _ast(image="original.png")
            document["blocks"].append({"t": "Para", "c": [{"t": "Link", "c": [
                ["", [], []], [{"t": "Str", "c": "Measured source"}], ["../../experiment/results.json", ""],
            ]}]})
            def convert(argv, **kwargs):
                if "--to=json" in argv:
                    return json.dumps(document)
                data = json.loads(kwargs["text"])
                if "--to=markdown" in argv:
                    image = data["blocks"][-2]["c"][0]["c"][-1][0]
                    self.assertEqual(data["blocks"][-1]["c"][0]["t"], "Span")
                    return f"![Measured figure]({image})\n"
                return "converted text"
            with patch("simple_ar.report.export.shutil.which", return_value="pandoc"), patch("simple_ar.report.export._run", side_effect=convert):
                result = export_acm_report(source, root / "acm")
            self.assertEqual(result["external_source_references"], ["../../experiment/results.json"])
            self.assertFalse(result["external_sources_included"])
            (root / "acm").rename(root / "moved")
            source.rename(root / "original-isolated")
            self.assertIn("figures/figure-1.png", (root / "moved/source.md").read_text())
            self.assertTrue((root / "moved/figures/figure-1.png").is_file())

    def test_generated_figure_placement_prefers_source_location_without_forcing_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._source(root)
            def convert(argv, **kwargs):
                if "--to=json" in argv:
                    return json.dumps(_ast())
                return "\\begin{figure}\nGenerated\\end{figure}\n\\begin{figure}[p]\nExplicit\\end{figure}"
            with patch("simple_ar.report.export.shutil.which", return_value="pandoc"), patch("simple_ar.report.export._run", side_effect=convert):
                export_acm_report(source, root / "acm")
            rendered = (root / "acm/body.tex").read_text()
            self.assertIn("\\begin{figure}[htbp]\nGenerated", rendered)
            self.assertIn("\\begin{figure}[p]\nExplicit", rendered)
            self.assertNotIn("\\begin{figure}[H]", rendered)

    def test_out_of_scope_image_and_unknown_citation_fail_before_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._source(root)
            (root / "outside.png").write_bytes(b"image")
            for document in (_ast(image="../outside.png"), _ast(citation="missing")):
                with self.subTest(document=document), patch("simple_ar.report.export.shutil.which", return_value="pandoc"), patch("simple_ar.report.export._run", return_value=json.dumps(document)):
                    with self.assertRaises(ReportExportError):
                        export_acm_report(source, root / "acm")
                    self.assertFalse((root / "acm").exists())

    def test_existing_export_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._source(root)
            output = root / "acm"
            output.mkdir()
            sentinel = output / "main.tex"
            sentinel.write_text("user edits", encoding="utf-8")
            with self.assertRaises(ReportExportError):
                export_acm_report(source, output)
            self.assertEqual(sentinel.read_text(), "user edits")

    def test_svg_internal_quoted_references_are_not_external_resources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "figure.svg"
            output = root / "export"
            output.mkdir()
            image.write_text('<svg><use href="#shape" style=\'fill:url("#color")\'/></svg>', encoding="utf-8")
            with patch("simple_ar.report.export.shutil.which", return_value="rsvg-convert"), patch("simple_ar.report.export._run") as run:
                self.assertEqual(_copy_image(image, output, 1), "figures/figure-1.pdf")
                run.assert_called_once()
            image.write_text('<svg><image href="https://example.test/image.png"/></svg>', encoding="utf-8")
            with self.assertRaises(ReportExportError):
                _copy_image(image, output, 2)

    def test_compile_failure_retains_diagnostics_and_limits_file_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("simple_ar.report.export.shutil.which", return_value="tex"), patch("simple_ar.report.export._run", side_effect=ReportExportError("missing package")) as run:
                result = _compile(root, True)
            self.assertEqual(result["status"], "compile_failed")
            self.assertFalse(result["compiled"])
            self.assertIn("missing package", (root / "build.log").read_text())
            self.assertIn("-no-shell-escape", run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs["env"]["openin_any"], "p")


if __name__ == "__main__":
    unittest.main()

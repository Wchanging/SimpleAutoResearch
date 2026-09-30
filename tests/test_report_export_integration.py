"""Opt-in real renderer/TeX check; no model, network or dependency installation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from simple_ar.report.export import export_acm_report, _compile, _nodes


@unittest.skipUnless(os.environ.get("SAR_TEST_EXPORT_INTEGRATION") == "1" and
    all(shutil.which(name) for name in ("pandoc", "rsvg-convert", "pdflatex", "bibtex")),
    "Opt-in integration requires Pandoc, librsvg and acmart-equipped TeX")
class PortableExportIntegrationTests(unittest.TestCase):
    def test_moved_export_rebuilds_without_original_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "input"
            original.mkdir()
            (original / "plot.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="160" viewBox="0 0 400 160">'
                '<rect width="400" height="160" fill="white"/>'
                '<text x="20" y="50" font-size="20">Portability fixture, not research data</text>'
                '<path d="M20 130 L360 70" stroke="blue" fill="none"/></svg>', encoding="utf-8")
            (original / "report_body.md").write_text(
                '# Portable export check\n\n## Abstract\n\nRenderer integration fixture.\n\n'
                '## Result\n\nFixture citation [@p0]. Inline math \\(1-\\alpha\\).\n\n'
                '![Fixture plot](plot.svg)\n\n[External evidence](../measurements.json).\n', encoding="utf-8")
            bibliography = '@misc{p0, title={Explicitly incomplete metadata fixture}}\n'
            (original / "references.bib").write_text(bibliography, encoding="utf-8")
            exported = root / "export"
            manifest = export_acm_report(original, exported, compile_pdf=True)
            self.assertTrue(manifest["compiled"], manifest)
            self.assertFalse(any("undefined citation" in row.lower() or "citation `p0'" in row.lower()
                                 for row in manifest["compile_warnings"]), manifest)
            self.assertEqual((original / "references.bib").read_text(), bibliography)
            moved = root / "moved"
            exported.rename(moved)
            original.rename(root / "input-unavailable")
            rebuilt = _compile(moved, True)
            self.assertTrue(rebuilt["compiled"])
            self.assertFalse(any("undefined citation" in row.lower() for row in rebuilt["compile_warnings"]))
            ast = json.loads(subprocess.check_output(
                ["pandoc", "--from=markdown", "--to=json", str(moved / "source.md")], text=True))
            images = [row["c"][-1][0] for row in _nodes(ast) if row.get("t") == "Image"]
            self.assertTrue(images)
            self.assertTrue(all((moved / name).is_file() for name in images))
            self.assertTrue((moved / "figures/figure-1.svg").is_file())
            self.assertEqual(manifest["external_source_references"], ["../measurements.json"])


if __name__ == "__main__":
    unittest.main()

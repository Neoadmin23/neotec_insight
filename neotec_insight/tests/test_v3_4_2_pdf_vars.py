"""v3.4.2 — PDFs had no table rules: wkhtmltopdf ignores CSS variables, so
every border written as var(--th-rule) was dropped. They are now resolved
before rendering."""
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402

DOC = """<html><head><style>
:root{--th-accent:#57257D;--th-rule:#cfcac0;--th-strong:var(--th-accent);--th-head-bg:#f4f2ee;}
table.rpt th,table.rpt td{padding:4px 7px;border:0.5pt solid var(--th-rule);}
table.rpt thead th{border:0.5pt solid var(--th-strong);background:var(--th-head-bg);}
table.rpt tr.grand td{border-bottom:1pt double var(--th-strong);color:var(--th-missing, #333);}
</style><style>:root{--th-rule:#999999;}</style></head>
<body><table class="rpt"><tr><td style="border-color:var(--th-rule)">x</td></tr></table></body></html>"""


class InlineVars(unittest.TestCase):
    def setUp(self):
        ff.install()
        from neotec_insight.neotec_insight.api import pdf
        self.pdf = pdf

    def tearDown(self):
        ff.uninstall()

    def test_borders_and_backgrounds_get_real_colours(self):
        out = self.pdf.inline_css_vars(DOC)
        self.assertNotIn("var(", out)
        self.assertIn("border:0.5pt solid #999999", out)          # later :root override wins
        self.assertIn("border:0.5pt solid #57257D", out)          # variable defined through another
        self.assertIn("background:#f4f2ee", out)
        self.assertIn("color:#333", out)                          # fallback used when undefined
        self.assertIn('style="border-color:#999999"', out)        # inline styles too

    def test_documents_without_variables_are_untouched(self):
        plain = "<html><body><table><tr><td>1</td></tr></table></body></html>"
        self.assertEqual(self.pdf.inline_css_vars(plain), plain)

    def test_the_pdf_pipeline_applies_it(self):
        src = (HERE.parents[0] / "neotec_insight" / "api" / "pdf.py").read_text()
        self.assertIn("return inline_css_vars(_restrict_resources(html))", src)

    def test_the_shared_table_css_uses_variables(self):
        # the cause: if this ever stops using variables, the fix above is moot but harmless
        css = (HERE.parents[1] / "reportManager" / "src" / "utils" / "branddoc.ts").read_text()
        self.assertTrue(re.search(r"solid var\(--th-rule\)", css))


if __name__ == "__main__":
    unittest.main()

"""v3.4.6 — image-based Letter Heads: header and footer built from their
images when the HTML is empty (the footer used to be dropped)."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402


class ImageLetterhead(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        frappe.utils.escape_html = lambda s: str(s).replace('"', "&quot;")
        frappe.utils.get_url = lambda: "https://neoirsaa.frappe.cloud"
        self.site.table("Letter Head")["IRSAA"] = {"name": "IRSAA", "letter_head_name": "IRSAA", "content": "",
                                                   "footer": "", "image": "/files/head.png", "align": "Center",
                                                   "footer_image": "/files/foot.png", "footer_align": "Left"}
        from neotec_insight.neotec_insight.api import report
        self.report = report

    def tearDown(self):
        ff.uninstall()

    def test_header_and_footer_from_images(self):
        out = self.report.get_letterhead("IRSAA")
        self.assertIn("/files/head.png", out["header_html"])
        self.assertIn("text-align:center", out["header_html"])
        self.assertIn("width:100%", out["header_html"])               # v3.4.7 — fits the page width
        self.assertIn("/files/foot.png", out["footer_html"])
        self.assertIn("text-align:left", out["footer_html"])

    def test_html_letterheads_unchanged(self):
        self.site.table("Letter Head")["IRSAA"].update({"content": "<b>H</b>", "footer": "<i>F</i>"})
        out = self.report.get_letterhead("IRSAA")
        self.assertEqual((out["header_html"], out["footer_html"]), ("<b>H</b>", "<i>F</i>"))


if __name__ == "__main__":
    unittest.main()

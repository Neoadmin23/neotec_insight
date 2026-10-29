"""v3.4.3 — the statement's customer/supplier prints directly above the table
(after the letterhead band, before the column headings), on every page."""
import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "reportManager" / "src"


class PartyBlock(unittest.TestCase):
    def test_print_and_pdf_order(self):
        doc = (SRC / "utils/reportdoc.ts").read_text()
        lh, party, heads = doc.index('<tr class="pr-lh">'), doc.index('<tr class="pr-party">'), doc.index("<tr>${th}</tr>")
        self.assertLess(lh, party)
        self.assertLess(party, heads)
        thead = doc[doc.index("<thead>"):doc.index("</thead>")]
        self.assertIn("pr-party", thead)                         # in <thead>: repeats on every page

    def test_excel_puts_it_after_the_band(self):
        doc = (SRC / "utils/reportdoc.ts").read_text()
        i_period = doc.index("if (b.showPeriod !== false && doc.period) bandRow(doc.period, S.meta);")
        i_party = doc.index("bandRow(doc.party.label, S.title, 18);")
        self.assertLess(i_period, i_party)

    def test_statement_uses_the_party_block_not_the_subtitle(self):
        st = (SRC / "features/statement/StatementApp.tsx").read_text()
        self.assertIn("party: { label: `${t(partyType)}: ${partyLabel}` }", st)
        self.assertFalse(re.search(r"subtitle:\s*`\$\{t\(partyType\)\}", st))


if __name__ == "__main__":
    unittest.main()

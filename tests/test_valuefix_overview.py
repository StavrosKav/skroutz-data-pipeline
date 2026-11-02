"""Fix 4: Overview carries a one-sentence takeaway from the basket index and
absorbs the old Insights tab (removed; #insights lands on Overview). No DB."""
import re
import unittest

from reporting import generate_dashboard as gd
from tests.test_kpi_accuracy import _fixture_data

IDX = {
    "phones": [{"date": "2026-08-04", "idx": 100.0, "n": 900},
               {"date": "2026-11-01", "idx": 98.8, "n": 880}],
    "tablets": [{"date": "2026-08-04", "idx": 100.0, "n": 300},
                {"date": "2026-11-01", "idx": 104.1, "n": 290}],
    "laptops": [{"date": "2026-08-04", "idx": 100.0, "n": 1200}],
}


class TestTakeaway(unittest.TestCase):
    def test_sentence(self):
        s = gd.index_takeaway(IDX)
        self.assertIn("4 Aug 2026", s)
        self.assertIn("tablets +4.1%", s)
        self.assertIn("phones -1.2%", s)
        self.assertLess(s.index("tablets"), s.index("phones"))
        self.assertNotIn("laptops", s)  # one point only: no change to report

    def test_empty(self):
        self.assertEqual(gd.index_takeaway({}), "")
        self.assertEqual(gd.index_takeaway([]), "")


class TestInsightsMerged(unittest.TestCase):
    def setUp(self):
        self.html = gd.render_html(_fixture_data())

    def test_no_insights_tab(self):
        self.assertNotIn('data-tab="insights"', self.html)
        self.assertNotIn('id="tab-insights"', self.html)

    def test_sections_in_overview(self):
        start = self.html.index('id="tab-overview"')
        end = self.html.index('id="tab-drops"')
        ov = self.html[start:end]
        for el in ("brand-charts", "donut-charts", "compare-section",
                   "market-index-chart", "index-takeaway"):
            self.assertIn(f'id="{el}"', ov)
        self.assertIn("Products Listed by Brand", ov)

    def test_old_hash_redirects(self):
        self.assertRegex(self.html, r"insights['\"]?\s*:\s*['\"]overview")
        self.assertIsNone(re.search(r"'5'\s*:\s*'insights'", self.html))

    def test_how_it_works_mentions_rules(self):
        about = self.html[self.html.index('id="about"'):]
        for word in ("Needs checking", "bulk repricing", "30 days", "&euro;5"):
            self.assertIn(word, about)

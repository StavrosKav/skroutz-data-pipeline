"""Final fix: the index chart heading and the takeaway sentence under it use
the same base date, the first day of the fixed-basket index. No DB."""
import unittest

from reporting import generate_dashboard as gd
from tests.test_kpi_accuracy import _fixture_data

IDX = {
    "phones": [{"date": "2026-08-06", "idx": 100.0, "n": 900},
               {"date": "2026-11-03", "idx": 98.8, "n": 880}],
    "tablets": [{"date": "2026-08-06", "idx": 100.0, "n": 300},
                {"date": "2026-11-03", "idx": 104.1, "n": 290}],
}


def _heading(html):
    i = html.index("Category Price Index")
    return html[i:html.index("</div>", i)]


class TestIndexDate(unittest.TestCase):
    def test_heading_matches_takeaway(self):
        data = dict(_fixture_data(), window_start="2026-08-05", market_index=IDX)
        html = gd.render_html(data)
        self.assertIn("Since 6 Aug 2026", _heading(html))
        self.assertNotIn("5 Aug", _heading(html))
        self.assertIn("Since 6 Aug 2026", gd.index_takeaway(IDX))

    def test_falls_back_to_window_start(self):
        data = dict(_fixture_data(), window_start="2026-08-05", market_index={})
        self.assertIn("Since 5 Aug 2026", _heading(gd.render_html(data)))

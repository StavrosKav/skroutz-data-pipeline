"""The public dashboard must never expose the personal watchlist or target prices."""
import inspect
import unittest
from unittest.mock import patch

from reporting import generate_dashboard as gd
from tests.test_kpi_accuracy import _fixture_data

SECRET_LABEL = "MY-SECRET-WATCH-ITEM"
SECRET_URL = "https://www.skroutz.gr/s/99999999/secret-watch.html"


class TestDashboardPrivacy(unittest.TestCase):
    def _render(self, extra=None):
        data = _fixture_data()
        if extra:
            data.update(extra)
        with patch.object(gd, "chart_url", return_value=""):
            return gd.render_html(data)

    def test_template_has_no_watchlist_section(self):
        html = self._render()
        low = html.lower()
        for needle in ("watchlist", "wl-list", "target reached", "target: €"):
            self.assertNotIn(needle, low)

    def test_stray_watchlist_data_is_stripped(self):
        html = self._render({"watchlist": [{"label": SECRET_LABEL, "url": SECRET_URL,
                                            "threshold": 123.45, "price": 100.0, "hit": True}]})
        self.assertNotIn(SECRET_LABEL, html)
        self.assertNotIn(SECRET_URL, html)
        self.assertNotIn("123.45", html)
        self.assertNotIn("watchlist", html.lower())

    def test_fetch_data_does_not_read_watchlist(self):
        src = inspect.getsource(gd).lower()
        self.assertNotIn("watchlist_path", src)
        self.assertNotIn("_load_watchlist", src)
        self.assertNotIn("threshold_eur", src)


if __name__ == "__main__":
    unittest.main()

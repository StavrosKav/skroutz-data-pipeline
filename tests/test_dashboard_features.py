"""Dashboard features: compact payload, sparklines, freshness, external assets,
one page language, mobile layout, CSV export, how-it-works section."""
import datetime
import json
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch

from reporting import dashboard_payload as dp
from reporting import generate_dashboard as gd
from tests.test_kpi_accuracy import _fixture_data

PROD = {"id": 7, "category": "phone", "brand": "Apple", "model": "iPhone 15", "name": "Apple iPhone 15",
        "price": 799.0, "rating": 4.7, "reviews": 120, "monthly": 33.3,
        "url": "https://www.skroutz.gr/s/123/Apple-iPhone-15.html", "color": "Black",
        "cv": 2.1, "floor_pct": 3.0, "atl": 775.0, "trend": "falling", "spark": [820, 810, 799]}


def _render(extra=None):
    data = _fixture_data()
    data.update(extra or {})
    return gd.render_html(data)


class TestCompactProducts(unittest.TestCase):
    def test_roundtrip_and_url_prefix(self):
        other = dict(PROD, id=8, url="https://example.invalid/x", spark=[])
        packed = dp.compact_products([PROD, other])
        self.assertEqual(dp.expand_products(packed), [PROD, other])
        self.assertNotIn("https://www.skroutz.gr/s/123", json.dumps(packed))

    def test_smaller_than_plain_json(self):
        rows = [dict(PROD, id=i) for i in range(200)]
        self.assertLess(len(json.dumps(dp.compact_products(rows))), 0.7 * len(json.dumps(rows)))

    def test_name_rebuilt_from_brand_model_when_model_present(self):
        p = dict(PROD, name="Something longer")
        self.assertEqual(dp.expand_products(dp.compact_products([p]))[0]["name"], "Apple iPhone 15")
        q = dict(PROD, model="", name="Only Name")
        self.assertEqual(dp.expand_products(dp.compact_products([q]))[0]["name"], "Only Name")

    def test_empty(self):
        self.assertEqual(dp.expand_products(dp.compact_products([])), [])


class TestCompactHistory(unittest.TestCase):
    def test_roundtrip(self):
        hist = {1: [{"date": "2026-10-01", "price": 10.0}, {"date": "2026-10-03", "price": 9.5}],
                2: [{"date": "2026-10-02", "price": 5.0}]}
        packed = dp.compact_history(hist)
        self.assertEqual(packed["dates"], ["2026-10-01", "2026-10-02", "2026-10-03"])
        self.assertEqual(dp.expand_history(packed), {"1": hist[1], "2": hist[2]})


class TestDownsample(unittest.TestCase):
    def test_keeps_first_last_and_length(self):
        vals = list(range(30))
        out = dp.downsample(vals, 8)
        self.assertEqual(len(out), 8)
        self.assertEqual((out[0], out[-1]), (0, 29))

    def test_short_series_unchanged(self):
        self.assertEqual(dp.downsample([3, 2], 8), [3, 2])
        self.assertEqual(dp.downsample([], 8), [])


class TestFreshness(unittest.TestCase):
    TZ = datetime.timezone(datetime.timedelta(hours=2))

    def test_fresh_run(self):
        gen = datetime.datetime(2026, 10, 31, 10, 30, tzinfo=self.TZ)
        f = dp.freshness(gen, datetime.date(2026, 10, 31))
        self.assertEqual(f["lag_days"], 0)
        self.assertFalse(f["stale_now"])
        self.assertEqual(f["stale_at"], "2026-11-01T16:30:00+02:00")

    def test_old_data_is_stale_at_once(self):
        gen = datetime.datetime(2026, 10, 31, 10, 30, tzinfo=self.TZ)
        f = dp.freshness(gen, datetime.date(2026, 10, 28))
        self.assertEqual(f["lag_days"], 3)
        self.assertTrue(f["stale_now"])


class TestRenderedPage(unittest.TestCase):
    def test_no_inline_base64_images_or_chartjs(self):
        html = _render()
        self.assertNotIn("data:image/png;base64", html)
        self.assertIn('src="../assets/chart.umd.min.js', html)
        self.assertNotIn("__CHARTJS_INLINE__", html)

    def test_chart_urls_are_relative(self):
        with patch.object(gd, "CHARTS_DIR") as cd:
            cd.__truediv__.return_value.exists.return_value = True
            html = _render()
        self.assertIn("../charts/price_trend_phone.png?v=2026-10-28", html)

    def test_single_language(self):
        html = _render()
        self.assertIn('<html lang="en"', html)
        self.assertIsNone(re.search(r"[\u0370-\u03ff]", html))

    def test_feature_hooks_present(self):
        html = _render({"freshness": {"generated_at": "x", "stale_at": "y", "lag_days": 0, "stale_now": False}})
        for needle in ('id="freshness"', 'id="about"', "github.com/StavrosKav/skroutz-data-pipeline#readme",
                       "function sparkSvg", "function exportDropsCSV", "function clearFilters",
                       "@media (max-width: 480px)", "\\ufeff"):
            self.assertIn(needle, html)

    def test_page_size_small_with_3000_products(self):
        prods = [dict(PROD, id=i, spark=[800 + i % 7] * 8) for i in range(3000)]
        html = _render({"products": prods})
        self.assertLess(len(html.encode()), 1_000_000)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestJsExpandMatchesPython(unittest.TestCase):
    def test_js_expand(self):
        html = _render({"products": [PROD], "history": {7: [{"date": "2026-10-01", "price": 9.0}]}})
        js = re.search(r"// <payload-expand>(.*?)// </payload-expand>", html, re.S).group(1)
        script = js + (
            "\nconst D=" + json.dumps({"products": dp.compact_products([PROD])}) + ";"
            "\nconst H=" + json.dumps(dp.compact_history({7: [{"date": "2026-10-01", "price": 9.0}]})) + ";"
            "\nconsole.log(JSON.stringify([expandProducts(D.products), expandHistory(H)]));")
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
        prods, hist = json.loads(out)
        self.assertEqual(prods, [PROD])
        self.assertEqual(hist, {"7": [{"date": "2026-10-01", "price": 9.0}]})


if __name__ == "__main__":
    unittest.main()

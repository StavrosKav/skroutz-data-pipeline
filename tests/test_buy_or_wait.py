"""Intelligence tab = "Buy or wait": today's price vs the product's own
90-day low / median / high on the days we recorded it. No DB."""
import unittest
from unittest.mock import MagicMock

from reporting import buy_or_wait as bw


def _row(**kw):
    base = dict(id=1, skroutz_id=111, category="phone", brand="Apple", model="iPhone 15",
                product_name="Apple iPhone 15 128GB", skroutz_link="https://www.skroutz.gr/s/111/x.html",
                current=800.0, lo=780.0, med=850.0, hi=900.0, n_days=60, n_drops=6)
    base.update(kw)
    return MagicMock(**base)


class TestSignal(unittest.TestCase):
    def test_near_low(self):
        self.assertEqual(bw.signal(790, 780, 850, 900), bw.NEAR_LOW)
        self.assertEqual(bw.signal(780, 780, 850, 900), bw.NEAR_LOW)

    def test_above_usual(self):
        self.assertEqual(bw.signal(900, 780, 850, 900), bw.ABOVE)

    def test_typical(self):
        self.assertEqual(bw.signal(850, 780, 850, 900), bw.TYPICAL)
        self.assertEqual(bw.signal(820, 780, 850, 900), bw.TYPICAL)

    def test_flat_price_is_typical_not_a_deal(self):
        self.assertEqual(bw.signal(500, 500, 500, 500), bw.TYPICAL)

    def test_low_but_not_below_median_is_typical(self):
        # a product that sat at its low most days: the low is its usual price
        self.assertEqual(bw.signal(500, 500, 500, 560), bw.TYPICAL)


class TestQuery(unittest.TestCase):
    def test_sql_rules(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        bw.fetch(conn)
        sql = " ".join(str(conn.execute.call_args[0][0]).split())
        params = conn.execute.call_args[0][1]
        self.assertEqual(params["days"], bw.WINDOW_DAYS)
        self.assertEqual(params["min_days"], bw.MIN_DAYS)
        self.assertIn("date > mx.d - :days", sql)                 # only recorded days in window
        self.assertIn("n_days >= :min_days", sql)                 # minimum history
        self.assertIn("cur.date = (SELECT d FROM mx)", sql)       # listed in the latest scrape
        self.assertIn("PERCENTILE_CONT(0.5)", sql)

    def test_constants(self):
        self.assertEqual(bw.WINDOW_DAYS, 90)
        self.assertGreaterEqual(bw.MIN_DAYS, 20)


class TestBuild(unittest.TestCase):
    def test_one_row_per_product_and_duplicate_names_distinguished(self):
        rows = [_row(id=1, skroutz_id=111, current=781.0),
                _row(id=2, skroutz_id=222, current=782.0),
                _row(id=3, skroutz_id=333, product_name="Other", current=899.0)]
        out = bw.build(rows)
        names = [r["name"] for r in out["near_low"]]
        self.assertEqual(len(names), 2)
        self.assertNotEqual(names[0], names[1])
        self.assertTrue(all("111" in n or "222" in n for n in names))
        self.assertEqual([r["name"] for r in out["above"]], ["Other"])
        self.assertEqual(out["counts"]["phone"], {"near_low": 2, "typical": 0, "above": 1, "total": 3})

    def test_row_fields(self):
        out = bw.build([_row(current=781.0)])
        r = out["near_low"][0]
        self.assertEqual(r["vs_median_pct"], round((781 - 850) / 850 * 100, 1))
        self.assertEqual(r["drop_rate_pct"], round(6 / 59 * 100))
        self.assertEqual((r["lo"], r["med"], r["hi"], r["days"]), (780.0, 850.0, 900.0, 60))

    def test_big_gap_flagged_check(self):
        out = bw.build([_row(current=400.0, lo=400.0), _row(id=2, skroutz_id=2, product_name="B", current=781.0)])
        flags = {r["name"]: r["suspect"] for r in out["near_low"]}
        self.assertTrue(flags["Apple iPhone 15 128GB"])
        self.assertFalse(flags["B"])

    def test_near_low_sorted_by_saving_and_limited(self):
        rows = [_row(id=i, skroutz_id=i, product_name=f"P{i}", current=780.0 + i % 3, med=850.0 + i)
                for i in range(500)]
        out = bw.build(rows, limit=50)
        self.assertEqual(len(out["near_low"]), 50)
        pcts = [r["vs_median_pct"] for r in out["near_low"]]
        self.assertEqual(pcts, sorted(pcts))
        self.assertEqual(out["counts"]["phone"]["near_low"], 500)


if __name__ == "__main__":
    unittest.main()


class TestDashboardTab(unittest.TestCase):
    def test_tab_replaced(self):
        from reporting import generate_dashboard as gd
        from tests.test_kpi_accuracy import _fixture_data
        data = _fixture_data()
        data["buy_or_wait"] = bw.build([_row(current=781.0)])
        html = gd.render_html(data)
        for needle in ("Buy or Wait", 'id="bow-body"', 'id="bow-summary"', "function buildBuyOrWait"):
            self.assertIn(needle, html)
        for gone in ('id="atl-body"', 'id="price-rating-chart"', 'id="tier-dist-charts"',
                     'id="discount-freq-charts"', "discount_data"):
            self.assertNotIn(gone, html)

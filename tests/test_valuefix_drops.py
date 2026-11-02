"""Fix 3: a drop must be at least MIN_DROP_PCT % and MIN_DROP_EUR euro, and
same-day bulk repricing collapses into one line. Same rule on the dashboard and
in Telegram. No DB."""
import inspect
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

import run_pipeline as rp
from alerts import telegram_bot as tb
from reporting import drops as dr
from reporting import generate_dashboard as gd
from reporting import queries
from tests.test_kpi_accuracy import _conn_returning, _fixture_data, _sql_and_params


def _d(i, pct, eur, cat="laptop", date="2026-11-02", brand="Lenovo"):
    return {"product_id": i, "category": cat, "brand": brand, "model": f"M{i}", "drop_date": date,
            "prev_price": 1000.0, "new_price": 1000.0 + eur, "drop_eur": eur, "drop_pct": pct,
            "skroutz_link": f"u{i}", "suspect": False}


class TestMeaningful(unittest.TestCase):
    def test_rule(self):
        self.assertEqual((dr.MIN_DROP_PCT, dr.MIN_DROP_EUR), (5.0, 5.0))
        self.assertTrue(dr.is_meaningful_drop(-5.0, -5.0))
        self.assertFalse(dr.is_meaningful_drop(-3.7, -111.0))   # 3.7% on a 3,000 laptop
        self.assertFalse(dr.is_meaningful_drop(-12.0, -3.0))    # 12% of a 25 euro band
        self.assertFalse(dr.is_meaningful_drop(None, -50.0))

    def test_sql_filter(self):
        self.assertIn("drop_pct <= -5.0", dr.MEANINGFUL_DROP_SQL)
        self.assertIn("drop_eur <= -5.0", dr.MEANINGFUL_DROP_SQL)

    def test_queries_apply_filter(self):
        conn = _conn_returning(18)
        queries.count_drops(conn, 0)
        self.assertIn(dr.MEANINGFUL_DROP_SQL, _sql_and_params(conn)[0])
        conn = _conn_returning(1)
        queries.count_drops_prev_day(conn)
        self.assertIn(dr.MEANINGFUL_DROP_SQL, _sql_and_params(conn)[0])
        with patch.object(queries.pd, "read_sql", return_value=pd.DataFrame()) as rs:
            queries.biggest_drops(MagicMock(), 0, limit=5)
        self.assertIn(dr.MEANINGFUL_DROP_SQL, " ".join(str(rs.call_args[0][0]).split()))

    def test_telegram_uses_same_filter(self):
        for fn in (rp.send_drop_digest, rp.send_success_summary, tb._cmd_drops, tb._cmd_stats):
            self.assertIn("MEANINGFUL_DROP_SQL", inspect.getsource(fn), fn.__name__)


class TestBulk(unittest.TestCase):
    def test_five_same_step_collapse(self):
        rows = [_d(i, -6.1 + i * 0.05, -60.0 - i, brand=b) for i, b in
                enumerate(["Lenovo", "HP", "Asus", "MSI", "Lenovo"])]
        rows.append(_d(9, -20.0, -200.0, cat="phone"))
        out = dr.group_bulk(rows)
        self.assertEqual(len(out), 2)
        bulk = [r for r in out if r.get("bulk")][0]
        self.assertEqual(bulk["count"], 5)
        self.assertEqual(bulk["category"], "laptop")
        self.assertEqual(bulk["drop_eur"], -64.0)
        self.assertIn("Lenovo", bulk["brands"])

    def test_four_stay_separate(self):
        rows = [_d(i, -6.0, -60.0) for i in range(4)]
        self.assertEqual(dr.group_bulk(rows), rows)

    def test_other_day_or_step_not_grouped(self):
        rows = [_d(i, -6.0, -60.0) for i in range(3)] + [_d(5 + i, -6.0, -60.0, date="2026-11-01") for i in range(3)]
        self.assertEqual(len(dr.group_bulk(rows)), 6)


class TestDashboard(unittest.TestCase):
    def test_template_shows_bulk_line_and_rule(self):
        html = gd.render_html(_fixture_data())
        self.assertIn("bulk repricing", html)
        self.assertIn("min_drop_pct", html)


if __name__ == "__main__":
    unittest.main()

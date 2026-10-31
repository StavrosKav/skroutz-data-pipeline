"""
tests/test_kpi_accuracy.py
--------------------------
Dashboard KPI accuracy: COUNT(*) queries, suspect-drop flag, Hot Deals
thresholds, and a render smoke test with fixture data. No DB required.
"""

import json
import re
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from reporting import generate_dashboard as gd
from reporting import queries


def _conn_returning(value):
    conn = MagicMock()
    conn.execute.return_value.scalar.return_value = value
    return conn


def _sql_and_params(conn):
    args = conn.execute.call_args[0]
    sql = str(args[0])
    params = args[1] if len(args) > 1 else {}
    return " ".join(sql.split()), params


class TestCountQueries(unittest.TestCase):
    def test_count_drops_is_count_star_without_limit(self):
        conn = _conn_returning(412)
        self.assertEqual(queries.count_drops(conn, 0), 412)
        sql, params = _sql_and_params(conn)
        self.assertIn("SELECT COUNT(*) FROM vw_drops_7d", sql)
        self.assertNotIn("LIMIT", sql)
        self.assertEqual(params, {"days": 0})

    def test_count_drops_counts_big_drops_too(self):
        conn = _conn_returning(5)
        queries.count_drops(conn, 7)
        sql, params = _sql_and_params(conn)
        self.assertNotIn("drop_pct >", sql)
        self.assertEqual(params, {"days": 7})

    def test_count_suspect_drops(self):
        conn = _conn_returning(3)
        self.assertEqual(queries.count_suspect_drops(conn, 0, 40.0), 3)
        sql, params = _sql_and_params(conn)
        self.assertIn("drop_pct <= -:suspect", sql)
        self.assertEqual(params, {"days": 0, "suspect": 40.0})

    def test_count_new_products(self):
        conn = _conn_returning(137)
        self.assertEqual(queries.count_new_products(conn, days=7), 137)
        sql, params = _sql_and_params(conn)
        self.assertIn("COUNT(*)", sql)
        self.assertIn("first_seen > (SELECT MAX(date) FROM price_snapshots) - :days", sql)
        self.assertNotIn("LIMIT", sql)
        self.assertEqual(params, {"days": 7})

    def test_count_disappeared(self):
        conn = _conn_returning(88)
        self.assertEqual(queries.count_disappeared(conn, days=30), 88)
        sql, params = _sql_and_params(conn)
        self.assertIn("FROM vw_disappeared", sql)
        self.assertNotIn("LIMIT", sql)
        self.assertEqual(params, {"days": 30})

    def test_null_count_is_zero(self):
        self.assertEqual(queries.count_drops(_conn_returning(None), 0), 0)
        self.assertEqual(queries.count_latest_products(_conn_returning(None)), 0)

    def test_fetch_counts_uses_suspect_threshold(self):
        conn = _conn_returning(10)
        with patch.object(queries, "count_drops", wraps=queries.count_drops) as cd:
            counts = gd.fetch_counts(conn)
        self.assertEqual(set(counts), {"drops_today", "drops_week", "suspect_drops_today",
                                       "suspect_drops_week", "new_week", "disappeared_30d",
                                       "latest_products", "listed_today", "listed_prev",
                                       "drops_prev"})
        self.assertTrue(cd.call_args_list)
        for call in cd.call_args_list:
            self.assertNotIn("exclude_suspect_pct", call.kwargs)


class TestHotDealsQuery(unittest.TestCase):
    def _run(self, **kw):
        with patch.object(queries.pd, "read_sql", return_value=pd.DataFrame()) as rs:
            queries.hot_deals("conn", limit=20, **kw)
        sql = " ".join(str(rs.call_args[0][0]).split())
        return sql, rs.call_args.kwargs["params"]

    def test_unfiltered_default_unchanged(self):
        sql, params = self._run()
        self.assertIn("FROM h ORDER BY", sql)
        self.assertEqual(params, {"limit": 20})

    def test_thresholds_applied(self):
        sql, params = self._run(min_drop_pct=3.0, min_drop_eur=5.0)
        self.assertIn("price_chg_pct <= -:min_pct", sql)
        self.assertIn("(price_prev - price_latest) >= :min_eur", sql)
        self.assertIn("ORDER BY hot_score DESC", sql)
        self.assertEqual(params, {"limit": 20, "min_pct": 3.0, "min_eur": 5.0})


class TestThresholdLogic(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(gd.SUSPECT_DROP_PCT, 40.0)
        self.assertGreater(gd.HOT_DEAL_MIN_DROP_PCT, 0)
        self.assertGreater(gd.HOT_DEAL_MIN_DROP_EUR, 0)
        self.assertEqual(gd.PRODUCTS_LIMIT, 3000)

    def test_is_suspect_drop(self):
        self.assertTrue(gd.is_suspect_drop(-52.95))
        self.assertTrue(gd.is_suspect_drop(-40.0))
        self.assertFalse(gd.is_suspect_drop(-39.9))
        self.assertFalse(gd.is_suspect_drop(-3.0))
        self.assertFalse(gd.is_suspect_drop(None))

    def test_is_hot_deal(self):
        self.assertTrue(gd.is_hot_deal(1000.0, 900.0))       # -10%, -€100
        self.assertFalse(gd.is_hot_deal(1000.0, 997.0))      # -0.3% (seen live)
        self.assertFalse(gd.is_hot_deal(50.0, 47.0))         # -6% but only -€3
        self.assertFalse(gd.is_hot_deal(1000.0, 500.0))      # -50% suspect
        self.assertFalse(gd.is_hot_deal(100.0, 110.0))       # rise
        self.assertFalse(gd.is_hot_deal(None, 10.0))

    def test_coerce_drops_flags_suspect(self):
        df = pd.DataFrame([
            {"prev_price": 1000, "new_price": 470, "drop_eur": -530, "drop_pct": -53.0, "drop_date": "2026-10-28"},
            {"prev_price": 1000, "new_price": 900, "drop_eur": -100, "drop_pct": -10.0, "drop_date": "2026-10-28"},
        ])
        out = gd._coerce_drops(df)
        self.assertEqual([d["suspect"] for d in out], [True, False])


def _fixture_data():
    drop = {"product_id": 1, "category": "phone", "brand": "Apple", "model": "iPhone 15",
            "product_name": "Apple iPhone 15", "drop_date": "2026-10-28", "prev_price": 900.0,
            "new_price": 800.0, "drop_eur": -100.0, "drop_pct": -11.1,
            "skroutz_link": "https://example.invalid/1", "suspect": False}
    sus = dict(drop, product_id=2, model="Galaxy S24", brand="Samsung", new_price=400.0,
               drop_eur=-500.0, drop_pct=-55.6, suspect=True)
    return {
        "generated": "2026-10-28", "total_products": 25873, "total_snapshots": 1000000,
        "last_updated": "2026-10-28", "window_start": "2026-01-01",
        "by_category": {"phone": {"count": 10, "median_price": 500.0}},
        "counts": {"drops_today": 412, "drops_week": 1900, "suspect_drops_today": 3,
                   "suspect_drops_week": 9, "new_week": 137, "disappeared_30d": 88,
                   "latest_products": 25873},
        "limits": {}, "thresholds": {"suspect_pct": 40.0, "hot_min_pct": 3.0, "hot_min_eur": 5.0},
        "drops": [sus, drop], "weekly_drops": [sus, drop], "hot_deals": [],
        "brand_data": {}, "brand_trend": {}, "products": [], "new_products": [],
        "disappeared": [], "history": {}, "buy_or_wait": {},
        "market_index": [],
    }


class TestRenderHtml(unittest.TestCase):
    def test_render_embeds_counts(self):
        with patch.object(gd, "chart_url", return_value=""):
            html = gd.render_html(_fixture_data())
        self.assertNotIn("__DATA_JSON__", html)
        m = re.search(r"const DATA\s*=\s*(\{.*?\});\s*\n", html)
        self.assertIsNotNone(m)
        data = json.loads(m.group(1))
        self.assertEqual(data["counts"]["drops_today"], 412)
        self.assertTrue(data["drops"][0]["suspect"])
        # template no longer derives the drops badge from hot_deals
        self.assertNotIn("DATA.drops.length + (DATA.hot_deals", html)
        self.assertIn("by reviews", html)


if __name__ == "__main__":
    unittest.main()


class TestNotSeen30d(unittest.TestCase):
    """'Disappeared' now means: not seen for MORE than 30 days."""

    def test_count_uses_greater_than(self):
        conn = _conn_returning(5)
        queries.count_disappeared(conn, days=30)
        sql, _ = _sql_and_params(conn)
        self.assertIn("days_since_last_seen > :days", sql)

    def test_list_uses_greater_than(self):
        with patch("reporting.queries.pd.read_sql") as rs:
            queries.disappeared(MagicMock(), days=30, limit=10)
        self.assertIn("days_since_last_seen > :days", str(rs.call_args[0][0]))
        self.assertEqual(rs.call_args.kwargs["params"], {"days": 30, "limit": 10})

    def test_view_sql_is_30_days_and_idempotent(self):
        from core.paths import ROOT
        ddl = (ROOT / "sql" / "vw_disappeared.sql").read_text(encoding="utf-8")
        self.assertIn("CREATE OR REPLACE VIEW vw_disappeared", ddl)
        self.assertIn("INTERVAL '30 days'", ddl)

    def test_refresh_applies_view_file_non_fatally(self):
        import run_pipeline as rp
        with patch("run_pipeline.get_engine", side_effect=Exception("down")):
            rp.apply_view_definitions()  # must not raise

    def test_template_label_says_over_30_days(self):
        # Page is English only (lang="en"); label keeps the "> 30 days" meaning.
        from core.paths import ROOT
        tpl = (ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("Not Seen for Over 30 Days", tpl)
        self.assertNotIn("Disappeared (30d)", tpl)

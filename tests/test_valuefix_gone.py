"""Fix 2: New & Gone shows products that stopped appearing in the last 30 days
(crossed the >30-day unseen line recently), not the all-time pile. No DB."""
import unittest
from unittest.mock import MagicMock, patch

from reporting import generate_dashboard as gd
from reporting import queries
from tests.test_kpi_accuracy import _conn_returning, _fixture_data, _sql_and_params


class TestRecentGone(unittest.TestCase):
    def test_count_has_upper_bound(self):
        conn = _conn_returning(1769)
        self.assertEqual(queries.count_disappeared(conn, days=30, window=30), 1769)
        sql, params = _sql_and_params(conn)
        self.assertIn("days_since_last_seen > :days", sql)
        self.assertIn("days_since_last_seen <= :days + :window", sql)
        self.assertEqual(params, {"days": 30, "window": 30})

    def test_list_has_upper_bound(self):
        with patch("reporting.queries.pd.read_sql") as rs:
            queries.disappeared(MagicMock(), days=30, limit=10, window=30)
        self.assertIn("days_since_last_seen <= :days + :window", str(rs.call_args[0][0]))

    def test_dashboard_uses_recent_window(self):
        self.assertEqual(gd.GONE_WINDOW_DAYS, 30)
        with patch.object(queries, "count_disappeared", return_value=5) as cd:
            counts = gd.fetch_counts(_conn_returning(1))
        self.assertEqual(cd.call_args.kwargs.get("window"), 30)
        self.assertIn("gone_30d", counts)
        self.assertNotIn("disappeared_30d", counts)

    def test_template_label_and_badge(self):
        html = gd.render_html(_fixture_data())
        self.assertIn("Stopped Appearing in the Last 30 Days", html)
        self.assertNotIn("disappeared_30d", html)
        self.assertNotIn("Not Seen for Over 30 Days", html)


if __name__ == "__main__":
    unittest.main()

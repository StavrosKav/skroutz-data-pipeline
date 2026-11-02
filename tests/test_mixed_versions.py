"""Products whose skroutz_id was reused by Skroutz for another variant (dedup_review,
RAM/storage differ): the price history may combine variants, so the dashboard marks them."""
import unittest
from unittest.mock import MagicMock

from reporting import buy_or_wait as bw
from reporting import mixed_versions as mv
from tests.test_buy_or_wait import _row
from tests.test_dashboard_features import _render


class TestFetch(unittest.TestCase):
    def test_returns_survivor_ids_from_dedup_review(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [MagicMock(product_id=5), MagicMock(product_id=9)]
        self.assertEqual(mv.fetch(conn), {5, 9})
        sql = " ".join(str(conn.execute.call_args[0][0]).split())
        self.assertIn("FROM dedup_review", sql)
        self.assertIn("survivor_id", sql)
        self.assertIn("COUNT(DISTINCT ram_gb) > 1 OR COUNT(DISTINCT storage_gb) > 1", sql)


class TestBuyOrWaitFlag(unittest.TestCase):
    def test_mixed_flag_on_rows(self):
        rows = [_row(id=1, skroutz_id=111, current=781.0), _row(id=2, skroutz_id=222, current=899.0)]
        out = bw.build(rows, mixed={1})
        self.assertEqual(out["near_low"], [])
        self.assertTrue(out["check"][0]["mixed"])
        self.assertFalse(out["above"][0]["mixed"])

    def test_default_no_mixed(self):
        out = bw.build([_row(current=781.0)])
        self.assertFalse(out["near_low"][0]["mixed"])


class TestTemplate(unittest.TestCase):
    def test_badge_and_note_in_page(self):
        html = _render()
        self.assertIn("function mixedBadge", html)
        self.assertIn("Mixed versions", html)
        self.assertIn("p.mixed ? mixedBadge()", html)
        self.assertIn("r.mixed ? ' ' + mixedBadge()", html)

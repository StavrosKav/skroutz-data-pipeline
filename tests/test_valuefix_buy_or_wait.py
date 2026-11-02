"""Fix 1: flagged rows leave the Buy or Wait lists and go to "Needs checking"
with a reason. No DB."""
import unittest
from unittest.mock import MagicMock

from reporting import buy_or_wait as bw


def _row(**kw):
    base = dict(id=1, skroutz_id=111, category="phone", brand="Apple", model="iPhone 15",
                product_name="Apple iPhone 15 128GB", skroutz_link="https://www.skroutz.gr/s/111/x.html",
                current=800.0, lo=780.0, med=850.0, hi=900.0, n_days=60, n_drops=6,
                n_jumps=0, big_step_pct=-3.0, big_step_date="2026-10-01")
    base.update(kw)
    return MagicMock(**base)


class TestNeedsCheck(unittest.TestCase):
    def test_big_gap_moves_to_check_with_step_reason(self):
        rows = [_row(current=400.0, lo=400.0, big_step_pct=-52.9, big_step_date="2026-10-31", n_jumps=1),
                _row(id=2, skroutz_id=2, product_name="B", current=781.0)]
        out = bw.build(rows)
        self.assertEqual([r["name"] for r in out["near_low"]], ["B"])
        self.assertEqual(len(out["check"]), 1)
        reason = out["check"][0]["reason"]
        self.assertIn("52.9%", reason)
        self.assertIn("2026-10-31", reason)
        self.assertEqual(out["counts"]["phone"]["near_low"], 1)
        self.assertEqual(out["counts"]["phone"]["check"], 1)

    def test_alternating_reason(self):
        out = bw.build([_row(current=400.0, lo=400.0, n_jumps=6, big_step_pct=-41.0)])
        self.assertIn("switches", out["check"][0]["reason"])

    def test_mixed_goes_to_check_even_when_above(self):
        out = bw.build([_row(current=950.0)], mixed={1})
        self.assertEqual(out["above"], [])
        self.assertIn("versions", out["check"][0]["reason"])

    def test_rule_documented_in_params(self):
        out = bw.build([])
        self.assertIn("check_rule", out["params"])
        self.assertEqual(out["check"], [])

    def test_sql_reports_biggest_step(self):
        for col in ("n_jumps", "big_step_pct", "big_step_date"):
            self.assertIn(col, bw.SQL)


if __name__ == "__main__":
    unittest.main()


class TestNeedsCheckTemplate(unittest.TestCase):
    def test_collapsed_section_rendered(self):
        from reporting import generate_dashboard as gd
        from tests.test_kpi_accuracy import _fixture_data
        data = _fixture_data()
        data["buy_or_wait"] = bw.build([_row(current=400.0, lo=400.0, big_step_pct=-50.0)])
        html = gd.render_html(data)
        self.assertIn('<details id="bow-check"', html)
        self.assertIn("Needs checking", html)
        self.assertIn("function buildBowCheck", html)

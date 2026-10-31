"""/analyze builds facts only from DB rows; the LLM sees only those facts."""
import datetime as dt
import unittest

from agents.ask import analyze as an
from agents.ask.guard import validate_sql


def fake_exec(sql):
    s = " ".join(sql.split())
    if s == "SELECT MAX(date) FROM price_snapshots":
        return ["max"], [(dt.date(2026, 10, 28),)]
    if "FROM mv_latest_prices" in s:
        return [], [(1200, 512.34, 399.0, 9.9, 2999.0)]
    if "FROM mv_daily_market_index" in s:
        return [], [(dt.date(2026, 9, 28), 500.0), (dt.date(2026, 10, 28), 475.0)]
    if "FROM mv_brand_summary" in s:
        return [], [("Samsung", 300, 450.0), ("Xiaomi", 250, 280.5)]
    if "COUNT(*), ROUND(AVG(drop_pct)" in s:
        return [], [(87, -6.2)]
    if "FROM vw_drops_7d" in s:
        return [], [("Apple", "iPhone 15", 899.0, 749.0, -16.7, dt.date(2026, 10, 27))]
    if "mv_price_trend_direction" in s:
        return [], [("falling", 40), ("stable", 900)]
    raise AssertionError(f"unexpected SQL: {s}")


class TestAnalyze(unittest.TestCase):

    def test_facts_come_from_rows(self):
        facts = "\n".join(an.build_facts(fake_exec, "phone"))
        for needle in ("2026-10-28", "1200", "€512.34", "-5.0%", "Samsung 300",
                       "87", "iPhone 15 €899.0 -> €749.0", "falling 40"):
            self.assertIn(needle, facts)

    def test_all_queries_are_category_scoped_and_read_only(self):
        for name, sql in an._queries("laptop").items():
            validate_sql(sql)  # same read-only/allowlist guard as /ask
            if name != "latest_date":
                self.assertIn("'laptop'", sql, name)

    def test_unknown_category_never_reaches_sql(self):
        with self.assertRaises(ValueError):
            an._queries("x'; DROP TABLE products; --")

    def test_llm_receives_only_facts_and_output_shows_them(self):
        seen = {}

        def llm(messages, max_tokens):
            seen["m"] = messages
            return "Οι τιμές έπεσαν 5%."
        out = an.analyze(fake_exec, llm, "phone")
        self.assertIn("ONLY the facts", seen["m"][0]["content"])
        self.assertIn("€512.34", seen["m"][1]["content"])
        self.assertIn("Οι τιμές έπεσαν 5%.", out)
        self.assertIn("από τη βάση", out)

    def test_llm_failure_falls_back_to_facts(self):
        def llm(messages, max_tokens):
            raise ValueError("NIM_API_KEY required")
        out = an.analyze(fake_exec, llm, "phone")
        self.assertIn("€512.34", out)

    def test_no_history_is_stated_not_invented(self):
        def ex(sql):
            if "mv_daily_market_index" in sql:
                return [], []
            return fake_exec(sql)
        self.assertIn("not enough history", "\n".join(an.build_facts(ex, "tablet")))

    def test_output_is_html_escaped(self):
        def ex(sql):
            if "mv_brand_summary" in sql:
                return [], [("<b>X</b>", 1, 1.0)]
            return fake_exec(sql)
        out = an.analyze(ex, None, "phone")
        self.assertIn("&lt;b&gt;X", out)


if __name__ == "__main__":
    unittest.main()

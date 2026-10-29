"""The daily 'disappeared' alert must use the same >30 day rule as vw_disappeared."""

import datetime as dt

from sqlalchemy import create_engine, text

import run_pipeline as rp

TODAY = dt.date(2026, 10, 29)


def _db(last_seen_days_ago):
    eng = create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE products (brand TEXT, model TEXT, category TEXT, product_name TEXT,"
                       " last_seen DATE, skroutz_link TEXT)"))
        for i, d in enumerate(last_seen_days_ago):
            c.execute(text("INSERT INTO products VALUES ('b','m','smartwatch',:n,:ls,'l')"),
                      {"n": f"p{i}", "ls": (TODAY - dt.timedelta(days=d)).isoformat()})
    return eng


def test_short_gaps_are_not_disappeared():
    # The Oct 29 false alarm: 1-2 day gaps (partial scrape), not gone products.
    with _db([1, 1, 2, 2, 5, 30]).connect() as c:
        assert rp.newly_disappeared_rows(c, TODAY) == []


def test_product_crossing_30_days_is_reported_once():
    with _db([30, 31, 32, 60]).connect() as c:
        rows = rp.newly_disappeared_rows(c, TODAY)
    assert [r.product_name for r in rows] == ["p1"]


def test_rule_matches_view():
    assert rp.DISAPPEARED_DAYS == 30
    assert "30 days" in open("sql/vw_disappeared.sql", encoding="utf-8").read()

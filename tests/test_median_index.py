"""
Audit item D: typical prices use medians, and the Overview "Category Price
Index" is a fixed-basket index (same products every day, base day = 100),
not a mean over a changing product mix. No DB.
"""
import re
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

from reporting import queries

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")


def _sql(fn, *a, **kw):
    seen = {}

    def fake_read_sql(q, conn, params=None):
        seen["sql"] = " ".join(str(q).split())
        seen["params"] = params
        return pd.DataFrame()
    orig = pd.read_sql
    pd.read_sql = fake_read_sql
    try:
        fn(MagicMock(), *a, **kw)
    finally:
        pd.read_sql = orig
    return seen


def test_category_snapshot_has_median():
    sql = _sql(queries.category_snapshot)["sql"]
    assert "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY s.price_eur) AS median_price" in sql


def test_brand_trend_is_median_from_snapshots():
    sql = _sql(queries.brand_trend, "phone")["sql"]
    assert "PERCENTILE_CONT(0.5)" in sql and "AS median_price" in sql
    assert "vw_brand_price_trend" not in sql


def test_basket_index_fixed_basket():
    s = _sql(queries.basket_index, days=90)
    sql = s["sql"]
    assert "base_day" in sql and "BASKET_COVERAGE" not in sql
    assert ":coverage" in sql and s["params"]["coverage"] == queries.BASKET_COVERAGE
    # geometric mean of price relatives (Jevons, as in consumer price indices);
    # a median of relatives sits at exactly 100 because most prices do not move.
    assert "EXP(AVG(LN(d.price / k.base_price))) * 100" in sql
    assert "vw_daily_market_index" not in sql


def test_stat_cards_say_median():
    assert "info.median_price" in HTML and "'Median ' + label + ' Price'" in HTML
    assert "info.avg_price" not in HTML


def test_trend_charts_say_median():
    assert "Avg Price Trend" not in HTML and "Median Price Trend" in HTML
    assert re.search(r"let brandMetric = 'median'", HTML)


def test_index_chart_sized_and_explained():
    assert 'id="market-index-wrap" style="height:280px;position:relative"' in HTML
    assert "maintainAspectRatio: false" in HTML
    assert "Category Price Index</b>" in HTML  # How it works bullet
    assert "p.idx" in HTML


def test_matplotlib_chart_uses_median():
    src = (ROOT / "reporting/charts_from_db.py").read_text(encoding="utf-8")
    assert "avg_price" not in src and "Median daily price" in src

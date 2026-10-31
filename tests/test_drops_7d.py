"""Drops are measured against min(yesterday, median of the previous 7 snapshots).

Real cases from 2026-10-31 (prices from the DB, newest last). A one-day spike
that returns to normal must not count as a big drop; a real cut must.
"""
import pathlib
from unittest.mock import MagicMock

import pytest

from reporting import queries
from reporting.drops import BIG_DROP_LABEL, drop_vs_baseline

CASES = {
    # name: (history oldest->newest before today, today, expected pct or None)
    "Colmi M42 spike":    ([23.09, 23.09, 23.09, 22.15, 22.15, 21.68, 85.99], 21.68, -6.11),
    "Colmi L10 spike":    ([20.05] * 6 + [33.90], 18.82, -6.13),
    "Oppo alternation":   ([264.6, 452.0, 264.6, 264.6, 452.0, 264.6, 452.0], 264.6, None),
    "Acer back to usual": ([2423.16] * 4 + [2423.16, 3505.9, 4023.26], 2359.72, -2.62),
    "WK WH-06 real":      ([41.5] * 7, 20.5, -50.60),
    "WK WH-05 real":      ([43.38, 43.38, 42.9, 43.38, 43.38, 43.38, 42.9], 24.0, -44.06),
    "Galaxy Watch5 real": ([267.31, 396.93, 267.12, 267.12, 267.12, 396.93, 396.93], 150.0, -43.89),
}


@pytest.mark.parametrize("name", CASES)
def test_known_cases(name):
    hist, today, want = CASES[name]
    got = drop_vs_baseline(hist, today)
    if want is None:
        assert got is None
    else:
        assert got is not None and got["drop_pct"] == pytest.approx(want, abs=0.01)


def test_needs_three_snapshots_of_history():
    assert drop_vs_baseline([50.0, 50.0], 10.0) is None


def test_no_repeat_when_price_stays_low():
    # Fell from 369 a week ago and is flat since: tiny move, not another -42%.
    got = drop_vs_baseline([369.11, 369.11, 369.11, 159.99, 212.91], 212.8)
    assert got is not None and got["drop_pct"] > -1


def test_big_drop_label_renamed():
    assert BIG_DROP_LABEL == "μεγάλη πτώση, έλεγξε"


def test_dashboard_counts_use_7d_view_and_include_big_drops():
    conn = MagicMock()
    conn.execute.return_value.scalar.return_value = 5
    queries.count_drops(conn, 0)
    sql = str(conn.execute.call_args.args[0])
    assert "vw_drops_7d" in sql and "drop_pct >" not in sql


def test_template_no_longer_hides_big_drops():
    html = pathlib.Path("templates/dashboard.html").read_text(encoding="utf-8")
    assert "!d.suspect" not in html
    assert "big drop, check" in html
    assert "not counted" not in html


def test_consumers_read_7d_view():
    for f in ("run_pipeline.py", "alerts/telegram_bot.py", "reporting/queries.py"):
        src = pathlib.Path(f).read_text(encoding="utf-8")
        assert "vw_biggest_drops" not in src, f

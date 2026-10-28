"""Pagination recovery, drift-before-dedup, run-date CSV pick, count deviation."""
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

import scrapers.scraper_core as sc
from agents.pipeline_monitoring import scraper_health_monitor as shm


def _row(i, link=None):
    return {"Product": f"P{i}", "Link": link or f"https://www.skroutz.gr/s/{i}/p.html",
            "Price_EUR": "10,00 €", "Specs": "x"}


def test_page_helpers():
    assert sc._page_number("https://x/c/1.html?o=a&page=7") == 7
    assert sc._page_number("https://x/c/1.html") == 1
    u = sc._with_page("https://x/c/1.html?o=a&page=7", 8)
    assert "o=a" in u and "page=8" in u and "page=7" not in u


def test_listing_total_parses_greek_count():
    d = MagicMock()
    d.find_element.return_value.text = "Smartwatches 2.345 προϊόντα"
    assert sc._listing_total(d) == 2345
    d.find_element.return_value.text = "nothing"
    assert sc._listing_total(d) is None


def _scrape(pages, advance, min_products=1):
    driver = MagicMock()
    driver.current_url = "https://www.skroutz.gr/c/40/x.html?page=1"
    driver.find_element.return_value.text = ""
    state = {"n": 0}
    loads = []

    def goto(_d):
        if state["n"] + 1 >= len(pages):
            return False
        state["n"] += 1
        return True

    def load(_d, url, *a, **k):
        loads.append(url)
        if "page=" in url:
            driver.current_url = url

    def adv(_d, _seen):
        ok = advance(state["n"], len(loads))
        if ok:
            driver.current_url = sc._with_page(driver.current_url, state["n"] + 1)
        return ok

    cfg = sc.ScraperConfig(category="phones", url="https://www.skroutz.gr/c/40/x.html",
                           folder="out", file_prefix="skroutz_phones", min_products=min_products)
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(sc, "HERE", tmp), \
         patch.object(sc, "_start_chrome", return_value=driver), \
         patch.object(sc, "_load_page", side_effect=load), \
         patch.object(sc, "_dismiss_overlays"), \
         patch.object(sc, "_wait_for_cards", side_effect=lambda d: pages[state["n"]]), \
         patch.object(sc, "parse_card", side_effect=lambda c, extract_memory_info=False: c), \
         patch.object(sc, "_goto_next_page", side_effect=goto), \
         patch.object(sc, "_wait_for_page_advance", side_effect=adv), \
         patch("scrapers.scraper_core.time.sleep"):
        sc.scrape(cfg)
        import pandas as pd
        out = os.path.join(tmp, "out")
        return pd.read_csv(os.path.join(out, os.listdir(out)[0])), loads


def test_non_advance_reloads_by_url_and_continues():
    pages = [[_row(i)] for i in range(1, 6)]
    # page 3 fails to advance once via Next; URL reload fixes it.
    calls = {"failed": False}

    def advance(n, _loads):
        if n == 2 and not calls["failed"]:
            calls["failed"] = True
            return False
        return True
    df, loads = _scrape(pages, advance)
    assert len(df) == 5
    assert any("page=3" in u for u in loads)


def test_two_stale_pages_no_longer_stop():
    pages = [[_row(1)], [_row(2)], [_row(1)], [_row(2)], [_row(3)]]
    df, _ = _scrape(pages, lambda n, loads: True)
    assert len(df) == 3


def test_wrap_detected_by_page_number():
    pages = [[_row(i)] for i in range(1, 10)]

    def advance(n, _loads):
        return True
    with patch.object(sc, "_page_number", side_effect=[1, 2, 2, 1]):
        df, _ = _scrape(pages, advance)
    assert len(df) == 2


def test_drift_guard_runs_before_dedup():
    import pandas as pd
    rows = [_row(i) for i in range(1, 51)] + [_row(i, link="N/A") for i in range(51, 61)]
    with patch.object(sc, "_save_debug_artifacts", create=True), pytest.raises(SystemExit):
        sc._check_markup_drift(pd.DataFrame(rows), "phones")
    # Same rows after dedup would have passed (only one N/A left).
    sc._check_markup_drift(pd.DataFrame(rows).drop_duplicates(subset="Link"), "phones")


def test_pick_run_csv_uses_run_date_not_mtime():
    files = ["skroutz_phones_2026-10-27.csv", "skroutz_phones_2026-10-28.csv"]
    assert shm.pick_run_csv(files, "2026-10-27") == files[0]
    assert shm.pick_run_csv(files, "2026-10-29") is None
    with patch.dict(os.environ, {"PIPELINE_DATE": "2026-10-28"}):
        assert shm.pick_run_csv(files) == files[1]


def test_count_deviation():
    assert shm.count_deviation(1000, [1000] * 7) is None
    assert shm.count_deviation(1190, [1000] * 7) is None
    assert shm.count_deviation(700, [1000] * 7) == -30.0
    assert shm.count_deviation(1300, [1000] * 7) == 30.0
    assert shm.count_deviation(10, []) is None


def test_health_monitor_flags_count_drop(tmp_path):
    folder = tmp_path / "Phones_skroutz"
    folder.mkdir()
    for d, n in [(f"2026-10-{x:02d}", 300) for x in range(20, 27)] + [("2026-10-27", 200)]:
        (folder / f"skroutz_phones_{d}.csv").write_text("h\n" + "r\n" * n, encoding="utf-8")
    mon = shm.ScraperHealthMonitor({"base_path": str(tmp_path), "categories": ["phones"],
                                    "min_rows": 100, "max_age_hours": 10_000})
    with patch.dict(shm.CATEGORY_FOLDERS, {"phones": "Phones_skroutz"}), \
         patch.dict(os.environ, {"PIPELINE_DATE": "2026-10-27"}):
        res = mon.process({})
    assert not res["healthy"]
    assert "7-day avg" in res["details"]["phones"]["reason"]

"""
tests/test_scrape.py
Scraping: card parsing, new-row collection, pagination guard.
Split from the former tests/test_{clean,load,scrape,alerts,telegram_cmds,ops}.py. No network or DB.
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch



# ─────────────────────────────────────────────────────────────────────────────
# parse_card against real skroutz markup (tests/fixtures/listing_card_phone.html)
# ─────────────────────────────────────────────────────────────────────────────

class _LxmlElement:
    """
    Minimal Selenium-element stand-in backed by lxml, so parse_card's real CSS
    and XPath selectors run against saved fixture markup without a browser.
    """

    def __init__(self, el):
        self._el = el

    def find_elements(self, by, sel):
        from selenium.webdriver.common.by import By
        found = self._el.xpath(sel) if by == By.XPATH else self._el.cssselect(sel)
        return [_LxmlElement(e) for e in found]

    def find_element(self, by, sel):
        found = self.find_elements(by, sel)
        if not found:
            raise LookupError(sel)
        return found[0]

    @property
    def text(self):
        return self._el.text_content().strip()

    def get_attribute(self, name):
        return self._el.get(name) or ""


class TestParseCardFixture(unittest.TestCase):
    """
    Parses a real listing card captured from skroutz.gr on 2026-07-17.

    This is the markup-change early-warning test: if skroutz renames a class,
    re-capture the fixture (first card's outerHTML from any listing page) —
    this failing on a fresh fixture means the selectors in scraper_core broke.
    """

    @classmethod
    def setUpClass(cls):
        import lxml.html
        from scrapers.scraper_core import parse_card
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixtures", "listing_card_phone.html")
        with open(path, encoding="utf-8") as f:
            card = lxml.html.fromstring(f.read())
        cls.row = parse_card(_LxmlElement(card), extract_memory_info=True)

    def test_product_title(self):
        self.assertEqual(self.row["Product"], "Samsung Galaxy A17 5G Dual SIM (4/128GB) Μπλε")

    def test_link_absolute_and_stripped(self):
        self.assertEqual(
            self.row["Link"],
            "https://www.skroutz.gr/s/62516671/samsung-galaxy-a17-5g-dual-sim-4-128gb-mple.html",
        )

    def test_price(self):
        self.assertEqual(self.row["Price_EUR"], "174.00")

    def test_reviews_count(self):
        self.assertEqual(self.row["Reviews"], "109")

    def test_rating(self):
        self.assertEqual(self.row["Rating"], "4.7")

    def test_installments(self):
        self.assertEqual(self.row["Installments_per_month"], "30,50")
        self.assertEqual(self.row["Installments_in_total"], "6")

    def test_specs(self):
        self.assertIn("Κάμερα 50MP", self.row["Specs"])


# ─────────────────────────────────────────────────────────────────────────────
# Pagination wraparound + chromedriver patcher race + scrape-stage timeout
# ─────────────────────────────────────────────────────────────────────────────

def _listing_row(i):
    return {
        "Product": f"P{i}",
        "Specs": "s",
        "Price_EUR": "10.00",
        "Installments_per_month": "N/A",
        "Installments_in_total": "N/A",
        "Rating": "N/A",
        "Reviews": "N/A",
        "Link": f"https://www.skroutz.gr/s/{i}/x.html",
    }


class TestCollectNewRows(unittest.TestCase):

    def test_first_page_all_new(self):
        from scrapers.scraper_core import _collect_new_rows
        seen = set()
        kept, n = _collect_new_rows([_listing_row(1), _listing_row(2)], seen)
        self.assertEqual(n, 2)
        self.assertEqual(len(kept), 2)
        self.assertEqual(seen, {
            "https://www.skroutz.gr/s/1/x.html",
            "https://www.skroutz.gr/s/2/x.html",
        })

    def test_wraparound_page_is_all_duplicates(self):
        from scrapers.scraper_core import _collect_new_rows
        seen = {"https://www.skroutz.gr/s/1/x.html", "https://www.skroutz.gr/s/2/x.html"}
        kept, n = _collect_new_rows([_listing_row(1), _listing_row(2)], seen)
        self.assertEqual(n, 0)
        self.assertEqual(kept, [])

    def test_na_links_kept_but_not_counted_as_progress(self):
        from scrapers.scraper_core import _collect_new_rows
        seen = set()
        na = _listing_row(1)
        na["Link"] = "N/A"
        kept, n = _collect_new_rows([na, _listing_row(2)], seen)
        self.assertEqual(n, 1)
        self.assertEqual(len(kept), 2)


class TestScrapePaginationGuard(unittest.TestCase):
    """scrape() must stop when listing pages wrap, not run until the 2h kill."""

    def _run(self, pages, goto_next=True, max_pages=None, min_products=None, expect_exit=False):
        import scrapers.scraper_core as scraper_core
        driver = MagicMock()
        driver.current_url = "https://www.skroutz.gr/c/40/x.html?page=1"
        call = {"n": 0}

        def wait_cards(_driver):
            i = min(call["n"], len(pages) - 1)
            return pages[i]

        def parse_card(card, extract_memory_info=False):
            return card

        def goto(_driver):
            call["n"] += 1
            driver.current_url = f"https://www.skroutz.gr/c/40/x.html?page={call['n'] + 1}"
            return goto_next

        min_val = scraper_core.MIN_PRODUCTS if min_products is None else min_products
        max_val = scraper_core.MAX_PAGES if max_pages is None else max_pages
        cfg = scraper_core.ScraperConfig(
            category="phones", url="https://example.test",
            folder="out", file_prefix="skroutz_phones",
            min_products=min_val,
        )
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(scraper_core, "HERE", tmp), \
             patch.object(scraper_core, "_start_chrome", return_value=driver), \
             patch.object(scraper_core, "_load_page"), \
             patch.object(scraper_core, "_wait_for_cards", side_effect=wait_cards), \
             patch.object(scraper_core, "parse_card", side_effect=parse_card), \
             patch.object(scraper_core, "_goto_next_page", side_effect=goto), \
             patch.object(scraper_core, "_wait_for_page_advance", return_value=True), \
             patch.object(scraper_core, "MAX_PAGES", max_val), \
             patch.object(scraper_core, "MIN_PRODUCTS", min_val), \
             patch("scrapers.scraper_core.time.sleep"):
            out = os.path.join(tmp, "out", f"skroutz_phones_{__import__('datetime').date.today().isoformat()}.csv")
            if max_pages is not None or expect_exit:
                with self.assertRaises(SystemExit) as ctx:
                    scraper_core.scrape(cfg)
                self.assertEqual(ctx.exception.code, 1)
                self.assertFalse(os.path.exists(out))
                return None
            scraper_core.scrape(cfg)
            self.assertTrue(os.path.exists(out))
            import pandas as pd
            return pd.read_csv(out)

    def test_stops_on_wrapped_duplicate_pages(self):
        def page(start, n=50):
            return [_listing_row(i) for i in range(start, start + n)]
        unique = [page(1), page(51), page(101), page(151), page(201)]
        pages = unique + unique[:2] + unique[:2]
        df = self._run(pages)
        self.assertEqual(len(df), 250)

    def test_max_pages_aborts_without_writing(self):
        pages = [[_listing_row(i)] for i in range(1, 8)]
        self._run(pages, max_pages=3)

    def test_category_floors_catch_a_single_listing_page(self):
        from scrapers.scraper_core import CONFIGS
        for name, cfg in CONFIGS.items():
            with self.subTest(category=name):
                self.assertGreaterEqual(cfg.min_products, 100)
                self.assertLess(cfg.min_products, 2000)

    def test_goto_next_follows_href_instead_of_click(self):
        import scrapers.scraper_core as scraper_core
        driver = MagicMock()
        btn = MagicMock()
        btn.get_attribute.return_value = "/c/40/x.html?page=2"
        with patch("scrapers.scraper_core.WebDriverWait") as wait, \
             patch.object(scraper_core, "_load_page") as load, \
             patch("scrapers.scraper_core.time.sleep"):
            wait.return_value.until.return_value = btn
            self.assertTrue(scraper_core._goto_next_page(driver))
        load.assert_called_once()
        self.assertIn("page=2", load.call_args[0][1])
        btn.click.assert_not_called()

    def test_run_date_honours_pipeline_date_env(self):
        import scrapers.scraper_core as scraper_core
        with patch.dict(os.environ, {"PIPELINE_DATE": "2026-08-31"}):
            self.assertEqual(scraper_core._run_date(), "2026-08-31")

    def test_thin_scrape_aborts_without_writing(self):
        # 2026-08-25: ~54 unique products then Next did not advance.
        pages = (
            [[_listing_row(i)] for i in range(1, 55)]
            + [[_listing_row(1)], [_listing_row(2)], [_listing_row(3)]]
        )
        self._run(pages, expect_exit=True)

    def test_same_url_with_new_products_keeps_going(self):
        pages = [[_listing_row(i)] for i in range(1, 5)]
        df = self._run(pages, min_products=1, goto_next=True)
        # goto_next True forever would wrap after page 4 into duplicates and
        # stop via stale; 4 unique products still written because min=1.
        self.assertEqual(len(df), 4)

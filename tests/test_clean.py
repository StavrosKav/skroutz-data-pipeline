"""
tests/test_clean.py
Cleaning: clean_price, RAM/storage extraction, reviews, empty raw CSV.
Split from the former tests/test_{clean,load,scrape,alerts,telegram_cmds,ops}.py. No network or DB.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

import importlib as _imp
from etl.clean_phones import extract_ram_storage


# ── clean_price
# ─────────────────────────────────────────────────────────────────────────────

# All four cleaner entry points re-export clean_price from clean_common; every
# case runs against each module so a broken re-export fails the suite.
CLEANER_MODULES = ("etl.clean_phones", "etl.clean_laptops", "etl.clean_tablets", "etl.clean_smartwatches")


class TestCleanPrice(unittest.TestCase):
    # NOTE: the scraper pre-processes prices before the cleaner sees them:
    #   scraper: "1.800,00 €" → "1.800.00" (comma→dot, strip €/spaces)
    # clean_price() receives the already-pre-processed format from the CSV.

    def _assert_all(self, raw, expected):
        import math
        for mod_name in CLEANER_MODULES:
            with self.subTest(module=mod_name):
                result = _imp.import_module(mod_name).clean_price(raw)
                if expected is None:
                    self.assertTrue(math.isnan(result))
                else:
                    self.assertAlmostEqual(result, expected)

    def test_plain_integer(self):
        self._assert_all("299", 299.0)

    def test_scraper_preprocessed_thousands(self):
        # "1.800,00 €" via scraper becomes "1.800.00" in the CSV
        self._assert_all("1.800.00", 1800.0)

    def test_scraper_preprocessed_high_value(self):
        self._assert_all("2.299.99", 2299.99)

    def test_scraper_preprocessed_1200(self):
        self._assert_all("1.200.00", 1200.0)

    def test_plain_with_euro_sign(self):
        self._assert_all("599 €", 599.0)

    def test_decimal_dot_format(self):
        self._assert_all("599.99", 599.99)

    def test_na_returns_nan(self):
        self._assert_all("N/A", None)

    def test_empty_returns_nan(self):
        self._assert_all("", None)

    def test_comma_decimal_format(self):
        # "249,00" (Greek decimal comma, no thousands) → 249.0
        self._assert_all("249,00", 249.0)

    def test_whitespace_stripped(self):
        self._assert_all("  249,00  ", 249.0)


# ── extract_ram_storage
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractRamStorage(unittest.TestCase):

    def _row(self, product="", memory_info="", specs=""):
        return {"Product": product, "Memory_Info": memory_info, "Specs": specs}

    def test_pattern1_gb(self):
        row = self._row(product="Samsung Galaxy A56 5G (8/256GB) Blue")
        self.assertEqual(extract_ram_storage(row), (8, 256))

    def test_pattern1_tb(self):
        row = self._row(product="Apple iPhone 17 Pro Max (8/1TB) Black")
        self.assertEqual(extract_ram_storage(row), (8, 1000))

    def test_pattern2_greek_label(self):
        row = self._row(memory_info="Μνήμη: 12/512GB")
        self.assertEqual(extract_ram_storage(row), (12, 512))

    def test_pattern2_greek_label_tb(self):
        row = self._row(memory_info="Μνήμη: 8/1TB")
        self.assertEqual(extract_ram_storage(row), (8, 1000))

    def test_no_match(self):
        row = self._row(product="Some Product Without Memory Info")
        self.assertEqual(extract_ram_storage(row), (None, None))

    def test_display_size_not_matched(self):
        # "(6.7/128)" — no GB/TB suffix must NOT match pattern1
        row = self._row(product="Phone (6.7/128) Black")
        self.assertEqual(extract_ram_storage(row), (None, None))

    def test_specs_fallback(self):
        row = self._row(specs='Μνήμη: 4/64GB, Οθόνη: 6.5"')
        self.assertEqual(extract_ram_storage(row), (4, 64))

    def test_high_ram(self):
        row = self._row(product="Gaming Phone (16/512GB) Gray")
        self.assertEqual(extract_ram_storage(row), (16, 512))


# ─────────────────────────────────────────────────────────────────────────────
# clean_reviews — recovery of malformed review counts
# ─────────────────────────────────────────────────────────────────────────────

class TestCleanReviews(unittest.TestCase):

    def _clean(self, values):
        import pandas as pd
        from etl.clean_common import clean_reviews
        return clean_reviews(pd.Series(values))

    def test_plain_counts(self):
        result = self._clean(["37", "416", "1"])
        self.assertEqual(result.tolist(), [37.0, 416.0, 1.0])

    def test_malformed_count_rating_joined(self):
        # The scraper's reviews element sometimes captured count and rating
        # joined by a newline; the count (first integer) must be recovered.
        result = self._clean(["1\n0.0", "12\n4.5"])
        self.assertEqual(result.tolist(), [1.0, 12.0])

    def test_na_and_missing_become_nan(self):
        result = self._clean(["N/A", None])
        self.assertTrue(result.isna().all())


class TestRunCleanEmpty(unittest.TestCase):

    def test_empty_raw_csv_exits_nonzero(self):
        import pandas as pd
        from etl.clean_common import CleanerConfig, run_clean
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = os.path.join(tmp, "Phones_skroutz")
            os.makedirs(raw_dir)
            today = __import__("datetime").date.today().isoformat()
            pd.DataFrame(columns=["Product", "Price_EUR"]).to_csv(
                os.path.join(raw_dir, f"skroutz_phones_{today}.csv"), index=False
            )
            cfg = CleanerConfig(
                category="phones",
                raw_folder="Phones_skroutz",
                raw_prefixes=("skroutz_phones",),
                clean_folder="Phones_skroutz_clean",
                final_columns=("Product", "Price_EUR"),
            )
            with patch("etl.clean_common.BASE", tmp):
                with self.assertRaises(SystemExit) as ctx:
                    run_clean(cfg)
            self.assertEqual(ctx.exception.code, 1)

"""
tests/test_pipeline.py
----------------------
High-value unit tests for the Skroutz Price Tracker pipeline.

Covers:
  - Price parsing logic (Greek number format edge cases)
  - RAM/storage extraction (all pattern variants)
  - DB loader helper coercions (_val, _int, _float)
  - Notification deduplication (_already_sent / _mark_sent)
  - Watchlist atomic write / read (telegram_bot._wl_write / _wl_read)
  - N/A link guard logic

Run:
  python -m pytest tests/ -v

No network or DB connections required — all tests are pure-unit.
"""

import os
import re
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─────────────────────────────────────────────────────────────────────────────
# Inline copies of Data_Phone pure functions (no file I/O at module level)
# Kept in sync with production code; changes there must be mirrored here.
# ─────────────────────────────────────────────────────────────────────────────

def clean_price(val):
    s = re.sub(r'[^\d.,]', '', str(val)).strip()
    if s.count('.') > 1:
        parts = s.split('.')
        s = ''.join(parts[:-1]) + '.' + parts[-1]
    s = s.replace(',', '.')
    return pd.to_numeric(s, errors='coerce')


def extract_ram_storage(row):
    text = str(row.get('Product', '')) + " " + str(row.get('Memory_Info', '')) + " " + str(row.get('Specs', ''))
    match = re.search(r'\((\d+)/(\d+)(GB|TB)\)', text, re.IGNORECASE)
    if match:
        ram, storage = int(match.group(1)), int(match.group(2))
        if (match.group(3) or '').upper() == 'TB':
            storage *= 1000
        return ram, storage
    match = re.search(r'Μνήμη:\s*(\d+)/(\d+)(GB|TB)?', text, re.IGNORECASE)
    if match:
        ram, storage = int(match.group(1)), int(match.group(2))
        if (match.group(3) or '').upper() == 'TB':
            storage *= 1000
        return ram, storage
    return None, None


# ─────────────────────────────────────────────────────────────────────────────
# Inline copies of 4csvsTOsql helper functions
# ─────────────────────────────────────────────────────────────────────────────

def _val(row, col):
    v = row.get(col)
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v

def _int(row, col):
    v = _val(row, col)
    try:
        return int(v) if v is not None else None
    except (ValueError, TypeError):
        return None

def _float(row, col):
    v = _val(row, col)
    try:
        return float(v) if v is not None else None
    except (ValueError, TypeError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 1. clean_price
# ─────────────────────────────────────────────────────────────────────────────

class TestCleanPrice(unittest.TestCase):
    # NOTE: the scraper pre-processes prices before the cleaner sees them:
    #   scraper: "1.800,00 €" → "1.800.00" (comma→dot, strip €/spaces)
    # clean_price() receives the already-pre-processed format from the CSV.

    def test_plain_integer(self):
        self.assertAlmostEqual(clean_price("299"), 299.0)

    def test_scraper_preprocessed_thousands(self):
        # "1.800,00 €" via scraper becomes "1.800.00" in the CSV
        self.assertAlmostEqual(clean_price("1.800.00"), 1800.0)

    def test_scraper_preprocessed_high_value(self):
        self.assertAlmostEqual(clean_price("2.299.99"), 2299.99)

    def test_scraper_preprocessed_1200(self):
        self.assertAlmostEqual(clean_price("1.200.00"), 1200.0)

    def test_plain_with_euro_sign(self):
        self.assertAlmostEqual(clean_price("599 €"), 599.0)

    def test_decimal_dot_format(self):
        self.assertAlmostEqual(clean_price("599.99"), 599.99)

    def test_na_returns_nan(self):
        import math
        self.assertTrue(math.isnan(clean_price("N/A")))

    def test_empty_returns_nan(self):
        import math
        self.assertTrue(math.isnan(clean_price("")))

    def test_comma_decimal_format(self):
        # "249,00" (Greek decimal comma, no thousands) → 249.0
        self.assertAlmostEqual(clean_price("249,00"), 249.0)

    def test_whitespace_stripped(self):
        self.assertAlmostEqual(clean_price("  249,00  "), 249.0)


# ─────────────────────────────────────────────────────────────────────────────
# 2. extract_ram_storage
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
# 3. Loader helpers: _val, _int, _float
# ─────────────────────────────────────────────────────────────────────────────

class TestLoaderHelpers(unittest.TestCase):

    def test_val_none(self):
        self.assertIsNone(_val({"x": None}, "x"))

    def test_val_nan(self):
        self.assertIsNone(_val({"x": float("nan")}, "x"))

    def test_val_present(self):
        self.assertEqual(_val({"x": "hello"}, "x"), "hello")

    def test_val_missing_key(self):
        self.assertIsNone(_val({}, "x"))

    def test_int_valid(self):
        self.assertEqual(_int({"x": "8"}, "x"), 8)

    def test_int_float_string_returns_none(self):
        # int("8.0") raises ValueError — _int only handles integer-format strings
        self.assertIsNone(_int({"x": "8.0"}, "x"))

    def test_int_non_numeric(self):
        self.assertIsNone(_int({"x": "N/A"}, "x"))

    def test_int_none(self):
        self.assertIsNone(_int({"x": None}, "x"))

    def test_float_valid(self):
        self.assertAlmostEqual(_float({"x": "299.99"}, "x"), 299.99)

    def test_float_none(self):
        self.assertIsNone(_float({"x": None}, "x"))

    def test_float_non_numeric(self):
        self.assertIsNone(_float({"x": "N/A"}, "x"))

    def test_int_negative_is_valid(self):
        # _int is a general coercion helper — negative ints are valid casts
        self.assertEqual(_int({"x": -5}, "x"), -5)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Notification deduplication
# ─────────────────────────────────────────────────────────────────────────────

class TestNotificationsDedup(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import notifications as n
        self._orig_base = n.BASE
        n.BASE = self.tmp.name
        os.makedirs(os.path.join(self.tmp.name, "logs"), exist_ok=True)

    def tearDown(self):
        import notifications as n
        n.BASE = self._orig_base
        self.tmp.cleanup()

    def test_not_sent_initially(self):
        import notifications as n
        self.assertFalse(n._already_sent("drops"))

    def test_mark_then_already_sent(self):
        import notifications as n
        n._mark_sent("drops")
        self.assertTrue(n._already_sent("drops"))

    def test_different_keys_independent(self):
        import notifications as n
        n._mark_sent("drops")
        self.assertFalse(n._already_sent("watchlist:https://example.com"))

    def test_multiple_marks_accumulate(self):
        import notifications as n
        n._mark_sent("drops")
        n._mark_sent("disappeared")
        self.assertTrue(n._already_sent("drops"))
        self.assertTrue(n._already_sent("disappeared"))

    def test_mark_sent_idempotent(self):
        import notifications as n
        n._mark_sent("drops")
        n._mark_sent("drops")
        self.assertTrue(n._already_sent("drops"))

    def test_tg_drops_skipped_when_already_sent(self):
        """tg_drops returns False without sending when already marked."""
        import notifications as n
        n._mark_sent("drops")
        # Pass fake rows with the expected attrs — function returns False immediately
        result = n.tg_drops([object()])
        self.assertFalse(result)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Watchlist atomic write / read
# ─────────────────────────────────────────────────────────────────────────────

class TestWatchlistAtomicWrite(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import telegram_bot as tb
        self._orig_path = tb._WL_PATH
        tb._WL_PATH = os.path.join(self.tmp.name, "watchlist.json")

    def tearDown(self):
        import telegram_bot as tb
        tb._WL_PATH = self._orig_path
        self.tmp.cleanup()

    def test_roundtrip(self):
        import telegram_bot as tb
        items = [{"url": "https://www.skroutz.gr/s/123/test.html", "label": "Test", "threshold_eur": 299.0}]
        tb._wl_write(items)
        result = tb._wl_read()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["label"], "Test")
        self.assertAlmostEqual(result[0]["threshold_eur"], 299.0)

    def test_empty_list(self):
        import telegram_bot as tb
        tb._wl_write([])
        self.assertEqual(tb._wl_read(), [])

    def test_no_tmp_file_after_write(self):
        import telegram_bot as tb
        tb._wl_write([{"url": "https://x.gr/s/1/a.html", "label": "X", "threshold_eur": 100.0}])
        self.assertFalse(os.path.exists(tb._WL_PATH + ".tmp"))

    def test_missing_file_returns_empty(self):
        import telegram_bot as tb
        self.assertEqual(tb._wl_read(), [])

    def test_unicode_roundtrip(self):
        import telegram_bot as tb
        items = [{"url": "https://x.gr/s/1/a.html", "label": "Κινητό Τηλέφωνο", "threshold_eur": 250.0}]
        tb._wl_write(items)
        self.assertEqual(tb._wl_read()[0]["label"], "Κινητό Τηλέφωνο")

    def test_url_dedup_update(self):
        import telegram_bot as tb
        url = "https://www.skroutz.gr/s/123/test.html"
        tb._wl_write([{"url": url, "label": "Test", "threshold_eur": 299.0}])
        result = tb._do_add(url, 250.0)
        self.assertIn("Updated", result)
        items = tb._wl_read()
        self.assertEqual(len(items), 1)
        self.assertAlmostEqual(items[0]["threshold_eur"], 250.0)

    def test_multiple_writes_are_independent(self):
        import telegram_bot as tb
        tb._wl_write([{"url": "https://a.gr/s/1/x.html", "label": "A", "threshold_eur": 100.0}])
        tb._wl_write([{"url": "https://b.gr/s/2/y.html", "label": "B", "threshold_eur": 200.0}])
        result = tb._wl_read()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["label"], "B")


# ─────────────────────────────────────────────────────────────────────────────
# 6. N/A link guard
# ─────────────────────────────────────────────────────────────────────────────

class TestNALinkGuard(unittest.TestCase):

    def _is_invalid(self, link):
        return not link or str(link).upper() == "N/A"

    def test_uppercase_na(self):
        self.assertTrue(self._is_invalid("N/A"))

    def test_lowercase_na(self):
        self.assertTrue(self._is_invalid("n/a"))

    def test_empty_string(self):
        self.assertTrue(self._is_invalid(""))

    def test_none(self):
        self.assertTrue(self._is_invalid(None))

    def test_valid_url(self):
        self.assertFalse(self._is_invalid("https://www.skroutz.gr/s/12345/Product.html"))

    def test_partial_na_not_caught(self):
        # "N/A phone" is not literally N/A
        self.assertFalse(self._is_invalid("N/A phone"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

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
  - Pipeline concurrency lock (_acquire_lock / _release_lock)
  - tg_send transport (no-token guard, HTTP status handling, retry)
  - /add and /remove argument parsing

Run:
  python -m pytest tests/ -v

No network or DB connections required — all tests are pure-unit.
"""

import json
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import importlib as _imp
from Data_Phone import extract_ram_storage

_csvs  = _imp.import_module('4csvsTOsql')
_val   = _csvs._val
_int   = _csvs._int
_float = _csvs._float


# ─────────────────────────────────────────────────────────────────────────────
# 1. clean_price
# ─────────────────────────────────────────────────────────────────────────────

# All four cleaner entry points re-export clean_price from clean_common; every
# case runs against each module so a broken re-export fails the suite.
CLEANER_MODULES = ("Data_Phone", "Data_Laptops", "Data_Tablets", "Data_Smartwatches")


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

    def test_corrupt_dedup_file_treated_as_empty(self):
        """A truncated/invalid JSON file must not make _already_sent crash or
        permanently return True/False for everything — it resets to empty."""
        import notifications as n
        with open(n._sent_file(), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        self.assertFalse(n._already_sent("drops"))

    def test_mark_sent_recovers_from_corrupt_file(self):
        """_mark_sent must still succeed (and self-heal the file) even when
        the existing dedup file on disk is corrupt."""
        import notifications as n
        with open(n._sent_file(), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        n._mark_sent("drops")
        self.assertTrue(n._already_sent("drops"))
        with open(n._sent_file(), encoding="utf-8") as f:
            json.load(f)  # must be valid JSON now

    def test_mark_sent_write_is_atomic_no_tmp_left_behind(self):
        import notifications as n
        n._mark_sent("drops")
        self.assertFalse(os.path.exists(n._sent_file() + ".tmp"))


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


# ─────────────────────────────────────────────────────────────────────────────
# 7. load_category (Stage 3 core function)
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadCategory(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _csv(self, content: str, name: str = "clean.csv") -> str:
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_skip_missing_file(self):
        conn = MagicMock()
        _csvs.load_category(conn, "phone", os.path.join(self.tmp.name, "nope.csv"))
        conn.execute.assert_not_called()

    def test_skip_all_na_links(self):
        path = self._csv("link,price_eur,product,brand,model\nN/A,299,Test,Samsung,X\n")
        conn = MagicMock()
        _csvs.load_category(conn, "phone", path)
        conn.execute.assert_not_called()

    def test_skip_empty_csv(self):
        path = self._csv("link,price_eur,product,brand,model\n")
        conn = MagicMock()
        _csvs.load_category(conn, "phone", path)
        conn.execute.assert_not_called()

    def test_executes_two_times_for_valid_row(self):
        url  = "https://www.skroutz.gr/s/1/test.html"
        path = self._csv(
            f"link,price_eur,product,brand,model\n{url},299.00,Samsung Galaxy X,Samsung,Galaxy X\n"
        )
        fake_row               = MagicMock()
        fake_row.skroutz_link  = url
        fake_row.id            = 42
        fake_row.is_new        = True

        conn = MagicMock()
        # calls: upsert products (RETURNING id/skroutz_link/is_new), insert snapshots
        conn.execute.side_effect = [[fake_row], MagicMock()]

        _csvs.load_category(conn, "phone", path)
        self.assertEqual(conn.execute.call_count, 2)

    def test_snapshot_not_inserted_when_id_missing(self):
        # If the upsert RETURNING set is empty, snapshot_rows is empty → no 2nd call
        url  = "https://www.skroutz.gr/s/2/other.html"
        path = self._csv(
            f"link,price_eur,product,brand,model\n{url},199.00,Xiaomi X,Xiaomi,X\n"
        )
        conn = MagicMock()
        conn.execute.side_effect = [[]]  # empty RETURNING set → empty id_map

        _csvs.load_category(conn, "phone", path)
        # 1 call: upsert only — no snapshot insert
        self.assertEqual(conn.execute.call_count, 1)


# ─────────────────────────────────────────────────────────────────────────────
# 8. _cmd_find parsing (no-DB paths only)
# ─────────────────────────────────────────────────────────────────────────────

class TestCmdFind(unittest.TestCase):
    from unittest.mock import patch, MagicMock

    def test_empty_args_returns_usage(self):
        import telegram_bot as tb
        result = tb._cmd_find("")
        self.assertIn("Usage:", result)

    def test_whitespace_args_returns_usage(self):
        import telegram_bot as tb
        result = tb._cmd_find("   ")
        self.assertIn("Usage:", result)

    def test_db_error_returns_error_string(self):
        from unittest.mock import patch
        import telegram_bot as tb
        with patch("telegram_bot.get_engine", side_effect=Exception("connection refused")):
            result = tb._cmd_find("galaxy s25")
        self.assertIn("❌", result)

    def test_no_results_message(self):
        from unittest.mock import patch, MagicMock
        import telegram_bot as tb
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_engine.connect.return_value.__exit__.return_value = False
        mock_conn.execute.return_value.fetchall.return_value = []
        with patch("telegram_bot.get_engine", return_value=mock_engine):
            result = tb._cmd_find("nonexistent xyz abc")
        self.assertIn("No products found", result)

    def test_results_contain_brand_and_price(self):
        from unittest.mock import patch, MagicMock
        import telegram_bot as tb
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_engine.connect.return_value.__exit__.return_value = False
        row = MagicMock()
        row.brand = "Samsung"
        row.model = "Galaxy S25"
        row.category = "phone"
        row.price_eur = 799.0
        row.all_time_low = 750.0
        row.pct_above_atl = 6.5
        mock_conn.execute.return_value.fetchall.return_value = [row]
        with patch("telegram_bot.get_engine", return_value=mock_engine):
            result = tb._cmd_find("galaxy s25")
        self.assertIn("Samsung", result)
        self.assertIn("799€", result)


# ─────────────────────────────────────────────────────────────────────────────
# 9. _cmd_history parsing (no-DB paths only)
# ─────────────────────────────────────────────────────────────────────────────

class TestCmdHistory(unittest.TestCase):

    def test_empty_args_returns_usage(self):
        import telegram_bot as tb
        result = tb._cmd_history("")
        self.assertIn("Usage:", result)

    def test_whitespace_args_returns_usage(self):
        import telegram_bot as tb
        result = tb._cmd_history("   ")
        self.assertIn("Usage:", result)

    def test_db_error_returns_error_string(self):
        from unittest.mock import patch
        import telegram_bot as tb
        with patch("telegram_bot.get_engine", side_effect=Exception("timeout")):
            result = tb._cmd_history("iphone 16")
        self.assertIn("❌", result)

    def test_no_product_found(self):
        from unittest.mock import patch, MagicMock
        import telegram_bot as tb
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_engine.connect.return_value.__exit__.return_value = False
        mock_conn.execute.return_value.fetchone.return_value = None
        with patch("telegram_bot.get_engine", return_value=mock_engine):
            result = tb._cmd_history("nonexistent xyz abc")
        self.assertIn("No product found", result)


# ─────────────────────────────────────────────────────────────────────────────
# 10. send_drop_digest
# ─────────────────────────────────────────────────────────────────────────────

class TestSendDropDigest(unittest.TestCase):

    def _mock_engine(self, rows):
        mock_engine = MagicMock()
        mock_conn   = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_engine.connect.return_value.__exit__.return_value  = False
        mock_conn.execute.return_value.fetchall.return_value    = rows
        return mock_engine

    def test_no_credentials_returns_immediately(self):
        import run_pipeline
        with patch("run_pipeline.GMAIL_APP_PASSWORD", ""), \
             patch.object(run_pipeline._notif, "_TOKEN", ""), \
             patch("run_pipeline.get_engine") as mock_ge:
            run_pipeline.send_drop_digest()
        mock_ge.assert_not_called()

    def test_no_drops_skips_smtp(self):
        import run_pipeline
        with patch("run_pipeline.GMAIL_APP_PASSWORD", "test-pw"), \
             patch("run_pipeline.get_engine", return_value=self._mock_engine([])), \
             patch("smtplib.SMTP") as mock_smtp:
            run_pipeline.send_drop_digest()
        mock_smtp.assert_not_called()

    def test_drops_present_calls_tg_drops(self):
        import run_pipeline
        row = MagicMock()
        row.brand = "Samsung"
        row.model = "S25"
        row.category = "phone"
        row.prev_price = 999.0
        row.new_price = 849.0
        row.drop_eur = -150.0
        row.drop_pct = -15.0
        with patch("run_pipeline.GMAIL_APP_PASSWORD", "test-pw"), \
             patch("run_pipeline.get_engine", return_value=self._mock_engine([row])), \
             patch.object(run_pipeline._notif, "tg_drops") as mock_tg, \
             patch("smtplib.SMTP"):
            run_pipeline.send_drop_digest()
        mock_tg.assert_called_once_with([row])

    def test_drops_present_calls_smtp(self):
        import run_pipeline
        row = MagicMock()
        row.brand = "Apple"
        row.model = "iPhone 16"
        row.category = "phone"
        row.prev_price = 1099.0
        row.new_price = 899.0
        row.drop_eur = -200.0
        row.drop_pct = -18.2
        with patch("run_pipeline.GMAIL_APP_PASSWORD", "test-pw"), \
             patch("run_pipeline.get_engine", return_value=self._mock_engine([row])), \
             patch.object(run_pipeline._notif, "tg_drops"), \
             patch("smtplib.SMTP") as mock_smtp:
            run_pipeline.send_drop_digest()
        mock_smtp.assert_called_once()

    def test_db_error_skips_smtp(self):
        import run_pipeline
        with patch("run_pipeline.GMAIL_APP_PASSWORD", "test-pw"), \
             patch("run_pipeline.get_engine", side_effect=Exception("DB down")), \
             patch("smtplib.SMTP") as mock_smtp:
            run_pipeline.send_drop_digest()
        mock_smtp.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 11. send_success_summary
# ─────────────────────────────────────────────────────────────────────────────

class TestSendSuccessSummary(unittest.TestCase):

    def _mock_engine(self, snaps, new_prods, drops, yesterday_snaps):
        def _scalar(val):
            r = MagicMock()
            r.scalar.return_value = val
            return r

        mock_engine = MagicMock()
        mock_conn   = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_engine.connect.return_value.__exit__.return_value  = False
        mock_conn.execute.side_effect = [
            _scalar(snaps), _scalar(new_prods), _scalar(drops), _scalar(yesterday_snaps),
        ]
        return mock_engine

    def test_no_credentials_returns_immediately(self):
        import run_pipeline
        with patch("run_pipeline.GMAIL_APP_PASSWORD", ""), \
             patch.object(run_pipeline._notif, "_TOKEN", ""), \
             patch("run_pipeline.get_engine") as mock_ge:
            run_pipeline.send_success_summary("0:01:30")
        mock_ge.assert_not_called()

    def test_normal_run_calls_tg_success(self):
        import run_pipeline
        with patch("run_pipeline.GMAIL_APP_PASSWORD", "test-pw"), \
             patch("run_pipeline.get_engine", return_value=self._mock_engine(7000, 50, 12, 6800)), \
             patch.object(run_pipeline._notif, "tg_success") as mock_tg, \
             patch("smtplib.SMTP"):
            run_pipeline.send_success_summary("0:01:30")
        mock_tg.assert_called_once()

    def test_email_subject_contains_ok(self):
        import run_pipeline
        subjects = []

        class _SMTP:
            def __init__(self, *a, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def starttls(self): pass
            def login(self, *a): pass
            def send_message(self, msg): subjects.append(msg["Subject"])

        with patch("run_pipeline.GMAIL_APP_PASSWORD", "test-pw"), \
             patch("run_pipeline.get_engine", return_value=self._mock_engine(7000, 50, 12, 6800)), \
             patch.object(run_pipeline._notif, "tg_success"), \
             patch("smtplib.SMTP", _SMTP):
            run_pipeline.send_success_summary("0:01:30")
        self.assertTrue(any("OK" in s for s in subjects))

    def test_anomaly_fires_tg_send(self):
        """snaps < 70% of yesterday triggers Telegram anomaly alert."""
        import run_pipeline
        with patch("run_pipeline.GMAIL_APP_PASSWORD", "test-pw"), \
             patch("run_pipeline.get_engine", return_value=self._mock_engine(3000, 10, 5, 5000)), \
             patch.object(run_pipeline._notif, "tg_send") as mock_tg_send, \
             patch.object(run_pipeline._notif, "tg_success"), \
             patch("smtplib.SMTP"):
            run_pipeline.send_success_summary("0:01:00")
        mock_tg_send.assert_called_once()
        self.assertIn("anomaly", mock_tg_send.call_args[0][0].lower())

    def test_no_anomaly_at_threshold(self):
        """snaps exactly = 70% of yesterday (boundary) does NOT trigger anomaly."""
        import run_pipeline
        with patch("run_pipeline.GMAIL_APP_PASSWORD", "test-pw"), \
             patch("run_pipeline.get_engine", return_value=self._mock_engine(3500, 10, 5, 5000)), \
             patch.object(run_pipeline._notif, "tg_send") as mock_tg_send, \
             patch.object(run_pipeline._notif, "tg_success"), \
             patch("smtplib.SMTP"):
            run_pipeline.send_success_summary("0:01:00")
        mock_tg_send.assert_not_called()

    def test_no_anomaly_when_snaps_sufficient(self):
        import run_pipeline
        with patch("run_pipeline.GMAIL_APP_PASSWORD", "test-pw"), \
             patch("run_pipeline.get_engine", return_value=self._mock_engine(4900, 10, 5, 5000)), \
             patch.object(run_pipeline._notif, "tg_send") as mock_tg_send, \
             patch.object(run_pipeline._notif, "tg_success"), \
             patch("smtplib.SMTP"):
            run_pipeline.send_success_summary("0:01:00")
        mock_tg_send.assert_not_called()

    def test_missing_gmail_password_skips_smtp(self):
        """With _TOKEN set but no GMAIL_APP_PASSWORD, _send_html_email returns early."""
        import run_pipeline
        with patch("run_pipeline.GMAIL_APP_PASSWORD", ""), \
             patch.object(run_pipeline._notif, "_TOKEN", "real-token"), \
             patch("run_pipeline.get_engine", return_value=self._mock_engine(7000, 50, 12, 6800)), \
             patch.object(run_pipeline._notif, "tg_success"), \
             patch("smtplib.SMTP") as mock_smtp:
            run_pipeline.send_success_summary("0:01:30")
        mock_smtp.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 11. Pipeline concurrency lock
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineLock(unittest.TestCase):

    def setUp(self):
        import run_pipeline
        self.rp  = run_pipeline
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = self.rp._LOCK_FILE
        self.rp._LOCK_FILE = os.path.join(self.tmp.name, "pipeline.lock")

    def tearDown(self):
        self.rp._LOCK_FILE = self._orig
        self.tmp.cleanup()

    def test_acquire_when_free(self):
        self.assertTrue(self.rp._acquire_lock())
        self.assertTrue(os.path.exists(self.rp._LOCK_FILE))

    def test_second_acquire_blocked(self):
        self.assertTrue(self.rp._acquire_lock())
        self.assertFalse(self.rp._acquire_lock())

    def test_release_then_acquire(self):
        self.rp._acquire_lock()
        self.rp._release_lock()
        self.assertTrue(self.rp._acquire_lock())

    def test_stale_lock_reclaimed(self):
        self.rp._acquire_lock()
        stale = time.time() - self.rp._LOCK_STALE_SECONDS - 60
        os.utime(self.rp._LOCK_FILE, (stale, stale))
        self.assertTrue(self.rp._acquire_lock())

    def test_release_missing_lock_no_error(self):
        self.rp._release_lock()

    def test_lock_contains_pid(self):
        self.rp._acquire_lock()
        with open(self.rp._LOCK_FILE) as f:
            self.assertEqual(f.read(), str(os.getpid()))


# ─────────────────────────────────────────────────────────────────────────────
# 12. tg_send transport (no-token guard, HTTP status, retry)
# ─────────────────────────────────────────────────────────────────────────────

class TestTgSend(unittest.TestCase):

    def _resp_cm(self, status):
        cm = MagicMock()
        cm.__enter__.return_value = MagicMock(status=status)
        cm.__exit__.return_value = False
        return cm

    def test_no_token_returns_false_without_network(self):
        import notifications as nf
        with patch.object(nf, "_TOKEN", ""), \
             patch("urllib.request.urlopen") as mock_open:
            self.assertFalse(nf.tg_send("hello"))
        mock_open.assert_not_called()

    def test_http_200_returns_true(self):
        import notifications as nf
        with patch.object(nf, "_TOKEN", "t"), patch.object(nf, "_CHAT_ID", "c"), \
             patch("urllib.request.urlopen", return_value=self._resp_cm(200)):
            self.assertTrue(nf.tg_send("hello"))

    def test_http_error_status_returns_false_no_retry(self):
        import notifications as nf
        with patch.object(nf, "_TOKEN", "t"), patch.object(nf, "_CHAT_ID", "c"), \
             patch("urllib.request.urlopen", return_value=self._resp_cm(500)) as mock_open:
            self.assertFalse(nf.tg_send("hello"))
        self.assertEqual(mock_open.call_count, 1)

    def test_transient_error_retries_once_then_succeeds(self):
        import notifications as nf
        import urllib.error
        with patch.object(nf, "_TOKEN", "t"), patch.object(nf, "_CHAT_ID", "c"), \
             patch("urllib.request.urlopen",
                   side_effect=[urllib.error.URLError("boom"), self._resp_cm(200)]) as mock_open, \
             patch("time.sleep"):
            self.assertTrue(nf.tg_send("hello"))
        self.assertEqual(mock_open.call_count, 2)

    def test_both_attempts_fail_returns_false(self):
        import notifications as nf
        import urllib.error
        with patch.object(nf, "_TOKEN", "t"), patch.object(nf, "_CHAT_ID", "c"), \
             patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("boom")) as mock_open, \
             patch("time.sleep"):
            self.assertFalse(nf.tg_send("hello"))
        self.assertEqual(mock_open.call_count, 2)


# ─────────────────────────────────────────────────────────────────────────────
# 13. /add and /remove argument parsing
# ─────────────────────────────────────────────────────────────────────────────

class TestCmdAddRemove(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import telegram_bot as tb
        self.tb = tb
        self._orig_path = tb._WL_PATH
        tb._WL_PATH = os.path.join(self.tmp.name, "watchlist.json")

    def tearDown(self):
        self.tb._WL_PATH = self._orig_path
        self.tmp.cleanup()

    def test_add_no_args_returns_usage(self):
        self.assertIn("Usage", self.tb._cmd_add(""))

    def test_add_missing_price_returns_usage(self):
        self.assertIn("Usage", self.tb._cmd_add("https://www.skroutz.gr/s/1/a.html"))

    def test_add_non_skroutz_url_rejected(self):
        self.assertIn("skroutz.gr", self.tb._cmd_add("https://example.com/x 100"))
        self.assertEqual(self.tb._wl_read(), [])

    def test_add_invalid_price_rejected(self):
        self.assertIn("Invalid price", self.tb._cmd_add("https://www.skroutz.gr/s/1/a.html abc"))
        self.assertEqual(self.tb._wl_read(), [])

    def test_add_comma_decimal_and_euro_sign_parsed(self):
        self.tb._cmd_add("https://www.skroutz.gr/s/1/a.html 299,50€")
        items = self.tb._wl_read()
        self.assertEqual(len(items), 1)
        self.assertAlmostEqual(items[0]["threshold_eur"], 299.5)

    def test_remove_non_numeric_returns_usage(self):
        self.assertIn("Usage", self.tb._cmd_remove("abc"))

    def test_remove_from_empty_watchlist(self):
        self.assertIn("empty", self.tb._cmd_remove("1"))

    def test_remove_out_of_range(self):
        self.tb._wl_write([{"url": "https://a.gr/s/1/x.html", "label": "A", "threshold_eur": 100.0}])
        self.assertIn("No item #5", self.tb._cmd_remove("5"))
        self.assertEqual(len(self.tb._wl_read()), 1)

    def test_remove_valid_item(self):
        self.tb._wl_write([{"url": "https://a.gr/s/1/x.html", "label": "A", "threshold_eur": 100.0}])
        result = self.tb._cmd_remove("1")
        self.assertIn("Removed", result)
        self.assertEqual(self.tb._wl_read(), [])


# ─────────────────────────────────────────────────────────────────────────────
# 14. update_readme_stats
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateReadmeStats(unittest.TestCase):

    README_TEMPLATE = (
        "# Title\n\n"
        "<!-- STATS:BADGES:START -->\n"
        "![Products](old-products-badge)\n"
        "![Snapshots](old-snapshots-badge)\n"
        "<!-- STATS:BADGES:END -->\n"
        "![Other](untouched-badge)\n\n"
        "<!-- STATS:TABLE:START -->\n"
        "old table content\n"
        "<!-- STATS:TABLE:END -->\n\n"
        "## Untouched section\n"
    )

    def _mock_engine(self, rows):
        mock_engine = MagicMock()
        mock_conn   = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_engine.connect.return_value.__exit__.return_value  = False

        def _scalar(val):
            r = MagicMock()
            r.scalar.return_value = val
            return r

        def _fetchall(rows):
            r = MagicMock()
            r.fetchall.return_value = rows
            return r

        total_products  = sum(r.products for r in rows)
        total_snapshots = sum(r.snapshots for r in rows)
        mock_conn.execute.side_effect = [
            _scalar(total_products), _scalar(total_snapshots), _fetchall(rows),
        ]
        return mock_engine

    def _row(self, category, products, snapshots, avg_price, min_price, max_price, brands):
        r = MagicMock()
        r.category, r.products, r.snapshots = category, products, snapshots
        r.avg_price, r.min_price, r.max_price, r.brands = avg_price, min_price, max_price, brands
        return r

    def _run_with_tempdir(self, rows):
        import run_pipeline
        with tempfile.TemporaryDirectory() as tmp:
            readme_path = os.path.join(tmp, "README.md")
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(self.README_TEMPLATE)
            with patch("run_pipeline.BASE", tmp), \
                 patch("run_pipeline.get_engine", return_value=self._mock_engine(rows)):
                run_pipeline.update_readme_stats()
            with open(readme_path, "r", encoding="utf-8") as f:
                return f.read()

    def test_rewrites_badges_and_table(self):
        rows = [self._row("laptop", 100, 2000, 500, 50, 5000, 10)]
        content = self._run_with_tempdir(rows)
        self.assertIn("Products-100-blue", content)
        self.assertIn("Snapshots-2k-green", content)
        self.assertIn("| Laptop | 100 | 2,000 | €500 | €50–€5,000 | 10 |", content)
        self.assertIn("| **Total** | **100** | **2,000** |", content)

    def test_leaves_surrounding_content_untouched(self):
        rows = [self._row("laptop", 100, 2000, 500, 50, 5000, 10)]
        content = self._run_with_tempdir(rows)
        self.assertIn("![Other](untouched-badge)", content)
        self.assertIn("## Untouched section", content)
        self.assertNotIn("old-products-badge", content)
        self.assertNotIn("old table content", content)

    def test_smartwatch_brand_count_omitted(self):
        rows = [self._row("smartwatch", 50, 900, 100, 5, 300, 999)]
        content = self._run_with_tempdir(rows)
        self.assertIn("| Smartwatch | 50 | 900 | €100 | €5–€300 | — |", content)
        self.assertNotIn("999", content)

    def test_db_error_does_not_touch_readme(self):
        import run_pipeline
        with tempfile.TemporaryDirectory() as tmp:
            readme_path = os.path.join(tmp, "README.md")
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(self.README_TEMPLATE)
            broken_engine = MagicMock()
            broken_engine.connect.side_effect = Exception("db down")
            with patch("run_pipeline.BASE", tmp), \
                 patch("run_pipeline.get_engine", return_value=broken_engine):
                run_pipeline.update_readme_stats()
            with open(readme_path, "r", encoding="utf-8") as f:
                content = f.read()
        self.assertEqual(content, self.README_TEMPLATE)


# ─────────────────────────────────────────────────────────────────────────────
# clean_reviews — recovery of malformed review counts
# ─────────────────────────────────────────────────────────────────────────────

class TestCleanReviews(unittest.TestCase):

    def _clean(self, values):
        import pandas as pd
        from clean_common import clean_reviews
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
        from scraper_core import parse_card
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

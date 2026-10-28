"""
tests/test_telegram_cmds.py
Telegram bot: watchlist storage and /find /history /add /remove /analyze.
Split from the former tests/test_{clean,load,scrape,alerts,telegram_cmds,ops}.py. No network or DB.
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch



# ── Watchlist atomic write / read
# ─────────────────────────────────────────────────────────────────────────────

class TestWatchlistAtomicWrite(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import alerts.telegram_bot as tb
        self._orig_path = tb._WL_PATH
        tb._WL_PATH = os.path.join(self.tmp.name, "watchlist.json")

    def tearDown(self):
        import alerts.telegram_bot as tb
        tb._WL_PATH = self._orig_path
        self.tmp.cleanup()

    def test_roundtrip(self):
        import alerts.telegram_bot as tb
        items = [{"url": "https://www.skroutz.gr/s/123/test.html", "label": "Test", "threshold_eur": 299.0}]
        tb._wl_write(items)
        result = tb._wl_read()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["label"], "Test")
        self.assertAlmostEqual(result[0]["threshold_eur"], 299.0)

    def test_empty_list(self):
        import alerts.telegram_bot as tb
        tb._wl_write([])
        self.assertEqual(tb._wl_read(), [])

    def test_no_tmp_file_after_write(self):
        import alerts.telegram_bot as tb
        tb._wl_write([{"url": "https://x.gr/s/1/a.html", "label": "X", "threshold_eur": 100.0}])
        self.assertFalse(os.path.exists(tb._WL_PATH + ".tmp"))

    def test_missing_file_returns_empty(self):
        import alerts.telegram_bot as tb
        self.assertEqual(tb._wl_read(), [])

    def test_unicode_roundtrip(self):
        import alerts.telegram_bot as tb
        items = [{"url": "https://x.gr/s/1/a.html", "label": "Κινητό Τηλέφωνο", "threshold_eur": 250.0}]
        tb._wl_write(items)
        self.assertEqual(tb._wl_read()[0]["label"], "Κινητό Τηλέφωνο")

    def test_url_dedup_update(self):
        import alerts.telegram_bot as tb
        url = "https://www.skroutz.gr/s/123/test.html"
        tb._wl_write([{"url": url, "label": "Test", "threshold_eur": 299.0}])
        result = tb._do_add(url, 250.0)
        self.assertIn("Updated", result)
        items = tb._wl_read()
        self.assertEqual(len(items), 1)
        self.assertAlmostEqual(items[0]["threshold_eur"], 250.0)

    def test_multiple_writes_are_independent(self):
        import alerts.telegram_bot as tb
        tb._wl_write([{"url": "https://a.gr/s/1/x.html", "label": "A", "threshold_eur": 100.0}])
        tb._wl_write([{"url": "https://b.gr/s/2/y.html", "label": "B", "threshold_eur": 200.0}])
        result = tb._wl_read()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["label"], "B")


# ── _cmd_find parsing (no-DB paths only)
# ─────────────────────────────────────────────────────────────────────────────

class TestCmdFind(unittest.TestCase):
    from unittest.mock import patch, MagicMock

    def test_empty_args_returns_usage(self):
        import alerts.telegram_bot as tb
        result = tb._cmd_find("")
        self.assertIn("Usage:", result)

    def test_whitespace_args_returns_usage(self):
        import alerts.telegram_bot as tb
        result = tb._cmd_find("   ")
        self.assertIn("Usage:", result)

    def test_db_error_returns_error_string(self):
        from unittest.mock import patch
        import alerts.telegram_bot as tb
        with patch("alerts.telegram_bot.get_engine", side_effect=Exception("connection refused")):
            result = tb._cmd_find("galaxy s25")
        self.assertIn("Backend unavailable", result)
        self.assertNotIn("connection refused", result)

    def test_no_results_message(self):
        from unittest.mock import patch
        import alerts.telegram_bot as tb
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_engine.connect.return_value.__exit__.return_value = False
        mock_conn.execute.return_value.fetchall.return_value = []
        with patch("alerts.telegram_bot.get_engine", return_value=mock_engine):
            result = tb._cmd_find("nonexistent xyz abc")
        self.assertIn("No products found", result)

    def test_results_contain_brand_and_price(self):
        from unittest.mock import patch
        import alerts.telegram_bot as tb
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
        with patch("alerts.telegram_bot.get_engine", return_value=mock_engine):
            result = tb._cmd_find("galaxy s25")
        self.assertIn("Samsung", result)
        self.assertIn("799€", result)


# ── _cmd_history parsing (no-DB paths only)
# ─────────────────────────────────────────────────────────────────────────────

class TestCmdHistory(unittest.TestCase):

    def test_empty_args_returns_usage(self):
        import alerts.telegram_bot as tb
        result = tb._cmd_history("")
        self.assertIn("Usage:", result)

    def test_whitespace_args_returns_usage(self):
        import alerts.telegram_bot as tb
        result = tb._cmd_history("   ")
        self.assertIn("Usage:", result)

    def test_db_error_returns_error_string(self):
        from unittest.mock import patch
        import alerts.telegram_bot as tb
        with patch("alerts.telegram_bot.get_engine", side_effect=Exception("timeout")):
            result = tb._cmd_history("iphone 16")
        self.assertIn("Backend unavailable", result)
        self.assertNotIn("timeout", result)

    def test_no_product_found(self):
        from unittest.mock import patch
        import alerts.telegram_bot as tb
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_engine.connect.return_value.__exit__.return_value = False
        mock_conn.execute.return_value.fetchone.return_value = None
        with patch("alerts.telegram_bot.get_engine", return_value=mock_engine):
            result = tb._cmd_history("nonexistent xyz abc")
        self.assertIn("No product found", result)


# ── /add and /remove argument parsing
# ─────────────────────────────────────────────────────────────────────────────

class TestCmdAddRemove(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import alerts.telegram_bot as tb
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


class TestCmdAnalyze(unittest.TestCase):

    def _run(self, arg):
        import alerts.telegram_nim as tn
        with patch.object(tn, "_analyze_backend", return_value=("EXEC", "LLM")), \
             patch("agents.ask.analyze.analyze", return_value="ok") as call:
            return tn.cmd_analyze(arg), call

    def test_smartwatches_does_not_strip_to_smartwatche(self):
        out, call = self._run("smartwatches")
        self.assertEqual(out, "ok")
        call.assert_called_once_with("EXEC", "LLM", "smartwatch")

    def test_phones_and_tablet_aliases(self):
        self.assertEqual(self._run("phones")[1].call_args.args[2], "phone")
        self.assertEqual(self._run("tablet")[1].call_args.args[2], "tablet")

    def test_unknown_category_returns_usage(self):
        out, call = self._run("cameras")
        self.assertIn("Usage", out)
        call.assert_not_called()

    def test_backend_failure_is_chat_safe(self):
        import alerts.telegram_nim as tn
        with patch.object(tn, "_analyze_backend", side_effect=RuntimeError("db down pw=x")):
            out = tn.cmd_analyze("phones")
        self.assertIn("unavailable", out)
        self.assertNotIn("pw=x", out)


class TestChatIsAskAlias(unittest.TestCase):

    def test_chat_routes_to_ask(self):
        import alerts.telegram_nim as tn
        with patch.object(tn, "cmd_ask", return_value="grounded") as ask:
            self.assertEqual(tn.cmd_chat("φθηνότερο iPhone;"), "grounded")
        ask.assert_called_once_with("φθηνότερο iPhone;")

    def test_dispatch_has_no_ungrounded_commands(self):
        import alerts.telegram_nim as tn
        m = {}
        tn.register_nim_commands(m, "x")
        self.assertEqual(set(m), {"/ask", "/chat", "/analyze"})
        for name in ("nim_chat", "nim_daily_summary", "nim_analyze_category", "cmd_summarize"):
            self.assertFalse(hasattr(tn, name), name)

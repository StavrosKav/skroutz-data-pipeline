"""
tests/test_alerts.py
Alerts and reporting side effects: dedup, digests, summaries, tg_send, README stats.
Split from the former tests/test_{clean,load,scrape,alerts,telegram_cmds,ops}.py. No network or DB.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch



# ── Notification deduplication
# ─────────────────────────────────────────────────────────────────────────────

class TestNotificationsDedup(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import alerts.notifications as n
        self._orig_base = n.BASE
        n.BASE = self.tmp.name
        os.makedirs(os.path.join(self.tmp.name, "logs"), exist_ok=True)

    def tearDown(self):
        import alerts.notifications as n
        n.BASE = self._orig_base
        self.tmp.cleanup()

    def test_not_sent_initially(self):
        import alerts.notifications as n
        self.assertFalse(n._already_sent("drops"))

    def test_mark_then_already_sent(self):
        import alerts.notifications as n
        n._mark_sent("drops")
        self.assertTrue(n._already_sent("drops"))

    def test_different_keys_independent(self):
        import alerts.notifications as n
        n._mark_sent("drops")
        self.assertFalse(n._already_sent("watchlist:https://example.com"))

    def test_multiple_marks_accumulate(self):
        import alerts.notifications as n
        n._mark_sent("drops")
        n._mark_sent("disappeared")
        self.assertTrue(n._already_sent("drops"))
        self.assertTrue(n._already_sent("disappeared"))

    def test_mark_sent_idempotent(self):
        import alerts.notifications as n
        n._mark_sent("drops")
        n._mark_sent("drops")
        self.assertTrue(n._already_sent("drops"))

    def test_tg_drops_skipped_when_already_sent(self):
        """tg_drops returns False without sending when already marked."""
        import alerts.notifications as n
        n._mark_sent("drops")
        # Pass fake rows with the expected attrs — function returns False immediately
        result = n.tg_drops([object()])
        self.assertFalse(result)

    def test_corrupt_dedup_file_treated_as_empty(self):
        """A truncated/invalid JSON file must not make _already_sent crash or
        permanently return True/False for everything — it resets to empty."""
        import alerts.notifications as n
        with open(n._sent_file(), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        self.assertFalse(n._already_sent("drops"))

    def test_mark_sent_recovers_from_corrupt_file(self):
        """_mark_sent must still succeed (and self-heal the file) even when
        the existing dedup file on disk is corrupt."""
        import alerts.notifications as n
        with open(n._sent_file(), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        n._mark_sent("drops")
        self.assertTrue(n._already_sent("drops"))
        with open(n._sent_file(), encoding="utf-8") as f:
            json.load(f)  # must be valid JSON now

    def test_mark_sent_write_is_atomic_no_tmp_left_behind(self):
        import alerts.notifications as n
        n._mark_sent("drops")
        self.assertFalse(os.path.exists(n._sent_file() + ".tmp"))


# ── send_drop_digest
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


# ── send_success_summary
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


# ── tg_send transport (no-token guard, HTTP status, retry)
# ─────────────────────────────────────────────────────────────────────────────

class TestTgSend(unittest.TestCase):

    def _resp_cm(self, status):
        cm = MagicMock()
        cm.__enter__.return_value = MagicMock(status=status)
        cm.__exit__.return_value = False
        return cm

    def test_no_token_returns_false_without_network(self):
        import alerts.notifications as nf
        with patch.object(nf, "_TOKEN", ""), \
             patch("urllib.request.urlopen") as mock_open:
            self.assertFalse(nf.tg_send("hello"))
        mock_open.assert_not_called()

    def test_http_200_returns_true(self):
        import alerts.notifications as nf
        with patch.object(nf, "_TOKEN", "t"), patch.object(nf, "_CHAT_ID", "c"), \
             patch("urllib.request.urlopen", return_value=self._resp_cm(200)):
            self.assertTrue(nf.tg_send("hello"))

    def test_http_error_status_returns_false_no_retry(self):
        import alerts.notifications as nf
        with patch.object(nf, "_TOKEN", "t"), patch.object(nf, "_CHAT_ID", "c"), \
             patch("urllib.request.urlopen", return_value=self._resp_cm(500)) as mock_open:
            self.assertFalse(nf.tg_send("hello"))
        self.assertEqual(mock_open.call_count, 1)

    def test_transient_error_retries_once_then_succeeds(self):
        import alerts.notifications as nf
        import urllib.error
        with patch.object(nf, "_TOKEN", "t"), patch.object(nf, "_CHAT_ID", "c"), \
             patch("urllib.request.urlopen",
                   side_effect=[urllib.error.URLError("boom"), self._resp_cm(200)]) as mock_open, \
             patch("time.sleep"):
            self.assertTrue(nf.tg_send("hello"))
        self.assertEqual(mock_open.call_count, 2)

    def test_both_attempts_fail_returns_false(self):
        import alerts.notifications as nf
        import urllib.error
        with patch.object(nf, "_TOKEN", "t"), patch.object(nf, "_CHAT_ID", "c"), \
             patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("boom")) as mock_open, \
             patch("time.sleep"):
            self.assertFalse(nf.tg_send("hello"))
        self.assertEqual(mock_open.call_count, 2)


# ── update_readme_stats
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

    def test_smartwatch_brand_count_shown(self):
        rows = [self._row("smartwatch", 50, 900, 100, 5, 300, 999)]
        content = self._run_with_tempdir(rows)
        self.assertIn("| Smartwatch | 50 | 900 | €100 | €5–€300 | 999 |", content)

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

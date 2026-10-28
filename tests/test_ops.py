"""
tests/test_ops.py
Operations: pipeline lock, Chrome start retries, process reaping.
Split from the former tests/test_{clean,load,scrape,alerts,telegram_cmds,ops}.py. No network or DB.
"""

import os
import subprocess
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch



# ── Pipeline concurrency lock
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


class TestStartChrome(unittest.TestCase):

    def test_retries_file_exists_then_succeeds(self):
        import scrapers.scraper_core as scraper_core
        driver = MagicMock()
        with patch.object(scraper_core.uc, "Chrome",
                          side_effect=[FileExistsError("race"), driver]) as chrome, \
             patch.object(scraper_core, "_chrome_major", return_value=140), \
             patch("scrapers.scraper_core.time.sleep"):
            self.assertIs(scraper_core._start_chrome(MagicMock()), driver)
        self.assertEqual(chrome.call_count, 2)

    def test_exhausted_retries_raise(self):
        import scrapers.scraper_core as scraper_core
        with patch.object(scraper_core.uc, "Chrome",
                          side_effect=FileExistsError("race")), \
             patch.object(scraper_core, "_chrome_major", return_value=140), \
             patch("scrapers.scraper_core.time.sleep"):
            with self.assertRaises(FileExistsError):
                scraper_core._start_chrome(MagicMock())


class TestReapProcesses(unittest.TestCase):

    class _Proc:
        def __init__(self, wait_s, returncode=0):
            self.wait_s = wait_s
            self.returncode = returncode
            self.args = ["python", "x.py"]
            self.killed = False

        def wait(self, timeout=None):
            if timeout is not None and self.wait_s > timeout:
                raise subprocess.TimeoutExpired(self.args, timeout)
            return self.returncode

        def kill(self):
            self.killed = True

    class _Log:
        def __init__(self):
            self.lines = []
            self.closed = False

        def write(self, s):
            self.lines.append(s)

        def close(self):
            self.closed = True

    def test_later_process_does_not_get_a_fresh_timeout(self):
        # Sequential wait(timeout) used to stack: first scraper ate the full
        # budget, the next one still got another full budget.
        import importlib
        mod = importlib.import_module("scrapers.run_all")
        slow = self._Proc(wait_s=5)
        later = self._Proc(wait_s=5)
        logs = [self._Log(), self._Log()]
        ticks = iter([0.0, 0.0, 5.0])  # deadline=1; later remaining=0
        with patch.object(mod.time, "monotonic", side_effect=lambda: next(ticks)), \
             patch.object(mod, "logging", MagicMock()):
            failed = mod._reap_processes(
                [("slow", slow, logs[0]), ("later", later, logs[1])],
                timeout=1,
            )
        self.assertTrue(failed)
        self.assertTrue(slow.killed)
        self.assertTrue(later.killed)
        self.assertTrue(logs[0].closed)
        self.assertTrue(logs[1].closed)

    def test_success_when_all_finish_in_time(self):
        import importlib
        mod = importlib.import_module("scrapers.run_all")
        a, b = self._Proc(0), self._Proc(0)
        logs = [self._Log(), self._Log()]
        with patch.object(mod, "logging", MagicMock()):
            self.assertFalse(mod._reap_processes(
                [("a", a, logs[0]), ("b", b, logs[1])], timeout=10,
            ))
        self.assertFalse(a.killed)


class TestWatchlistSkroutzId(unittest.TestCase):
    def test_parse(self):
        import run_pipeline
        self.assertEqual(run_pipeline._watchlist_skroutz_id("https://www.skroutz.gr/s/123/old-slug.html?x=1"), 123)
        self.assertIsNone(run_pipeline._watchlist_skroutz_id("https://www.skroutz.gr/c/40/x.html"))
        self.assertIsNone(run_pipeline._watchlist_skroutz_id(""))

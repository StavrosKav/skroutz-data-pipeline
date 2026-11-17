"""
tests/test_telegram_bot_logging.py
Telegram bot: polling backs off when Telegram is unreachable, and the bot's
log file rotates. No network.

On 2026-11-01 the laptop lost its connection and the bot logged
3.4M "getUpdates failed" lines (~320 MB) in one day: _post() swallows the
error and returns {}, so the polling loop never reached its backoff.
"""

import logging
import logging.handlers
import os
import tempfile
import unittest
from unittest.mock import patch

import alerts.telegram_bot as tb


class _Stop(BaseException):  # not caught by the loop's `except Exception`
    pass


class TestPollingBackoff(unittest.TestCase):

    def test_failed_get_updates_triggers_backoff(self):
        sleeps = []

        def fake_sleep(s):
            sleeps.append(s)
            if len(sleeps) >= 3:
                raise _Stop

        posts = []

        def fake_post(method, payload):
            posts.append(method)
            if len(posts) > 3:  # a tight loop would spin forever; stop it
                raise _Stop
            return {}  # what _post returns when the network is down

        with patch.object(tb, "TOKEN", "x"), patch.object(tb, "CHAT_ID", "1"), \
             patch.object(tb, "_set_commands"), patch.object(tb, "_send"), \
             patch.object(tb, "_post", side_effect=fake_post), \
             patch.object(tb.time, "sleep", side_effect=fake_sleep):
            with self.assertRaises(_Stop):
                tb.run()

        # one getUpdates per wait, and the wait grows
        self.assertEqual(len(posts), 3)
        self.assertEqual(sleeps, [5, 10, 20])

    def test_http_error_response_triggers_backoff(self):
        with patch.object(tb, "_post", return_value={"ok": False, "error_code": 502}):
            with self.assertRaises(RuntimeError):
                tb._get_updates(0)

    def test_ok_response_returns_updates(self):
        with patch.object(tb, "_post", return_value={"ok": True, "result": [{"update_id": 7}]}):
            self.assertEqual(tb._get_updates(0), [{"update_id": 7}])


class TestLogRotation(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = logging.getLogger()
        self._saved = (self.root.handlers[:], self.root.level)
        self.root.handlers = []

    def tearDown(self):
        for h in self.root.handlers:
            h.close()
        self.root.handlers, level = self._saved
        self.root.setLevel(level)
        self.tmp.cleanup()

    def test_defaults_5mb_x3(self):
        tb._setup_logging(self.tmp.name)
        files = [h for h in self.root.handlers
                 if isinstance(h, logging.handlers.RotatingFileHandler)]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].maxBytes, 5 * 1024 * 1024)
        self.assertEqual(files[0].backupCount, 3)
        self.assertEqual(os.path.basename(files[0].baseFilename), "telegram_bot.app.log")

    def test_file_rotates_and_keeps_n_backups(self):
        tb._setup_logging(self.tmp.name, max_bytes=200, backups=2)
        log = logging.getLogger("tb-test")
        for i in range(100):
            log.warning("getUpdates failed: line %d", i)
        names = sorted(os.listdir(self.tmp.name))
        self.assertEqual(names, ["telegram_bot.app.log",
                                 "telegram_bot.app.log.1",
                                 "telegram_bot.app.log.2"])
        for n in names:
            self.assertLess(os.path.getsize(os.path.join(self.tmp.name, n)), 400)

    def test_stderr_only_gets_errors(self):
        # The launcher appends stderr to telegram_bot.err.log, which nothing
        # rotates — keep warnings out of it.
        tb._setup_logging(self.tmp.name)
        streams = [h for h in self.root.handlers
                   if type(h) is logging.StreamHandler]
        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0].level, logging.ERROR)


if __name__ == "__main__":
    unittest.main()

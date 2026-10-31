"""Daily Telegram summary: drops show how many look like scrape errors."""
import unittest
from unittest.mock import patch

import alerts.notifications as n
import reporting.generate_dashboard as gd


class SuccessSuspectDrops(unittest.TestCase):
    def _msg(self, **kw):
        with patch.object(n, "tg_send", return_value=True) as s:
            n.tg_success(6424, 50, 757, "0:25:38", **kw)
        return s.call_args[0][0]

    def test_shows_suspect_count_and_threshold(self):
        msg = self._msg(suspect=7)
        self.assertIn("757", msg)
        self.assertIn(f"7 μεγάλη πτώση, έλεγξε ≥{gd.SUSPECT_DROP_PCT:.0f}%", msg)

    def test_no_suspect_note_when_zero(self):
        self.assertNotIn("μεγάλη πτώση, έλεγξε", self._msg(suspect=0))

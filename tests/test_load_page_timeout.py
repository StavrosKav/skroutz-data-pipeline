"""_load_page must survive a hung first page load (2026-10-31: phones hung 120 s
in driver.get -> urllib3 ReadTimeoutError, never retried, whole scrape failed)."""
import unittest
from unittest.mock import MagicMock, patch

from urllib3.exceptions import ReadTimeoutError

from scrapers import scraper_core as sc


class LoadPageTimeoutTests(unittest.TestCase):
    def test_retries_after_client_read_timeout(self):
        driver = MagicMock()
        driver.get.side_effect = [ReadTimeoutError(None, "/session", "Read timed out"), None]
        with patch.object(sc.time, "sleep"):
            sc._load_page(driver, "https://example.test")
        self.assertEqual(driver.get.call_count, 2)

    def test_sets_page_load_timeout_below_client_timeout(self):
        driver = MagicMock()
        sc._load_page(driver, "https://example.test")
        driver.set_page_load_timeout.assert_called_once_with(sc.PAGE_LOAD_TIMEOUT)
        self.assertLess(sc.PAGE_LOAD_TIMEOUT, 120)

    def test_gives_up_after_all_attempts(self):
        driver = MagicMock()
        driver.get.side_effect = ReadTimeoutError(None, "/session", "Read timed out")
        with patch.object(sc.time, "sleep"), self.assertRaises(ReadTimeoutError):
            sc._load_page(driver, "https://example.test")
        self.assertEqual(driver.get.call_count, 3)


if __name__ == "__main__":
    unittest.main()

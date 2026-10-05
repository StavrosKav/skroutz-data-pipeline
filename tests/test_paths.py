"""Pin path anchors so package moves cannot silently redirect data I/O."""
from __future__ import annotations

import unittest
from pathlib import Path

from core.paths import (
    ROOT,
    CLEAN_DIR,
    CHARTS_DIR,
    DASHBOARD_DIR,
    LOGS_DIR,
    CONFIG_DIR,
    WATCHLIST_PATH,
    INDEX_HTML,
)


class TestPaths(unittest.TestCase):
    def test_root_is_repo(self):
        self.assertTrue((ROOT / "run_pipeline.py").is_file())
        self.assertTrue((ROOT / "index.html").is_file())
        self.assertTrue((ROOT / ".nojekyll").is_file())

    def test_dashboard_out_dir_contract(self):
        import generate_dashboard as gd
        self.assertEqual(Path(gd.OUT_DIR).resolve(), DASHBOARD_DIR.resolve())
        self.assertEqual(Path(gd.CHARTS_DIR).resolve(), CHARTS_DIR.resolve())
        self.assertEqual(DASHBOARD_DIR, ROOT / "dashboard")
        self.assertEqual(
            DASHBOARD_DIR / "dashboard_latest.html",
            ROOT / "dashboard" / "dashboard_latest.html",
        )

    def test_charts_path(self):
        import charts_from_db as cf
        self.assertEqual(Path(cf.CHARTS_DIR).resolve(), CHARTS_DIR.resolve())
        self.assertEqual(CHARTS_DIR, ROOT / "charts")

    def test_clean_config_logs(self):
        self.assertEqual(CLEAN_DIR, ROOT / "Clean")
        self.assertEqual(CONFIG_DIR, ROOT / "config")
        self.assertEqual(LOGS_DIR, ROOT / "logs")
        self.assertEqual(WATCHLIST_PATH, ROOT / "watchlist.json")

    def test_index_html_points_at_locked_cv_path(self):
        text = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("dashboard/dashboard_latest.html", text)

    def test_auto_commit_targets_unchanged(self):
        import run_pipeline
        src = Path(run_pipeline.__file__).read_text(encoding="utf-8")
        self.assertIn('"charts"', src)
        self.assertIn('"dashboard/dashboard_latest.html"', src)
        self.assertIn('"README.md"', src)
        self.assertIn("STATS:BADGES:START", src)
        self.assertIn("STATS:TABLE:START", src)

    def test_clean_and_scraper_use_root(self):
        import clean_common
        import scraper_core
        self.assertEqual(Path(clean_common.BASE).resolve(), ROOT.resolve())
        self.assertEqual(Path(scraper_core.HERE).resolve(), ROOT.resolve())


if __name__ == "__main__":
    unittest.main()

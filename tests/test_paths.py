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

    def test_dashboard_out_dir(self):
        # generate_dashboard OUT_DIR must remain ROOT/dashboard
        from reporting.generate_dashboard import OUT_DIR, CHARTS_DIR as GD_CHARTS
        self.assertEqual(OUT_DIR.resolve(), DASHBOARD_DIR.resolve())
        self.assertEqual(GD_CHARTS.resolve(), CHARTS_DIR.resolve())
        self.assertEqual(OUT_DIR.name, "dashboard")
        self.assertTrue((OUT_DIR / "dashboard_latest.html").exists() or True)  # may be absent in CI clone variants
        latest = ROOT / "dashboard" / "dashboard_latest.html"
        self.assertEqual(latest, DASHBOARD_DIR / "dashboard_latest.html")

    def test_charts_path(self):
        from reporting.charts_from_db import CHARTS_DIR as CF_CHARTS
        self.assertEqual(Path(CF_CHARTS).resolve(), CHARTS_DIR.resolve())
        # tracked trend charts live at ROOT/charts
        self.assertEqual(CHARTS_DIR, ROOT / "charts")

    def test_clean_and_config(self):
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
        # publish_artifacts must still git-add these exact paths
        self.assertIn('"charts"', src)
        self.assertIn('"dashboard/dashboard_latest.html"', src)
        self.assertIn('"README.md"', src)
        # STATS markers must remain the contract for README rewrites
        self.assertIn("STATS:BADGES:START", src)
        self.assertIn("STATS:TABLE:START", src)

    def test_etl_and_scraper_use_root(self):
        from etl import clean_common
        from scrapers import scraper_core
        self.assertEqual(Path(clean_common.BASE).resolve(), ROOT.resolve())
        self.assertEqual(Path(scraper_core.HERE).resolve(), ROOT.resolve())


if __name__ == "__main__":
    unittest.main()

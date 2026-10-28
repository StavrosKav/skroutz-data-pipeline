"""
Scraper Health Monitor subagent for the Pipeline Monitoring Agent.
Checks scraper outputs for failures and anomalies.
"""

import os
import re
import logging
from typing import Optional
from datetime import date, datetime

from agents.base import BaseAgent
from core.paths import CATEGORY_FOLDERS

logger = logging.getLogger(__name__)

COUNT_DEVIATION_PCT = 20
_DATE_RE = re.compile(r"_(\d{4}-\d{2}-\d{2})\.csv$")


def _run_date() -> str:
    """Same rule as scrapers/etl: PIPELINE_DATE env, else today."""
    return os.environ.get("PIPELINE_DATE") or date.today().isoformat()


def csv_date(name: str):
    m = _DATE_RE.search(name)
    return m.group(1) if m else None


def pick_run_csv(files, run_date=None):
    """The CSV named for the run date (not newest mtime), or None."""
    run_date = run_date or _run_date()
    for f in sorted(files):
        if csv_date(f) == run_date:
            return f
    return None


def count_deviation(today_count: int, history: list, pct: float = COUNT_DEVIATION_PCT):
    """Return % deviation vs trailing average if > pct, else None (needs history)."""
    if not history:
        return None
    avg = sum(history) / len(history)
    if avg <= 0:
        return None
    dev = (today_count - avg) / avg * 100
    return round(dev, 1) if abs(dev) > pct else None


def _row_count(path: str) -> int:
    with open(path, "r", encoding="utf-8") as f:
        return sum(1 for _ in f) - 1


class ScraperHealthMonitor(BaseAgent):
    """Monitors the health of scrapers by checking output files."""

    def __init__(self, config: Optional[dict] = None):
        super().__init__("scraper_health_monitor", config)
        self.base_path = self.config.get(
            "base_path",
            os.path.join(os.path.dirname(__file__), "..", ".."),
        )
        self.categories = self.config.get(
            "categories", ["phones", "laptops", "smartwatches", "tablets"]
        )
        self.min_files = self.config.get("min_files", 1)
        self.min_rows = self.config.get("min_rows", 200)
        self.max_age_hours = self.config.get("max_age_hours", 25)

    def process(self, input_data: dict) -> dict:
        """
        Check the health of scrapers for the given pipeline run.
        Input: Dictionary with pipeline run info (optional).
        Output: Dictionary with health status for each category.
        """
        health_status = {}
        issues = []

        for category in self.categories:
            status = self._check_category(category)
            health_status[category] = status
            if not status["healthy"]:
                issues.append(f"{category}: {status['reason']}")

        overall_health = len(issues) == 0
        return {
            "healthy": overall_health,
            "issues": issues,
            "details": health_status,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def _check_category(self, category: str) -> dict:
        """Check the health of a specific category's scraper output."""
        folder_name = CATEGORY_FOLDERS.get(category, f"{category.capitalize()}_skroutz")

        folder_path = os.path.join(self.base_path, folder_name)
        if not os.path.exists(folder_path):
            return {
                "healthy": False,
                "reason": f"Folder not found: {folder_path}",
                "file_count": 0,
                "latest_file": None,
                "row_count": 0,
            }

        # Get the most recent CSV file
        csv_files = [
            f
            for f in os.listdir(folder_path)
            if f.endswith(".csv") and f.startswith("skroutz_")
        ]
        if not csv_files:
            return {
                "healthy": False,
                "reason": "No CSV files found",
                "file_count": 0,
                "latest_file": None,
                "row_count": 0,
            }

        run_date = _run_date()
        latest_file = pick_run_csv(csv_files, run_date)
        if latest_file is None:
            return {
                "healthy": False,
                "reason": f"No CSV for run date {run_date}",
                "file_count": len(csv_files),
                "latest_file": None,
                "row_count": 0,
            }
        file_path = os.path.join(folder_path, latest_file)

        # Check file age
        file_mod_time = datetime.fromtimestamp(os.path.getmtime(file_path))
        age_hours = (datetime.now() - file_mod_time).total_seconds() / 3600
        if age_hours > self.max_age_hours:
            return {
                "healthy": False,
                "reason": f"File too old: {age_hours:.1f} hours",
                "file_count": len(csv_files),
                "latest_file": latest_file,
                "row_count": 0,
                "age_hours": age_hours,
            }

        # Check row count (skip header)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                line_count = sum(1 for line in f)
            row_count = line_count - 1  # subtract header
            if row_count < self.min_rows:
                return {
                    "healthy": False,
                    "reason": f"Too few rows: {row_count} < {self.min_rows}",
                    "file_count": len(csv_files),
                    "latest_file": latest_file,
                    "row_count": row_count,
                }
        except Exception as e:
            return {
                "healthy": False,
                "reason": f"Error reading file: {str(e)}",
                "file_count": len(csv_files),
                "latest_file": latest_file,
                "row_count": 0,
            }

        # Count check vs trailing 7 dated CSVs before the run date.
        prior = sorted(f for f in csv_files if (csv_date(f) or "") < run_date)[-7:]
        history = []
        for f in prior:
            try:
                history.append(_row_count(os.path.join(folder_path, f)))
            except Exception:
                continue
        dev = count_deviation(row_count, history)
        if dev is not None:
            return {
                "healthy": False,
                "reason": (
                    f"Row count {row_count} deviates {dev:+.1f}% from 7-day avg "
                    f"{sum(history) / len(history):.0f}"
                ),
                "file_count": len(csv_files),
                "latest_file": latest_file,
                "row_count": row_count,
                "age_hours": round(age_hours, 1),
            }

        return {
            "healthy": True,
            "reason": "OK",
            "file_count": len(csv_files),
            "latest_file": latest_file,
            "row_count": row_count,
            "age_hours": round(age_hours, 1),
        }


def create_scraper_health_monitor(config: Optional[dict] = None) -> ScraperHealthMonitor:
    """Factory function to create a ScraperHealthMonitor instance."""
    return ScraperHealthMonitor(config)
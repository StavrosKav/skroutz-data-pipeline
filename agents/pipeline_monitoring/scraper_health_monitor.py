"""
Scraper Health Monitor subagent for the Pipeline Monitoring Agent.
Checks scraper outputs for failures and anomalies.
"""

import os
import logging
from typing import Optional
from datetime import datetime

from agents.base import BaseAgent

logger = logging.getLogger(__name__)


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
        self.min_rows = self.config.get("min_rows", 10)
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
        # Map category to folder name (the 'laptos' typo lives in the CSV
        # filenames, e.g. skroutz_laptos_YYYY-MM-DD.csv, not the folder)
        folder_map = {
            "phones": "Phones_skroutz",
            "laptops": "Laptops_skroutz",
            "smartwatches": "Smartwatches_skroutz",
            "tablets": "Tablets_skroutz",
        }
        folder_name = folder_map.get(category, f"{category.capitalize()}_skroutz")

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

        # Sort by modification time (newest first)
        csv_files.sort(
            key=lambda x: os.path.getmtime(os.path.join(folder_path, x)), reverse=True
        )
        latest_file = csv_files[0]
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
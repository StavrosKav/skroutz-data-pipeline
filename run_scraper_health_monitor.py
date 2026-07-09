#!/usr/bin/env python
"""
Scraper Health Monitor Script
-----------------------------
Runs the ScraperHealthMonitor agent and logs the results.
Exits with code 0 if all scrapers are healthy, non-zero otherwise.
"""

import os
import sys
import logging
import json
from datetime import datetime

# Add the project root to the path so we can import agents
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from agents.pipeline_monitoring.scraper_health_monitor import create_scraper_health_monitor

# Setup logging
log_dir = os.path.join(BASE, "logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, f"scraper_health_{datetime.now().date()}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file, encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)

def load_config():
    """Load configuration from JSON files."""
    config = {}
    # Load agent config
    agent_config_path = os.path.join(BASE, "config", "agents.json")
    if os.path.exists(agent_config_path):
        try:
            with open(agent_config_path, 'r') as f:
                agent_config = json.load(f)
                config.update(agent_config)
        except Exception as e:
            logger.warning(f"Failed to load agent config from {agent_config_path}: {e}")

    # Load pipeline config
    pipeline_config_path = os.path.join(BASE, "config", "pipeline.json")
    if os.path.exists(pipeline_config_path):
        try:
            with open(pipeline_config_path, 'r') as f:
                pipeline_config = json.load(f)
                config.update(pipeline_config)
        except Exception as e:
            logger.warning(f"Failed to load pipeline config from {pipeline_config_path}: {e}")

    return config

def main():
    logger.info("=== Scraper Health Monitor started ===")

    # Load configuration
    config = load_config()

    # Extract the scraper health monitor config
    monitor_config = config.get("scraper_health_monitor", {})

    # Create the monitor with the loaded config
    monitor = create_scraper_health_monitor(monitor_config)

    # Run the health check
    result = monitor.process({})  # input data is not used, but required by interface

    # Log the results
    logger.info(f"Scraper Health Check Results: {result}")

    # Log details for each category
    if "details" in result:
        for category, status in result["details"].items():
            if status["healthy"]:
                logger.info(f"[{category.upper()}] HEALTHY: {status['reason']} "
                            f"(files: {status['file_count']}, rows: {status['row_count']}, "
                            f"age: {status.get('age_hours', 'N/A')}h)")
            else:
                logger.error(f"[{category.upper()}] UNHEALTHY: {status['reason']} "
                             f"(files: {status['file_count']}, rows: {status['row_count']})")

    # Log any issues
    if result.get("issues"):
        for issue in result["issues"]:
            logger.warning(f"HEALTH ISSUE: {issue}")

    # Determine exit code: 0 if healthy, 1 if any issues
    healthy = result.get("healthy", True)
    if not healthy:
        logger.error("Scraper health check FAILED. Pipeline will stop.")
        return 1
    else:
        logger.info("Scraper health check PASSED.")
        return 0

if __name__ == "__main__":
    sys.exit(main())
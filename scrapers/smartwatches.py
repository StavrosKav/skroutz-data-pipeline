"""
smartwatches.py — smartwatches scraper entry point.

All scraping logic lives in scraper_core.py; this file exists so
scrapers.run_all and Task Scheduler can keep launching it by name.

Output: Smartwatches_skroutz/skroutz_Smartwatches_<YYYY-MM-DD>.csv
"""

from scrapers.scraper_core import CONFIGS, scrape

if __name__ == "__main__":
    scrape(CONFIGS["smartwatches"])

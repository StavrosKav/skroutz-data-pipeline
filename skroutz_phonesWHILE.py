"""
skroutz_phonesWHILE.py — smartphones scraper entry point.

All scraping logic lives in scraper_core.py; this file exists so
1scriptToGet4.py and Task Scheduler can keep launching it by name.

Output: Phones_skroutz/skroutz_phones_<YYYY-MM-DD>.csv
"""

from scraper_core import CONFIGS, scrape

if __name__ == "__main__":
    scrape(CONFIGS["phones"])

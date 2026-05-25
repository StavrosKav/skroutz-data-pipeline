"""
1scriptToGet4.py
----------------
Stage 1 of the daily Skroutz price-tracking pipeline.

Launches all four category scrapers in parallel as separate subprocesses:
  - skroutz_phonesWHILE.py
  - skroutz_SmartwatchesWHILE.py
  - skroutz_tabletsWHILE.py
  - skroutz_laptopsWHILE.py

Each scraper opens its own Chrome window, paginates through skroutz.gr,
and saves a date-stamped raw CSV to its category folder.

A short delay (LAUNCH_DELAY seconds) is inserted between each launch to avoid
all four browsers hammering the site simultaneously and triggering bot-detection.
stdout/stderr from every subprocess is captured to a dated log file under logs/.

Called by run_pipeline.py (Stage 1); can also be run standalone.
"""

import subprocess
import sys
import datetime
import os
import logging
import time

# ── Scraper scripts to run (order determines launch sequence) ─────────────────
HERE = os.path.dirname(os.path.abspath(__file__))

SCRIPTS = [
    os.path.join(HERE, "skroutz_phonesWHILE.py"),
    os.path.join(HERE, "skroutz_SmartwatchesWHILE.py"),
    os.path.join(HERE, "skroutz_tabletsWHILE.py"),
    os.path.join(HERE, "skroutz_laptopsWHILE.py"),
]

# Log directory for per-scraper stdout/stderr output
LOG_DIR = os.path.join(HERE, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Seconds to wait between launching each scraper.
# Staggering the launches reduces the chance of simultaneous bot-detection triggers.
LAUNCH_DELAY = 13


def run_all_scrapers():
    """
    Spawn each scraper as its own subprocess and capture its output to a dated log file.

    All four processes run concurrently after being launched with LAUNCH_DELAY
    seconds between each start. This function blocks until every scraper finishes.
    Uses sys.executable so the correct Python interpreter (e.g. Anaconda) is always used.
    """
    date_str = datetime.date.today().isoformat()
    procs = []

    for script in SCRIPTS:
        name = os.path.splitext(os.path.basename(script))[0]
        log_path = os.path.join(LOG_DIR, f"{name}_{date_str}.log")
        log_file = open(log_path, "a", encoding="utf-8")
        logging.info(f"Starting {name} — logging to {log_path}")

        # sys.executable ensures we invoke the same interpreter that launched this script
        p = subprocess.Popen(
            [sys.executable, script],
            stdout=log_file,
            stderr=log_file,
            shell=False,
        )
        procs.append((name, p, log_file))

        logging.info(f"Waiting {LAUNCH_DELAY}s before launching next scraper...")
        time.sleep(LAUNCH_DELAY)

    # Wait for all scrapers to finish and report their exit status
    for name, proc, log_file in procs:
        ret = proc.wait()
        log_file.close()
        if ret == 0:
            logging.info(f"{name} completed successfully.")
        else:
            logging.error(f"{name} exited with code {ret} — check its log file.")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    run_all_scrapers()


if __name__ == "__main__":
    main()

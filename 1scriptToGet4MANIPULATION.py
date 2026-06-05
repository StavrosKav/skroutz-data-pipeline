"""
1scriptToGet4MANIPULATION.py
-----------------------------
Stage 2 of the daily Skroutz price-tracking pipeline.

Runs all four data-cleaning scripts sequentially as separate subprocesses:
  - Data_Phone.py
  - Data_Smartwatches.py
  - Data_Tablets.py
  - Data_Laptops.py

Each cleaning script reads today's raw CSV produced by the scrapers (Stage 1),
applies price normalisation, brand/model extraction, installment conversion,
and writes a cleaned CSV to the Clean/ folder.

A short delay (LAUNCH_DELAY seconds) is inserted between launches to avoid
I/O contention when multiple scripts write to disk simultaneously.
stdout/stderr from every subprocess is captured to a dated log file under logs/.

Called by run_pipeline.py (Stage 2); can also be run standalone after Stage 1.
"""

import subprocess
import sys
import datetime
import os
import logging
import time

# ── Cleaning scripts to run (order does not affect correctness) ───────────────
HERE = os.path.dirname(os.path.abspath(__file__))

SCRIPTS = [
    os.path.join(HERE, "Data_Phone.py"),
    os.path.join(HERE, "Data_Smartwatches.py"),
    os.path.join(HERE, "Data_Tablets.py"),
    os.path.join(HERE, "Data_Laptops.py"),
]

# Log directory for per-script stdout/stderr output
LOG_DIR = os.path.join(HERE, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Seconds to wait between launching each cleaning script
LAUNCH_DELAY = 6


def run_all_cleaners():
    """
    Spawn each cleaning script as its own subprocess and capture its output to a dated log.

    All four processes run concurrently after being launched with LAUNCH_DELAY seconds
    between each start. This function blocks until every cleaner finishes.
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

        logging.info(f"Waiting {LAUNCH_DELAY}s before launching next cleaner...")
        time.sleep(LAUNCH_DELAY)

    # Wait for all cleaners to finish and report their exit status
    TIMEOUT = 1800  # 30-minute hard ceiling per cleaner
    any_failed = False
    for name, proc, log_file in procs:
        try:
            try:
                ret = proc.wait(timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()  # reap the process handle
                log_file.write(f"\n[TIMEOUT] {name} killed after {TIMEOUT}s\n")
                logging.error(f"{name} timed out after {TIMEOUT//60}m — killed.")
                any_failed = True
                continue
        finally:
            log_file.close()
        if ret == 0:
            logging.info(f"{name} completed successfully.")
        else:
            logging.error(f"{name} exited with code {ret} — check its log file.")
            any_failed = True
    return any_failed


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if run_all_cleaners():
        sys.exit(1)


if __name__ == "__main__":
    main()

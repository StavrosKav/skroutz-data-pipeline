"""
run_pipeline.py
---------------
Master orchestration script for the daily Skroutz price-tracking pipeline.

Pipeline stages (run sequentially):
  1. Scrape   — 1scriptToGet4.py
                Launches Chrome, scrapes all 4 product categories from skroutz.gr,
                saves raw CSVs to the category folders.

  2. Clean    — 1scriptToGet4MANIPULATION.py
                Reads raw CSVs, applies data cleaning and feature extraction
                (price normalisation, RAM/storage parsing, brand/model/color split),
                saves cleaned CSVs to Clean/.

  3. Load SQL — 4csvsTOsql.py
                Upserts cleaned data into PostgreSQL (products + price_snapshots).

Abort behaviour:
  If any stage exits with a non-zero return code the pipeline stops immediately,
  preventing corrupted or partial data from reaching the database.
  An alert email is sent to ALERT_TO so silent failures never go unnoticed.

Typical usage:
  python run_pipeline.py

For automation, configure Windows Task Scheduler to run this script daily.
"""

import subprocess
import sys
import logging
import datetime
import os
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Resolve script paths relative to this file so the pipeline works from any working directory
BASE = os.path.dirname(os.path.abspath(__file__))

STAGES = [
    ("Scrape",   os.path.join(BASE, "1scriptToGet4.py")),
    ("Clean",    os.path.join(BASE, "1scriptToGet4MANIPULATION.py")),
    ("Load SQL", os.path.join(BASE, "4csvsTOsql.py")),
]

# ── Email alerts ───────────────────────────────────────────────────────────────
# Set ALERT_EMAIL and GMAIL_APP_PASSWORD in your .env file to enable alerts.
# Generate a Gmail App Password at:
#   myaccount.google.com → Security → 2-Step Verification → App Passwords
ALERT_FROM         = os.environ.get("ALERT_EMAIL", "")
ALERT_TO           = os.environ.get("ALERT_EMAIL", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")


def send_failure_alert(stage, returncode):
    """Send a Gmail alert when a pipeline stage fails. No-ops if password not set."""
    if not GMAIL_APP_PASSWORD:
        logger.warning("Alert email not sent — GMAIL_APP_PASSWORD is not configured.")
        return
    log_path = os.path.join(BASE, "logs", f"pipeline_{datetime.date.today()}.log")
    msg = EmailMessage()
    msg["Subject"] = f"[Skroutz Pipeline] FAILED — {stage} — {datetime.date.today()}"
    msg["From"]    = ALERT_FROM
    msg["To"]      = ALERT_TO
    msg.set_content(
        f"Stage '{stage}' exited with code {returncode}.\n\n"
        f"Date:  {datetime.datetime.now():%Y-%m-%d %H:%M}\n"
        f"Log:   {log_path}\n\n"
        "The pipeline was aborted. No downstream stages ran.\n"
        "Fix the issue and re-run run_pipeline.py manually to recover today's data."
    )
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.starttls()
            smtp.login(ALERT_FROM, GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
        logger.info("Failure alert email sent.")
    except Exception as e:
        logger.warning(f"Could not send alert email: {e}")


def run_stage(label, script):
    """
    Run a single pipeline stage as a subprocess.
    On failure: sends an alert email then exits, so downstream stages
    never run against incomplete input data.
    """
    logger.info(f"=== {label} started ===")
    result = subprocess.run([sys.executable, script])
    if result.returncode != 0:
        logger.error(f"{label} failed (exit {result.returncode}). Aborting pipeline.")
        send_failure_alert(label, result.returncode)
        sys.exit(result.returncode)
    logger.info(f"=== {label} complete ===")


if __name__ == "__main__":
    start = datetime.datetime.now()
    for label, script in STAGES:
        run_stage(label, script)
    elapsed = datetime.datetime.now() - start
    logger.info(f"Pipeline finished in {elapsed}")

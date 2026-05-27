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

  Set SKIP_SCRAPE=1 to skip the scraping stage and run only Clean + Load.
  This is set automatically in docker-compose.yml (Chrome cannot run headless
  without triggering bot-detection).

For automation, configure Windows Task Scheduler to run this script daily.
"""

import subprocess
import sys
import logging
import datetime
import os
import smtplib
import json
from email.message import EmailMessage
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Resolve script paths relative to this file so the pipeline works from any working directory
BASE = os.path.dirname(os.path.abspath(__file__))

_ALL_STAGES = [
    ("Scrape",   os.path.join(BASE, "1scriptToGet4.py")),
    ("Clean",    os.path.join(BASE, "1scriptToGet4MANIPULATION.py")),
    ("Load SQL", os.path.join(BASE, "4csvsTOsql.py")),
]

# Set SKIP_SCRAPE=1 in environments where Chrome cannot run (e.g. Docker)
_skip_scrape = os.environ.get("SKIP_SCRAPE", "").lower() in ("1", "true", "yes")
if _skip_scrape:
    logger.info("SKIP_SCRAPE=1 — skipping Scrape stage, running Clean + Load only")
STAGES = [s for s in _ALL_STAGES if not (_skip_scrape and s[0] == "Scrape")]

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


def run_charts():
    """Regenerate price trend charts. Non-fatal — pipeline result is unaffected if this fails."""
    logger.info("=== Charts started ===")
    result = subprocess.run([sys.executable, os.path.join(BASE, "charts_from_db.py")])
    if result.returncode != 0:
        logger.warning("Charts step failed — pipeline result is unaffected.")
    else:
        logger.info("=== Charts complete ===")


def send_drop_digest():
    """Email today's top price drops after a successful pipeline run. No-ops if password not set."""
    if not GMAIL_APP_PASSWORD:
        return
    db_url = (
        f"postgresql+psycopg2://{os.environ.get('DB_USER', 'postgres')}:"
        f"{os.environ.get('DB_PASSWORD', '')}@"
        f"{os.environ.get('DB_HOST', 'localhost')}:"
        f"{os.environ.get('DB_PORT', '5432')}/"
        f"{os.environ.get('DB_NAME', 'SkroutzPR')}"
    )
    try:
        engine = create_engine(db_url)
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT brand, model, category, prev_price, new_price, drop_eur, drop_pct "
                "FROM vw_biggest_drops "
                "WHERE drop_date = CURRENT_DATE "
                "ORDER BY drop_eur ASC LIMIT 10"
            )).fetchall()
    except Exception as e:
        logger.warning(f"Drop digest: DB query failed — {e}")
        return
    if not rows:
        logger.info("No price drops today — digest not sent.")
        return
    header = f"{'Brand':<20} {'Model':<30} {'Cat':<12} {'Was €':>8} {'Now €':>8} {'Drop €':>8} {'Drop %':>7}"
    sep    = "-" * len(header)
    lines  = [header, sep]
    for r in rows:
        brand = (r.brand or "")[:20]
        model = (r.model or "")[:30]
        lines.append(
            f"{brand:<20} {model:<30} {r.category:<12} "
            f"{float(r.prev_price):>8.2f} {float(r.new_price):>8.2f} "
            f"{float(r.drop_eur):>8.2f} {float(r.drop_pct):>7.1f}%"
        )
    msg = EmailMessage()
    msg["Subject"] = f"[Skroutz] {len(rows)} price drops today — {datetime.date.today()}"
    msg["From"]    = ALERT_FROM
    msg["To"]      = ALERT_TO
    msg.set_content(
        f"Top price drops from today's scrape ({datetime.date.today()}):\n\n"
        + "\n".join(lines)
        + "\n\nFull history: query vw_biggest_drops in the database."
    )
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.starttls()
            smtp.login(ALERT_FROM, GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
        logger.info(f"Drop digest sent — {len(rows)} deals.")
    except Exception as e:
        logger.warning(f"Could not send drop digest: {e}")


def _get_engine():
    """Return a SQLAlchemy engine using .env credentials."""
    return create_engine(
        f"postgresql+psycopg2://{os.environ.get('DB_USER', 'postgres')}:"
        f"{os.environ.get('DB_PASSWORD', '')}@"
        f"{os.environ.get('DB_HOST', 'localhost')}:"
        f"{os.environ.get('DB_PORT', '5432')}/"
        f"{os.environ.get('DB_NAME', 'SkroutzPR')}"
    )


def _send_email(subject, body):
    """Send a plain-text email via Gmail. No-ops if password not set."""
    if not GMAIL_APP_PASSWORD:
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = ALERT_FROM
    msg["To"]      = ALERT_TO
    msg.set_content(body)
    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
        smtp.login(ALERT_FROM, GMAIL_APP_PASSWORD)
     
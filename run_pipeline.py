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
from sqlalchemy import text

from db import get_engine

load_dotenv()

# Resolve script paths relative to this file so the pipeline works from any working directory
BASE = os.path.dirname(os.path.abspath(__file__))

_log_dir = os.path.join(BASE, "logs")
os.makedirs(_log_dir, exist_ok=True)
_log_file = os.path.join(_log_dir, f"pipeline_{datetime.date.today()}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        # FileHandler uses delay=True so it only opens the file on the first write,
        # avoiding a PermissionError when run_pipeline.bat has already opened the
        # same log file for its own stdout redirect (Windows file-handle conflict).
        logging.FileHandler(_log_file, encoding="utf-8", delay=True),
    ],
)
logger = logging.getLogger(__name__)

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
    try:
        engine = get_engine()
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
            f"{abs(float(r.drop_eur)):>8.2f} {float(r.drop_pct):>7.1f}%"
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


def _send_email(subject, body):
    """Send a plain-text email via Gmail. No-ops if password not set."""
    if not GMAIL_APP_PASSWORD:
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = ALERT_FROM
    msg["To"]      = ALERT_TO
    msg.set_content(body)
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.starttls()
            smtp.login(ALERT_FROM, GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
    except Exception as e:
        logger.warning(f"_send_email failed: {e}")


def send_watchlist_alerts():
    """
    Check watchlist.json against today's prices and email when a product
    is at or below its threshold.

    watchlist.json format (array of objects):
      [
        {
          "url":           "https://www.skroutz.gr/s/...",
          "label":         "iPhone 17 Pro Max 512GB",
          "threshold_eur": 1650.00
        },
        ...
      ]

    Edit watchlist.json to add or remove tracked products.
    """
    watchlist_path = os.path.join(BASE, "watchlist.json")
    if not os.path.exists(watchlist_path):
        logger.info("watchlist.json not found — skipping watchlist check.")
        return
    try:
        with open(watchlist_path, encoding="utf-8") as f:
            items = json.load(f)
    except Exception as e:
        logger.warning(f"Watchlist: could not read watchlist.json — {e}")
        return
    if not items:
        return

    try:
        engine = get_engine()
        with engine.connect() as conn:
            hits = []
            for item in items:
                url       = item.get("url", "").strip()
                label     = item.get("label", url)
                threshold = float(item.get("threshold_eur", 0))
                if not url:
                    continue
                row = conn.execute(text(
                    "SELECT brand, model, category, price_eur, skroutz_link "
                    "FROM vw_latest_prices "
                    "WHERE skroutz_link = :url "
                    "   OR skroutz_link LIKE :url_prefix"
                ), {"url": url, "url_prefix": url.split("?")[0] + "%"}).fetchone()
                if row is None:
                    logger.warning(f"Watchlist: '{label}' not found in DB (URL may not match).")
                    continue
                if float(row.price_eur) <= threshold:
                    hits.append({
                        "label":     label,
                        "brand":     row.brand or "",
                        "model":     row.model or "",
                        "category":  row.category,
                        "price":     float(row.price_eur),
                        "threshold": threshold,
                        "url":       row.skroutz_link,
                    })
    except Exception as e:
        logger.warning(f"Watchlist: DB query failed — {e}")
        return

    if not hits:
        logger.info("Watchlist: no thresholds crossed today.")
        return

    lines = [f"{'Product':<45} {'Now €':>8} {'Target €':>9}", "-" * 65]
    for h in hits:
        name = f"{h['brand']} {h['model']}".strip() or h["label"]
        lines.append(f"{name[:45]:<45} {h['price']:>8.2f} {h['threshold']:>9.2f}")
        lines.append(f"  → {h['url']}")
    body = (
        f"{len(hits)} watchlist item(s) hit their target price on {datetime.date.today()}:\n\n"
        + "\n".join(lines)
        + "\n\nUpdate watchlist.json to change thresholds or remove items."
    )
    try:
        _send_email(
            subject=f"[Skroutz] 🎯 {len(hits)} price target(s) reached — {datetime.date.today()}",
            body=body,
        )
        logger.info(f"Watchlist alert sent — {len(hits)} hit(s).")
    except Exception as e:
        logger.warning(f"Watchlist: could not send email — {e}")


def send_disappeared_alert():
    """
    Email a summary of products that disappeared from Skroutz in the last 2 days.
    Useful for spotting discontinued models or unusually cheap listings that
    got pulled. Non-fatal — pipeline result is unaffected if this fails.
    """
    if not GMAIL_APP_PASSWORD:
        return
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT brand, model, category, product_name, last_seen, "
                "       days_since_last_seen, skroutz_link "
                "FROM vw_disappeared "
                "WHERE days_since_last_seen BETWEEN 1 AND 2 "
                "ORDER BY category, last_seen DESC"
            )).fetchall()
    except Exception as e:
        logger.warning(f"Disappeared alert: DB query failed — {e}")
        return

    if not rows:
        logger.info("No newly disappeared products today.")
        return

    header = f"{'Brand':<18} {'Model':<28} {'Cat':<12} {'Last seen':>10}"
    sep    = "-" * len(header)
    lines  = [header, sep]
    for r in rows:
        brand = (r.brand or "")[:18]
        model = (r.model or r.product_name or "")[:28]
        lines.append(
            f"{brand:<18} {model:<28} {r.category:<12} {str(r.last_seen):>10}"
        )
        lines.append(f"  → {r.skroutz_link}")

    body = (
        f"{len(rows)} product(s) disappeared from Skroutz in the last 2 days "
        f"({datetime.date.today()}):\n\n"
        + "\n".join(lines)
        + "\n\nThese products have not appeared in any scrape for 1–2 days. "
        "They may be discontinued, out of stock, or temporarily unlisted."
    )
    try:
        _send_email(
            subject=f"[Skroutz] ⚠️ {len(rows)} product(s) disappeared — {datetime.date.today()}",
            body=body,
        )
        logger.info(f"Disappeared alert sent — {len(rows)} product(s).")
    except Exception as e:
        logger.warning(f"Disappeared alert: could not send email — {e}")


def run_dashboard():
    """Generate the HTML dashboard. Non-fatal — pipeline result is unaffected if this fails."""
    logger.info("=== Dashboard started ===")
    result = subprocess.run([sys.executable, os.path.join(BASE, "generate_dashboard.py")])
    if result.returncode != 0:
        logger.warning("Dashboard generation failed — pipeline result is unaffected.")
    else:
        logger.info("=== Dashboard complete ===")


def send_success_summary(elapsed):
    """Email a brief daily summary after a successful pipeline run. No-ops if password not set."""
    if not GMAIL_APP_PASSWORD:
        return
    try:
        engine = get_engine()
        with engine.connect() as conn:
            snaps     = conn.execute(text("SELECT COUNT(*) FROM price_snapshots WHERE date = CURRENT_DATE")).scalar()
            new_prods = conn.execute(text("SELECT COUNT(*) FROM products WHERE first_seen = CURRENT_DATE")).scalar()
            drops     = conn.execute(text("SELECT COUNT(*) FROM vw_biggest_drops WHERE drop_date = CURRENT_DATE")).scalar()
    except Exception as e:
        logger.warning(f"Success summary: DB query failed — {e}")
        return
    _send_email(
        subject=f"[Skroutz] Pipeline OK — {datetime.date.today()}",
        body=(
            f"Daily pipeline completed successfully in {elapsed}.\n\n"
            f"  Snapshots loaded : {snaps:,}\n"
            f"  New products     : {new_prods:,}\n"
            f"  Price drops today: {drops}\n\n"
            f"Date: {datetime.datetime.now():%Y-%m-%d %H:%M}\n"
            f"Log:  logs/pipeline_{datetime.date.today()}.log\n"
        ),
    )
    logger.info(f"Success summary sent — {snaps:,} snapshots, {new_prods:,} new products, {drops} drops.")


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
    run_charts()
    send_drop_digest()
    send_watchlist_alerts()
    send_disappeared_alert()
    run_dashboard()
    elapsed = datetime.datetime.now() - start
    logger.info(f"Pipeline finished in {elapsed}")

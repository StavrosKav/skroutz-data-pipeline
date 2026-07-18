"""
run_pipeline.py
---------------
Master orchestration script for the daily Skroutz price-tracking pipeline.

Pipeline stages (run sequentially):
  1. Scrape   — 1scriptToGet4.py                           [fatal]
                Launches Chrome, scrapes all 4 product categories from skroutz.gr,
                saves raw CSVs to the category folders.

  2. Scraper Health Monitor — run_scraper_health_monitor.py [observer]
                Checks raw CSVs exist, are fresh, and have enough rows.
                Warns via Telegram on problems; never blocks the pipeline.

  3. Clean    — 1scriptToGet4MANIPULATION.py                [fatal]
                Reads raw CSVs, applies data cleaning and feature extraction
                (price normalisation, RAM/storage parsing, brand/model/color split),
                saves cleaned CSVs to Clean/.

  4. Data Quality Agent — run_data_quality_agent.py         [observer]
                Read-only quality report on the day's raw CSVs, written to
                logs/data_quality_YYYY-MM-DD.json. Never modifies data.

  5. Load SQL — 4csvsTOsql.py                               [fatal]
                Upserts cleaned data into PostgreSQL (products + price_snapshots).

Abort behaviour:
  If a fatal stage exits with a non-zero return code the pipeline stops
  immediately, preventing corrupted or partial data from reaching the database.
  An alert email is sent to ALERT_TO so silent failures never go unnoticed.
  Observer stages only report on data produced by other stages: on failure they
  send a Telegram warning and the pipeline continues.

Typical usage:
  python run_pipeline.py

  Set SKIP_SCRAPE=1 to skip the scraping stage and run only Clean + Load.
  This is set automatically in docker-compose.yml (Chrome cannot run headless
  without triggering bot-detection).

For automation, configure Windows Task Scheduler to run this script daily.
"""

import html
import subprocess
import sys
import logging
import datetime
import os
import re
import smtplib
import json
import time
from email.message import EmailMessage
from dotenv import load_dotenv
from sqlalchemy import text

from db import get_engine
import notifications as _notif

load_dotenv()

# Resolve script paths relative to this file so the pipeline works from any working directory
BASE = os.path.dirname(os.path.abspath(__file__))

_log_dir  = os.path.join(BASE, "logs")
os.makedirs(_log_dir, exist_ok=True)
_log_file = os.path.join(_log_dir, f"pipeline_{datetime.date.today()}.log")

_handlers: list[logging.Handler] = [logging.StreamHandler()]
try:
    _handlers.append(logging.FileHandler(_log_file, encoding="utf-8"))
except OSError as _e:
    _notif.tg_send(
        f"⚠️ <b>Pipeline log locked</b>\n"
        f"Cannot open <code>{_log_file}</code>\n"
        f"{_e}\n"
        f"Pipeline will run — check for a duplicate instance."
    )
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=_handlers,
)
logger = logging.getLogger(__name__)


def _cleanup_old_logs(days: int = 30) -> None:
    """Delete pipeline, scraper, dedup, and data-quality-report files older than `days` days."""
    cutoff = datetime.date.today() - datetime.timedelta(days=days)
    removed = 0
    for fname in os.listdir(_log_dir):
        if not (fname.endswith(".log") or
                (fname.startswith("tg_sent_") and fname.endswith(".json")) or
                (fname.startswith("data_quality_") and fname.endswith(".json"))):
            continue
        m = re.search(r"(\d{4}-\d{2}-\d{2})", fname)
        if not m:
            continue
        try:
            if datetime.date.fromisoformat(m.group(1)) < cutoff:
                os.remove(os.path.join(_log_dir, fname))
                removed += 1
        except Exception:
            pass
    if removed:
        logger.info(f"Cleaned up {removed} log file(s) older than {days} days.")

_LOCK_FILE = os.path.join(BASE, "pipeline.lock")
_LOCK_STALE_SECONDS = 2 * 3600


def _acquire_lock() -> bool:
    """One pipeline at a time — a concurrent run doubles scraper load and risks
    bot detection. A lock older than 2h can only be a crashed run's leftover
    (a full run takes ~10-15 min), so it is reclaimed."""
    try:
        if time.time() - os.path.getmtime(_LOCK_FILE) > _LOCK_STALE_SECONDS:
            os.remove(_LOCK_FILE)
    except OSError:
        pass
    try:
        fd = os.open(_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(str(os.getpid()))
    return True


def _release_lock() -> None:
    try:
        os.remove(_LOCK_FILE)
    except FileNotFoundError:
        pass


_ALL_STAGES = [
    ("Scrape",   os.path.join(BASE, "1scriptToGet4.py")),
    ("Clean",    os.path.join(BASE, "1scriptToGet4MANIPULATION.py")),
    ("Load SQL", os.path.join(BASE, "4csvsTOsql.py")),
]

# Set SKIP_SCRAPE=1 in environments where Chrome cannot run (e.g. Docker)
_skip_scrape = os.environ.get("SKIP_SCRAPE", "").lower() in ("1", "true", "yes")
if _skip_scrape:
    logger.info("SKIP_SCRAPE=1 — skipping Scrape stage, running Clean + Load only")

# Build STAGES as (label, script, fatal). Core stages (fatal=True) abort the
# pipeline on failure so corrupted data never reaches the database. Observer
# stages (fatal=False) only report on data someone else produced — their
# failure must never block Clean/Load (2026-07-09: a crash in the health
# monitor blocked a whole day's load of perfectly good scrape data).
_STAGES = []
for name, path in _ALL_STAGES:
    if _skip_scrape and name == "Scrape":
        continue  # skip this stage
    _STAGES.append((name, path, True))
    # After Scrape, add health monitor if we didn't skip scrape
    if name == "Scrape" and not _skip_scrape:
        _STAGES.append(("Scraper Health Monitor", os.path.join(BASE, "run_scraper_health_monitor.py"), False))
    # After Clean, add data quality agent
    if name == "Clean":
        _STAGES.append(("Data Quality Agent", os.path.join(BASE, "run_data_quality_agent.py"), False))
STAGES = _STAGES

# ── Email alerts ───────────────────────────────────────────────────────────────
# Set ALERT_EMAIL and GMAIL_APP_PASSWORD in your .env file to enable alerts.
# Generate a Gmail App Password at:
#   myaccount.google.com → Security → 2-Step Verification → App Passwords
ALERT_FROM         = os.environ.get("ALERT_EMAIL", "")
ALERT_TO           = os.environ.get("ALERT_EMAIL", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")


# ── HTML email helpers ─────────────────────────────────────────────────────────

def _html_shell(title: str, subtitle: str, body: str) -> str:
    today = datetime.date.today().strftime("%B %d, %Y")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f2f5;padding:32px 12px;">
    <tr><td align="center">
      <table cellpadding="0" cellspacing="0" style="max-width:620px;width:100%;background:#ffffff;border-radius:10px;overflow:hidden;box-shadow:0 4px 18px rgba(0,0,0,0.11);">
        <tr>
          <td style="background:#0d1b2a;padding:0;">
            <div style="border-left:4px solid #e63946;padding:22px 26px;">
              <p style="margin:0 0 4px;font-size:10px;color:#e63946;letter-spacing:2.5px;text-transform:uppercase;font-weight:700;">Skroutz Price Tracker</p>
              <h1 style="margin:0;font-size:19px;color:#ffffff;font-weight:700;line-height:1.3;">{title}</h1>
              <p style="margin:5px 0 0;font-size:12px;color:#7a8fa6;">{subtitle}</p>
            </div>
          </td>
        </tr>
        <tr>
          <td style="padding:26px 26px 18px;">
            {body}
          </td>
        </tr>
        <tr>
          <td style="background:#f8fafc;padding:13px 26px;border-top:1px solid #e8ecf0;">
            <p style="margin:0;font-size:11px;color:#aab0bb;">
              Skroutz Price Tracker &nbsp;·&nbsp; {today} &nbsp;·&nbsp;
              <a href="https://www.skroutz.gr" style="color:#aab0bb;text-decoration:none;">skroutz.gr</a>
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _pct_badge(pct: float) -> str:
    val = abs(pct)
    if val >= 30:
        bg, fg = "#1a6b35", "#ffffff"
    elif val >= 20:
        bg, fg = "#2d9e4f", "#ffffff"
    elif val >= 10:
        bg, fg = "#52b87c", "#ffffff"
    else:
        bg, fg = "#d4edda", "#1a6b35"
    return (
        f'<span style="background:{bg};color:{fg};padding:3px 8px;border-radius:12px;'
        f'font-size:11px;font-weight:700;white-space:nowrap;">&#x2193; {val:.1f}%</span>'
    )


def _send_html_email(subject: str, html: str, plain: str) -> None:
    if not GMAIL_APP_PASSWORD:
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = ALERT_FROM
    msg["To"]      = ALERT_TO
    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(ALERT_FROM, GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError, ConnectionError) as e:
        logger.warning(f"_send_html_email failed: {e}")


def send_failure_alert(stage, returncode):
    log_path = os.path.join(BASE, "logs", f"pipeline_{datetime.date.today()}.log")
    _notif.tg_failure(stage, returncode, log_path)
    if not GMAIL_APP_PASSWORD:
        if not _notif._TOKEN:
            logger.warning("Alert not sent — neither TELEGRAM_BOT_TOKEN nor GMAIL_APP_PASSWORD is configured.")
        return
    now_str  = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    body_html = f"""
      <div style="background:#fff3f3;border:1px solid #f5c6cb;border-radius:8px;padding:18px 22px;margin-bottom:22px;">
        <p style="margin:0 0 4px;font-size:11px;font-weight:700;color:#c0392b;text-transform:uppercase;letter-spacing:1px;">Pipeline aborted</p>
        <p style="margin:0;font-size:24px;font-weight:700;color:#c0392b;">Stage: {stage}</p>
      </div>
      <table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;">
        <tr style="border-bottom:1px solid #f0f2f5;">
          <td style="padding:11px 4px;font-size:13px;color:#6b7280;width:110px;">Exit code</td>
          <td style="padding:11px 4px;font-size:13px;color:#111827;font-weight:600;">{returncode}</td>
        </tr>
        <tr style="border-bottom:1px solid #f0f2f5;">
          <td style="padding:11px 4px;font-size:13px;color:#6b7280;">Time</td>
          <td style="padding:11px 4px;font-size:13px;color:#111827;">{now_str}</td>
        </tr>
        <tr>
          <td style="padding:11px 4px;font-size:13px;color:#6b7280;">Log</td>
          <td style="padding:11px 4px;font-size:11px;color:#374151;word-break:break-all;">{log_path}</td>
        </tr>
      </table>
      <div style="margin-top:22px;padding:14px 16px;background:#fffbeb;border-left:3px solid #f59e0b;border-radius:4px;">
        <p style="margin:0;font-size:13px;color:#92400e;">
          No downstream stages ran. Fix the issue and re-run
          <code style="background:#fef3c7;padding:1px 5px;border-radius:3px;">run_pipeline.py</code>
          manually to recover today's data.
        </p>
      </div>"""

    try:
        _send_html_email(
            subject = f"[Skroutz Pipeline] FAILED — {stage} — {datetime.date.today()}",
            html    = _html_shell(
                title    = f"Pipeline Failed — {stage}",
                subtitle = f"Stage exited with code {returncode} · {now_str}",
                body     = body_html,
            ),
            plain = (
                f"Stage '{stage}' exited with code {returncode}.\n\n"
                f"Time:  {now_str}\nLog:   {log_path}\n\n"
                "No downstream stages ran. Re-run run_pipeline.py manually."
            ),
        )
        logger.info("Failure alert email sent.")
    except Exception as e:
        logger.warning(f"Could not send alert email: {e}")


# Every analytical view backed by a MATERIALIZED VIEW (analytics.sql v4) —
# refreshed here, once per day, right after Load SQL. All ten carry a UNIQUE
# index so CONCURRENTLY works: readers (dashboard/bot/Streamlit) never see the
# view locked or empty mid-refresh.
MATVIEWS = [
    "mv_latest_prices",
    "mv_price_history",
    "mv_price_floor",
    "mv_price_volatility",
    "mv_price_trend_direction",
    "mv_brand_summary",
    "mv_brand_price_trend",
    "mv_daily_market_index",
    "mv_restock_pricing",
    "mv_review_velocity",
]


def refresh_matviews():
    """
    Refresh the analytics materialized views so today's data shows up in
    vw_biggest_drops, vw_latest_prices, etc. Non-fatal — pipeline result is
    unaffected if this fails (a stale matview is a staleness bug, not data
    loss; the underlying tables Load SQL just wrote are untouched).
    """
    logger.info("=== Materialized view refresh started ===")
    t = datetime.datetime.now()
    try:
        engine = get_engine()
        failed = []
        for name in MATVIEWS:
            try:
                with engine.begin() as conn:
                    conn.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {name}"))
            except Exception as e:
                failed.append(name)
                logger.warning(f"Matview refresh failed for {name}: {e}")
        elapsed = (datetime.datetime.now() - t).total_seconds()
        if failed:
            logger.warning(
                f"=== Materialized view refresh finished with errors in {elapsed:.0f}s — "
                f"failed: {', '.join(failed)} ==="
            )
        else:
            logger.info(f"=== Materialized view refresh complete in {elapsed:.0f}s — {len(MATVIEWS)} views ===")
    except Exception as e:
        logger.warning(f"Matview refresh: could not obtain DB engine — {e}")


def run_charts():
    """Regenerate price trend charts. Non-fatal — pipeline result is unaffected if this fails."""
    logger.info("=== Charts started ===")
    t = datetime.datetime.now()
    result = subprocess.run([sys.executable, os.path.join(BASE, "charts_from_db.py")])
    elapsed = (datetime.datetime.now() - t).total_seconds()
    if result.returncode != 0:
        logger.warning(f"Charts step failed after {elapsed:.0f}s — pipeline result is unaffected.")
    else:
        logger.info(f"=== Charts complete in {elapsed:.0f}s ===")


def send_drop_digest():
    if not GMAIL_APP_PASSWORD and not _notif._TOKEN:
        return
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT brand, model, category, prev_price, new_price, drop_eur, drop_pct "
                "FROM vw_biggest_drops "
                "WHERE drop_date = CURRENT_DATE "
                "ORDER BY ABS(drop_eur) DESC LIMIT 10"
            )).fetchall()
    except Exception as e:
        logger.warning(f"Drop digest: DB query failed — {e}")
        return
    if not rows:
        logger.info("No price drops today — digest not sent.")
        return
    _notif.tg_drops(rows)

    CAT_ICON = {"phone": "📱", "laptop": "💻", "smartwatch": "⌚", "tablet": "📟"}
    thead = """
      <thead>
        <tr style="background:#0d1b2a;">
          <th style="padding:10px 10px;text-align:left;font-size:11px;color:#7a8fa6;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Brand</th>
          <th style="padding:10px 10px;text-align:left;font-size:11px;color:#7a8fa6;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Model</th>
          <th style="padding:10px 8px;text-align:center;font-size:11px;color:#7a8fa6;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Type</th>
          <th style="padding:10px 10px;text-align:right;font-size:11px;color:#7a8fa6;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Was</th>
          <th style="padding:10px 10px;text-align:right;font-size:11px;color:#7a8fa6;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Now</th>
          <th style="padding:10px 10px;text-align:right;font-size:11px;color:#7a8fa6;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Saved</th>
          <th style="padding:10px 10px;text-align:center;font-size:11px;color:#7a8fa6;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Drop</th>
        </tr>
      </thead>"""
    tbody_rows = []
    for i, r in enumerate(rows):
        bg    = "#f8fafc" if i % 2 == 0 else "#ffffff"
        brand = html.escape(str(r.brand or "—"))
        model = html.escape(str(r.model or "—"))
        tbody_rows.append(
            f'<tr style="background:{bg};">'
            f'<td style="padding:10px 10px;font-size:13px;color:#111827;font-weight:600;">{brand}</td>'
            f'<td style="padding:10px 10px;font-size:12px;color:#374151;">{model}</td>'
            f'<td style="padding:10px 8px;font-size:14px;text-align:center;">{CAT_ICON.get(r.category, "")}</td>'
            f'<td style="padding:10px 10px;font-size:12px;color:#9ca3af;text-align:right;text-decoration:line-through;">{float(r.prev_price):.2f}&nbsp;€</td>'
            f'<td style="padding:10px 10px;font-size:13px;color:#111827;font-weight:700;text-align:right;">{float(r.new_price):.2f}&nbsp;€</td>'
            f'<td style="padding:10px 10px;font-size:13px;color:#1a6b35;font-weight:600;text-align:right;">&#x2212;{abs(float(r.drop_eur)):.2f}&nbsp;€</td>'
            f'<td style="padding:10px 10px;text-align:center;">{_pct_badge(float(r.drop_pct))}</td>'
            f'</tr>'
        )
    body_html = (
        f'<p style="margin:0 0 16px;font-size:14px;color:#374151;">'
        f'Here are today\'s <strong>{len(rows)}</strong> biggest price drops across all categories.</p>'
        f'<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;border-radius:8px;border:1px solid #e8ecf0;">'
        f'<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;min-width:480px;">'
        f'{thead}<tbody>{"".join(tbody_rows)}</tbody></table></div>'
    )
    header = f"{'Brand':<18} {'Model':<28} {'Cat':<12} {'Was €':>8} {'Now €':>8} {'Saved €':>8} {'Drop %':>7}"
    plain_rows = []
    for r in rows:
        plain_rows.append(
            f"{(r.brand or '')[:18]:<18} {(r.model or '')[:28]:<28} {r.category:<12} "
            f"{float(r.prev_price):>8.2f} {float(r.new_price):>8.2f} "
            f"{abs(float(r.drop_eur)):>8.2f} {abs(float(r.drop_pct)):>7.1f}%"
        )
    plain = (
        f"Top price drops from today's scrape ({datetime.date.today()}):\n\n"
        + header + "\n" + "-" * len(header) + "\n"
        + "\n".join(plain_rows)
    )
    try:
        _send_html_email(
            subject = f"[Skroutz] {len(rows)} price drops today — {datetime.date.today()}",
            html    = _html_shell(
                title    = f"{len(rows)} Price Drops Today",
                subtitle = f"Top deals from today's scrape · {datetime.date.today()}",
                body     = body_html,
            ),
            plain = plain,
        )
        logger.info(f"Drop digest sent — {len(rows)} deals.")
    except Exception as e:
        logger.warning(f"Could not send drop digest: {e}")


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
    if not GMAIL_APP_PASSWORD and not _notif._TOKEN:
        return
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

    _notif.tg_watchlist(hits)

    thead = """
      <thead>
        <tr style="background:#0d1b2a;">
          <th style="padding:10px 10px;text-align:left;font-size:11px;color:#7a8fa6;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Product</th>
          <th style="padding:10px 10px;text-align:center;font-size:11px;color:#7a8fa6;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Type</th>
          <th style="padding:10px 10px;text-align:right;font-size:11px;color:#7a8fa6;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Now</th>
          <th style="padding:10px 10px;text-align:right;font-size:11px;color:#7a8fa6;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Target</th>
          <th style="padding:10px 10px;text-align:right;font-size:11px;color:#7a8fa6;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Below by</th>
          <th style="padding:10px 10px;text-align:center;font-size:11px;color:#7a8fa6;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Link</th>
        </tr>
      </thead>"""
    tbody_rows = []
    for i, h in enumerate(hits):
        bg        = "#f8fafc" if i % 2 == 0 else "#ffffff"
        name      = html.escape((f"{h['brand']} {h['model']}".strip() or h["label"]))
        below_eur = h["threshold"] - h["price"]
        below_pct = 100.0 * below_eur / h["threshold"] if h["threshold"] else 0
        tbody_rows.append(
            f'<tr style="background:{bg};">'
            f'<td style="padding:10px 10px;font-size:13px;color:#111827;font-weight:600;">{name}</td>'
            f'<td style="padding:10px 10px;font-size:12px;color:#374151;text-align:center;">{h["category"]}</td>'
            f'<td style="padding:10px 10px;font-size:13px;color:#1a6b35;font-weight:700;text-align:right;">{h["price"]:.2f}&nbsp;€</td>'
            f'<td style="padding:10px 10px;font-size:12px;color:#9ca3af;text-align:right;">{h["threshold"]:.2f}&nbsp;€</td>'
            f'<td style="padding:10px 10px;text-align:right;">{_pct_badge(below_pct)}</td>'
            f'<td style="padding:10px 10px;text-align:center;">'
            f'<a href="{h["url"]}" style="display:inline-block;background:#0d1b2a;color:#ffffff;font-size:11px;font-weight:600;'
            f'padding:5px 12px;border-radius:6px;text-decoration:none;white-space:nowrap;">View &#x2197;</a></td>'
            f'</tr>'
        )
    body_html = (
        f'<p style="margin:0 0 16px;font-size:14px;color:#374151;">'
        f'<strong>{len(hits)}</strong> watched product(s) are at or below your target price today.</p>'
        f'<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;border-radius:8px;border:1px solid #e8ecf0;">'
        f'<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;min-width:480px;">'
        f'{thead}<tbody>{"".join(tbody_rows)}</tbody></table></div>'
        f'<p style="margin:16px 0 0;font-size:12px;color:#9ca3af;">Edit watchlist.json to change thresholds or remove items.</p>'
    )
    plain_lines = [f"{'Product':<42} {'Now €':>8} {'Target €':>9}", "-" * 62]
    for h in hits:
        name = f"{h['brand']} {h['model']}".strip() or h["label"]
        plain_lines.append(f"{name[:42]:<42} {h['price']:>8.2f} {h['threshold']:>9.2f}")
        plain_lines.append(f"  {h['url']}")
    plain = (
        f"{len(hits)} watchlist item(s) hit their target price on {datetime.date.today()}:\n\n"
        + "\n".join(plain_lines)
    )
    try:
        _send_html_email(
            subject = f"[Skroutz] {len(hits)} price target(s) reached — {datetime.date.today()}",
            html    = _html_shell(
                title    = f"{len(hits)} Price Target(s) Reached",
                subtitle = f"Watched products at or below your threshold · {datetime.date.today()}",
                body     = body_html,
            ),
            plain = plain,
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
    if not GMAIL_APP_PASSWORD and not _notif._TOKEN:
        return
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT brand, model, category, product_name, last_seen, "
                "       (CURRENT_DATE - last_seen) AS days_since_last_seen, skroutz_link "
                "FROM products "
                "WHERE last_seen BETWEEN CURRENT_DATE - 2 AND CURRENT_DATE - 1 "
                "ORDER BY category, last_seen DESC"
            )).fetchall()
    except Exception as e:
        logger.warning(f"Disappeared alert: DB query failed — {e}")
        return

    if not rows:
        logger.info("No newly disappeared products today.")
        return

    _notif.tg_disappeared(rows)

    CAT_ICON = {"phone": "📱", "laptop": "💻", "smartwatch": "⌚", "tablet": "📟"}
    thead = """
      <thead>
        <tr style="background:#0d1b2a;">
          <th style="padding:10px 10px;text-align:left;font-size:11px;color:#7a8fa6;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Brand</th>
          <th style="padding:10px 10px;text-align:left;font-size:11px;color:#7a8fa6;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Model</th>
          <th style="padding:10px 8px;text-align:center;font-size:11px;color:#7a8fa6;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Type</th>
          <th style="padding:10px 10px;text-align:center;font-size:11px;color:#7a8fa6;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Last Seen</th>
          <th style="padding:10px 10px;text-align:center;font-size:11px;color:#7a8fa6;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Days Gone</th>
          <th style="padding:10px 10px;text-align:center;font-size:11px;color:#7a8fa6;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Link</th>
        </tr>
      </thead>"""
    tbody_rows = []
    for i, r in enumerate(rows):
        bg    = "#f8fafc" if i % 2 == 0 else "#ffffff"
        brand = html.escape(str(r.brand or "—"))
        model = html.escape(str(r.model or r.product_name or "—"))
        days  = int(r.days_since_last_seen) if r.days_since_last_seen else "?"
        days_badge = (
            f'<span style="background:#fff3cd;color:#856404;padding:2px 7px;border-radius:10px;'
            f'font-size:11px;font-weight:700;">{days}d</span>'
        )
        tbody_rows.append(
            f'<tr style="background:{bg};">'
            f'<td style="padding:10px 10px;font-size:13px;color:#111827;font-weight:600;">{brand}</td>'
            f'<td style="padding:10px 10px;font-size:12px;color:#374151;">{model}</td>'
            f'<td style="padding:10px 8px;font-size:14px;text-align:center;">{CAT_ICON.get(r.category, "")}</td>'
            f'<td style="padding:10px 10px;font-size:12px;color:#374151;text-align:center;">{r.last_seen}</td>'
            f'<td style="padding:10px 10px;text-align:center;">{days_badge}</td>'
            f'<td style="padding:10px 10px;text-align:center;">'
            f'<a href="{r.skroutz_link}" style="display:inline-block;background:#0d1b2a;color:#ffffff;font-size:11px;font-weight:600;'
            f'padding:5px 12px;border-radius:6px;text-decoration:none;white-space:nowrap;">View &#x2197;</a></td>'
            f'</tr>'
        )
    note = (
        '<div style="margin-top:18px;padding:13px 16px;background:#fffbeb;border-left:3px solid #f59e0b;border-radius:4px;">'
        '<p style="margin:0;font-size:12px;color:#92400e;">These products have not appeared in any scrape for 1–2 days. '
        'They may be discontinued, out of stock, or temporarily unlisted.</p></div>'
    )
    body_html = (
        f'<p style="margin:0 0 16px;font-size:14px;color:#374151;">'
        f'<strong>{len(rows)}</strong> product(s) were not seen in the last 1–2 days.</p>'
        f'<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;border-radius:8px;border:1px solid #e8ecf0;">'
        f'<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;min-width:480px;">'
        f'{thead}<tbody>{"".join(tbody_rows)}</tbody></table></div>{note}'
    )
    header = f"{'Brand':<18} {'Model':<28} {'Cat':<12} {'Last seen':>10}"
    plain_lines = [header, "-" * len(header)]
    for r in rows:
        plain_lines.append(
            f"{(r.brand or '')[:18]:<18} {(r.model or r.product_name or '')[:28]:<28} "
            f"{r.category:<12} {str(r.last_seen):>10}"
        )
        plain_lines.append(f"  {r.skroutz_link}")
    plain = (
        f"{len(rows)} product(s) disappeared from Skroutz in the last 2 days "
        f"({datetime.date.today()}):\n\n" + "\n".join(plain_lines)
    )
    try:
        _send_html_email(
            subject = f"[Skroutz] {len(rows)} product(s) disappeared — {datetime.date.today()}",
            html    = _html_shell(
                title    = f"{len(rows)} Products Disappeared",
                subtitle = f"Not seen in the last 1–2 days · {datetime.date.today()}",
                body     = body_html,
            ),
            plain = plain,
        )
        logger.info(f"Disappeared alert sent — {len(rows)} product(s).")
    except Exception as e:
        logger.warning(f"Disappeared alert: could not send email — {e}")


def run_dashboard():
    """Generate the HTML dashboard. Non-fatal — pipeline result is unaffected if this fails."""
    logger.info("=== Dashboard started ===")
    t = datetime.datetime.now()
    result = subprocess.run([sys.executable, os.path.join(BASE, "generate_dashboard.py")])
    elapsed = (datetime.datetime.now() - t).total_seconds()
    if result.returncode != 0:
        logger.warning(f"Dashboard generation failed after {elapsed:.0f}s — pipeline result is unaffected.")
    else:
        logger.info(f"=== Dashboard complete in {elapsed:.0f}s ===")


def update_readme_stats():
    """
    Rewrite the auto-generated stat block(s) in README.md — product/snapshot totals,
    per-category table, last pipeline run date. Non-fatal — pipeline result is
    unaffected if this fails. Same simple direct-count SQL style as send_success_summary().
    """
    logger.info("=== README stats started ===")
    t = datetime.datetime.now()
    readme_path = os.path.join(BASE, "README.md")
    try:
        engine = get_engine()
        with engine.connect() as conn:
            total_products  = conn.execute(text("SELECT COUNT(*) FROM products")).scalar()
            total_snapshots = conn.execute(text("SELECT COUNT(*) FROM price_snapshots")).scalar()
            rows = conn.execute(text(
                "SELECT p.category, "
                "       COUNT(DISTINCT p.id)    AS products, "
                "       COUNT(s.id)              AS snapshots, "
                "       ROUND(AVG(s.price_eur))  AS avg_price, "
                "       ROUND(MIN(s.price_eur))  AS min_price, "
                "       ROUND(MAX(s.price_eur))  AS max_price, "
                "       COUNT(DISTINCT p.brand)  AS brands "
                "FROM products p "
                "JOIN price_snapshots s ON s.product_id = p.id "
                "GROUP BY p.category ORDER BY p.category"
            )).fetchall()
    except Exception as e:
        logger.warning(f"README stats: DB query failed — {e}")
        return

    def _abbrev(n):
        return f"{round(n / 1000)}k" if n >= 1000 else str(n)

    products_badge  = f'![Products](https://img.shields.io/badge/Products-{total_products:,}-blue?style=flat-square)'.replace(",", "%2C")
    snapshots_badge = f'![Snapshots](https://img.shields.io/badge/Snapshots-{_abbrev(total_snapshots)}-green?style=flat-square)'
    badges_block    = f"{products_badge}\n{snapshots_badge}"

    CAT_LABELS = {"laptop": "Laptop", "phone": "Phone", "smartwatch": "Smartwatch", "tablet": "Tablet"}
    table_rows = []
    for r in rows:
        label  = CAT_LABELS.get(r.category, r.category.capitalize())
        avg_p  = f"€{int(r.avg_price):,}" if r.avg_price is not None else "—"
        rng    = f"€{int(r.min_price):,}–€{int(r.max_price):,}" if r.min_price is not None else "—"
        # Smartwatch brand extraction is unreliable (garbage/placeholder values inflate
        # the distinct count into the hundreds) — omit rather than show a misleading number.
        brands = str(r.brands) if r.brands and r.category != "smartwatch" else "—"
        table_rows.append(f"| {label} | {r.products:,} | {r.snapshots:,} | {avg_p} | {rng} | {brands} |")
    table_rows.append(f"| **Total** | **{total_products:,}** | **{total_snapshots:,}** | | | |")

    today_str   = datetime.date.today().isoformat()
    table_block = (
        "| Category | Products | Snapshots | Avg Price | Range | Brands |\n"
        "|---|---|---|---|---|---|\n"
        + "\n".join(table_rows)
        + f"\n\nUpdated daily via Task Scheduler · last pipeline run: {today_str}"
    )

    try:
        with open(readme_path, "r", encoding="utf-8") as f:
            content = f.read()
        content = re.sub(
            r"(<!-- STATS:BADGES:START -->\n).*?(\n<!-- STATS:BADGES:END -->)",
            lambda m: m.group(1) + badges_block + m.group(2),
            content, count=1, flags=re.DOTALL,
        )
        content = re.sub(
            r"(<!-- STATS:TABLE:START -->\n).*?(\n<!-- STATS:TABLE:END -->)",
            lambda m: m.group(1) + table_block + m.group(2),
            content, count=1, flags=re.DOTALL,
        )
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        logger.warning(f"README stats: could not rewrite README.md — {e}")
        return

    elapsed = (datetime.datetime.now() - t).total_seconds()
    logger.info(
        f"=== README stats complete in {elapsed:.0f}s — "
        f"{total_products:,} products, {total_snapshots:,} snapshots ==="
    )


def publish_artifacts():
    """
    Commit today's refreshed artifacts (charts, dashboard, README stats) and
    push to origin so the GitHub Pages dashboard updates daily without a
    manual push. Non-fatal — pipeline result is unaffected if this fails.
    Only whitelisted artifact paths are staged; if the index already holds
    unrelated staged changes (work in progress), skip entirely rather than
    sweep them into an automated commit.
    """
    logger.info("=== Publish artifacts started ===")
    t = datetime.datetime.now()

    def _git(*args, timeout=120):
        return subprocess.run(
            ["git", "-C", BASE, *args],
            capture_output=True, text=True, timeout=timeout,
        )

    if _git("diff", "--cached", "--quiet").returncode != 0:
        logger.warning("Publish artifacts: index already has staged changes — skipping auto-commit.")
        return
    branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != "main":
        logger.warning(f"Publish artifacts: on branch '{branch}', not main — skipping auto-commit.")
        return
    add = _git("add", "--", "charts", "dashboard/dashboard_latest.html", "README.md")
    if add.returncode != 0:
        logger.warning(f"Publish artifacts: git add failed — {add.stderr.strip()}")
        return
    if _git("diff", "--cached", "--quiet").returncode == 0:
        logger.info("Publish artifacts: no artifact changes to commit.")
        return
    msg = f"chore: refresh charts, dashboard and README stats from {datetime.date.today()} run"
    commit = _git("commit", "-m", msg)
    if commit.returncode != 0:
        logger.warning(f"Publish artifacts: git commit failed — {commit.stderr.strip()}")
        return
    push = _git("push", "origin", "main", timeout=300)
    elapsed = (datetime.datetime.now() - t).total_seconds()
    if push.returncode != 0:
        logger.warning(f"Publish artifacts: git push failed after {elapsed:.0f}s — {push.stderr.strip()}")
        _notif.tg_send(
            "⚠️ <b>Artifact push failed</b>\n"
            "Committed locally but not pushed — the public GitHub Pages dashboard is stale.\n"
            f"<code>{html.escape(push.stderr.strip()[:300])}</code>"
        )
        return
    logger.info(f"=== Publish artifacts complete in {elapsed:.0f}s — pushed to origin/main ===")


def send_success_summary(elapsed):
    """Email + Telegram daily summary after a successful pipeline run."""
    if not GMAIL_APP_PASSWORD and not _notif._TOKEN:
        return
    try:
        engine = get_engine()
        with engine.connect() as conn:
            snaps          = conn.execute(text("SELECT COUNT(*) FROM price_snapshots WHERE date = CURRENT_DATE")).scalar()
            new_prods      = conn.execute(text("SELECT COUNT(*) FROM products WHERE first_seen = CURRENT_DATE")).scalar()
            drops          = conn.execute(text("SELECT COUNT(*) FROM vw_biggest_drops WHERE drop_date = CURRENT_DATE")).scalar()
            yesterday_snaps = conn.execute(text("SELECT COUNT(*) FROM price_snapshots WHERE date = CURRENT_DATE - 1")).scalar() or 0
    except Exception as e:
        logger.warning(f"Success summary: DB query failed — {e}")
        return
    if yesterday_snaps > 0 and snaps < yesterday_snaps * 0.7:
        logger.warning(
            f"ANOMALY: Today {snaps:,} snapshots vs yesterday {yesterday_snaps:,} — "
            f"possible partial scrape ({100 * snaps // yesterday_snaps}%)"
        )
        _notif.tg_send(
            f"⚠️ <b>Snapshot anomaly detected</b>\n"
            f"Today: <b>{snaps:,}</b> vs yesterday: <b>{yesterday_snaps:,}</b>\n"
            f"Coverage: <b>{100 * snaps // yesterday_snaps}%</b> — possible partial scrape"
        )
    elapsed_str = str(elapsed).split(".")[0]  # trim microseconds
    _notif.tg_success(snaps, new_prods, drops, elapsed_str)
    now_str     = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    def _stat_card(value, label, color="#0d1b2a"):
        return (
            f'<td style="width:25%;padding:0 6px;text-align:center;">'
            f'<div style="background:#f8fafc;border:1px solid #e8ecf0;border-radius:8px;padding:16px 8px;">'
            f'<p style="margin:0;font-size:22px;font-weight:700;color:{color};">{value}</p>'
            f'<p style="margin:4px 0 0;font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.8px;">{label}</p>'
            f'</div></td>'
        )

    cards = (
        '<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:separate;border-spacing:0;">'
        '<tr>'
        + _stat_card(f"{snaps:,}",     "Snapshots",    "#0d1b2a")
        + _stat_card(f"+{new_prods:,}", "New Products", "#1a6b35")
        + _stat_card(str(drops),       "Price Drops",  "#e63946")
        + _stat_card(elapsed_str,      "Duration",     "#6b7280")
        + '</tr></table>'
    )
    body_html = (
        f'<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:14px 18px;margin-bottom:22px;">'
        f'<p style="margin:0;font-size:14px;color:#166534;font-weight:600;">&#x2713; Pipeline completed successfully</p>'
        f'<p style="margin:4px 0 0;font-size:12px;color:#166534;">{now_str}</p>'
        f'</div>'
        f'{cards}'
        f'<p style="margin:18px 0 0;font-size:12px;color:#9ca3af;">'
        f'Log: <code style="background:#f3f4f6;padding:1px 5px;border-radius:3px;">'
        f'logs/pipeline_{datetime.date.today()}.log</code></p>'
    )
    plain = (
        f"Daily pipeline completed successfully in {elapsed_str}.\n\n"
        f"  Snapshots loaded : {snaps:,}\n"
        f"  New products     : {new_prods:,}\n"
        f"  Price drops today: {drops}\n\n"
        f"Date: {now_str}\n"
        f"Log:  logs/pipeline_{datetime.date.today()}.log\n"
    )
    try:
        _send_html_email(
            subject = f"[Skroutz] Pipeline OK — {datetime.date.today()}",
            html    = _html_shell(
                title    = "Pipeline Completed",
                subtitle = f"All stages passed · {elapsed_str} total · {now_str}",
                body     = body_html,
            ),
            plain = plain,
        )
    except Exception as e:
        logger.warning(f"Success summary: could not send email — {e}")
        return
    logger.info(f"Success summary sent — {snaps:,} snapshots, {new_prods:,} new products, {drops} drops.")


def run_stage(label, script, fatal=True):
    """
    Run a single pipeline stage as a subprocess.
    Fatal stage failure: sends an alert email then exits, so downstream stages
    never run against incomplete input data.
    Observer stage failure: logs + Telegram warning, pipeline continues.
    """
    logger.info(f"=== {label} started ===")
    t = datetime.datetime.now()
    result = subprocess.run([sys.executable, script])
    elapsed = (datetime.datetime.now() - t).total_seconds()
    if result.returncode != 0:
        if fatal:
            logger.error(f"{label} failed (exit {result.returncode}) after {elapsed:.0f}s. Aborting pipeline.")
            send_failure_alert(label, result.returncode)
            sys.exit(result.returncode)
        logger.error(f"{label} failed (exit {result.returncode}) after {elapsed:.0f}s. Observer stage — continuing.")
        _notif.tg_send(
            f"⚠️ <b>{html.escape(label)} failed</b> (exit {result.returncode})\n"
            f"Observer stage — pipeline continues. Check logs/pipeline_{datetime.date.today()}.log"
        )
        return
    logger.info(f"=== {label} complete in {elapsed:.0f}s ===")


if __name__ == "__main__":
    if not _acquire_lock():
        logger.warning("Another pipeline instance is already running — exiting.")
        _notif.tg_send(
            "⚠️ <b>Pipeline skipped</b>\n"
            "Another instance is already running (pipeline.lock present)."
        )
        sys.exit(0)
    try:
        start = datetime.datetime.now()
        _cleanup_old_logs()
        _notif.tg_pipeline_start()
        try:
            with get_engine().connect() as _conn:
                _last_date = _conn.execute(text("SELECT MAX(date) FROM price_snapshots")).scalar()
            if _last_date and (datetime.date.today() - _last_date).days > 1:
                _gap = (datetime.date.today() - _last_date).days
                logger.warning(f"Pipeline gap: last run was {_last_date} ({_gap} days ago)")
                _notif.tg_send(
                    f"⚠️ <b>Pipeline gap detected</b>\n"
                    f"Last successful run: <b>{_last_date}</b> ({_gap} days ago)\n"
                    f"Data may be stale — check Task Scheduler."
                )
        except Exception:
            pass
        for label, script, fatal in STAGES:
            run_stage(label, script, fatal)
        for _fn, _label in [
            (refresh_matviews,        "Matview refresh"),
            (run_charts,              "Charts"),
            (send_drop_digest,        "Drop digest"),
            (send_watchlist_alerts,   "Watchlist alerts"),
            (send_disappeared_alert,  "Disappeared alert"),
            (run_dashboard,           "Dashboard"),
            (update_readme_stats,     "README stats"),
            (publish_artifacts,       "Publish artifacts"),
        ]:
            try:
                _fn()
            except Exception as _e:
                logger.error(f"{_label} raised an unhandled exception: {_e}")
                _notif.tg_send(f"⚠️ <b>{_label} failed</b>\n<code>{_e}</code>")
        elapsed = datetime.datetime.now() - start
        send_success_summary(elapsed)
        logger.info(f"Pipeline finished in {elapsed}")
    finally:
        _release_lock()

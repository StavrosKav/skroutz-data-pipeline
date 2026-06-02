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
import notifications as _notif

load_dotenv()

# Resolve script paths relative to this file so the pipeline works from any working directory
BASE = os.path.dirname(os.path.abspath(__file__))

os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
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
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
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


def run_charts():
    """Regenerate price trend charts. Non-fatal — pipeline result is unaffected if this fails."""
    logger.info("=== Charts started ===")
    result = subprocess.run([sys.executable, os.path.join(BASE, "charts_from_db.py")])
    if result.returncode != 0:
        logger.warning("Charts step failed — pipeline result is unaffected.")
    else:
        logger.info("=== Charts complete ===")


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
                "ORDER BY drop_eur ASC LIMIT 10"
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
        bg = "#f8fafc" if i % 2 == 0 else "#ffffff"
        tbody_rows.append(
            f'<tr style="background:{bg};">'
            f'<td style="padding:10px 10px;font-size:13px;color:#111827;font-weight:600;">{r.brand or "—"}</td>'
            f'<td style="padding:10px 10px;font-size:12px;color:#374151;">{r.model or "—"}</td>'
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
            f"{abs(float(r.drop_eur)):>8.2f} {float(r.drop_pct):>7.1f}%"
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
        bg   = "#f8fafc" if i % 2 == 0 else "#ffffff"
        name = f"{h['brand']} {h['model']}".strip() or h["label"]
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
        model = (r.model or r.product_name or "—")
        days  = int(r.days_since_last_seen) if r.days_since_last_seen else "?"
        days_badge = (
            f'<span style="background:#fff3cd;color:#856404;padding:2px 7px;border-radius:10px;'
            f'font-size:11px;font-weight:700;">{days}d</span>'
        )
        tbody_rows.append(
            f'<tr style="background:{bg};">'
            f'<td style="padding:10px 10px;font-size:13px;color:#111827;font-weight:600;">{r.brand or "—"}</td>'
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
    result = subprocess.run([sys.executable, os.path.join(BASE, "generate_dashboard.py")])
    if result.returncode != 0:
        logger.warning("Dashboard generation failed — pipeline result is unaffected.")
    else:
        logger.info("=== Dashboard complete ===")


def send_success_summary(elapsed):
    """Email + Telegram daily summary after a successful pipeline run."""
    if not GMAIL_APP_PASSWORD and not _notif._TOKEN:
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


def run_dbt_tests():
    """
    Run dbt tests after the Load SQL stage.
    Aborts the pipeline if any test fails so the dashboard never renders corrupt data.
    Skipped silently if dbt is not installed or dbt_project/ does not exist.
    """
    dbt_dir = os.path.join(BASE, "dbt_project")
    if not os.path.isdir(dbt_dir):
        return
    import shutil
    if not shutil.which("dbt"):
        logger.warning("dbt not found on PATH — skipping data quality tests. Install with: pip install dbt-postgres")
        return
    logger.info("=== dbt tests started ===")
    env = {**os.environ, "DBT_PROFILES_DIR": dbt_dir}
    result = subprocess.run(
        ["dbt", "test", "--project-dir", dbt_dir, "--profiles-dir", dbt_dir],
        env=env,
    )
    if result.returncode != 0:
        logger.error("dbt tests FAILED — aborting pipeline to protect dashboard integrity.")
        send_failure_alert("dbt test", result.returncode)
        sys.exit(result.returncode)
    logger.info("=== dbt tests passed ===")


if __name__ == "__main__":
    start = datetime.datetime.now()
    _notif.tg_pipeline_start()
    for label, script in STAGES:
        run_stage(label, script)
    run_dbt_tests()
    run_charts()
    send_drop_digest()
    send_watchlist_alerts()
    send_disappeared_alert()
    run_dashboard()
    elapsed = datetime.datetime.now() - start
    logger.info(f"Pipeline finished in {elapsed}")

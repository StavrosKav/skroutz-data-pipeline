"""
telegram_bot.py
---------------
Interactive Telegram bot for the Skroutz Price Tracker.

Commands:
  /status         — last pipeline run result (tail of today's log)
  /drops          — today's top 10 price drops from the DB
  /watchlist      — numbered watchlist with live prices
  /add <url> <€>  — add a product to the watchlist
  /remove <n>     — remove item #n from the watchlist
  /find <name>    — search products by name
  /stats          — database snapshot counts
  /cancel         — cancel any in-progress conversation
  /help           — list all commands

Conversation flow (easiest way to add):
  Send any skroutz.gr URL → bot looks it up and asks for your target price
  Reply with a number     → bot adds it to the watchlist

Run separately from the pipeline (e.g. a terminal or Task Scheduler):
  python telegram_bot.py

Uses long-polling — no public URL or webhook needed.
Only responds to TELEGRAM_CHAT_ID to block unauthorized access.
"""

import datetime
import html
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

from db import get_engine

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [%(levelname)s] %(message)s",
    datefmt= "%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE     = os.path.dirname(os.path.abspath(__file__))
TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID",  "")
_API     = f"https://api.telegram.org/bot{TOKEN}"
_WL_PATH = os.path.join(BASE, "watchlist.json")

# Conversation state: chat_id → {url, label, current_price}
# Tracks users mid-flow waiting to supply a target price.
_pending: dict = {}


# ── Core network helpers ───────────────────────────────────────────────────────

def _e(text) -> str:
    return html.escape(str(text))


def _post(method: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        f"{_API}/{method}", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=35) as r:
            return json.loads(r.read())
    except Exception as e:
        logger.warning(f"{method} failed: {e}")
        return {}


def _send(text_: str, chat_id: str = None, reply_markup=None) -> None:
    payload = {
        "chat_id":    chat_id or CHAT_ID,
        "text":       text_,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    _post("sendMessage", payload)


def _get_updates(offset: int) -> list:
    return _post("getUpdates", {
        "offset":          offset,
        "timeout":         30,
        "allowed_updates": ["message"],
    }).get("result", [])


# ── Watchlist I/O ──────────────────────────────────────────────────────────────

def _wl_read() -> list:
    if not os.path.exists(_WL_PATH):
        return []
    with open(_WL_PATH, encoding="utf-8") as f:
        return json.load(f)


def _wl_write(items: list) -> None:
    with open(_WL_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def _url_clean(url: str) -> str:
    """Strip query params and trailing slash for consistent storage/matching."""
    return url.split("?")[0].rstrip("/")


def _url_slug_label(url: str) -> str:
    """Derive a readable label from the URL slug when the product isn't in the DB."""
    slug = _url_clean(url).split("/")[-1]
    slug = re.sub(r"\.html?$", "", slug)
    return slug.replace("-", " ").title()


def _lookup_product(url: str) -> tuple:
    """
    Query DB for the product's brand+model and current price.
    Returns (label, price_eur) — label falls back to the URL slug if not found.
    """
    clean = _url_clean(url)
    try:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT brand, model, price_eur FROM vw_latest_prices "
                "WHERE skroutz_link = :url OR skroutz_link LIKE :pfx"
            ), {"url": clean, "pfx": clean + "%"}).fetchone()
        if row:
            label = f"{row.brand or ''} {row.model or ''}".strip()
            return label or _url_slug_label(url), float(row.price_eur)
    except Exception as e:
        logger.warning(f"Product lookup failed: {e}")
    return _url_slug_label(url), None


def _do_add(url: str, threshold: float) -> str:
    """
    Add url+threshold to watchlist.json.
    If the URL already exists, updates the threshold instead of duplicating.
    """
    clean         = _url_clean(url)
    label, current = _lookup_product(url)
    items         = _wl_read()

    for item in items:
        if _url_clean(item.get("url", "")) == clean:
            old = item["threshold_eur"]
            item["threshold_eur"] = threshold
            item["label"]         = label
            _wl_write(items)
            return (
                f"✏️ <b>Updated:</b> {_e(label)}\n"
                f"Target changed: {old:.0f}€ → <b>{threshold:.0f}€</b>"
            )

    items.append({"url": clean, "label": label, "threshold_eur": threshold})
    _wl_write(items)

    price_line = f"  Currently <b>{current:.0f}€</b>\n" if current else ""
    gap        = current - threshold if current else None
    gap_line   = f"  <i>Need a {gap:.0f}€ drop ({100*gap/current:.0f}%)</i>\n" if gap and gap > 0 else ""
    return (
        f"✅ <b>Added to watchlist!</b>\n\n"
        f"<b>{_e(label)}</b>\n"
        f"{price_line}"
        f"{gap_line}"
        f"  Target: <b>{threshold:.0f}€</b>"
    )


# ── Command handlers ───────────────────────────────────────────────────────────

def _cmd_status() -> str:
    today    = datetime.date.today()
    log_file = os.path.join(BASE, "logs", f"pipeline_{today}.log")
    if not os.path.exists(log_file):
        return f"⚠️ No pipeline log found for today ({today})."

    with open(log_file, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    last = "".join(lines[-20:])
    if "Pipeline finished" in last:
        status = "✅ Completed"
    elif "FAILED" in last or "failed" in last:
        status = "❌ Failed"
    else:
        status = "⏳ In progress or incomplete"

    tail = "\n".join(
        f"<code>{_e(l.strip())}</code>" for l in lines[-6:] if l.strip()
    )
    return f"📋 <b>Pipeline status — {today}</b>\n{status}\n\n{tail}"


def _cmd_drops() -> str:
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT brand, model, category, prev_price, new_price, drop_eur, drop_pct "
                "FROM vw_biggest_drops WHERE drop_date = CURRENT_DATE "
                "ORDER BY drop_eur ASC LIMIT 10"
            )).fetchall()
    except Exception as e:
        return f"❌ DB error: {_e(str(e))}"

    if not rows:
        return "No price drops recorded today."

    CAT_ICON = {"phone": "📱", "laptop": "💻", "smartwatch": "⌚", "tablet": "📟"}
    lines = [f"<b>Top {len(rows)} drops today:</b>\n"]
    for r in rows:
        icon = CAT_ICON.get(r.category, "🏷️")
        lines.append(
            f"{icon} <b>{_e(r.brand or '')} {_e(r.model or '')}</b>\n"
            f"  {float(r.prev_price):.0f}€ → <b>{float(r.new_price):.0f}€</b>"
            f"  (-{abs(float(r.drop_eur)):.0f}€ / {float(r.drop_pct):.1f}%)"
        )
    return "\n".join(lines)


def _cmd_watchlist() -> str:
    items = _wl_read()
    if not items:
        return (
            "Watchlist is empty.\n\n"
            "Send any skroutz.gr URL to add a product,\n"
            "or use <code>/add &lt;url&gt; &lt;price&gt;</code>"
        )
    try:
        engine = get_engine()
        count  = len(items)
        lines  = [f"<b>Watchlist — {count} item{'s' if count != 1 else ''}:</b>\n"]
        with engine.connect() as conn:
            for i, item in enumerate(items, 1):
                url       = item.get("url", "").strip()
                label     = item.get("label", url)
                threshold = float(item.get("threshold_eur", 0))
                row = conn.execute(text(
                    "SELECT brand, model, price_eur FROM vw_latest_prices "
                    "WHERE skroutz_link = :url OR skroutz_link LIKE :pfx"
                ), {"url": url, "pfx": url + "%"}).fetchone()

                if row:
                    price = float(row.price_eur)
                    flag  = "✅" if price <= threshold else "⏳"
                    name  = _e(f"{row.brand or ''} {row.model or ''}".strip() or label)
                    diff  = price - threshold
                    hint  = f"<i>({diff:+.0f}€)</i>"
                    lines.append(f"{i}. {flag} <b>{name}</b>  {price:.0f}€ → {threshold:.0f}€  {hint}")
                else:
                    lines.append(f"{i}. ❓ <b>{_e(label)}</b>  target {threshold:.0f}€  <i>(not in DB)</i>")

        lines.append("\n<i>/remove &lt;number&gt; to delete  ·  send a URL to add</i>")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ DB error: {_e(str(e))}"


def _cmd_add(args: str) -> str:
    parts = args.strip().split()
    if len(parts) < 2:
        return (
            "Usage: <code>/add &lt;skroutz_url&gt; &lt;target_price&gt;</code>\n\n"
            "Or just send a skroutz.gr URL and I'll guide you."
        )
    url = parts[0]
    if "skroutz.gr" not in url:
        return "❌ URL must be from skroutz.gr"
    try:
        threshold = float(parts[1].replace(",", ".").replace("€", ""))
    except ValueError:
        return "❌ Invalid price.\nExample: <code>/add https://www.skroutz.gr/s/... 299</code>"
    return _do_add(url, threshold)


def _cmd_remove(args: str) -> str:
    try:
        n = int(args.strip())
    except ValueError:
        return "Usage: <code>/remove &lt;number&gt;</code>\nSee /watchlist for item numbers."

    items = _wl_read()
    if not items:
        return "Watchlist is already empty."
    if n < 1 or n > len(items):
        return f"❌ No item #{n}. Watchlist has {len(items)} item(s). See /watchlist."

    removed   = items.pop(n - 1)
    _wl_write(items)
    label     = removed.get("label", removed.get("url", "?"))
    threshold = removed.get("threshold_eur", 0)
    return f"🗑 Removed: <b>{_e(label)}</b>  (target was {threshold:.0f}€)"


def _cmd_find(args: str) -> str:
    q = args.strip()
    if not q:
        return "Usage: <code>/find &lt;name&gt;</code>\nExample: <code>/find galaxy s25</code>"
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT brand, model, category, price_eur, skroutz_link "
                "FROM vw_latest_prices "
                "WHERE brand ILIKE :q OR model ILIKE :q "
                "   OR (brand || ' ' || model) ILIKE :q "
                "ORDER BY price_eur "
                "LIMIT 5"
            ), {"q": f"%{q}%"}).fetchall()
    except Exception as e:
        return f"❌ DB error: {_e(str(e))}"

    if not rows:
        return f"No products found matching <b>{_e(q)}</b>."

    CAT_ICON = {"phone": "📱", "laptop": "💻", "smartwatch": "⌚", "tablet": "📟"}
    lines = [f"🔍 <b>Results for \"{_e(q)}\":</b>\n"]
    for r in rows:
        icon = CAT_ICON.get(r.category, "🏷️")
        lines.append(
            f"{icon} <b>{_e(r.brand or '')} {_e(r.model or '')}</b>  "
            f"<b>{float(r.price_eur):.0f}€</b>"
        )
    if len(rows) == 5:
        lines.append("\n<i>Top 5 shown — refine search for more specific results.</i>")
    lines.append("\n<i>Send a product URL to add it to your watchlist.</i>")
    return "\n".join(lines)


def _cmd_best(args: str) -> str:
    """Products closest to their all-time low. Optional category filter."""
    cat = args.strip().lower()
    CAT_ALIASES = {
        "phone": "phone", "phones": "phone",
        "laptop": "laptop", "laptops": "laptop",
        "smartwatch": "smartwatch", "smartwatches": "smartwatch", "watches": "smartwatch",
        "tablet": "tablet", "tablets": "tablet",
    }
    CAT_ICON = {"phone": "📱", "laptop": "💻", "smartwatch": "⌚", "tablet": "📟"}

    resolved = CAT_ALIASES.get(cat)
    if cat and not resolved:
        return (
            "❌ Unknown category.\n"
            "Use: <code>phones</code>, <code>laptops</code>, "
            "<code>smartwatches</code>, <code>tablets</code>\n"
            "Or just <code>/best</code> for all categories."
        )

    where = "AND lp.category = :cat" if resolved else ""
    params = {"cat": resolved} if resolved else {}

    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT lp.brand, lp.model, lp.category, lp.price_eur,
                       pf.all_time_low,
                       ROUND(
                           100.0 * (lp.price_eur - pf.all_time_low)
                           / NULLIF(pf.all_time_low, 0), 1
                       ) AS pct_above_atl
                FROM vw_latest_prices lp
                JOIN vw_price_floor pf ON pf.product_id = lp.id
                WHERE lp.price_eur > 50
                  AND pf.all_time_low > 0
                  AND pf.snapshot_count >= 10
                  AND (pf.all_time_high - pf.all_time_low) >= 20
                  {where}
                ORDER BY pct_above_atl ASC,
                         (pf.all_time_high - pf.all_time_low) DESC
                LIMIT 8
            """), params).fetchall()
    except Exception as e:
        return f"❌ DB error: {_e(str(e))}"

    if not rows:
        return "No data found. Run the pipeline at least a few times to build price history."

    cat_label = resolved.title() + "s" if resolved else "all categories"
    lines = [f"🏆 <b>Closest to all-time low — {cat_label}:</b>\n"]
    for r in rows:
        icon    = CAT_ICON.get(r.category, "🏷️")
        pct     = float(r.pct_above_atl)
        atl     = float(r.all_time_low)
        price   = float(r.price_eur)
        if pct < 1.0:
            badge = "🔥 <b>AT ATL</b>"
        elif pct < 5.0:
            badge = f"<b>+{pct:.1f}%</b> above ATL"
        else:
            badge = f"+{pct:.1f}% above ATL"
        lines.append(
            f"{icon} <b>{_e(r.brand or '')} {_e(r.model or '')}</b>\n"
            f"  <b>{price:.0f}€</b>  |  ATL {atl:.0f}€  |  {badge}"
        )
    lines.append("\n<i>ATL = all-time low in the database · min. 5 snapshots required</i>")
    return "\n".join(lines)


def _cmd_stats() -> str:
    try:
        engine = get_engine()
        with engine.connect() as conn:
            total_p  = conn.execute(text("SELECT COUNT(*) FROM products")).scalar()
            total_s  = conn.execute(text("SELECT COUNT(*) FROM price_snapshots")).scalar()
            today_s  = conn.execute(text("SELECT COUNT(*) FROM price_snapshots WHERE date = CURRENT_DATE")).scalar()
            today_d  = conn.execute(text("SELECT COUNT(*) FROM vw_biggest_drops WHERE drop_date = CURRENT_DATE")).scalar()
            last_run = conn.execute(text("SELECT MAX(date) FROM price_snapshots")).scalar()
    except Exception as e:
        return f"❌ DB error: {_e(str(e))}"

    return (
        f"📊 <b>Database stats</b>\n\n"
        f"Products tracked:  <b>{total_p:,}</b>\n"
        f"Total snapshots:   <b>{total_s:,}</b>\n"
        f"Today's snapshots: <b>{today_s:,}</b>\n"
        f"Today's drops:     <b>{today_d}</b>\n"
        f"Last run:          <b>{last_run}</b>"
    )


def _cmd_help() -> str:
    return (
        "🤖 <b>Skroutz Price Tracker Bot</b>\n\n"
        "<b>Pipeline</b>\n"
        "/status         — last pipeline run result\n"
        "/drops          — today's top price drops\n"
        "/stats          — database snapshot counts\n\n"
        "<b>Watchlist</b>\n"
        "/watchlist      — numbered list with live prices\n"
        "/add &lt;url&gt; &lt;€&gt;  — add a product\n"
        "/remove &lt;n&gt;     — remove item #n\n"
        "/cancel         — cancel in-progress flow\n\n"
        "<b>Search &amp; Discovery</b>\n"
        "/find &lt;name&gt;    — search products by name\n"
        "/best           — top deals closest to all-time low\n"
        "/best &lt;cat&gt;   — filter by phones/laptops/smartwatches/tablets\n\n"
        "<i>Tip: send any skroutz.gr URL and I'll guide you through adding it.</i>"
    )


# ── Conversation flow ──────────────────────────────────────────────────────────

def _handle_url(url: str, chat_id: str) -> str:
    label, current = _lookup_product(url)
    _pending[chat_id] = {"url": url, "label": label, "current_price": current}

    price_line = f"Currently <b>{current:.0f}€</b>\n\n" if current else "Not yet in the database.\n\n"
    return (
        f"🔍 <b>{_e(label)}</b>\n"
        f"{price_line}"
        f"What's your target price? Send a number in €\n"
        f"<i>(/cancel to abort)</i>"
    )


def _handle_price_reply(text_: str, chat_id: str) -> str:
    try:
        threshold = float(text_.replace(",", ".").replace("€", "").strip())
    except ValueError:
        return (
            "❌ That doesn't look like a price.\n"
            "Send a number like <code>299</code> or <code>299.99</code>"
        )
    pending = _pending.pop(chat_id, None)
    if not pending:
        return "No pending product. Send a skroutz.gr URL first."
    return _do_add(pending["url"], threshold)


# ── Message dispatcher ─────────────────────────────────────────────────────────

def _dispatch(text_: str, chat_id: str) -> None:
    if text_.startswith("/"):
        _pending.pop(chat_id, None)
        parts = text_.split(None, 1)
        cmd   = parts[0].lower()
        args  = parts[1] if len(parts) > 1 else ""

        dispatch_map = {
            "/start":     lambda: _cmd_help(),
            "/help":      lambda: _cmd_help(),
            "/status":    lambda: _cmd_status(),
            "/drops":     lambda: _cmd_drops(),
            "/watchlist": lambda: _cmd_watchlist(),
            "/stats":     lambda: _cmd_stats(),
            "/add":       lambda: _cmd_add(args),
            "/remove":    lambda: _cmd_remove(args),
            "/find":      lambda: _cmd_find(args),
            "/best":      lambda: _cmd_best(args),
            "/cancel":    lambda: "Cancelled.",
        }
        handler = dispatch_map.get(cmd)
        if handler:
            _send(handler(), chat_id=chat_id)
        else:
            _send("Unknown command. Send /help for the list.", chat_id=chat_id)

    elif "skroutz.gr" in text_:
        _send(_handle_url(text_.strip(), chat_id), chat_id=chat_id)

    elif chat_id in _pending and re.match(r"^[\d.,]+\s*€?$", text_.strip()):
        _send(_handle_price_reply(text_, chat_id), chat_id=chat_id)

    elif chat_id in _pending:
        _send(
            "Send a number for the target price (e.g. <code>299</code>), "
            "or /cancel to abort.",
            chat_id=chat_id,
        )
    else:
        _send("Unknown input. Send /help for the list.", chat_id=chat_id)


# ── Polling loop ───────────────────────────────────────────────────────────────

def run() -> None:
    if not TOKEN or not CHAT_ID:
        logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in .env — exiting.")
        return

    logger.info("Telegram bot polling started.")
    _send("🤖 <b>Skroutz bot online.</b> Send /help for available commands.")

    offset = 0
    while True:
        try:
            updates = _get_updates(offset)
            for update in updates:
                offset  = update["update_id"] + 1
                msg     = update.get("message", {})
                text_   = msg.get("text", "").strip()
                chat_id = str(msg.get("chat", {}).get("id", ""))

                if chat_id != CHAT_ID:
                    logger.warning(f"Ignored message from unauthorized chat_id={chat_id}")
                    continue

                if text_:
                    logger.info(f"Message: {text_[:60]!r}")
                    _dispatch(text_, chat_id)

        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            break
        except Exception as e:
            logger.warning(f"Polling error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    run()

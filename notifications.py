"""
notifications.py
----------------
Telegram Bot notification layer for the Skroutz pipeline.

Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in your .env file to enable.
Get a bot token:  message @BotFather on Telegram → /newbot
Get your chat ID: message @userinfobot after starting a conversation with your bot

If either variable is unset, all functions are silent no-ops so the pipeline
runs normally without Telegram configured.

Deduplication: each alert type (drops, watchlist, disappeared) is sent at most
once per calendar day. A file logs/tg_sent_YYYY-MM-DD.json tracks what was sent.
Re-running the pipeline the same day will not produce duplicate notifications.

Usage:
    from notifications import tg_send, tg_pipeline_start, tg_failure
    from notifications import tg_drops, tg_watchlist, tg_disappeared, tg_success
"""

import datetime
import html
import json
import logging
import os
import time
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID",   "")
_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

_MAX_LEN = 4096

BASE = os.path.dirname(os.path.abspath(__file__))


# ── HTML escaping ──────────────────────────────────────────────────────────────

def _e(text) -> str:
    """Escape text for Telegram HTML parse mode."""
    return html.escape(str(text))


def _truncate(text: str, keep: int = _MAX_LEN - 200) -> str:
    if len(text) <= keep:
        return text
    return text[:keep] + f"\n\n<i>... {len(text) - keep} more characters omitted</i>"


# ── Deduplication ──────────────────────────────────────────────────────────────

def _sent_file() -> str:
    return os.path.join(BASE, "logs", f"tg_sent_{datetime.date.today()}.json")


def _already_sent(key: str) -> bool:
    try:
        path = _sent_file()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return key in json.load(f)
    except Exception:
        pass
    return False


def _mark_sent(key: str) -> None:
    try:
        path = _sent_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        sent = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                sent = json.load(f)
        sent[key] = datetime.datetime.now().isoformat()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sent, f)
    except Exception:
        pass


# ── Core sender ────────────────────────────────────────────────────────────────

def tg_send(message: str, parse_mode: str = "HTML", reply_markup=None) -> bool:
    """
    Send a Telegram message to the configured chat.

    Returns True on success, False on any failure.
    No-ops (returns False) if TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is unset.
    Retries once after 3 s on transient connection errors.
    """
    if not _TOKEN or not _CHAT_ID:
        return False

    payload: dict = {
        "chat_id":    _CHAT_ID,
        "text":       _truncate(message),
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url     = _API_URL.format(token=_TOKEN),
        data    = data,
        headers = {"Content-Type": "application/json"},
        method  = "POST",
    )

    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    return True
                logger.warning(f"Telegram returned HTTP {resp.status}")
                return False
        except (urllib.error.URLError, OSError) as e:
            if attempt == 0:
                logger.warning(f"Telegram send failed (attempt 1): {e} — retrying in 3s")
                time.sleep(3)
            else:
                logger.warning(f"Telegram send failed (attempt 2): {e}")

    return False


# ── Formatted message builders ─────────────────────────────────────────────────

def tg_pipeline_start() -> bool:
    """Notify that the pipeline has started (no dedup — always useful on re-runs)."""
    now = datetime.datetime.now().strftime("%H:%M")
    msg = f"⏳ <b>Pipeline started</b>  <code>{now}</code>"
    return tg_send(msg)


def tg_failure(stage: str, returncode: int, log_path: str) -> bool:
    """Send a pipeline failure alert (no dedup — always send on failure)."""
    msg = (
        f"🚨 <b>Pipeline FAILED</b>\n\n"
        f"<b>Stage:</b> {_e(stage)}\n"
        f"<b>Exit code:</b> <code>{returncode}</code>\n"
        f"<b>Log:</b> <code>{_e(log_path)}</code>\n\n"
        f"No downstream stages ran. Re-run <code>run_pipeline.py</code> to recover today's data."
    )
    return tg_send(msg)


def tg_drops(rows) -> bool:
    """
    Send today's price drop digest.
    rows: sequence with attributes brand, model, category, prev_price, new_price,
          drop_eur, drop_pct
    Skipped silently if already sent today.
    """
    if not rows or _already_sent("drops"):
        return False

    CAT_ICON = {"phone": "📱", "laptop": "💻", "smartwatch": "⌚", "tablet": "📟"}
    lines = ["<b>Top price drops today:</b>\n"]
    for r in rows[:10]:
        icon  = CAT_ICON.get(r.category, "🏷️")
        brand = _e(r.brand or "")
        model = _e(r.model or "")
        saved = abs(float(r.drop_eur))
        pct   = float(r.drop_pct)
        lines.append(
            f"{icon} <b>{brand} {model}</b>\n"
            f"  {float(r.prev_price):.0f}€ → <b>{float(r.new_price):.0f}€</b>"
            f"  (-{saved:.0f}€ / {pct:.1f}%)"
        )

    msg = "\n".join(lines)
    if len(rows) > 10:
        msg += f"\n\n<i>... and {len(rows) - 10} more drops</i>"

    ok = tg_send(msg)
    if ok:
        _mark_sent("drops")
    return ok


def tg_watchlist(hits) -> bool:
    """
    Send one message per watchlist hit, each with an inline 'View on Skroutz' button.
    hits: list of dicts with keys label, brand, model, price, threshold, url
    Each hit is deduped independently by URL so a failed send is retried on re-run.
    """
    if not hits:
        return False

    any_sent = False
    for h in hits:
        item_key = f"watchlist:{h.get('url') or h['label']}"
        if _already_sent(item_key):
            continue

        name      = _e(f"{h['brand']} {h['model']}".strip() or h["label"])
        price     = _e(f"{h['price']:.2f}€")
        tgt       = _e(f"{h['threshold']:.2f}€")
        below_eur = h["threshold"] - h["price"]
        below_pct = 100.0 * below_eur / h["threshold"] if h["threshold"] else 0

        msg = (
            f"🎯 <b>Price target reached!</b>\n\n"
            f"<b>{name}</b>\n"
            f"Now: <b>{price}</b>  |  Target: {tgt}\n"
            f"<i>Below target by {below_eur:.2f}€ ({below_pct:.1f}%)</i>"
        )
        markup = None
        if h.get("url"):
            markup = {"inline_keyboard": [[{"text": "View on Skroutz →", "url": h["url"]}]]}

        if tg_send(msg, reply_markup=markup):
            _mark_sent(item_key)
            any_sent = True

    return any_sent


def tg_disappeared(rows) -> bool:
    """
    Send disappeared products alert.
    rows: sequence with attributes brand, model/product_name, category, last_seen
    Skipped silently if already sent today.
    """
    if not rows or _already_sent("disappeared"):
        return False

    lines = [f"⚠️ <b>{len(rows)} product(s) disappeared</b>\n"]
    for r in rows[:15]:
        brand = _e(r.brand or "")
        model = _e(str(getattr(r, "model", None) or getattr(r, "product_name", "") or ""))
        lines.append(f"• <b>{brand} {model}</b>  <i>last seen: {_e(str(r.last_seen))}</i>")

    if len(rows) > 15:
        lines.append(f"\n<i>... and {len(rows) - 15} more</i>")

    ok = tg_send("\n".join(lines))
    if ok:
        _mark_sent("disappeared")
    return ok


def tg_success(snaps: int, new_prods: int, drops: int, elapsed: str) -> bool:
    """Send a daily pipeline success summary (no dedup — always send)."""
    msg = (
        f"✅ <b>Pipeline completed</b>\n\n"
        f"📊 Snapshots loaded: <b>{snaps:,}</b>\n"
        f"🆕 New products: <b>+{new_prods:,}</b>\n"
        f"📉 Price drops: <b>{drops}</b>\n"
        f"⏱ Duration: <code>{_e(elapsed)}</code>"
    )
    return tg_send(msg)

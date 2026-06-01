"""
notifications.py
----------------
Telegram Bot notification layer for the Skroutz pipeline.

Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in your .env file to enable.
Get a bot token:  message @BotFather on Telegram → /newbot
Get your chat ID: message @userinfobot after starting a conversation with your bot

If either variable is unset, all functions in this module are silent no-ops so
the pipeline runs normally without Telegram configured.

Usage:
    from notifications import tg_send, tg_failure, tg_drops, tg_watchlist
    from notifications import tg_disappeared, tg_success
"""

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID",   "")
_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

_MAX_LEN = 4096   # Telegram hard cap per message


# ── Escape helpers ─────────────────────────────────────────────────────────────

_ESCAPE_CHARS = r"\_*[]()~`>#+-=|{}.!"

def _esc(text: str) -> str:
    """Escape characters that have special meaning in MarkdownV2."""
    for ch in _ESCAPE_CHARS:
        text = text.replace(ch, f"\\{ch}")
    return text


def _truncate(text: str, keep: int = _MAX_LEN - 200) -> str:
    """Trim to keep chars; append a count of omitted characters if needed."""
    if len(text) <= keep:
        return text
    return text[:keep] + f"\n\n_\\.\\.\\. {len(text) - keep} more characters omitted_"


# ── Core sender ────────────────────────────────────────────────────────────────

def tg_send(message: str, parse_mode: str = "MarkdownV2") -> bool:
    """
    Send a Telegram message to the configured chat.

    Returns True on success, False on any failure.
    No-ops (returns False) if TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is unset.
    """
    if not _TOKEN or not _CHAT_ID:
        return False

    payload = json.dumps({
        "chat_id":    _CHAT_ID,
        "text":       _truncate(message),
        "parse_mode": parse_mode,
    }).encode("utf-8")

    req = urllib.request.Request(
        url     = _API_URL.format(token=_TOKEN),
        data    = payload,
        headers = {"Content-Type": "application/json"},
        method  = "POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = resp.status == 200
    except (urllib.error.URLError, OSError) as e:
        logger.warning(f"Telegram send failed: {e}")
        return False

    if not ok:
        logger.warning(f"Telegram send returned HTTP {resp.status}")
    return ok


# ── Formatted message builders ─────────────────────────────────────────────────

def tg_failure(stage: str, returncode: int, log_path: str) -> bool:
    """Send a pipeline failure alert."""
    msg = (
        f"🚨 *Pipeline FAILED*\n\n"
        f"*Stage:* {_esc(stage)}\n"
        f"*Exit code:* `{returncode}`\n"
        f"*Log:* `{_esc(log_path)}`\n\n"
        f"No downstream stages ran\\. Re\\-run `run_pipeline\\.py` to recover today's data\\."
    )
    return tg_send(msg)


def tg_drops(rows) -> bool:
    """
    Send today's price drop digest.
    rows: list/sequence with attributes brand, model, category, prev_price,
          new_price, drop_eur, drop_pct
    """
    if not rows:
        return False

    CAT_ICON = {"phone": "📱", "laptop": "💻", "smartwatch": "⌚", "tablet": "📟"}
    lines = ["*Top price drops today:*\n"]
    for r in rows[:10]:
        icon  = CAT_ICON.get(r.category, "🏷️")
        brand = _esc(str(r.brand or ""))
        model = _esc(str(r.model or ""))
        saved = abs(float(r.drop_eur))
        pct   = float(r.drop_pct)
        lines.append(
            f"{icon} *{brand} {model}*\n"
            f"  {_esc(f'{float(r.prev_price):.0f}€')} → *{_esc(f'{float(r.new_price):.0f}€')}*"
            f"  \\(\\-{_esc(f'{saved:.0f}€')} / {_esc(f'{pct:.1f}')}%\\)"
        )

    msg = "\n".join(lines)
    if len(rows) > 10:
        msg += f"\n\n_\\.\\.\\. and {len(rows) - 10} more drops_"
    return tg_send(msg)


def tg_watchlist(hits) -> bool:
    """
    Send watchlist price target alert.
    hits: list of dicts with keys label, brand, model, price, threshold, url
    """
    if not hits:
        return False

    lines = [f"🎯 *{len(hits)} price target\\(s\\) reached\\!*\n"]
    for h in hits:
        name  = _esc(f"{h['brand']} {h['model']}".strip() or h["label"])
        price = _esc(f"{h['price']:.2f}€")
        tgt   = _esc(f"{h['threshold']:.2f}€")
        url   = h.get("url", "")
        lines.append(f"• *{name}*\n  Now: *{price}*  Target: {tgt}")
        if url:
            lines.append(f"  [View on Skroutz]({url})")
    return tg_send("\n".join(lines))


def tg_disappeared(rows) -> bool:
    """
    Send disappeared products alert.
    rows: sequence with attributes brand, model/product_name, category, last_seen
    """
    if not rows:
        return False

    lines = [f"⚠️ *{len(rows)} product\\(s\\) disappeared*\n"]
    for r in rows[:15]:
        brand = _esc(str(r.brand or ""))
        model = _esc(str(getattr(r, "model", None) or getattr(r, "product_name", "") or ""))
        lines.append(f"• *{brand} {model}*  _last seen: {r.last_seen}_")

    if len(rows) > 15:
        lines.append(f"\n_\\.\\.\\. and {len(rows) - 15} more_")
    return tg_send("\n".join(lines))


def tg_success(snaps: int, new_prods: int, drops: int, elapsed: str) -> bool:
    """Send a daily pipeline success summary."""
    msg = (
        f"✅ *Pipeline completed*\n\n"
        f"📊 Snapshots loaded: *{_esc(f'{snaps:,}')}*\n"
        f"🆕 New products: *{_esc(f'+{new_prods:,}')}*\n"
        f"📉 Price drops: *{_esc(str(drops))}*\n"
        f"⏱ Duration: `{_esc(elapsed)}`"
    )
    return tg_send(msg)

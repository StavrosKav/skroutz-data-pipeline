"""Create (or re-key) the read-only DB role used by /ask and /analyze.

Run once as the pipeline user:  python -m ops.create_ask_role
- role skroutz_ask: LOGIN, no superuser, SELECT only on agents.ask.schema.ALLOWED_TABLES
  plus BOT_VIEWS (the read-only views the Telegram bot's non-AI commands query)
- python -m ops.create_ask_role --grants-only  re-applies the grants, keeps the password
- default_transaction_read_only = on, statement_timeout = 5s
- a new random password is written straight into .env (ASK_DB_USER / ASK_DB_PASSWORD)
  and is never printed.
"""

from __future__ import annotations

import os
import re
import secrets
import sys
from pathlib import Path

from psycopg2 import sql

from agents.ask.schema import ALLOWED_TABLES
from core.db import get_engine

ROLE = "skroutz_ask"
# Views used by alerts/telegram_bot.py. They expose only products/price_snapshots data,
# which the role can already read; /ask is still limited to ALLOWED_TABLES by its guard.
BOT_VIEWS = ("vw_latest_prices", "vw_biggest_drops", "vw_price_floor", "vw_price_history", "vw_restock_pricing", "vw_drops_7d")
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _write_env(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    keep = [ln for ln in lines if not re.match(rf"^\s*({'|'.join(values)})\s*=", ln)]
    keep += [f"{k}={v}" for k, v in values.items()]
    path.write_text("\n".join(keep) + "\n", encoding="utf-8")


def _grant_selects(cur) -> int:
    rels = sorted(set(ALLOWED_TABLES) | set(BOT_VIEWS))
    for t in rels:
        cur.execute(sql.SQL("GRANT SELECT ON public.{} TO {}").format(sql.Identifier(t), sql.Identifier(ROLE)))
    return len(rels)


def grants_only() -> None:
    raw = get_engine().raw_connection()
    try:
        n = _grant_selects(raw.cursor())
        raw.commit()
    finally:
        raw.close()
    print(f"GRANT SELECT on {n} relations to {ROLE}: done (password unchanged)")


def main() -> None:
    password = secrets.token_urlsafe(32)
    db_name = os.environ.get("DB_NAME", "SkroutzPR")
    raw = get_engine().raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE,))
        verb = "ALTER" if cur.fetchone() else "CREATE"
        cur.execute(sql.SQL(verb + " ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD {}")
                    .format(sql.Identifier(ROLE), sql.Literal(password)))
        cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(db_name), sql.Identifier(ROLE)))
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(ROLE)))
        _grant_selects(cur)
        cur.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(sql.Identifier(ROLE)))
        cur.execute(sql.SQL("ALTER ROLE {} SET statement_timeout = '5s'").format(sql.Identifier(ROLE)))
        raw.commit()
    finally:
        raw.close()
    _write_env(ENV_PATH, {"ASK_DB_USER": ROLE, "ASK_DB_PASSWORD": password})
    print(f"{verb} ROLE {ROLE}: done, SELECT on {len(set(ALLOWED_TABLES) | set(BOT_VIEWS))} relations, credentials written to .env")


if __name__ == "__main__":
    grants_only() if "--grants-only" in sys.argv else main()

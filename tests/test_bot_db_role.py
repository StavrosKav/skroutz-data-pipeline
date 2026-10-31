"""Non-AI bot commands are read-only: they must use the read-only role and fail closed."""
import inspect

import pytest

from alerts import telegram_bot
from core import db


def test_bot_never_uses_pipeline_engine():
    src = inspect.getsource(telegram_bot)
    assert "get_engine(" not in src
    assert not hasattr(telegram_bot, "get_engine")


def test_bot_commands_fail_closed_without_readonly_role(monkeypatch):
    monkeypatch.delenv("ASK_DB_USER", raising=False)
    monkeypatch.delenv("ASK_DB_PASSWORD", raising=False)
    monkeypatch.setattr(db, "_ask_engine", None)

    def boom():
        raise AssertionError("bot fell back to the superuser engine")

    monkeypatch.setattr(db, "get_engine", boom)
    out = telegram_bot._cmd_stats()
    assert "ASK_DB_USER" in out or "unavailable" in out.lower()


def test_readonly_role_is_granted_the_bot_views():
    from ops import create_ask_role
    for v in ("vw_latest_prices", "vw_price_floor", "vw_price_history", "vw_restock_pricing", "vw_biggest_drops", "vw_drops_7d"):
        assert v in create_ask_role.BOT_VIEWS


def test_readonly_engine_is_the_ask_engine(monkeypatch):
    monkeypatch.setenv("ASK_DB_USER", "skroutz_ask")
    monkeypatch.setenv("ASK_DB_PASSWORD", "x")
    monkeypatch.setattr(db, "_ask_engine", None)
    assert db.get_readonly_engine().url.username == "skroutz_ask"


@pytest.mark.parametrize("name", ["_lookup_product", "_cmd_drops", "_cmd_watchlist", "_cmd_find",
                                  "_cmd_history", "_cmd_best", "_cmd_stats", "_cmd_restock"])
def test_each_db_command_uses_readonly_engine(name):
    src = inspect.getsource(getattr(telegram_bot, name))
    assert "get_readonly_engine()" in src

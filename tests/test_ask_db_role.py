"""/ask must use its own read-only DB role and fail closed without it."""

import pytest

from core import db


def test_ask_engine_fails_closed_without_env(monkeypatch):
    monkeypatch.delenv("ASK_DB_USER", raising=False)
    monkeypatch.delenv("ASK_DB_PASSWORD", raising=False)
    monkeypatch.setattr(db, "_ask_engine", None)
    with pytest.raises(RuntimeError, match="ASK_DB_USER"):
        db.get_ask_engine()


def test_ask_engine_refuses_pipeline_user(monkeypatch):
    monkeypatch.setenv("ASK_DB_USER", "postgres")
    monkeypatch.setenv("ASK_DB_PASSWORD", "x")
    monkeypatch.setattr(db, "_ask_engine", None)
    with pytest.raises(RuntimeError, match="superuser|pipeline"):
        db.get_ask_engine()


def test_ask_engine_uses_ask_user(monkeypatch):
    monkeypatch.setenv("ASK_DB_USER", "skroutz_ask")
    monkeypatch.setenv("ASK_DB_PASSWORD", "x")
    monkeypatch.setattr(db, "_ask_engine", None)
    eng = db.get_ask_engine()
    assert eng.url.username == "skroutz_ask"


def test_pg_executor_defaults_to_ask_engine(monkeypatch):
    from agents.ask import runtime
    monkeypatch.delenv("ASK_DB_USER", raising=False)
    monkeypatch.setattr(db, "_ask_engine", None)
    with pytest.raises(RuntimeError):
        runtime.make_pg_executor()

"""
db.py
-----
Single source of truth for the PostgreSQL engine.

Uses sqlalchemy.engine.URL.create() so special characters in DB_PASSWORD
(%, @, /, :, #, etc.) are handled correctly. All pipeline scripts import
get_engine() from here instead of building the URL themselves.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from dotenv import load_dotenv

load_dotenv()

_engine = None


def get_engine():
    """Return the shared engine, creating it once per process."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            URL.create(
                drivername="postgresql+psycopg2",
                username=os.environ.get("DB_USER", "postgres"),
                password=os.environ.get("DB_PASSWORD", ""),
                host=os.environ.get("DB_HOST", "localhost"),
                port=int(os.environ.get("DB_PORT", "5432")),
                database=os.environ.get("DB_NAME", "SkroutzPR"),
            ),
            pool_pre_ping=True,
        )
    return _engine


_ask_engine = None
_PIPELINE_USERS = {"postgres"}


def get_ask_engine():
    """Engine for the /ask and /analyze agents only.

    Uses its own read-only role (ASK_DB_USER / ASK_DB_PASSWORD). Fails closed:
    if the role is not configured it raises instead of falling back to the
    pipeline user, which is a superuser.
    """
    global _ask_engine
    if _ask_engine is None:
        user = os.environ.get("ASK_DB_USER", "").strip()
        password = os.environ.get("ASK_DB_PASSWORD", "")
        if not user or not password:
            raise RuntimeError("ASK_DB_USER / ASK_DB_PASSWORD not set: /ask needs its own read-only DB role")
        if user in _PIPELINE_USERS or user == os.environ.get("DB_USER", "postgres"):
            raise RuntimeError("ASK_DB_USER must not be the pipeline user or a superuser")
        _ask_engine = create_engine(
            URL.create(
                drivername="postgresql+psycopg2",
                username=user,
                password=password,
                host=os.environ.get("DB_HOST", "localhost"),
                port=int(os.environ.get("DB_PORT", "5432")),
                database=os.environ.get("DB_NAME", "SkroutzPR"),
            ),
            pool_pre_ping=True,
        )
    return _ask_engine

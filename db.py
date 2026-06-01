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
        _engine = create_engine(URL.create(
            drivername="postgresql+psycopg2",
            username=os.environ.get("DB_USER", "postgres"),
            password=os.environ.get("DB_PASSWORD", ""),
            host=os.environ.get("DB_HOST", "localhost"),
            port=int(os.environ.get("DB_PORT", "5432")),
            database=os.environ.get("DB_NAME", "SkroutzPR"),
        ))
    return _engine

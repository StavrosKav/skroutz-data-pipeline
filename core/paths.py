"""Project path anchors — single source of truth for the repo root.

Every module that needs Clean/, charts/, dashboard/, logs/, config/, or
category CSV folders must import ROOT from here instead of deriving paths
from dirname(__file__). That keeps data I/O pointed at the repo root even
after scripts move into packages.
"""
from __future__ import annotations

from pathlib import Path

# core/paths.py → parents[1] is the repository root
ROOT = Path(__file__).resolve().parent.parent

CLEAN_DIR = ROOT / "Clean"
CHARTS_DIR = ROOT / "charts"
DASHBOARD_DIR = ROOT / "dashboard"
LOGS_DIR = ROOT / "logs"
CONFIG_DIR = ROOT / "config"
TEMPLATES_DIR = ROOT / "templates"
ASSETS_DIR = ROOT / "assets"
WATCHLIST_PATH = ROOT / "watchlist.json"
INDEX_HTML = ROOT / "index.html"

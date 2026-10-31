"""BLE001 narrowing: expected failures are still handled, real bugs now surface."""
import os
import subprocess
import tempfile
from unittest import mock

import pytest
from sqlalchemy.exc import OperationalError

import run_pipeline as rp

_DB_DOWN = OperationalError("SELECT 1", {}, Exception("down"))


@pytest.mark.parametrize("fn", [rp.send_drop_digest, rp.send_disappeared_alert,
                                rp.check_snapshot_coverage, rp.refresh_matviews])
def test_db_errors_are_still_swallowed(fn):
    with mock.patch.object(rp, "get_engine", side_effect=_DB_DOWN), \
         mock.patch.object(rp, "apply_view_definitions"), mock.patch.object(rp, "_run_log_note"):
        fn()  # no exception


@pytest.mark.parametrize("fn", [rp.send_drop_digest, rp.send_disappeared_alert, rp.check_snapshot_coverage])
def test_programming_errors_now_surface(fn):
    # main() still catches them per step (logged + Telegram), but they are no longer
    # silently reported as "DB query failed".
    with mock.patch.object(rp, "get_engine", side_effect=TypeError("bug")), \
         mock.patch.object(rp, "GMAIL_APP_PASSWORD", "x"), \
         mock.patch.object(rp, "_run_log_note"), pytest.raises(TypeError):
        fn()


def test_log_cleanup_ignores_bad_dates():
    with tempfile.TemporaryDirectory() as d, mock.patch.object(rp, "_log_dir", d):
        open(os.path.join(d, "pipeline_2020-13-45.log"), "w").close()
        open(os.path.join(d, "pipeline_2020-01-01.log"), "w").close()
        rp._cleanup_old_logs()
        assert os.listdir(d) == ["pipeline_2020-13-45.log"]


def test_watchlist_bad_json_is_skipped():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "watchlist.json"), "w") as f:
            f.write("{not json")
        with mock.patch.object(rp, "BASE", d), mock.patch.object(rp, "get_engine") as ge:
            rp.send_watchlist_alerts()
        ge.assert_not_called()


def test_chrome_version_missing_returns_zero():
    from scrapers import scraper_core as sc
    for err in (FileNotFoundError("no powershell"), subprocess.TimeoutExpired("x", 5)):
        with mock.patch.object(sc.subprocess, "check_output", side_effect=err):
            assert sc._chrome_major() == 0

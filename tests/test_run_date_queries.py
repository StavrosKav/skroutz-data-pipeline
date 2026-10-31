"""
tests/test_run_date_queries.py
Bug hunt 2026-10-30: the success summary and drop digest must count the
pipeline's own run date (PIPELINE_DATE, pinned at start), not the DB's
CURRENT_DATE. A run that starts before midnight and loads after it would
otherwise report 0 snapshots and raise a false "snapshot anomaly".
No network or DB.
"""

import warnings
from unittest.mock import MagicMock, patch

import run_pipeline as rp
from agents.pipeline_monitoring.scraper_health_monitor import ScraperHealthMonitor


def _engine(scalar=0, rows=()):
    eng, conn = MagicMock(), MagicMock()
    eng.connect.return_value.__enter__.return_value = conn
    eng.connect.return_value.__exit__.return_value = False
    conn.execute.return_value.scalar.return_value = scalar
    conn.execute.return_value.fetchall.return_value = list(rows)
    return eng, conn


def _sql_and_params(conn):
    out = []
    for call in conn.execute.call_args_list:
        sql = str(call.args[0])
        params = call.args[1] if len(call.args) > 1 else call.kwargs.get("parameters", {})
        out.append((sql, params))
    return out


def test_success_summary_counts_pipeline_date(monkeypatch):
    monkeypatch.setenv("PIPELINE_DATE", "2026-10-08")
    eng, conn = _engine(scalar=100)
    with patch.object(rp, "GMAIL_APP_PASSWORD", "pw"), \
         patch.object(rp, "get_engine", return_value=eng), \
         patch.object(rp._notif, "tg_success"), patch.object(rp._notif, "tg_send"), \
         patch("smtplib.SMTP"):
        rp.send_success_summary("0:01:00")
    calls = _sql_and_params(conn)
    assert len(calls) == 7  # snaps, new, drops, yesterday, suspect, new_week, volume
    for sql, params in calls:
        assert "CURRENT_DATE" not in sql
        assert params.get("d") == "2026-10-08"


def test_drop_digest_uses_pipeline_date(monkeypatch):
    monkeypatch.setenv("PIPELINE_DATE", "2026-10-08")
    eng, conn = _engine(rows=[])
    with patch.object(rp, "GMAIL_APP_PASSWORD", "pw"), \
         patch.object(rp, "get_engine", return_value=eng):
        rp.send_drop_digest()
    (sql, params), = _sql_and_params(conn)
    assert "CURRENT_DATE" not in sql
    assert params.get("d") == "2026-10-08"


def test_pipeline_date_falls_back_to_today(monkeypatch):
    monkeypatch.delenv("PIPELINE_DATE", raising=False)
    import datetime
    assert rp._pipeline_date() == datetime.date.today().isoformat()


def test_health_timestamp_has_no_deprecation_warning(tmp_path):
    mon = ScraperHealthMonitor({"base_path": str(tmp_path), "categories": []})
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        ts = mon.process({})["timestamp"]
    assert ts.endswith("Z") and "+" not in ts

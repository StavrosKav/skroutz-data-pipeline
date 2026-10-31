"""
Audit item C: per-category volume alarm, now the one rule in reporting.volume
(median of the previous recorded days, category < 85%). Never fails the run.
"""
from unittest.mock import MagicMock, patch

from reporting import volume


def test_below_threshold_alerts():
    out = volume.category_volume_alerts({"phone": 800, "laptop": 1800}, {"phone": 1000.0, "laptop": 1810.0})
    assert [a["category"] for a in out] == ["phone"]
    assert out[0]["pct"] == 80.0 and out[0]["today"] == 800 and out[0]["base7"] == 1000.0


def test_exactly_85_percent_is_ok():
    assert volume.category_volume_alerts({"phone": 850}, {"phone": 1000.0}) == []


def test_missing_category_today_counts_as_zero():
    out = volume.category_volume_alerts({}, {"tablet": 380.0})
    assert out[0]["category"] == "tablet" and out[0]["today"] == 0


def test_no_history_no_alert():
    assert volume.category_volume_alerts({"phone": 5}, {}) == []


def test_sql_uses_run_date_and_previous_recorded_days():
    sql = " ".join(volume.VOLUME_SQL.split())
    assert "CAST(:d AS date) - :lookback" in sql and "< CAST(:d AS date)" in sql
    assert "CURRENT_DATE" not in sql


def test_fetch_returns_dicts():
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [("phone", 800, 1000.0, 7), ("laptop", 0, None, 0)]
    today, base = volume.fetch(conn, "2026-10-31")
    assert today == {"phone": 800, "laptop": 0} and base == {"phone": 1000.0}


def test_summary_reports_coverage_alerts(monkeypatch):
    import run_pipeline as rp
    monkeypatch.setenv("PIPELINE_DATE", "2026-10-31")
    eng = MagicMock()
    conn = eng.connect.return_value.__enter__.return_value
    conn.execute.return_value.scalar.return_value = 100
    alert = {"category": "phone", "today": 10, "base7": 100.0, "pct": 10.0}
    with patch.object(rp, "GMAIL_APP_PASSWORD", "pw"), patch.object(rp, "get_engine", return_value=eng), \
         patch.object(rp._notif, "tg_send"), patch("smtplib.SMTP"), \
         patch.object(rp, "_VOLUME_ALERTS", [alert]), \
         patch.object(rp._notif, "tg_success") as tg:
        rp.send_success_summary("0:01:00")
    assert tg.call_args.kwargs["volume_alerts"][0]["category"] == "phone"


def test_telegram_line():
    from alerts import notifications as n
    with patch.object(n, "tg_send", return_value=True) as s:
        n.tg_success(1, 0, 0, "0:01", volume_alerts=[{"category": "phone", "today": 800, "base7": 1000.0, "pct": 80.0}])
    assert "Low volume" in s.call_args.args[0] and "phone" in s.call_args.args[0]

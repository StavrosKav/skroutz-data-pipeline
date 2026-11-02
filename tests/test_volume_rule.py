"""One volume rule: today vs the median of the previous recorded days (max 7),
category < 85% or total < 80% warns; needs >= 3 prior days. Used by both the
coverage step and the success summary (no second query, no second rule)."""
from unittest.mock import MagicMock, patch

from reporting import volume


def test_single_rule_constants():
    assert volume.THRESHOLD == 0.85 and volume.TOTAL_THRESHOLD == 0.80
    assert volume.LOOKBACK_DAYS == 7 and volume.MIN_PRIOR_DAYS == 3


def test_sql_uses_median_not_average():
    sql = " ".join(volume.VOLUME_SQL.split())
    assert "percentile_cont(0.5)" in sql and "AVG(" not in sql


def test_fetch_drops_categories_with_too_little_history_and_keeps_total():
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [
        ("phone", 800, 1000.0, 7), ("tablet", 10, 400.0, 2), (volume.TOTAL, 810, 1400.0, 7)]
    today, base = volume.fetch(conn, "2026-10-31")
    assert base == {"phone": 1000.0, volume.TOTAL: 1400.0}
    assert today["tablet"] == 10


def test_total_uses_its_own_threshold():
    # 82% of baseline: category alarm (85%) fires, total alarm (80%) does not
    out = volume.category_volume_alerts({"phone": 82, volume.TOTAL: 82},
                                        {"phone": 100.0, volume.TOTAL: 100.0})
    assert [a["category"] for a in out] == ["phone"]


def test_between_70_and_85_percent_now_warns_in_coverage_step():
    import run_pipeline as rp
    with patch.object(rp, "get_engine"), \
         patch.object(volume, "fetch", return_value=({"phone": 75}, {"phone": 100.0})), \
         patch.object(rp, "_run_log_note") as note, patch.object(rp._notif, "tg_send") as tg:
        rp.check_snapshot_coverage()
    assert note.call_args.args[1] == "warn" and "phone" in tg.call_args.args[0]
    assert [a["category"] for a in rp._VOLUME_ALERTS] == ["phone"]


def test_summary_reuses_coverage_result_without_new_query(monkeypatch):
    import run_pipeline as rp
    monkeypatch.setenv("PIPELINE_DATE", "2026-10-31")
    eng = MagicMock()
    eng.connect.return_value.__enter__.return_value.execute.return_value.scalar.return_value = 100
    cached = [{"category": "phone", "today": 1, "base7": 100.0, "pct": 1.0}]
    with patch.object(rp, "_VOLUME_ALERTS", cached), patch.object(rp, "GMAIL_APP_PASSWORD", "pw"), \
         patch.object(rp, "get_engine", return_value=eng), patch.object(rp._notif, "tg_send"), \
         patch("smtplib.SMTP"), patch.object(volume, "fetch") as f, \
         patch.object(rp._notif, "tg_success") as tg:
        rp.send_success_summary("0:01:00")
    f.assert_not_called()
    assert tg.call_args.kwargs["volume_alerts"] == cached

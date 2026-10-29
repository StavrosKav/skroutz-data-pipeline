import datetime as dt

from ops.backup_db import prune


def test_prune_keeps_7_days_and_other_files(tmp_path):
    today = dt.date(2026, 10, 29)
    for d in range(10):
        (tmp_path / f"skroutz_daily_{today - dt.timedelta(days=d):%Y%m%d}.dump").write_text("x")
    keep = ["skroutz_pre_dedup_20261028.dump", "skroutz_daily_20200101.dump.tmp", "notes.txt"]
    for n in keep:
        (tmp_path / n).write_text("x")
    removed = prune(tmp_path, today)
    assert len(removed) == 3
    left = sorted(p.name for p in tmp_path.iterdir())
    assert len([n for n in left if n.startswith("skroutz_daily_") and n.endswith(".dump")]) == 7
    assert all(n in left for n in keep)

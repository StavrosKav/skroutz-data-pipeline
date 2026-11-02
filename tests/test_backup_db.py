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


def _age(p, days, now):
    import os
    t = (now - dt.timedelta(days=days)).timestamp()
    os.utime(p, (t, t))


def test_prune_manual_only_old_pre_dumps_and_bundles(tmp_path):
    from ops.backup_db import prune_manual
    now = dt.datetime(2026, 11, 20, 12, 0)
    files = {"skroutz_pre_a_20261028.dump": 20, "old.bundle": 30, "skroutz_pre_b.dump": 5,
             "new.bundle": 13, "skroutz_daily_20261001.dump": 50, "notes.txt": 99}
    for n, d in files.items():
        (tmp_path / n).write_text("x")
        _age(tmp_path / n, d, now)
    would = prune_manual(tmp_path, now, dry_run=True)
    assert sorted(p.name for p in would) == ["old.bundle", "skroutz_pre_a_20261028.dump"]
    assert len(list(tmp_path.iterdir())) == 6
    removed = prune_manual(tmp_path, now)
    assert sorted(p.name for p in removed) == ["old.bundle", "skroutz_pre_a_20261028.dump"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "new.bundle", "notes.txt", "skroutz_daily_20261001.dump", "skroutz_pre_b.dump"]


def test_already_done_today(tmp_path):
    from ops.backup_db import already_done
    today = dt.date(2026, 11, 2)
    assert not already_done(tmp_path, today)
    (tmp_path / "skroutz_daily_20261102.dump").write_text("x")
    assert already_done(tmp_path, today)

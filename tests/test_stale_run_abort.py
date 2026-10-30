"""
tests/test_stale_run_abort.py
2026-10-30: a run killed hard (power off, Task Scheduler kill) never writes its
finish row and stays 'running' forever (run #6 since 2026-10-08). Each new run
closes 'running' rows older than the 6 h lock window as failed with a reason.
No network or DB.
"""

from unittest.mock import MagicMock, patch

import run_pipeline as rp


def _engine():
    eng, conn = MagicMock(), MagicMock()
    eng.begin.return_value.__enter__.return_value = conn
    eng.begin.return_value.__exit__.return_value = False
    conn.execute.return_value.fetchall.return_value = [(6,)]
    conn.execute.return_value.scalar.return_value = 99
    return eng, conn


def test_run_start_aborts_stale_running_rows():
    eng, conn = _engine()
    with patch.object(rp, "get_engine", return_value=eng):
        rp._run_log_start()
    sqls = [(str(c.args[0]), c.args[1] if len(c.args) > 1 else {}) for c in conn.execute.call_args_list]
    upd = [(s, p) for s, p in sqls if s.lstrip().startswith("UPDATE pipeline_runs")]
    assert len(upd) == 1
    s, p = upd[0]
    assert "status = 'running'" in s and "status = 'failed'" in s
    assert "started_at <" in s
    assert p["secs"] == rp._LOCK_STALE_SECONDS
    assert "aborted" in p["reason"]
    # the abort runs before this run's own row is inserted, so it can never close itself
    idx_upd = [i for i, (s, _) in enumerate(sqls) if s.lstrip().startswith("UPDATE")][0]
    idx_ins = [i for i, (s, _) in enumerate(sqls) if s.lstrip().startswith("INSERT")][0]
    assert idx_upd < idx_ins


def test_abort_failure_does_not_block_run_start():
    eng, conn = _engine()
    calls = {"n": 0}

    def execute(stmt, params=None):
        calls["n"] += 1
        if str(stmt).lstrip().startswith("UPDATE"):
            raise RuntimeError("db hiccup")
        return MagicMock(scalar=MagicMock(return_value=7))

    conn.execute.side_effect = execute
    with patch.object(rp, "get_engine", return_value=eng):
        rp._run_log_start()
    assert rp._RUN_ID == 7

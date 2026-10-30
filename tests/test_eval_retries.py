"""A NIM error (429/404/5xx/timeout) must be retried, then counted as FAIL, never stop the run."""
from types import SimpleNamespace as NS

from evals import run_ask_eval as r

ITEM = {"id": "q1", "question": "x", "check": {"type": "no_data"}}


def _res(error=None):
    return NS(sql=None, rows=[], answer="a", error=error, attempts=1, latency_ms=5)


class Agent:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    def ask(self, q):
        self.calls += 1
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


def test_transient_error_is_retried_then_succeeds():
    a = Agent([_res("HTTPStatusError"), _res()])
    out = r.run_item(a, ITEM, execute=None, tries=3, sleep=lambda s: None)
    assert a.calls == 2 and out["error"] is None and out["tries"] == 2


def test_raised_exception_after_all_tries_is_fail_not_crash():
    a = Agent([TimeoutError("t")] * 3)
    out = r.run_item(a, ITEM, execute=None, tries=3, sleep=lambda s: None)
    assert a.calls == 3 and out["ok"] is False and "TimeoutError" in out["why"]


def test_reference_sql_error_is_fail_not_crash():
    def boom(sql):
        raise RuntimeError("db down")
    out = r.run_item(Agent([]), {**ITEM, "reference_sql": "select 1"}, execute=boom,
                     tries=2, sleep=lambda s: None)
    assert out["ok"] is False and "RuntimeError" in out["why"]


def test_guard_rejection_is_not_retried():
    a = Agent([_res("query rejected by guard: not a SELECT")])
    r.run_item(a, ITEM, execute=None, tries=3, sleep=lambda s: None)
    assert a.calls == 1

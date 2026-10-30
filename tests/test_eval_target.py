"""The eval target must follow the real item count, and a run must leave a committable report."""
from evals import run_ask_eval as r


def test_target_scales_with_item_count():
    n = len(r.load_items())
    assert n == 23
    assert r.target_for(n) == 19          # 80% rounded up, not the old fixed 16 (from 20 items)


def test_docstring_no_longer_claims_20_items():
    assert "/20" not in (r.__doc__ or "")


def test_report_lists_every_question(tmp_path):
    rows = [{"id": "a", "ok": True, "why": "match", "model": "m1", "latency_ms": 10},
            {"id": "b", "ok": False, "why": "wrong_result: x", "model": "m1", "latency_ms": 20}]
    out = tmp_path / "rep.md"
    r.write_report(out, rows, date="2026-10-30")
    md = out.read_text(encoding="utf-8")
    assert "1/2" in md and "2026-10-30" in md and "m1" in md
    assert "| a | PASS" in md and "| b | FAIL" in md


def test_report_dir_is_not_gitignored():
    import subprocess
    p = subprocess.run(["git", "check-ignore", "-q", "evals/results/ask_eval_2026-10-30.md"], cwd=r.BASE)
    assert p.returncode == 1

"""Run the /ask eval against the real DB + NIM.

    python evals/run_ask_eval.py [--only ID ...] [--out evals/last_run.json]

Needs DB_* and NIM_API_KEY in .env. Read-only: every query runs through the
same read-only executor the bot uses. Target: >= 16/20.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from dotenv import load_dotenv  # noqa: E402

from agents.ask.agent import AskAgent  # noqa: E402
from agents.ask.runtime import make_nim_llm, make_pg_executor  # noqa: E402
from evals.checks import check  # noqa: E402

EVAL_FILE = BASE / "evals" / "ask_eval.yaml"
TARGET = 16


def load_items(path: Path = EVAL_FILE) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        import yaml  # optional; only if the file is hand-edited into block YAML
        data = yaml.safe_load(text)
    return data["items"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--out", default=str(BASE / "evals" / "last_run.json"))
    a = ap.parse_args()
    load_dotenv(BASE / ".env")

    execute = make_pg_executor()
    agent = AskAgent(make_nim_llm(), execute, llm_attempts=1, cache_size=0)
    items = [i for i in load_items() if not a.only or i["id"] in a.only]

    results, passed = [], 0
    for it in items:
        ref_cols, ref_rows = ([], [])
        if it.get("reference_sql"):
            c, r = execute(it["reference_sql"])
            ref_cols, ref_rows = list(c), [list(x) for x in r]
        res = agent.ask(it["question"])
        ok, why = check(it["check"], ref_cols, ref_rows, res.rows, res.answer)
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {it['id']:<32} {why[:100]}")
        results.append({"id": it["id"], "ok": ok, "why": why, "sql": res.sql,
                        "answer": res.answer, "error": res.error,
                        "attempts": res.attempts, "latency_ms": res.latency_ms})

    print(f"\nAccuracy: {passed}/{len(items)}  (target {TARGET}/{len(items)})")
    Path(a.out).write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str),
                           encoding="utf-8")
    return 0 if passed >= min(TARGET, len(items)) else 1


if __name__ == "__main__":
    raise SystemExit(main())

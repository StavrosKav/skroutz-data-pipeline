"""Model x prompt comparison for the /ask eval.

    python evals/compare.py --models ultra bolt gptoss --prompt-variant v1 [--tag r1]
    python evals/compare.py --report          # rebuild evals/RESULTS.md only

One JSON per (model, variant, tag) in evals/results/. Fallback is disabled so
every number belongs to the model named. Read-only DB (same executor as the
bot). Needs NIM_API_KEY / DB_* in .env; nothing secret is written out.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

RESULTS_DIR = BASE / "evals" / "results"
RESULTS_MD = BASE / "evals" / "RESULTS.md"
REASONING_HEADROOM = 2500
REQUEST_TIMEOUT_S = 120.0

MODELS = {
    "ultra": "nvidia/nemotron-3-ultra-550b-a55b",
    "bolt": "nvidia/nemotron-3.5-bolt-e4b",
    "gptoss": "openai/gpt-oss-20b",
}
FAILURE_TYPES = ("no_sql", "invalid_sql", "wrong_result", "timeout", "api_error")


def resolve_model(name: str) -> str:
    return MODELS.get(name, name)


class RecordingLLM:
    """LLM callable for one fixed model (no fallback) that logs every call."""

    def __init__(self, model: str, chat=None):
        self.model = model
        self.calls: list[dict] = []
        self._chat = chat or self._nim_chat

    async def _nim_chat(self, messages, max_tokens):
        from alerts.nim_client import NIMClient
        async with NIMClient(default_timeout=REQUEST_TIMEOUT_S) as c:
            r = await c.chat(messages, task=self.model, temperature=0.0,
                             max_tokens=max_tokens + REASONING_HEADROOM, fallback=False)
            return r.content, r.model, r.usage

    def __call__(self, messages: list[dict], max_tokens: int) -> str:
        t0 = time.perf_counter()
        rec: dict[str, Any] = {"model": self.model}
        try:
            content, model, usage = asyncio.run(self._chat(messages, max_tokens))
            rec.update(model=model, content=content or "",
                       prompt_tokens=int((usage or {}).get("prompt_tokens", 0)),
                       completion_tokens=int((usage or {}).get("completion_tokens", 0)))
            return content or ""
        except Exception as exc:
            rec["error"] = type(exc).__name__
            raise
        finally:
            rec["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            self.calls.append(rec)


def classify(ok: bool, error: str | None, sql: str | None, calls: list[dict]) -> str | None:
    """None when passed, else one of FAILURE_TYPES."""
    if ok:
        return None
    errs = [c.get("error", "") for c in calls if c.get("error")]
    if any("Timeout" in e for e in errs) or (error and "Timeout" in error):
        return "timeout"
    if errs:
        return "api_error"
    if sql is None and error:
        raw = " ".join(c.get("content", "") for c in calls).lower()
        return "invalid_sql" if ("select" in raw or "with" in raw) else "no_sql"
    return "wrong_result"


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def summarize(rows: list[dict]) -> dict:
    lat = [r["latency_ms"] / 1000 for r in rows]
    fails = Counter(r["failure"] for r in rows if r["failure"])
    n = len(rows) or 1
    return {
        "n": len(rows),
        "passed": sum(r["ok"] for r in rows),
        "sql_valid": sum(r["sql_valid"] for r in rows),
        "median_s": round(statistics.median(lat), 1) if lat else 0.0,
        "p90_s": round(_pct(lat, 0.9), 1),
        "prompt_tokens_avg": round(sum(r["prompt_tokens"] for r in rows) / n),
        "completion_tokens_avg": round(sum(r["completion_tokens"] for r in rows) / n),
        "failures": {k: fails.get(k, 0) for k in FAILURE_TYPES},
        "models_used": sorted({m for r in rows for m in r["models_used"]}),
    }


def run_one(model_key: str, variant: str, items, execute, check, llm=None) -> dict:
    from agents.ask.agent import AskAgent
    from evals.prompts import sql_system

    model = resolve_model(model_key)
    llm = llm or RecordingLLM(model)
    agent = AskAgent(llm, execute, llm_attempts=1, cache_size=0, sql_system=sql_system(variant))
    rows = []
    for it in items:
        ref_cols, ref_rows = [], []
        if it.get("reference_sql"):
            c, r = execute(it["reference_sql"])
            ref_cols, ref_rows = list(c), [list(x) for x in r]
        llm.calls.clear()
        t0 = time.perf_counter()
        res = agent.ask(it["question"])
        wall = int((time.perf_counter() - t0) * 1000)
        ok, why = check(it["check"], ref_cols, ref_rows, res.rows, res.answer)
        calls = list(llm.calls)
        row = {
            "id": it["id"], "ok": bool(ok), "why": why,
            "failure": classify(ok, res.error, res.sql, calls),
            "sql_valid": res.sql is not None and res.error is None,
            "sql": res.sql, "error": res.error, "latency_ms": wall,
            "llm_calls": len(calls),
            "prompt_tokens": sum(c.get("prompt_tokens", 0) for c in calls),
            "completion_tokens": sum(c.get("completion_tokens", 0) for c in calls),
            "models_used": sorted({c.get("model", model) for c in calls}),
        }
        print(f"{'PASS' if ok else 'FAIL'} {model_key:<7}{variant} {it['id']:<30} "
              f"{wall/1000:6.1f}s {row['failure'] or ''}", flush=True)
        rows.append(row)
    return {"model": model, "model_key": model_key, "variant": variant,
            "summary": summarize(rows), "items": rows}


def load_runs(d: Path = RESULTS_DIR) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))]


def render_markdown(runs: list[dict]) -> str:
    out = ["# /ask eval: model x prompt comparison", "",
           "Generated by `evals/compare.py --report`. 20 Greek questions over the real "
           "Skroutz DB (read-only); pass = result values match a live reference query. "
           "Fallback disabled, temperature 0. **Small set: one question = 5 points, so "
           "differences of 1-2 questions are within noise.**", "",
           "| Run | Model | Prompt | Accuracy | Valid SQL | Median s | p90 s | "
           "Prompt tok/q | Compl. tok/q | no_sql | invalid_sql | wrong_result | timeout | api_error |",
           "|" + "---|" * 14]
    for r in runs:
        s = r["summary"]
        f = s["failures"]
        out.append(
            f"| {r.get('tag', '')} | `{r['model']}` | {r['variant']} | "
            f"{s['passed']}/{s['n']} | {s['sql_valid']}/{s['n']} | {s['median_s']} | {s['p90_s']} | "
            f"{s['prompt_tokens_avg']} | {s['completion_tokens_avg']} | "
            + " | ".join(str(f.get(k, 0)) for k in FAILURE_TYPES) + " |")
    out += ["", "## Per-question pass matrix", ""]
    ids = [i["id"] for i in runs[0]["items"]] if runs else []
    hdr = [f"{r['model_key']}/{r['variant']}/{r.get('tag', '')}" for r in runs]
    out.append("| Question | " + " | ".join(hdr) + " |")
    out.append("|---|" + "---|" * len(runs))
    for qid in ids:
        cells = []
        for r in runs:
            it = next((x for x in r["items"] if x["id"] == qid), None)
            cells.append("-" if it is None else ("✓" if it["ok"] else (it["failure"] or "✗")))
        out.append(f"| {qid} | " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def write_report(conclusions_path: Path | None = None) -> Path:
    md = render_markdown(load_runs())
    cpath = conclusions_path or (BASE / "evals" / "CONCLUSIONS.md")
    if cpath.exists():
        md += "\n" + cpath.read_text(encoding="utf-8")
    RESULTS_MD.write_text(md, encoding="utf-8")
    return RESULTS_MD


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=["ultra"])
    ap.add_argument("--prompt-variant", default="v1", choices=["v0", "v1", "v2"])
    ap.add_argument("--tag", default="r1")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--report", action="store_true", help="only rebuild RESULTS.md")
    a = ap.parse_args()
    if a.report:
        print(write_report())
        return 0

    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")
    from agents.ask.runtime import make_pg_executor
    from evals.checks import check
    from evals.run_ask_eval import load_items

    execute = make_pg_executor()
    items = [i for i in load_items() if not a.only or i["id"] in a.only]
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    for m in a.models:
        run = run_one(m, a.prompt_variant, items, execute, check)
        run["tag"] = a.tag
        run["finished_at"] = datetime.now().isoformat(timespec="seconds")
        p = RESULTS_DIR / f"{m}_{a.prompt_variant}_{a.tag}.json"
        p.write_text(json.dumps(run, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        s = run["summary"]
        print(f"== {m} {a.prompt_variant} {a.tag}: {s['passed']}/{s['n']} "
              f"median {s['median_s']}s p90 {s['p90_s']}s -> {p.name}", flush=True)
    write_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

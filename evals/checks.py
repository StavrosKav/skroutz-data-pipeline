"""Result checks for the /ask eval: compare values, never answer text."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from agents.ask.agent import NO_DATA, OFF_TOPIC


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    try:
        return float(str(v).replace("€", "").replace(",", ".").strip())
    except ValueError:
        return None


def same(a: Any, b: Any, tol: float = 0.0) -> bool:
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        return abs(na - nb) <= max(tol * abs(nb), 0.005)
    return str(a).strip().lower() == str(b).strip().lower()


def _in_row(val: Any, row: Sequence[Any], tol: float) -> bool:
    return any(same(c, val, tol) for c in row)


def check(spec: dict, ref_cols: list[str], ref_rows: list[list[Any]],
          got_rows: list[list[Any]], answer: str) -> tuple[bool, str]:
    kind = spec["type"]
    tol = float(spec.get("tol", 0.0))
    if kind == "no_data":
        ok = not got_rows or answer.strip() in (NO_DATA, OFF_TOPIC)
        return ok, "expected no data"
    if not ref_rows:
        ok = not got_rows or answer.strip() == NO_DATA
        return ok, "reference empty → expect no data"
    if not got_rows:
        return False, "agent returned no rows"
    idx = ref_cols.index(spec["key"])
    if kind in ("value", "top1"):
        want = ref_rows[0][idx]
        if _in_row(want, got_rows[0], tol):
            return True, f"{spec['key']}={want}"
        alt = spec.get("alt_key")
        if alt:
            w2 = ref_rows[0][ref_cols.index(alt)]
            if _in_row(w2, got_rows[0], tol):
                return True, f"{alt}={w2}"
        return False, f"expected {spec['key']}={want} in first row, got {got_rows[0]}"
    if kind == "set":
        want = [r[idx] for r in ref_rows]
        flat = [c for r in got_rows for c in r]
        hit = sum(any(same(c, w, tol) for c in flat) for w in want)
        need = float(spec.get("min_overlap", 1.0))
        return hit / len(want) >= need, f"{hit}/{len(want)} reference values found"
    raise ValueError(f"unknown check type {kind}")

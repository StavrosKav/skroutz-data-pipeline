"""Guard for LLM prose before it reaches Telegram.

Incident 2026-11-16: the /ask answer step degenerated into "dao dao dao ..."
(11,920 chars); Telegram rejected it (400, > 4096 chars) and the user got nothing.
`is_degenerate` flags runaway/looping text; `fallback_answer` builds a short,
deterministic answer straight from the query rows so the user still gets the data.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

SUMMARY_FAILED = "⚠️ Η σύνοψη απέτυχε· δες τα δεδομένα από τη βάση:"
MAX_ANSWER_CHARS = 1500   # ANSWER_SYSTEM asks for <= 5 lines; anything far longer is runaway
MAX_NGRAM = 4             # look for loops of 1..4 tokens
MAX_REPEATS = 6           # the same n-gram 6+ times in a row is a loop
FALLBACK_ROWS = 10
FALLBACK_LINE_CHARS = 200

_TOKEN = re.compile(r"\w+|[^\w\s]")


def is_degenerate(text: str) -> bool:
    """True when the text is too long for a short answer or loops on an n-gram."""
    if len(text) > MAX_ANSWER_CHARS:
        return True
    toks = _TOKEN.findall(text.lower())
    for n in range(1, MAX_NGRAM + 1):
        run = 0  # consecutive positions where the sequence repeats with period n
        for i in range(n, len(toks)):
            if toks[i] == toks[i - n]:
                run += 1
                if run >= n * (MAX_REPEATS - 1):
                    return True
            else:
                run = 0
    return False


def _cell(v: Any) -> str:
    return "—" if v is None else str(v)


def fallback_answer(cols: Sequence[str], rows: Sequence[Sequence[Any]],
                    max_rows: int = FALLBACK_ROWS) -> str:
    """Short plain-text answer built only from the rows (no LLM)."""
    lines = [SUMMARY_FAILED]
    for r in rows[:max_rows]:
        line = " · ".join(f"{c}: {_cell(v)}" for c, v in zip(cols, r))
        if len(line) > FALLBACK_LINE_CHARS:
            line = line[:FALLBACK_LINE_CHARS - 1] + "…"
        lines.append(f"• {line}")
    if len(rows) > max_rows:
        lines.append(f"… και {len(rows) - max_rows} ακόμη (σύνολο {len(rows)} γραμμές)")
    return "\n".join(lines)

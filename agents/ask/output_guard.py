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
MAX_REPEATS = 6           # the same word n-gram 6+ times in a row is a loop ("dao dao dao ...")
# Units with no letters (numbers, €, |, -, .) repeat legitimately in data answers
# ("10.00, 10.00, ...", "|------|", "......"); only a far longer run counts. Kept above
# ROWS_IN_PROMPT (30) so listing one identical value for every row is never flagged.
MAX_REPEATS_NON_WORD = 32
FALLBACK_ROWS = 10
FALLBACK_LINE_CHARS = 200

_TOKEN = re.compile(r"\w+|[^\w\s]")
_THINK_CLOSED = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_OPEN = re.compile(r"<think>.*\Z", re.IGNORECASE | re.DOTALL)
_GREEK = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]")
_LATIN = re.compile(r"[a-zA-Z]")


def strip_reasoning(text: str) -> str:
    """Drop <think>...</think> blocks (and an unclosed one cut off by max_tokens)."""
    return _THINK_OPEN.sub("", _THINK_CLOSED.sub("", text or "")).strip()


def _has_letter(tokens: list[str]) -> bool:
    return any(any(ch.isalpha() for ch in t) for t in tokens)


def degenerate_reason(text: str) -> str | None:
    """Why the text is unusable as an answer, or None when it looks fine.

    Rules: empty; too long for a short answer; U+FFFD replacement char (broken decoding);
    a word mixing Latin and Greek letters ("daoflouktuάει"); a word n-gram looping 6+ times;
    a non-word unit (digits/punctuation) looping 32+ times."""
    if not text or not text.strip():
        return "empty"
    if len(text) > MAX_ANSWER_CHARS:
        return f"too_long({len(text)}>{MAX_ANSWER_CHARS})"
    if "\ufffd" in text:
        return "replacement_char"
    toks = _TOKEN.findall(text.lower())
    for t in toks:
        if _GREEK.search(t) and _LATIN.search(t):
            return f"mixed_script({t[:40]})"
    for n in range(1, MAX_NGRAM + 1):
        run = 0  # consecutive positions where the sequence repeats with period n
        for i in range(n, len(toks)):
            if toks[i] != toks[i - n]:
                run = 0
                continue
            run += 1
            unit = toks[i - n + 1:i + 1]
            limit = MAX_REPEATS if _has_letter(unit) else MAX_REPEATS_NON_WORD
            if run >= n * (limit - 1):
                return f"loop(n={n}, unit={' '.join(unit)[:40]!r})"
    return None


def is_degenerate(text: str) -> bool:
    """True when the text is runaway, looping or corrupted (see degenerate_reason)."""
    return degenerate_reason(text) is not None


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

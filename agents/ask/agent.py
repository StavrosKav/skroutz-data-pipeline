"""AskAgent: question -> guarded SQL -> rows -> grounded Greek answer.

Flow
  1. LLM writes one SELECT using SCHEMA_CONTEXT (prompt kept small).
  2. guard.validate_sql: SELECT-only, single statement, allowlist, LIMIT.
  3. executor runs it (read-only txn + statement_timeout, see db.py).
  4. On a guard or DB error: one self-correction round with the error text.
  5. Empty result -> fixed "δεν υπάρχει στα δεδομένα" (no LLM call).
  6. LLM answers in Greek from the rows only; the SQL is shown to the user.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from agents.ask.guard import SQLGuardError, validate_sql
from agents.ask.retry import call_with_retry
from agents.ask.schema import SCHEMA_CONTEXT

# llm(messages, max_tokens) -> text
LLM = Callable[[list[dict], int], str]
# executor(sql) -> (column names, rows)
Executor = Callable[[str], tuple[Sequence[str], Sequence[Sequence[Any]]]]

OFF_TOPIC = ("Μπορώ να απαντήσω μόνο σε ερωτήσεις για τιμές κινητών, laptops, tablets "
             "και smartwatches στο Skroutz, π.χ. /ask φθηνότερο iPhone 15 σήμερα.")
NO_DATA = "Δεν υπάρχει στα δεδομένα."
MAX_QUESTION_CHARS = 500
SQL_MAX_TOKENS = 400
ANSWER_MAX_TOKENS = 350
ROWS_IN_PROMPT = 30
ROWS_JSON_MAX_CHARS = 6000

SQL_SYSTEM = (
    "You translate Greek or English questions about Greek electronics prices (skroutz.gr) "
    "into ONE read-only PostgreSQL SELECT. Use only these relations:\n"
    f"{SCHEMA_CONTEXT}\n"
    "Rules: output only SQL inside ```sql ... ```. One statement. No writes. "
    "Always ORDER BY when ranking and add LIMIT <= 50. Return readable columns "
    "(product_name, brand, price_eur...). If the question cannot be answered from "
    "these tables, output exactly: SELECT 'NO_DATA' AS no_data WHERE false"
)

ANSWER_SYSTEM = (
    "Απαντάς στα ελληνικά, σύντομα (έως 5 γραμμές), ΜΟΝΟ από τις γραμμές αποτελέσματος "
    "που σου δίνονται. Μην προσθέτεις γνώση εκτός δεδομένων, μην μαντεύεις. Τιμές σε €. "
    "Αν οι γραμμές δεν απαντούν στην ερώτηση, γράψε: 'Δεν υπάρχει στα δεδομένα.'"
)


@dataclass
class AskResult:
    question: str
    answer: str
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    error: str | None = None
    attempts: int = 0
    cached: bool = False
    latency_ms: int = 0


def extract_sql(text: str) -> str:
    m = re.search(r"```(?:sql)?\s*(.+?)```", text, re.DOTALL | re.IGNORECASE)
    sql = m.group(1) if m else text
    # strip stray/unbalanced fences and a leading "sql" language tag
    sql = re.sub(r"```(?:sql)?", "", sql, flags=re.IGNORECASE).strip()
    sql = re.sub(r"^sql\s*\n", "", sql, flags=re.IGNORECASE)
    return sql.strip().rstrip(";").strip()


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (int, float, str, bool)):
        return v
    return str(v)  # Decimal, date, ...


class AskAgent:
    def __init__(self, llm: LLM, executor: Executor, *, cache_size: int = 128,
                 cache_ttl_s: float = 3600, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, llm_attempts: int = 3,
                 sql_system: str | None = None):
        self.llm = llm
        self.executor = executor
        self.cache_size = cache_size
        self.cache_ttl_s = cache_ttl_s
        self._clock = clock
        self._sleep = sleep
        self.llm_attempts = llm_attempts
        self.sql_system = sql_system or SQL_SYSTEM
        self._cache: dict[str, tuple[float, AskResult]] = {}

    # ── cache ────────────────────────────────────────────────────────────
    @staticmethod
    def _key(q: str) -> str:
        return " ".join(q.lower().split())

    def _cache_get(self, q: str) -> AskResult | None:
        hit = self._cache.get(self._key(q))
        if hit and self._clock() - hit[0] < self.cache_ttl_s:
            return hit[1]
        return None

    def _cache_put(self, q: str, r: AskResult) -> None:
        if self.cache_size <= 0:
            return
        if len(self._cache) >= self.cache_size:
            self._cache.pop(next(iter(self._cache)))
        self._cache[self._key(q)] = (self._clock(), r)

    # ── steps ────────────────────────────────────────────────────────────
    def _llm(self, messages: list[dict], max_tokens: int) -> str:
        return call_with_retry(lambda: self.llm(messages, max_tokens),
                               attempts=self.llm_attempts, sleep=self._sleep)

    def generate_sql(self, question: str, feedback: str | None = None,
                     previous_sql: str | None = None) -> str:
        msgs = [{"role": "system", "content": self.sql_system},
                {"role": "user", "content": question}]
        if feedback:
            msgs.append({"role": "assistant", "content": f"```sql\n{previous_sql}\n```"})
            msgs.append({"role": "user", "content":
                         f"That SQL failed: {feedback[:400]}\nFix it. Output only the corrected SQL."})
        return extract_sql(self._llm(msgs, SQL_MAX_TOKENS))

    def _run_sql(self, question: str) -> tuple[str, list[str], list[list[Any]], int]:
        """Generate, guard, execute; one self-correction round on failure."""
        raw = self.generate_sql(question)
        for attempt in (1, 2):
            try:
                safe = validate_sql(raw)
                cols, rows = self.executor(safe)
                return safe, list(cols), [list(r) for r in rows], attempt
            except Exception as exc:  # guard or DB error
                if attempt == 2:
                    raise
                msg = str(exc) if isinstance(exc, SQLGuardError) else f"{type(exc).__name__}: {exc}"
                raw = self.generate_sql(question, feedback=msg, previous_sql=raw)
        raise AssertionError("unreachable")

    def answer_from_rows(self, question: str, cols: list[str], rows: list[list[Any]]) -> str:
        data = [dict(zip(cols, map(_jsonable, r))) for r in rows[:ROWS_IN_PROMPT]]
        blob = json.dumps(data, ensure_ascii=False)[:ROWS_JSON_MAX_CHARS]
        note = f" (εμφανίζονται {len(data)} από {len(rows)})" if len(rows) > len(data) else ""
        msgs = [{"role": "system", "content": ANSWER_SYSTEM},
                {"role": "user", "content": f"Ερώτηση: {question}\nΓραμμές{note}:\n{blob}"}]
        return self._llm(msgs, ANSWER_MAX_TOKENS).strip()

    # ── public ───────────────────────────────────────────────────────────
    def ask(self, question: str) -> AskResult:
        t0 = self._clock()
        question = (question or "").strip()[:MAX_QUESTION_CHARS]
        if not question:
            return AskResult(question, "Γράψε μια ερώτηση, π.χ. /ask φθηνότερο iPhone 15")
        cached = self._cache_get(question)
        if cached:
            cached.cached = True
            return cached
        try:
            sql, cols, rows, attempts = self._run_sql(question)
        except SQLGuardError as exc:
            return AskResult(question, "Δεν μπόρεσα να φτιάξω ασφαλές ερώτημα.", error=str(exc), attempts=2)
        except Exception as exc:  # noqa: BLE001
            return AskResult(question, "Σφάλμα κατά την εκτέλεση του ερωτήματος.",
                             error=f"{type(exc).__name__}", attempts=2)
        if cols == ["no_data"]:
            res = AskResult(question, OFF_TOPIC, sql, cols, rows, attempts=attempts)
        elif not rows:
            res = AskResult(question, NO_DATA, sql, cols, rows, attempts=attempts)
        else:
            res = AskResult(question, self.answer_from_rows(question, cols, rows), sql, cols, rows,
                            attempts=attempts)
        res.latency_ms = int((self._clock() - t0) * 1000)
        self._cache_put(question, res)
        return res


def format_telegram(res: AskResult) -> str:
    """HTML for Telegram: answer + the SQL used (grounding/transparency)."""
    import html
    out = [html.escape(res.answer)]
    if res.sql:
        out.append(f"\n<b>SQL</b> ({len(res.rows)} γραμμές{', cache' if res.cached else ''}):")
        out.append(f"<pre>{html.escape(res.sql[:1500])}</pre>")
    if res.error:
        out.append(f"<i>{html.escape(res.error[:300])}</i>")
    return "\n".join(out)

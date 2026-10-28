# `/ask` — grounded text-to-SQL agent

Ask the price database a question in Greek (or English) from Telegram and get
an answer built **only** from rows returned by a read-only SQL query, with the
SQL shown underneath.

```
/ask ποιο είναι το φθηνότερο iPhone 15 σήμερα;
```

## Architecture

```mermaid
flowchart TD
  U["Telegram /ask question"] --> C{"Cache hit?"}
  C -- yes --> R["Reply: answer + SQL"]
  C -- no --> G["LLM (NVIDIA NIM)\nquestion + curated schema → SQL"]
  G --> V["SQL guard\nSELECT-only · 1 statement\ntable allowlist · LIMIT ≤ 200"]
  V -- rejected --> F["Self-correct once\n(error fed back to LLM)"]
  V -- ok --> X["PostgreSQL\nREAD ONLY txn · statement_timeout 5s"]
  X -- SQL error --> F
  F --> V
  X -- 0 rows --> N["'Δεν υπάρχει στα δεδομένα.'"]
  X -- rows --> A["LLM: Greek answer\nfrom rows only (≤30 rows, token cap)"]
  A --> R
  N --> R
```

| Piece | File | Notes |
|---|---|---|
| Orchestration | `agents/ask/agent.py` | Pure: LLM and executor are injected callables. Cache (LRU-ish, 1 h TTL), token caps, one self-correction round. |
| SQL guard | `agents/ask/guard.py` | Strips strings/comments, then: first word SELECT/WITH, no `;`, no DDL/DML/`SET`/`COPY`/`INTO`, no dangerous functions (`pg_sleep`, `dblink`, …), only allowlisted relations or CTEs, LIMIT enforced. |
| Schema context | `agents/ask/schema.py` | ~15 lines describing the 9 allowlisted relations. Same list is the allowlist. |
| Retries | `agents/ask/retry.py`, `alerts/nim_client.py` | Retry only timeouts, network errors, 429, 5xx. Other 4xx fail fast. |
| Runtime adapters | `agents/ask/runtime.py` | NIM LLM + read-only Postgres executor. The only file that touches network/DB. |
| Telegram | `alerts/telegram_nim.py` (`cmd_ask`) | Registered with the other AI commands. |

Defence in depth: even if the guard missed something, the query runs inside
`SET TRANSACTION READ ONLY` with `SET LOCAL statement_timeout`, and the
transaction is always rolled back. For production, also use a dedicated DB
role with `SELECT` on the allowlisted relations only.

## Evaluation

`evals/ask_eval.yaml` holds 20 Greek questions (cheapest product, biggest drop
this week, counts per category, price history, ATL, ratings, one out-of-scope
question). Each has a **reference SQL** run live on the DB; the check compares
result *values* (top-1, scalar with tolerance, or set overlap), never answer text.

```
python evals/run_ask_eval.py          # prints PASS/FAIL per item + accuracy
```

Target: **≥ 16/20**. Per-item SQL, answer, attempts and latency are written to
`evals/last_run.json` (git-ignored).

Unit tests (`tests/test_ask_agent.py`) use a fake LLM and fake executor: guard
rejections, LIMIT capping, empty-result path, self-correction, cache, retry policy.

# AGENTS.md — Skroutz Price Tracker

## Environment
- Python: `.venv\Scripts\python.exe` — always use this full path
- Project:  `C:\Users\StavrosKV\Documents\Projects\ProjectsPY`
- Shell: PowerShell (Windows 11). Use PowerShell syntax for all commands.
- Git user: StavrosKav / branch: main
- IDE: VS Code with Codex extension

## Project Overview
Daily price-tracking pipeline for skroutz.gr (Greece's largest e-commerce aggregator).

Pipeline stages (run sequentially by `run_pipeline.py`):
  1. Scrape   → `1scriptToGet4.py`              raw CSVs per category
  2. Clean    → `1scriptToGet4MANIPULATION.py`  cleaned CSVs to Clean/
  3. Load SQL → `4csvsTOsql.py`                 upserts into PostgreSQL

Post-pipeline (non-fatal, each runs independently after Load SQL):
  - `refresh_matviews()`        → REFRESH MATERIALIZED VIEW CONCURRENTLY for the 10 mv_* views (analytics.sql v4); runs first, everything downstream reads them
  - `run_charts()`              → charts/price_trend_*.png
  - `send_drop_digest()`        → Gmail: today's top 10 price drops
  - `send_watchlist_alerts()`   → Gmail: watchlist.json threshold hits
  - `send_disappeared_alert()`  → Gmail: products not seen in 1–2 days
  - `run_dashboard()`           → dashboard/dashboard_latest.html
  - `update_readme_stats()`     → rewrites README.md's STATS:BADGES/STATS:TABLE blocks from live DB counts
  - `send_success_summary(elapsed)` → Gmail: daily OK summary (snapshots, new products, drop count)

Automation: Windows Task Scheduler at 08:00 via `run_pipeline.bat`.

## Key Files
| File | Role |
|---|---|
| `run_pipeline.py` | Master orchestrator — do not modify lightly |
| `db.py` | SQLAlchemy engine singleton — `get_engine()` creates once per process; uses `URL.create()` to handle special chars (%, @, :) in DB_PASSWORD |
| `1scriptToGet4.py` | Stage 1: launches 4 scrapers in parallel (subprocess) |
| `1scriptToGet4MANIPULATION.py` | Stage 2: launches 4 cleaners in parallel |
| `4csvsTOsql.py` | Stage 3: upserts to PostgreSQL |
| `scraper_core.py` | Shared scraping engine — `scrape(CONFIGS[cat])`: pagination, card parsing, bounded retries, markup-drift guard, atomic CSV writes |
| `clean_common.py` | Shared cleaning engine — `run_clean(CleanerConfig)`: clean_price, brand/model split, installments, review-count recovery |
| `skroutz_phonesWHILE.py` | Scraper entry point — phones → Phones_skroutz/ |
| `skroutz_laptopsWHILE.py` | Scraper entry point — laptops → Laptops_skroutz/ |
| `skroutz_tabletsWHILE.py` | Scraper entry point — tablets → Tablets_skroutz/ |
| `skroutz_SmartwatchesWHILE.py` | Scraper entry point — smartwatches → Smartwatches_skroutz/ |
| `Data_Phone.py` | Cleaner entry point — phones (adds RAM/camera/display/battery enrichment) |
| `Data_Laptops.py` | Cleaner entry point — laptops |
| `Data_Tablets.py` | Cleaner entry point — tablets |
| `Data_Smartwatches.py` | Cleaner entry point — smartwatches |
| `charts_from_db.py` | Brand price-trend charts (dark-themed PNG per category) |
| `generate_dashboard.py` | Self-contained HTML dashboard from PostgreSQL |
| `analytics.sql` | 15 views: run once against DB to enable all analytics |
| `watchlist.json` | Price alert targets (array of {url, label, threshold_eur}) |
| `run_pipeline.bat` | Task Scheduler launcher — update PYTHON path inside before registering |
| `notifications.py` | Telegram notification layer — HTML parse mode, dedup, inline buttons, retry |
| `telegram_bot.py` | Interactive Telegram bot — long-polling; /status /drops /watchlist /add /remove /find /stats /history /best /restock /cancel; URL→price conversation flow for adding watchlist items |
| `streamlit_app.py` | Interactive Streamlit dashboard; live DB queries cached 1h; runs at localhost:8501 |
| `tests/test_pipeline.py` | pytest test suite (unit tests for pipeline, DB helpers, notifications) |
| `create_new_schema.sql` | DDL for a fresh PostgreSQL install (run once on a new DB) |

## Database
- Engine: PostgreSQL 16
- Name: `SkroutzPR`
- Connection: via `.env` → DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
- Helper: `db.py` exports `get_engine()` — use this everywhere; never inline credentials
- Schema: `products` (static metadata) + `price_snapshots` (daily rows)
- Key constraint: `price_snapshots` has UNIQUE(product_id, date) — pipeline is re-run safe
- Analytics views (run analytics.sql once to create, 15 views — see README's Analytics Views table for the full list). 10 of the 15 are backed by MATERIALIZED VIEWs (`mv_*`, analytics.sql v4 section) refreshed daily by `refresh_matviews()` — view names/columns unchanged, consumers unaffected
- pg_trgm GIN trigram indexes on `products.brand/model/product_name` and `mv_latest_prices.brand/model` power leading-wildcard ILIKE search (`/find`, `/history`) — sequential scan otherwise
- Scale: see README (auto-updated) — Live Market Snapshot table + Products/Snapshots badges

## Running Code
```powershell
# Standard Python execution
& ".venv\Scripts\python.exe" <script.py>

# Full pipeline
& ".venv\Scripts\python.exe" run_pipeline.py

# Charts only (needs DB connection)
& ".venv\Scripts\python.exe" charts_from_db.py

# Dashboard only
& ".venv\Scripts\python.exe" generate_dashboard.py

# Skip scraping, run Clean + Load only (for Docker workflow)
$env:SKIP_SCRAPE = "1"
& ".venv\Scripts\python.exe" run_pipeline.py

# Streamlit dashboard (runs until stopped — separate terminal)
& ".venv\Scripts\python.exe" -m streamlit run streamlit_app.py
# Opens at http://localhost:8501

# Telegram bot (runs until stopped — separate terminal)
& ".venv\Scripts\python.exe" telegram_bot.py

# Run tests
& ".venv\Scripts\python.exe" -m pytest tests/ -v

# Lint check
& ".venv\Scripts\python.exe" -m ruff check .
```

## Services
Two long-running processes run independently of the pipeline:

| Service | Command | Port/Channel |
|---|---|---|
| Streamlit dashboard | streamlit run streamlit_app.py | http://localhost:8501 |
| Telegram bot | python telegram_bot.py | Telegram (TELEGRAM_CHAT_ID in .env) |

Both require DB_* vars in .env. Neither is called by run_pipeline.py —
start them manually or as separate Task Scheduler / Windows Service entries.

## Testing
& ".venv\Scripts\python.exe" -m pytest tests/ -v

- Test file: tests/test_pipeline.py
- Covers: DB helpers, pipeline stage entry points, notification dedup logic
- Does NOT run scrapers or write to the live DB — uses mocks/fixtures
- Lint: & ".venv\Scripts\python.exe" -m ruff check .

## Logs & Outputs
- Pipeline log: `logs/pipeline_YYYY-MM-DD.log` (created daily)
- Scraper logs: `logs/skroutz_phonesWHILE_YYYY-MM-DD.log`, `logs/Data_Phone_YYYY-MM-DD.log`, etc. (in logs/ folder, one file per scraper/cleaner per day)
- Charts output: `charts/price_trend_{phone,laptop,smartwatch,tablet}.png`
- Dashboard: `dashboard/dashboard_latest.html` (+ dated copy)
- Telegram dedup cache: `logs/tg_sent_YYYY-MM-DD.json` (auto-created by notifications.py; prevents duplicate alerts)
- Run lock: `pipeline.lock` (blocks concurrent pipeline runs; auto-removed on exit, reclaimed if older than 2h)

## Docker Notes
CRITICAL: Scrapers CANNOT run in Docker — Skroutz bot-detection blocks headless Chrome.
Docker workflow: run scrapers on Windows first, then `docker compose up --build` for stages 2+3.
The SKIP_SCRAPE=1 env var is set automatically in docker-compose.yml.

## Coding Conventions
- No comments unless the WHY is non-obvious (not what the code does)
- No error handling for impossible scenarios — trust framework guarantees
- Credentials always via `.env` / `get_engine()` — never hardcoded
- CSVs are gitignored — never commit data files under any circumstance
- All scripts resolve BASE = os.path.dirname(os.path.abspath(__file__)) — always use this pattern
- Each scraper writes to its own dated CSV: e.g. Phones_skroutz/skroutz_phones_YYYY-MM-DD.csv (PascalCase folder, skroutz_ prefix). Laptops files before 2026-07-17 use the historical "skroutz_laptos_" typo; Data_Laptops.py falls back to it automatically

## What NOT To Do
- NEVER use bare `python` — always the venv path above
- NEVER create new files without explicit instruction
- NEVER touch `.env` — it contains live production credentials
- NEVER add backwards-compatibility shims — change the code directly
- NEVER add features or abstractions beyond what the task asks for
- NEVER commit anything in Phones_skroutz/, Laptops_skroutz/, Tablets_skroutz/, Smartwatches_skroutz/
- NEVER run scrapers inside Docker — Skroutz bot-detection blocks headless Chrome; Docker only runs Stage 2 + 3 via SKIP_SCRAPE=1
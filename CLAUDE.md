# CLAUDE.md — Skroutz Price Tracker

## Environment
- Python: `C:\Users\StavrosKV\anaconda33\python.exe` — always use this full path
- Project:  `C:\Users\StavrosKV\Documents\Projects\ProjectsPY`
- Shell: PowerShell (Windows 11). Use PowerShell syntax for all commands.
- Git user: StavrosKav / branch: main
- IDE: VS Code with Claude Code extension

## Project Overview
Daily price-tracking pipeline for skroutz.gr (Greece's largest e-commerce aggregator).

Pipeline stages (run sequentially by `run_pipeline.py`):
  1. Scrape   → `1scriptToGet4.py`              raw CSVs per category
  2. Clean    → `1scriptToGet4MANIPULATION.py`  cleaned CSVs to Clean/
  3. Load SQL → `4csvsTOsql.py`                 upserts into PostgreSQL

Post-pipeline (non-fatal, each runs independently after Load SQL):
  - `run_charts()`              → charts/price_trend_*.png
  - `send_drop_digest()`        → Gmail: today's top 10 price drops
  - `send_watchlist_alerts()`   → Gmail: watchlist.json threshold hits
  - `send_disappeared_alert()`  → Gmail: products not seen in 1–2 days
  - `run_dashboard()`           → dashboard/dashboard_latest.html
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
| `skroutz_phonesWHILE.py` | Selenium scraper — phones → Phones_skroutz/ |
| `skroutz_laptopsWHILE.py` | Selenium scraper — laptops → Laptops_skroutz/ |
| `skroutz_tabletsWHILE.py` | Selenium scraper — tablets → Tablets_skroutz/ |
| `skroutz_SmartwatchesWHILE.py` | Selenium scraper — smartwatches → Smartwatches_skroutz/ |
| `Data_Phone.py` | Cleaner — phones |
| `Data_Laptops.py` | Cleaner — laptops |
| `Data_Tablets.py` | Cleaner — tablets |
| `Data_Smartwatches.py` | Cleaner — smartwatches |
| `charts_from_db.py` | Brand price-trend charts (dark-themed PNG per category) |
| `generate_dashboard.py` | Self-contained HTML dashboard from PostgreSQL |
| `analytics.sql` | 12 views: run once against DB to enable all analytics |
| `watchlist.json` | Price alert targets (array of {url, label, threshold_eur}) |
| `run_pipeline.bat` | Task Scheduler launcher — update PYTHON path inside before registering |
| `notifications.py` | Telegram notification layer — HTML parse mode, dedup, inline buttons, retry |
| `telegram_bot.py` | Interactive Telegram bot — long-polling; /status /drops /watchlist /add /remove /find /stats; URL→price conversation flow for adding watchlist items |
| `backfill_models.py` | One-time: backfill brand/model fields |
| `migrate_data.py` | One-time: flat → normalized schema migration |

## Database
- Engine: PostgreSQL 16
- Name: `SkroutzPR`
- Connection: via `.env` → DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
- Helper: `db.py` exports `get_engine()` — use this everywhere; never inline credentials
- Schema: `products` (static metadata) + `price_snapshots` (daily rows)
- Key constraint: `price_snapshots` has UNIQUE(product_id, date) — pipeline is re-run safe
- Analytics views (run analytics.sql once to create):
    vw_latest_prices, vw_price_history, vw_biggest_drops, vw_brand_summary, vw_disappeared,
    vw_price_volatility, vw_brand_price_trend, vw_hot_deals, vw_price_floor,
    vw_brand_discount_freq, vw_review_velocity, vw_restock_pricing
- Scale: ~19,607 products | ~146,405 snapshots | ~19k new rows/day

## Running Code
```powershell
# Standard Python execution
& "C:\Users\StavrosKV\anaconda33\python.exe" <script.py>

# Full pipeline
& "C:\Users\StavrosKV\anaconda33\python.exe" run_pipeline.py

# Charts only (needs DB connection)
& "C:\Users\StavrosKV\anaconda33\python.exe" charts_from_db.py

# Dashboard only
& "C:\Users\StavrosKV\anaconda33\python.exe" generate_dashboard.py

# Skip scraping, run Clean + Load only (for Docker workflow)
$env:SKIP_SCRAPE = "1"
& "C:\Users\StavrosKV\anaconda33\python.exe" run_pipeline.py
```

## Logs & Outputs
- Pipeline log: `logs/pipeline_YYYY-MM-DD.log` (created daily)
- Scraper logs: `scraper_phones.log`, `scraper_laptops.log`, etc. (in project root)
- Charts output: `charts/price_trend_{phone,laptop,smartwatch,tablet}.png`
- Dashboard: `dashboard/dashboard_latest.html` (+ dated copy)

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
- Each scraper writes to its own dated CSV: e.g. Phones_skroutz/skroutz_phones_YYYY-MM-DD.csv (PascalCase folder, skroutz_ prefix; laptops uses "laptos" typo in filename)

## What NOT To Do
- NEVER use bare `python` — always the full Anaconda path above
- NEVER create new files without explicit instruction
- NEVER touch `.env` — it contains live production credentials
- NEVER add backwards-compatibility shims — change the code directly
- NEVER add features or abstractions beyond what the task asks for
- NEVER commit anything in Phones_skroutz/, Laptops_skroutz/, Tablets_skroutz/, Smartwatches_skroutz/
- NEVER run scrapers inside Docker — Skroutz bot-detection blocks headless Chrome; Docker only runs Stage 2 + 3 via SKIP_SCRAPE=1
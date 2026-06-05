# Skroutz Price Tracker — Full Pipeline & Schema

## High-Level Architecture

```
Windows Task Scheduler (08:00 daily)
          │
          ▼
    run_pipeline.py   ◄── orchestrator; aborts on any stage failure
          │
    ┌─────┴────────────────────────────────────────────────────────┐
    │                   CORE STAGES (sequential)                   │
    │                                                              │
    │  [1] SCRAPE         [2] CLEAN          [3] LOAD SQL          │
    │  1scriptToGet4.py   1scriptToGet4       4csvsTOsql.py        │
    │  (parallel x4)      MANIPULATION.py    (upserts to PG)       │
    │        │            (parallel x4)            │               │
    │        │                  │                  │               │
    │   Selenium/Chrome    data cleaning      PostgreSQL            │
    │   skroutz.gr         + feature extract  SkroutzPR DB         │
    └─────────────────────────────────────────────────────────────-┘
          │
    ┌─────┴──────────────────────────────── POST-PIPELINE (non-fatal, independent) ──┐
    │                                                                                 │
    │  run_charts()          send_drop_digest()     send_watchlist_alerts()           │
    │  charts_from_db.py     top 10 drops today     watchlist.json threshold hits     │
    │  → charts/*.png        → Gmail + Telegram      → Gmail + Telegram               │
    │                                                                                 │
    │  send_disappeared_alert()    run_dashboard()    send_success_summary()          │
    │  products gone 1–2 days      generate_dashboard  snapshots / new / drops count  │
    │  → Gmail + Telegram          .py → dashboard/    → Gmail + Telegram             │
    │                              dashboard_latest                                   │
    │                              .html                                              │
    └─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Stage 1 — Scrape

**Script:** `1scriptToGet4.py`  
Launches 4 scrapers **in parallel** as subprocesses:

| Scraper | Category | Output folder | Filename pattern |
|---|---|---|---|
| `skroutz_phonesWHILE.py` | phone | `Phones_skroutz/` | `skroutz_phones_YYYY-MM-DD.csv` |
| `skroutz_laptopsWHILE.py` | laptop | `Laptops_skroutz/` | `skroutz_laptos_YYYY-MM-DD.csv` |
| `skroutz_tabletsWHILE.py` | tablet | `Tablets_skroutz/` | `skroutz_tablets_YYYY-MM-DD.csv` |
| `skroutz_SmartwatchesWHILE.py` | smartwatch | `Smartwatches_skroutz/` | `skroutz_smartwatches_YYYY-MM-DD.csv` |

Each scraper uses **Selenium + undetected-chromedriver**, scrolling through all pages.  
Subprocess timeout: 2 hours per scraper. On timeout, the process is killed and the handle reaped.  
Log handles are closed in `finally` regardless of exit path.

Root-level scraper logs (`scraper_*.log`) are **overwritten** on each run (`mode="w"`) — historical logs are preserved in dated files under `logs/`.

> **Docker constraint:** Scrapers CANNOT run in Docker — Skroutz bot-detection blocks headless Chrome.  
> Set `SKIP_SCRAPE=1` to bypass Stage 1 (done automatically in `docker-compose.yml`).

---

## Stage 2 — Clean

**Script:** `1scriptToGet4MANIPULATION.py`  
Launches 4 cleaners in parallel:

| Cleaner | Input | Output |
|---|---|---|
| `Data_Phone.py` | `Phones_skroutz/*.csv` | `Clean/Phones_skroutz_clean/clean_YYYY-MM-DD.csv` |
| `Data_Laptops.py` | `Laptops_skroutz/*.csv` | `Clean/Laptops_skroutz_clean/clean_YYYY-MM-DD.csv` |
| `Data_Tablets.py` | `Tablets_skroutz/*.csv` | `Clean/Tablets_skroutz_clean/clean_YYYY-MM-DD.csv` |
| `Data_Smartwatches.py` | `Smartwatches_skroutz/*.csv` | `Clean/Smartwatches_skroutz_clean/clean_YYYY-MM-DD.csv` |

**Operations per cleaner:**
- Price normalisation (strip `€`, commas, handle ranges)
- Brand / model / color extraction
- RAM / storage parsing (e.g. `"8GB RAM / 256GB"` → `ram_gb=8`, `storage_gb=256`)
- Camera count, display size, battery info extraction
- Deduplication within the daily file

---

## Stage 3 — Load SQL

**Script:** `4csvsTOsql.py`  
Reads all 4 cleaned CSVs and **upserts** into PostgreSQL using SQLAlchemy.

- **`products`** table: INSERT on first-seen URL; UPDATE `last_seen` on every run
- **`price_snapshots`** table: INSERT with `ON CONFLICT (product_id, date) DO NOTHING` — pipeline is re-run safe
- **Batch upsert:** uses `executemany` (5 batch queries per category instead of per-row queries)  
  ~19k products + ~19k snapshots loaded in ~20 total queries across all 4 categories; ~3–5s wall time
- Rows with missing or `"N/A"` links are skipped before any DB interaction
- New product count is measured via pre/post COUNT (correct on re-runs, unlike `xmax` trick)

---

## Database Schema

```
products
─────────────────────────────────────────────────────
 id              SERIAL PRIMARY KEY
 category        VARCHAR(20)       'phone'|'laptop'|'tablet'|'smartwatch'
 skroutz_link    TEXT  UNIQUE      canonical URL — natural key
 product_name    TEXT
 brand           VARCHAR(100)
 model           TEXT
 specs           TEXT              raw spec string from listing card
 ram_gb          INTEGER           phone/tablet only
 storage_gb      INTEGER           phone/tablet only
 num_cameras     INTEGER
 camera_type     VARCHAR(50)
 display_inches  NUMERIC(4,1)
 battery_info    VARCHAR(50)
 display_info    TEXT
 color           VARCHAR(100)
 first_seen      DATE
 last_seen       DATE

price_snapshots
─────────────────────────────────────────────────────
 id                      SERIAL PRIMARY KEY
 product_id              INTEGER  → products.id
 date                    DATE
 price_eur               NUMERIC(10,2)
 installments_per_month  NUMERIC(8,2)
 installments_in_total   NUMERIC(8,2)
 rating                  NUMERIC(3,1)   0.0 – 5.0
 reviews                 INTEGER
 UNIQUE (product_id, date)

Indexes
─────────────────────────────────────────────────────
 idx_price_snapshots_product_date  (product_id, date)
 idx_price_snapshots_date          (date)
 idx_products_brand                (brand)
 idx_products_category             (category)
 idx_products_last_seen            (last_seen)
```

**Scale:** ~19,600 products · ~146,000 snapshots · ~19,000 new rows/day

---

## Analytics Views  (`analytics.sql` — run once)

| View | Purpose |
|---|---|
| `vw_latest_prices` | Most recent price + metadata per product |
| `vw_price_history` | Full daily history with LAG-based day-over-day change |
| `vw_biggest_drops` | All negative price changes, ordered by size |
| `vw_brand_summary` | Min/max/avg/median price per brand per category |
| `vw_disappeared` | Products not seen for 7+ days |
| `vw_price_volatility` | 30-day coefficient of variation (stddev/avg) |
| `vw_brand_price_trend` | Daily avg price per brand/category (for charts) |
| `vw_hot_deals` | Price drop AND review surge vs. previous scrape |
| `vw_price_floor` | All-time low + high per product |
| `vw_brand_discount_freq` | % of days each brand had ≥3% drop (last 90 days) |
| `vw_near_atl` | Products within N% of their all-time low |
| `vw_price_trend_direction` | 7-day vs 30-day avg → falling / stable / rising |
| `vw_daily_market_index` | Daily avg/min/max price per category (macro trend) |

---

## Notification Layer

### `notifications.py` — Telegram
Sends HTML-formatted messages via Bot API.  
Deduplicates per day via `logs/tg_sent_YYYY-MM-DD.json`.  
These dedup files are cleaned up alongside `.log` files (30-day rotation).

| Function | Trigger | Content |
|---|---|---|
| `tg_pipeline_start()` | Pipeline begins | "Pipeline started" |
| `tg_failure(stage, code, log)` | Any stage fails | Stage name + exit code + log path |
| `tg_drops(rows)` | After Stage 3 | Top 10 price drops today |
| `tg_watchlist(hits)` | After Stage 3 | Watchlist threshold hits |
| `tg_disappeared(rows)` | After Stage 3 | Products gone 1–2 days |
| `tg_success(snaps, new, drops, elapsed)` | Pipeline end | Summary stats |

**Config:** `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in `.env`

### `run_pipeline.py` — Gmail
HTML email via SMTP (Gmail App Password). SMTP connection has a 30-second timeout.

| Function | Subject | Trigger |
|---|---|---|
| `send_failure_alert(stage, code)` | `[Skroutz Pipeline] FAILED — {stage}` | Stage failure |
| `send_drop_digest()` | `[Skroutz] N price drops today` | Top 10 from `vw_biggest_drops` |
| `send_watchlist_alerts()` | `[Skroutz] N price target(s) reached` | `watchlist.json` thresholds |
| `send_disappeared_alert()` | `[Skroutz] N product(s) disappeared` | `products.last_seen` 1–2 days ago |
| `send_success_summary(elapsed)` | `[Skroutz] Pipeline OK` | All stages passed |

Each stage logs its **wall-clock elapsed time** (`=== Stage complete in Xs ===`).  
`send_success_summary()` logs a warning if today's snapshot count is < 50% of yesterday's (partial scrape detection).

**Config:** `ALERT_EMAIL` + `GMAIL_APP_PASSWORD` in `.env`

---

## Interactive Telegram Bot (`telegram_bot.py`)

Long-polling bot, runs as a separate process (not part of the daily pipeline).  
Polling errors use **exponential backoff** (5s → 10s → 20s … capped at 300s) instead of a flat retry.  
`watchlist.json` writes are **atomic** (`os.replace`) — a crash mid-write can never corrupt the file.

| Command | Action |
|---|---|
| `/status` | Latest pipeline run status + timestamp |
| `/drops` | Top price drops from today's scrape |
| `/watchlist` | List current watchlist items |
| `/add <url>` | Start conversation to add a product + threshold |
| `/remove <n>` | Remove watchlist item by index |
| `/find <query>` | Search products by name/brand |
| `/stats` | DB stats: total products, snapshots, categories |

---

## Outputs

| Artifact | Path | Notes |
|---|---|---|
| Raw CSVs | `Phones_skroutz/`, `Laptops_skroutz/`, etc. | Scrapers (Stage 1); gitignored |
| Clean CSVs | `Clean/` | Cleaners (Stage 2); gitignored |
| Price charts (PNG) | `charts/price_trend_{phone,laptop,smartwatch,tablet}.png` | `charts_from_db.py` |
| HTML dashboard | `dashboard/dashboard_latest.html` | `generate_dashboard.py` |
| Pipeline log | `logs/pipeline_YYYY-MM-DD.log` | Rotated after 30 days |
| Scraper logs (root) | `scraper_{phones,laptops,tablets,smartwatches}.log` | Overwritten each run (`mode="w"`) |
| Scraper logs (dated) | `logs/skroutz_*WHILE_YYYY-MM-DD.log` | Preserved by subprocess launcher |
| Telegram dedup | `logs/tg_sent_YYYY-MM-DD.json` | Rotated after 30 days alongside `.log` files |

---

## Configuration Files

| File | Purpose |
|---|---|
| `.env` | DB credentials, Gmail App Password, Telegram tokens |
| `watchlist.json` | Array of `{url, label, threshold_eur}` objects; written atomically |
| `run_pipeline.bat` | Windows Task Scheduler launcher |
| `db.py` | SQLAlchemy engine singleton — `get_engine()`; `pool_pre_ping=True` auto-validates pooled connections after DB restart |

---

## Failure Behaviour

```
Stage fails (non-zero exit code)
         │
         ├─► send_failure_alert()  → Gmail + Telegram
         │
         └─► sys.exit()  ← pipeline aborts; no downstream stage runs
```

Post-pipeline steps (charts, emails, dashboard) are **non-fatal** — a failure there does not
abort the pipeline and is only logged as a warning.

---

## Automation

```
Windows Task Scheduler
  └─► run_pipeline.bat  (08:00 daily)
        └─► python run_pipeline.py
```

Docker alternative (no scraping):
```
SKIP_SCRAPE=1  →  Stage 2 (Clean) + Stage 3 (Load SQL) only
```

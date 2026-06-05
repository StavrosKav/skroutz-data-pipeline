# Skroutz Price Tracker

![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791?logo=postgresql&logoColor=white)
![Selenium](https://img.shields.io/badge/Selenium-4.41-43B02A?logo=selenium&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

An end-to-end Python data engineering pipeline that scrapes daily product listings from [Skroutz.gr](https://www.skroutz.gr) — Greece's largest e-commerce aggregator — cleans and enriches the raw data, loads it into a normalized PostgreSQL database, and delivers automated price intelligence via a self-contained HTML dashboard, an interactive Streamlit dashboard, Gmail alerts, and a Telegram bot.

> **~19,607 products tracked · 4 categories · ~202,000 price snapshots · ~19,000 new rows/day · daily since June 2025**

---

## Table of Contents

- [Architecture](#architecture)
- [Pipeline Stages](#pipeline-stages)
- [Database Schema](#database-schema)
- [Analytics Views](#analytics-views)
- [HTML Dashboard](#dashboard)
- [Streamlit Dashboard](#streamlit-dashboard)
- [Telegram Bot](#telegram-bot)
- [Email Alerts](#email-alerts)
- [Tech Stack](#tech-stack)
- [Setup](#setup)
- [Project Structure](#project-structure)
- [Data Coverage](#data-coverage)
- [Sample Output](#sample-output)
- [Disclaimer](#disclaimer)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          run_pipeline.py                            │
│                       (Master Orchestrator)                         │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
          ┌─────────────────────▼─────────────────────┐
          │            Stage 1 — Scrape               │
          │         1scriptToGet4.py                  │
          │   ┌──────┐ ┌────────┐ ┌──────┐ ┌───────┐ │
          │   │Phones│ │Laptops │ │Tabs  │ │Watches│ │  (parallel)
          │   └──┬───┘ └───┬────┘ └──┬───┘ └───┬───┘ │
          └──────┼─────────┼─────────┼──────────┼─────┘
                 │         │         │          │
          raw CSVs per category folder (date-stamped)
                 │         │         │          │
          ┌──────┼─────────┼─────────┼──────────┼─────┐
          │            Stage 2 — Clean               │
          │      1scriptToGet4MANIPULATION.py         │
          │   ┌──────┐ ┌────────┐ ┌──────┐ ┌───────┐ │
          │   │Phone │ │Laptops │ │Tabs  │ │Watches│ │  (parallel)
          │   └──┬───┘ └───┬────┘ └──┬───┘ └───┬───┘ │
          └──────┼─────────┼─────────┼──────────┼─────┘
                 │         │         │          │
               clean CSVs → Clean/ folder
                 │
          ┌──────▼───────────────────────────────────┐
          │          Stage 3 — Load SQL               │
          │              4csvsTOsql.py                │
          │   ON CONFLICT upsert → PostgreSQL         │
          │   products + price_snapshots tables       │
          └──────────────────┬───────────────────────┘
                             │
          ┌──────────────────▼───────────────────────┐
          │           Post-Pipeline (non-fatal)       │
          │  charts_from_db.py  →  PNG trend charts  │
          │  generate_dashboard.py  →  HTML dashboard │
          │  Gmail alerts: drops · watchlist ·        │
          │                disappeared · summary      │
          │  Telegram: same alerts via Bot API        │
          └──────────────────────────────────────────┘
```

`run_pipeline.py` orchestrates all three stages sequentially. A non-zero exit from any stage aborts the pipeline and sends a Gmail failure alert — data gaps are never silent. Automated daily at **08:00** via Windows Task Scheduler.

---

## Pipeline Stages

### Stage 1 — Scrape (`1scriptToGet4.py`)

Launches four Selenium scrapers in parallel with staggered starts (13 s apart) to reduce fingerprinting risk. Each scraper uses `undetected-chromedriver` to bypass Skroutz's bot detection and writes a dated raw CSV to its category folder.

| Scraper | Output folder | Filename pattern |
|---|---|---|
| `skroutz_phonesWHILE.py` | `Phones_skroutz/` | `skroutz_phones_YYYY-MM-DD.csv` |
| `skroutz_laptopsWHILE.py` | `Laptops_skroutz/` | `skroutz_laptos_YYYY-MM-DD.csv` |
| `skroutz_tabletsWHILE.py` | `Tablets_skroutz/` | `skroutz_tablets_YYYY-MM-DD.csv` |
| `skroutz_SmartwatchesWHILE.py` | `Smartwatches_skroutz/` | `skroutz_Smartwatches_YYYY-MM-DD.csv` |

### Stage 2 — Clean (`1scriptToGet4MANIPULATION.py`)

Launches four per-category cleaners in parallel. Each applies:

- Greek number format normalisation → float (`1.100,00 €` → `1100.0`)
- Brand / model / color extraction via regex with multi-pass fallback patterns
- Installment parsing (`44,10 €/μήνα σε 24 δόσεις` → `installments_per_month=44.10`, `installments_in_total=24`)
- Phone-specific: RAM/storage parsing, display size, camera count extraction
- Cleaned output written to `Clean/`

### Stage 3 — Load SQL (`4csvsTOsql.py`)

Upserts all four cleaned CSVs into PostgreSQL in a single pass:

- `products` — static metadata inserted once; only `last_seen` updated on re-scrape
- `price_snapshots` — one row per product per day; `UNIQUE(product_id, date)` makes the pipeline fully re-run safe
- Logs per-category counts: new products added and total snapshots loaded

---

## Database Schema

Two-table normalized design separating static product metadata from daily price observations:

```sql
products (
    id             SERIAL PRIMARY KEY,
    category       VARCHAR(20),     -- phone | laptop | smartwatch | tablet
    skroutz_link   TEXT UNIQUE,     -- natural key used for all upserts
    product_name   TEXT,
    brand          VARCHAR(100),
    model          TEXT,
    color          VARCHAR(100),
    specs          TEXT,
    ram_gb         INTEGER,
    storage_gb     INTEGER,
    num_cameras    INTEGER,
    camera_type    VARCHAR(50),
    display_inches NUMERIC(4,1),
    battery_info   VARCHAR(50),
    display_info   TEXT,
    first_seen     DATE,
    last_seen      DATE
)

price_snapshots (
    id                     SERIAL PRIMARY KEY,
    product_id             INTEGER REFERENCES products(id),
    date                   DATE,
    price_eur              NUMERIC,
    installments_per_month NUMERIC,
    installments_in_total  NUMERIC,
    rating                 NUMERIC,
    reviews                INTEGER,
    UNIQUE (product_id, date)      -- idempotent: safe to re-run any day
)
```

Static metadata is written once; price history grows by ~19,000 rows per day.

---

## Analytics Views

`analytics.sql` defines **13 PostgreSQL views** that turn raw snapshots into actionable intelligence. Run it once against the database in pgAdmin, DBeaver, or psql.

| View | Purpose |
|---|---|
| `vw_latest_prices` | Current price, rating, and snapshot date for every product |
| `vw_price_history` | Full daily price history with day-over-day change (`LAG()`) |
| `vw_biggest_drops` | Largest single-day absolute price drops, all time |
| `vw_brand_summary` | Avg / median / min / max price per brand per category |
| `vw_disappeared` | Products not seen in the last 7 days (likely delisted) |
| `vw_price_volatility` | 30-day coefficient of variation per product (deal quality signal) |
| `vw_brand_price_trend` | Daily avg price per brand/category (brand comparison over time) |
| `vw_hot_deals` | Price drop + review surge — compares two most recent scrape dates |
| `vw_price_floor` | All-time low and high per product |
| `vw_brand_discount_freq` | % of days each brand had a ≥3% drop (last 90 days) |
| `vw_near_atl` | Products currently within 10% of their all-time low |
| `vw_price_trend_direction` | 7-day vs 30-day avg momentum — falling / stable / rising |
| `vw_daily_market_index` | Daily avg/min/max price per category (macro market trend) |

### Sample Queries

```sql
-- Top 10 cheapest laptops right now
SELECT brand, model, price_eur
FROM vw_latest_prices
WHERE category = 'laptop'
ORDER BY price_eur ASC
LIMIT 10;
```

```sql
-- All price drops in the last 7 days, largest first
SELECT brand, model, drop_date, prev_price, new_price, drop_eur, drop_pct
FROM vw_biggest_drops
WHERE drop_date >= CURRENT_DATE - 7
ORDER BY drop_eur ASC
LIMIT 20;
```

```sql
-- Brands that discount most often (phones)
SELECT brand, discount_count, total_snapshots,
       ROUND(100.0 * discount_count / total_snapshots, 1) AS discount_pct
FROM vw_brand_discount_freq
WHERE category = 'phone'
ORDER BY discount_pct DESC;
```

```sql
-- Products at or near their all-time low
SELECT lp.brand, lp.model, lp.category,
       pf.all_time_low, lp.price_eur AS current_price,
       ROUND(100.0 * (lp.price_eur - pf.all_time_low) / pf.all_time_low, 1) AS pct_above_atl
FROM vw_near_atl lp
JOIN vw_price_floor pf ON pf.product_id = lp.id
ORDER BY pct_above_atl ASC
LIMIT 20;
```

---

## Dashboard

`generate_dashboard.py` produces a **self-contained HTML file** — all data is embedded as JSON so it works offline with no server required. Generated automatically after each pipeline run and also runnable standalone.

```
dashboard/dashboard_latest.html    ← always points to today's run
dashboard/dashboard_YYYY-MM-DD.html
```

Visual design: Inter font, glassmorphism stat cards with animated countUp, gradient fills on all charts, HSL-interpolated ATL proximity bars, tab fade-in animation, and a sticky footer.

### Dashboard Tabs

| Tab | Content |
|---|---|
| **Overview** | 90-day category price index (line chart with gradient fill), PNG price-trend charts per category |
| **Price Drops** | Hot deals (price drop + review surge), today's and weekly drops table — filterable by Min Drop € |
| **Products** | Full product search — category, price range, color, trend, Near ATL checkbox; sortable columns |
| **New & Gone** | New arrivals this week, recently disappeared products, watchlist status |
| **Insights** | Brand analysis (avg / median / range / count), market-share donuts, interactive brand price comparison |
| **Intelligence** | Near ATL table with HSL proximity bars, brand discount frequency, price tier distribution, price vs. rating scatter |

---

## Telegram Bot

`telegram_bot.py` provides an interactive long-polling bot for real-time price intelligence. Run it separately (e.g. always-on terminal or Task Scheduler).

| Command | Description |
|---|---|
| `/status` | Last pipeline run result and log tail |
| `/drops [category]` | Today's top price drops |
| `/best [category]` | Products closest to their all-time low |
| `/find <name>` | Search products by name + ATL context |
| `/history <name>` | Full 14-day price timeline with ATL/ATH context |
| `/watchlist` | Numbered list with live prices vs targets |
| `/add <url> <€>` | Add a product to the watchlist |
| `/remove <n>` | Remove watchlist item #n |
| `/stats` | DB stats: products, snapshots, today's drops |
| `/cancel` | Cancel any in-progress conversation |
| `/help` | List all available commands |

Send any skroutz.gr URL to the bot and it guides you through adding it to the watchlist.

Configure `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`. The bot only responds to the configured `CHAT_ID`.

---

## Streamlit Dashboard

`streamlit_app.py` provides an interactive live dashboard that queries PostgreSQL directly (results cached 1 h). It runs as a separate long-running process alongside the pipeline.

```bash
streamlit run streamlit_app.py
# Opens at http://localhost:8501
```

| Tab | Content |
|---|---|
| **Overview** | 4-metric summary (new arrivals, today's snaps, biggest drop, near-ATL count); category cards with colour-coded borders; brand price trend chart |
| **Price Drops** | Horizontal bar chart of top-10 drops; full drop dataframe with CSV export; daily activity chart |
| **Products** | Searchable product table with Sort by selector, volatility ProgressColumn, ATL proximity ProgressColumn |
| **Watchlist** | Card layout with current price / target / status; progress bar showing proximity to target; hit items sorted first |
| **Analytics** | Brand discount frequency with median reference line; near-ATL scatter + table; price volatility leaderboard; market index; trend direction |

---

## Email Alerts

All alerts are sent via Gmail SMTP using an App Password. Configure `ALERT_EMAIL` and `GMAIL_APP_PASSWORD` in `.env`.

| Alert | Trigger | Content |
|---|---|---|
| **Failure alert** | Any pipeline stage exits non-zero | Stage name, exit code, log path — sent immediately on abort |
| **Price drop digest** | After each successful run | Top 10 drops of the day sorted by €, with brand / model / category |
| **Watchlist alert** | Product price ≤ threshold in `watchlist.json` | Name, current price, target price, direct Skroutz link |
| **Disappeared alert** | Product absent from scrape for 1–2 days | Brand, model, category, last seen date, Skroutz link |
| **Success summary** | After each successful run | Elapsed time, snapshots loaded, new products, total drops today |

### Watchlist

Add products to `watchlist.json` to receive an email when they hit your target price:

```json
[
  {
    "url":           "https://www.skroutz.gr/s/...",
    "label":         "iPhone 16 Pro Max 256GB",
    "threshold_eur": 1250.00
  }
]
```

---

## Tech Stack

| Layer | Library / Tool | Version |
|---|---|---|
| Scraping | Selenium | 4.41.0 |
| Scraping | undetected-chromedriver | 3.5.5 |
| Processing | pandas | 2.3.3 |
| Processing | numpy | 2.3.5 |
| Database ORM | SQLAlchemy | 2.0.43 |
| Database driver | psycopg2-binary | 2.9.12 |
| Database engine | PostgreSQL | 16 |
| Visualisation | matplotlib | 3.10.6 |
| Visualisation | plotly | ≥5.18 |
| Interactive dashboard | Streamlit | ≥1.35 |
| Config | python-dotenv | 1.1.0 |
| Orchestration | subprocess + Windows Task Scheduler | — |
| Alerting | smtplib (Gmail SMTP) | stdlib |
| Notifications | Telegram Bot API (stdlib urllib) | — |
| Containerisation | Docker + Compose | — |
| Testing | pytest | — |
| Linting | ruff | — |
| Runtime | Python | 3.13 |

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```ini
DB_HOST=localhost
DB_PORT=5432
DB_NAME=SkroutzPR
DB_USER=postgres
DB_PASSWORD=your_password

ALERT_EMAIL=you@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx   # Gmail App Password (not your account password)
```

Generate a Gmail App Password at: **myaccount.google.com → Security → 2-Step Verification → App Passwords**

### 3. Create the database schema

Run `create_new_schema.sql` against your PostgreSQL instance in pgAdmin or psql:

```bash
psql -U postgres -d SkroutzPR -f create_new_schema.sql
```

### 4. Create the analytics views (one-time)

```bash
psql -U postgres -d SkroutzPR -f analytics.sql
```

### 5. Run the pipeline

```powershell
& "C:\path\to\python.exe" run_pipeline.py
```

### 6. Run with Docker (Clean + Load only)

The scrapers require a real Chrome window and **cannot run inside Docker** — Skroutz bot-detection blocks headless Chrome. Docker is therefore scoped to Stage 2 (Clean) and Stage 3 (Load) via `SKIP_SCRAPE=1`.

**Typical workflow:**

1. Run scrapers on Windows to produce raw CSVs:
   ```powershell
   & "C:\path\to\python.exe" 1scriptToGet4.py
   ```

2. Run Clean + Load in Docker (no local Postgres install needed):
   ```bash
   cp .env.example .env    # fill in DB_PASSWORD
   docker compose up --build
   ```

The schema is applied automatically on first run via the `initdb` mount. Raw CSV folders are mounted read-only into the container.

### 7. Automate with Windows Task Scheduler

Edit `run_pipeline.bat` and update the `PYTHON` path, then register:

```powershell
schtasks /create /tn "SkroutzDailyPipeline" /tr "C:\path\to\run_pipeline.bat" /sc DAILY /st 08:00 /f
```

---

## Project Structure

```
├── run_pipeline.py                # Master orchestrator — scrape → clean → load → alerts
│
├── 1scriptToGet4.py               # Stage 1: parallel scraper launcher (4 workers)
├── skroutz_phonesWHILE.py         #   └─ Selenium scraper — phones
├── skroutz_laptopsWHILE.py        #   └─ Selenium scraper — laptops
├── skroutz_tabletsWHILE.py        #   └─ Selenium scraper — tablets
├── skroutz_SmartwatchesWHILE.py   #   └─ Selenium scraper — smartwatches
│
├── 1scriptToGet4MANIPULATION.py   # Stage 2: parallel cleaner launcher (4 workers)
├── Data_Phone.py                  #   └─ Cleaner — phones
├── Data_Laptops.py                #   └─ Cleaner — laptops
├── Data_Tablets.py                #   └─ Cleaner — tablets
├── Data_Smartwatches.py           #   └─ Cleaner — smartwatches
│
├── 4csvsTOsql.py                  # Stage 3: PostgreSQL upsert loader
├── db.py                          # SQLAlchemy engine singleton (get_engine())
│
├── charts_from_db.py              # Price trend charts — PNG per category
├── generate_dashboard.py          # Self-contained HTML dashboard from PostgreSQL
├── streamlit_app.py               # Interactive Streamlit dashboard (live DB queries)
├── notifications.py               # Telegram notification layer — dedup, retry, backoff
├── telegram_bot.py                # Interactive Telegram bot — long-polling
├── analytics.sql                  # 13 analytical views — run once in DB
├── watchlist.json                 # Price alert targets [{url, label, threshold_eur}]
│
├── tests/
│   └── test_pipeline.py           # 49 unit tests (pytest) — parsing, coercions, I/O
│
├── Skroutz_data_EDA.py            # Exploratory data analysis & single-day charts
├── backfill_models.py             # One-time: backfill brand/model fields
├── migrate_data.py                # One-time: flat → normalized schema migration
│
├── create_new_schema.sql          # Database DDL — run once to create tables
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── run_pipeline.bat               # Task Scheduler launcher — update PYTHON path inside
└── .env.example                   # Credential template — copy to .env, never commit .env
```

---

## Data Coverage

Current database state as of **2026-06-05**:

| Category | Products | Avg Daily Snapshots |
|---|---|---|
| Phones | ~5,100 | ~5,100 |
| Laptops | ~6,300 | ~6,300 |
| Smartwatches | ~6,300 | ~6,300 |
| Tablets | ~1,600 | ~1,300 |
| **Total** | **~19,300** | **~19,000** |

**Total snapshots:** ~202,000  
**Date range:** 2025-06-10 → present (~12 months of daily history)

---

## Sample Output

### Cleaned data row (phones)

| Brand | Model | RAM | Storage | Price (€) | Rating | Reviews | Date |
|---|---|---|---|---|---|---|---|
| Xiaomi | Redmi Note 14 Pro 5G | 8 GB | 256 GB | 256.63 | 4.7 | 133 | 2026-06-01 |
| Apple | iPhone 16 Pro Max | 8 GB | 256 GB | 1352.00 | 4.7 | 165 | 2026-06-01 |
| Samsung | Galaxy S25 | 12 GB | 256 GB | 759.00 | 4.8 | 421 | 2026-06-01 |

Each row becomes one `price_snapshots` record linked to its `products` entry by foreign key. The pipeline appends ~19,000 rows per day and is fully idempotent — re-running the same day is safe.

### Price Trends

Multi-day price history for the top 6 most-reviewed products per category, generated by `charts_from_db.py`:

**Phones — top 6 by reviews**
![Phone price trends](charts/price_trend_phone.png)

**Laptops — top 6 by reviews**
![Laptop price trends](charts/price_trend_laptop.png)

**Smartwatches — top 6 by reviews**
![Smartwatch price trends](charts/price_trend_smartwatch.png)

**Tablets — top 6 by reviews**
![Tablet price trends](charts/price_trend_tablet.png)

Run `charts_from_db.py` to regenerate (requires `analytics.sql` views to be present in the database).

---

## Disclaimer

This project is for **personal learning and portfolio purposes only**. No scraped data is stored in this repository — all CSVs are excluded via `.gitignore`. The scraper accesses only publicly visible listing pages and makes no attempt to bypass authentication or access private data. Use responsibly and in accordance with the target site's Terms of Service.

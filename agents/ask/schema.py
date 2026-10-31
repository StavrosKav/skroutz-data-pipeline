"""Curated schema context for the LLM, and the table allowlist the guard enforces.

Kept deliberately small: only the relations a question can need, with the
columns that matter. Every relation listed here is in ALLOWED_TABLES and
nothing else is.
"""

ALLOWED_TABLES: frozenset[str] = frozenset({
    "products",
    "price_snapshots",
    "mv_latest_prices",
    "mv_price_history",
    "vw_drops_7d",
    "mv_price_floor",
    "mv_brand_summary",
    "mv_daily_market_index",
    "mv_price_trend_direction",
})

SCHEMA_CONTEXT = """\
PostgreSQL. Categories: 'phone','laptop','tablet','smartwatch'. Prices in EUR. One snapshot per product per day.
products(id, category, skroutz_id BIGINT UNIQUE (Skroutz product number, stable key), skroutz_link, product_name, brand, model, specs, ram_gb, storage_gb, display_inches, color, first_seen DATE, last_seen DATE)
price_snapshots(product_id -> products.id, date DATE, price_eur, rating 0-5, reviews)
mv_latest_prices(id = product id, category, brand, model, product_name, last_price_date, price_eur, rating, reviews, first_seen, last_seen, skroutz_link)  -- latest known price per product
mv_price_history(product_id, category, brand, model, product_name, date, price_eur, prev_price, price_change, pct_change, rating, reviews)  -- day-over-day change
vw_drops_7d(product_id, category, brand, model, product_name, drop_date, prev_price, new_price, drop_eur NEGATIVE, drop_pct NEGATIVE, skroutz_link, yesterday_price, baseline_7d, n_hist)  -- price drops: prev_price = LEAST(yesterday's price, median of the previous 7 snapshots); a drop needs new_price below both and >= 3 earlier snapshots. Use this for every drop question. Drops of 40% or more are real rows (flagged 'big drop, check'), never exclude them
mv_price_floor(product_id, all_time_low, all_time_high, snapshot_count)
mv_brand_summary(category, brand, product_count, avg_price, min_price, max_price, median_price, total_snapshots)  -- over ALL snapshots ever (all history, weighted by days listed); for current prices aggregate mv_latest_prices of currently listed products
mv_daily_market_index(category, date, avg_price, min_price, max_price, products_tracked)
mv_price_trend_direction(product_id, avg_7d, avg_30d, trend 'falling'|'rising'|'stable')  -- every product with a snapshot in the last 30 days, includes delisted ones; join products for last_seen
Notes: "today" = the latest date in price_snapshots (SELECT MAX(date) FROM price_snapshots), not CURRENT_DATE.
"Still listed" = last_seen = that latest date. Match product names with ILIKE '%...%'.
COUNTS: any "how many products/X ..." question, including "how many X do we have/track/are there" and counts by
status or trend ("how many products have a falling trend / are rated 5 / dropped") (Greek: "πόσα ... έχουμε/υπάρχουν/
παρακολουθούμε", "πόσα προϊόντα έχουν/είναι ...") means products
CURRENTLY LISTED: WHERE last_seen = (SELECT MAX(date) FROM price_snapshots). The products table also keeps
delisted products, so never COUNT(*) all of products for such questions. Count all products ever seen only when
the question says ever / all time / historically / since the start (Greek: "ποτέ", "συνολικά από την αρχή", "ιστορικά").
A product asked about "today"/"now" (cheapest, history of the cheapest...) is picked among currently listed products.
"Last N days" / "this week" (N=7) = date > (latest date) - N, i.e. the N most recent days including today.
Search for a product/model name in product_name (the full title), not in model (often short or NULL).
When sorting by price use NULLS LAST (cheapest/most expensive must be real prices).
When the question asks for the median (διάμεσος/διάμεση) use median_price or PERCENTILE_CONT(0.5), never AVG.
"New this week" = first_seen >= latest date - 7.
Aggregates of prices (median, average, min, max per brand/category) describe current prices by default: compute them
from mv_latest_prices of currently listed products. Use mv_brand_summary only when the question asks about all history
(ever / historically / over time).
"""

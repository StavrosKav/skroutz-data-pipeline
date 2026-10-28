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
    "vw_biggest_drops",
    "mv_price_floor",
    "mv_brand_summary",
    "mv_daily_market_index",
    "mv_price_trend_direction",
})

SCHEMA_CONTEXT = """\
PostgreSQL. Categories: 'phone','laptop','tablet','smartwatch'. Prices in EUR. One snapshot per product per day.
products(id, category, skroutz_link, product_name, brand, model, specs, ram_gb, storage_gb, display_inches, color, first_seen DATE, last_seen DATE)
price_snapshots(product_id -> products.id, date DATE, price_eur, rating 0-5, reviews)
mv_latest_prices(id = product id, category, brand, model, product_name, last_price_date, price_eur, rating, reviews, first_seen, last_seen, skroutz_link)  -- latest known price per product
mv_price_history(product_id, category, brand, model, product_name, date, price_eur, prev_price, price_change, pct_change, rating, reviews)  -- day-over-day change
vw_biggest_drops(product_id, category, brand, model, product_name, drop_date, prev_price, new_price, drop_eur NEGATIVE, drop_pct NEGATIVE, skroutz_link)  -- rows where price fell vs previous snapshot
mv_price_floor(product_id, all_time_low, all_time_high, snapshot_count)
mv_brand_summary(category, brand, product_count, avg_price, min_price, max_price, median_price, total_snapshots)
mv_daily_market_index(category, date, avg_price, min_price, max_price, products_tracked)
mv_price_trend_direction(product_id, avg_7d, avg_30d, trend 'falling'|'rising'|'stable')
Notes: "today" = the latest date in price_snapshots (SELECT MAX(date) FROM price_snapshots), not CURRENT_DATE.
"Still listed" = last_seen = that latest date. Match product names with ILIKE '%...%'.
"""

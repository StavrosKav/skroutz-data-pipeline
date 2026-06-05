"""
migrate_data.py
---------------
One-time migration script: moves all historical data from the original flat tables
(phones, laptops, smartwaches, tablets) into the new normalised schema
(products + price_snapshots).

Why the schema was changed
--------------------------
The original design stored every daily scrape as a new row in a single flat table,
duplicating static product metadata (brand, model, specs) on every row.
The new schema separates:
  • products        — static metadata, one row per unique product
  • price_snapshots — daily price/rating observations, one row per product per day

This reduces storage, enables clean time-series queries, and avoids data anomalies.

Usage
-----
Run ONCE after executing create_new_schema.sql in DBeaver or psql.
Safe to re-run: ON CONFLICT guards prevent duplicate rows.
The old tables are left intact until the migration is manually verified.
"""

import os
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

# ── Database connection ────────────────────────────────────────────────────────
DB_USER     = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_HOST     = os.environ.get("DB_HOST", "localhost")
DB_PORT     = os.environ.get("DB_PORT", "5432")
DB_NAME     = os.environ.get("DB_NAME", "SkroutzPR")

engine = create_engine(
    f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

# Map each old flat table to the category label used in the new schema
OLD_TABLES = [
    ("phones",      "phone"),
    ("laptops",     "laptop"),
    ("smartwaches", "smartwatch"),   # note: original table name has a typo
    ("tablets",     "tablet"),
]


# ── Helper functions for safe type conversion ──────────────────────────────────

def _val(row, col):
    """Return None for NaN / missing values, otherwise the raw value."""
    v = row.get(col)
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v

def _int(row, col):
    """Cast a column value to int; return None on failure or missing data."""
    v = _val(row, col)
    try:
        return int(v) if v is not None else None
    except (ValueError, TypeError):
        return None

def _float(row, col):
    """Cast a column value to float; return None on failure or missing data."""
    v = _val(row, col)
    try:
        return float(v) if v is not None else None
    except (ValueError, TypeError):
        return None


# ── Migration logic ───────────────────────────────────────────────────────────

def migrate_table(conn, df, category):
    """
    Migrate one flat table into the products + price_snapshots schema.

    Approach:
      - Group rows by 'link' (the unique product identifier).
      - Use the oldest row in each group for the canonical product metadata,
        since early scrapes tend to have the most complete product name.
      - Insert one price_snapshot per date per product.

    Returns tuple: (products_upserted, snapshots_inserted, snapshots_skipped)
    """
    df.columns = [c.lower() for c in df.columns]

    products_upserted  = 0
    snapshots_inserted = 0
    snapshots_skipped  = 0

    for link, group in df.groupby("link"):
        # Pick the earliest scrape date as the canonical metadata source
        row = group.sort_values("date_added").iloc[0]

        result = conn.execute(text("""
            INSERT INTO products (
                category, skroutz_link, product_name, brand, model, specs,
                ram_gb, storage_gb, num_cameras, camera_type,
                display_inches, battery_info, display_info, color,
                first_seen, last_seen
            ) VALUES (
                :category, :skroutz_link, :product_name, :brand, :model, :specs,
                :ram_gb, :storage_gb, :num_cameras, :camera_type,
                :display_inches, :battery_info, :display_info, :color,
                :first_seen, :last_seen
            )
            ON CONFLICT (skroutz_link) DO UPDATE SET
                last_seen = GREATEST(products.last_seen, EXCLUDED.last_seen)
            RETURNING id
        """), {
            "category":       category,
            "skroutz_link":   str(link),
            "product_name":   _val(row, "product"),
            "brand":          _val(row, "brand"),
            "model":          _val(row, "model"),
            "specs":          _val(row, "specs"),
            "ram_gb":         _int(row, "ram_gb"),
            "storage_gb":     _int(row, "storage_gb"),
            "num_cameras":    _int(row, "num_cameras"),
            "camera_type":    _val(row, "camera_type"),
            "display_inches": _float(row, "display_inches"),
            "battery_info":   _val(row, "battery_info"),
            "display_info":   _val(row, "display_info"),
            "color":          _val(row, "color"),
            "first_seen":     group["date_added"].min(),   # earliest date seen
            "last_seen":      group["date_added"].max(),   # most recent date seen
        })
        product_id = result.fetchone()[0]
        products_upserted += 1

        # Insert one snapshot per day from the history
        for _, snap in group.iterrows():
            r = conn.execute(text("""
                INSERT INTO price_snapshots (
                    product_id, date,
                    price_eur, installments_per_month, installments_in_total,
                    rating, reviews
                ) VALUES (
                    :product_id, :date,
                    :price_eur, :installments_per_month, :installments_in_total,
                    :rating, :reviews
                )
                ON CONFLICT (product_id, date) DO NOTHING
            """), {
                "product_id":             product_id,
                "date":                   snap["date_added"],
                "price_eur":              _float(snap, "price_eur"),
                "installments_per_month": _float(snap, "installments_per_month"),
                "installments_in_total":  _float(snap, "installments_in_total"),
                "rating":                 _float(snap, "rating"),
                "reviews":                _int(snap, "reviews"),
            })
            if r.rowcount:
                snapshots_inserted += 1
            else:
                snapshots_skipped += 1

    return products_upserted, snapshots_inserted, snapshots_skipped


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    for old_table, category in OLD_TABLES:
        print(f"\n--- Migrating '{old_table}' → category='{category}' ---")

        # Read-only fetch uses a plain connection so it doesn't hold a write lock
        with engine.connect() as read_conn:
            try:
                df = pd.read_sql(f'SELECT * FROM "{old_table}"', read_conn)
            except Exception as e:
                print(f"  Could not read {old_table}: {e}")
                continue

        if df.empty:
            print("  Empty table, skipping.")
            continue

        # Each table migrates atomically — a failure here doesn't affect other tables
        with engine.begin() as conn:
            pu, si, sk = migrate_table(conn, df, category)
        print(f"  Products upserted : {pu}")
        print(f"  Snapshots inserted: {si}")
        print(f"  Snapshots skipped (duplicate): {sk}")

    print("\nMigration complete.")
    print("Verify in DBeaver, then you can DROP the old flat tables when ready.")


if __name__ == "__main__":
    main()

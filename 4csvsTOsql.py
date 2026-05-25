"""
4csvsTOsql.py
-------------
Loads today's cleaned CSVs (phones, laptops, smartwatches, tablets) into
the PostgreSQL database SkroutzPR.

Database schema (two tables):
  • products        — one row per unique product (identified by skroutz_link)
  • price_snapshots — one row per product per day (price, rating, installments)

Upsert strategy:
  • products: INSERT … ON CONFLICT (skroutz_link) → update last_seen date only;
              static metadata (brand, model, specs) is never overwritten.
  • price_snapshots: INSERT … ON CONFLICT (product_id, date) → DO NOTHING;
                     running this script twice on the same day is safe.

Run after the cleaning scripts have produced today's CSV files.
"""

import pandas as pd
import datetime
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

# ── Database connection ────────────────────────────────────────────────────────
DB_USER     = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_HOST     = os.environ.get("DB_HOST", "localhost")
DB_PORT     = os.environ.get("DB_PORT", "5432")
DB_NAME     = os.environ.get("DB_NAME", "SkroutzPR")

today = datetime.date.today().isoformat()
base  = os.path.join('.', 'Clean')

# Map each product category to its cleaned CSV file for today
CATEGORY_FILES = [
    ("phone",      os.path.join(base, "Phones_skroutz_clean",       f"clean_{today}.csv")),
    ("laptop",     os.path.join(base, "Laptops_skroutz_clean",      f"clean_{today}.csv")),
    ("smartwatch", os.path.join(base, "Smartwatches_skroutz_clean", f"clean_{today}.csv")),
    ("tablet",     os.path.join(base, "Tablets_skroutz_clean",      f"clean_{today}.csv")),
]

engine = create_engine(
    f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)


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


# ── Core loader ───────────────────────────────────────────────────────────────

def load_category(conn, category, file_path):
    """
    Load one category's cleaned CSV into the database.

    For each row:
      1. Upsert the product into `products` (keyed by skroutz_link).
         New rows are inserted; existing rows only have `last_seen` updated.
      2. Insert a price snapshot into `price_snapshots` for today's date.
         Duplicate (product_id, date) pairs are silently ignored.

    Parameters
    ----------
    conn      : active SQLAlchemy connection
    category  : label string ('phone', 'laptop', 'smartwatch', 'tablet')
    file_path : path to today's cleaned CSV for this category
    """
    if not os.path.exists(file_path):
        print(f"SKIP (not found): {file_path}")
        return

    df = pd.read_csv(file_path)
    df.columns = [c.lower() for c in df.columns]   # normalise headers to lowercase

    new_products = 0
    snapshots    = 0

    for _, row in df.iterrows():
        link = _val(row, "link")
        if not link:
            continue   # rows without a URL cannot be reliably identified

        # Upsert product — static metadata inserted once; last_seen updated on every run
        # xmax = 0 is a PostgreSQL internal flag: true only on a fresh INSERT (not UPDATE)
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
                last_seen = EXCLUDED.last_seen
            RETURNING id, (xmax = 0) AS is_new
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
            "first_seen":     today,
            "last_seen":      today,
        })
        product_id, is_new = result.fetchone()
        if is_new:
            new_products += 1

        # Insert today's price snapshot — idempotent (safe to re-run)
        conn.execute(text("""
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
            "date":                   today,
            "price_eur":              _float(row, "price_eur"),
            "installments_per_month": _float(row, "installments_per_month"),
            "installments_in_total":  _float(row, "installments_in_total"),
            "rating":                 _float(row, "rating"),
            "reviews":                _int(row, "reviews"),
        })
        snapshots += 1

    conn.commit()
    print(f"{category:12s}: {new_products} new products | {snapshots} snapshots loaded")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    with engine.connect() as conn:
        for category, file_path in CATEGORY_FILES:
            load_category(conn, category, file_path)
    print("Done.")

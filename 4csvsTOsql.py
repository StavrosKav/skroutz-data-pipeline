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
import logging
import os
import sys
from sqlalchemy import text
from dotenv import load_dotenv

from db import get_engine

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE = os.path.dirname(os.path.abspath(__file__))


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

    Uses two batch operations instead of per-row queries:
      1. Batch upsert all products via unnest(), returning ids + new-row flags
         in the same round-trip.
      2. Batch insert all price snapshots (one executemany call).

    Parameters
    ----------
    conn      : active SQLAlchemy connection
    category  : label string ('phone', 'laptop', 'smartwatch', 'tablet')
    file_path : path to today's cleaned CSV for this category
    """
    today = datetime.date.today().isoformat()
    if not os.path.exists(file_path):
        logger.warning(f"SKIP (not found): {file_path}")
        return

    df = pd.read_csv(file_path)
    df.columns = [c.lower() for c in df.columns]   # normalise headers to lowercase

    products_rows   = []
    snapshot_extras = []   # price/rating fields, ordered parallel to products_rows

    for _, row in df.iterrows():
        link = _val(row, "link")
        if not link or str(link).upper() == "N/A":
            continue   # rows without a URL cannot be reliably identified
        link = str(link)
        products_rows.append({
            "category":       category,
            "skroutz_link":   link,
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
        snapshot_extras.append({
            "skroutz_link":           link,
            "price_eur":              _float(row, "price_eur"),
            "installments_per_month": _float(row, "installments_per_month"),
            "installments_in_total":  _float(row, "installments_in_total"),
            "rating":                 _float(row, "rating"),
            "reviews":                _int(row, "reviews"),
        })

    if not products_rows:
        logger.warning(f"{category:12s}: no valid rows in {file_path}")
        return

    # Batch upsert products via unnest() — one round-trip for the whole category.
    # xmax = 0 on a returned row means it came from the INSERT branch, not the
    # ON CONFLICT UPDATE branch, so new_products falls out of the same statement
    # instead of a pair of COUNT(*) queries taken before/after the upsert.
    result = conn.execute(text("""
        INSERT INTO products (
            category, skroutz_link, product_name, brand, model, specs,
            ram_gb, storage_gb, num_cameras, camera_type,
            display_inches, battery_info, display_info, color,
            first_seen, last_seen
        )
        SELECT * FROM unnest(
            :category::text[], :skroutz_link::text[], :product_name::text[],
            :brand::text[], :model::text[], :specs::text[],
            :ram_gb::int[], :storage_gb::int[], :num_cameras::int[], :camera_type::text[],
            :display_inches::float8[], :battery_info::text[], :display_info::text[], :color::text[],
            :first_seen::date[], :last_seen::date[]
        ) AS t(
            category, skroutz_link, product_name, brand, model, specs,
            ram_gb, storage_gb, num_cameras, camera_type,
            display_inches, battery_info, display_info, color,
            first_seen, last_seen
        )
        ON CONFLICT (skroutz_link) DO UPDATE SET last_seen = EXCLUDED.last_seen
        RETURNING id, skroutz_link, (xmax = 0) AS is_new
    """), {
        col: [r[col] for r in products_rows]
        for col in products_rows[0].keys()
    })

    new_products = 0
    id_map = {}
    for row in result:
        id_map[row.skroutz_link] = row.id
        if row.is_new:
            new_products += 1

    # Build and batch-insert all snapshots — one executemany call
    snapshot_rows = [
        {
            "product_id":             id_map[e["skroutz_link"]],
            "date":                   today,
            "price_eur":              e["price_eur"],
            "installments_per_month": e["installments_per_month"],
            "installments_in_total":  e["installments_in_total"],
            "rating":                 e["rating"],
            "reviews":                e["reviews"],
        }
        for e in snapshot_extras
        if e["skroutz_link"] in id_map
    ]

    if snapshot_rows:
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
        """), snapshot_rows)

    logger.info(f"{category:12s}: {new_products} new products | {len(snapshot_rows)} snapshots loaded")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    today = datetime.date.today().isoformat()
    base  = os.path.join(BASE, 'Clean')
    CATEGORY_FILES = [
        ("phone",      os.path.join(base, "Phones_skroutz_clean",       f"clean_{today}.csv")),
        ("laptop",     os.path.join(base, "Laptops_skroutz_clean",      f"clean_{today}.csv")),
        ("smartwatch", os.path.join(base, "Smartwatches_skroutz_clean", f"clean_{today}.csv")),
        ("tablet",     os.path.join(base, "Tablets_skroutz_clean",      f"clean_{today}.csv")),
    ]
    engine = get_engine()
    loaded, failed = [], []
    for category, file_path in CATEGORY_FILES:
        try:
            with engine.begin() as conn:   # own transaction: one category's failure can't roll back the others
                load_category(conn, category, file_path)
            loaded.append(category)
        except Exception:
            logger.exception(f"{category:12s}: FAILED — transaction rolled back")
            failed.append(category)

    if loaded:
        logger.info(f"Loaded: {', '.join(loaded)}")
    if failed:
        logger.error(f"Failed: {', '.join(failed)}")
        sys.exit(1)
    logger.info("Done.")

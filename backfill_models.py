"""
backfill_models.py
------------------
One-time script to populate NULL brand/model fields in the products table.

Why this is needed
------------------
Historical products (migrated from the old flat tables in 2025) were imported
before the cleaning scripts had brand/model extraction. As a result, ~60-85% of
laptop, smartwatch, and tablet records have model = NULL.

This script applies the same extraction logic used by the daily cleaning scripts
directly to the stored product_name column, then writes the results back to the DB.

Safe to re-run: only rows where model IS NULL are touched.

Run once after the migration is complete.
"""

import re
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

engine = create_engine(
    f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

# ── Extraction patterns (same logic as the daily cleaning scripts) ─────────────
# pattern_full: name ends with (RAM/StorageGB) optionally followed by a color.
#   e.g. "HP Elitebook 630 G10 13.3" (i7-1355U/16GB/512GB)"
#        "Apple MacBook Air 15.3" (M4/16GB/512GB) Midnight"
# pattern_simple: first word = Brand, everything else = Model (fallback).
pattern_full = re.compile(r"""(?x)
    ^(?P<Brand>[^ ]+)\s+(?P<Model>.+?)
    \(\s*\d+/\d+(?:GB|TB)\)\s*(?P<Color>.*)$
""")
pattern_simple = re.compile(r"^(?P<Brand>[^ ]+)\s+(?P<Model>.+)$")


def extract(product_name):
    """
    Return (brand, model) extracted from a product name string.
    Returns (None, None) when no pattern matches (e.g. single-word names).
    """
    if not product_name or not isinstance(product_name, str):
        return None, None
    name = product_name.strip()
    m = pattern_full.match(name)
    if m:
        return m.group("Brand"), m.group("Model").strip()
    m = pattern_simple.match(name)
    if m:
        return m.group("Brand"), m.group("Model").strip()
    return None, None


def backfill(conn):
    # ── Fetch all products with missing model ──────────────────────────────────
    rows = conn.execute(text("""
        SELECT id, product_name, brand, category
        FROM products
        WHERE model IS NULL
        ORDER BY category, id
    """)).fetchall()

    print(f"Products with NULL model: {len(rows)}")

    updates = []
    skipped = 0

    for row_id, product_name, existing_brand, category in rows:
        brand, model = extract(product_name)
        if model is None:
            skipped += 1
            continue
        # Keep existing brand if already set; only fill in if missing
        final_brand = existing_brand if existing_brand else brand
        updates.append({"id": row_id, "brand": final_brand, "model": model})

    print(f"  Extractable: {len(updates)}  |  Unextractable (single-word etc.): {skipped}")

    # ── Batch update ──────────────────────────────────────────────────────────
    if updates:
        conn.execute(
            text("UPDATE products SET brand = :brand, model = :model WHERE id = :id"),
            updates,
        )
        conn.commit()
        print(f"  Updated {len(updates)} rows.")
    else:
        print("  Nothing to update.")


def verify(conn):
    print()
    print("=== Model coverage after backfill ===")
    rows = conn.execute(text("""
        SELECT category, COUNT(*) AS total, COUNT(model) AS has_model
        FROM products
        GROUP BY category ORDER BY category
    """)).fetchall()
    for r in rows:
        pct = round(100 * r[2] / r[1]) if r[1] else 0
        print(f"  {r[0]:<12} {r[2]}/{r[1]} ({pct}%)")


if __name__ == "__main__":
    with engine.connect() as conn:
        backfill(conn)
        verify(conn)

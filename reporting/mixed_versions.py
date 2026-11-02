"""
mixed_versions.py
-----------------
Skroutz sometimes reuses one product id (/s/<id>/) for a different variant, e.g.
Xiaomi 12 8/256 first, later 8/128. The 2026-10-28 dedup merged such rows and
logged them in dedup_review; groups whose parsed RAM or storage differ are
"mixed versions": their price history may combine variants. Display flag only,
no data is changed.
"""
from sqlalchemy import text

SQL = """
SELECT DISTINCT survivor_id AS product_id
FROM dedup_review
WHERE skroutz_id IN (
    SELECT skroutz_id FROM dedup_review
    GROUP BY skroutz_id
    HAVING COUNT(DISTINCT ram_gb) > 1 OR COUNT(DISTINCT storage_gb) > 1)
"""


def fetch(conn):
    """Set of products.id whose history may mix variants."""
    return {int(r.product_id) for r in conn.execute(text(SQL)).fetchall()}

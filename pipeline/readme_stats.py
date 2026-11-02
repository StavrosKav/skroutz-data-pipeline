"""Rewrite the auto-generated stats block in README.md (moved from run_pipeline.py)."""
import datetime
import logging
import os
import re

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from core.db import get_engine
from core.paths import ROOT

logger = logging.getLogger(__name__)
BASE = str(ROOT)


def update_readme_stats():
    """
    Rewrite the auto-generated stat block(s) in README.md — product/snapshot totals,
    per-category table, last pipeline run date. Non-fatal — pipeline result is
    unaffected if this fails. Same simple direct-count SQL style as send_success_summary().
    """
    logger.info("=== README stats started ===")
    t = datetime.datetime.now()
    readme_path = os.path.join(BASE, "README.md")
    try:
        engine = get_engine()
        with engine.connect() as conn:
            total_products  = conn.execute(text("SELECT COUNT(*) FROM products")).scalar()
            total_snapshots = conn.execute(text("SELECT COUNT(*) FROM price_snapshots")).scalar()
            rows = conn.execute(text(
                "SELECT p.category, "
                "       COUNT(DISTINCT p.id)    AS products, "
                "       COUNT(s.id)              AS snapshots, "
                "       ROUND(AVG(s.price_eur))  AS avg_price, "
                "       ROUND(MIN(s.price_eur))  AS min_price, "
                "       ROUND(MAX(s.price_eur))  AS max_price, "
                "       (SELECT COUNT(*) FROM (SELECT b.brand FROM products b "
                "          WHERE b.category = p.category AND b.brand IS NOT NULL "
                "            AND LOWER(b.brand) NOT IN ('smartwatch', 'unknown', '') "
                "          GROUP BY b.brand HAVING COUNT(*) >= 5) bb) AS brands "
                "FROM products p "
                "JOIN price_snapshots s ON s.product_id = p.id "
                "GROUP BY p.category ORDER BY p.category"
            )).fetchall()
    except SQLAlchemyError as e:
        logger.warning(f"README stats: DB query failed — {e}")
        return

    def _abbrev(n):
        return f"{round(n / 1000)}k" if n >= 1000 else str(n)

    products_badge  = f'![Products](https://img.shields.io/badge/Products-{total_products:,}-blue?style=flat-square)'.replace(",", "%2C")
    snapshots_badge = f'![Snapshots](https://img.shields.io/badge/Snapshots-{_abbrev(total_snapshots)}-green?style=flat-square)'
    badges_block    = f"{products_badge}\n{snapshots_badge}"

    CAT_LABELS = {"laptop": "Laptop", "phone": "Phone", "smartwatch": "Smartwatch", "tablet": "Tablet"}
    table_rows = []
    for r in rows:
        label  = CAT_LABELS.get(r.category, r.category.capitalize())
        avg_p  = f"€{int(r.avg_price):,}" if r.avg_price is not None else "—"
        rng    = f"€{int(r.min_price):,}–€{int(r.max_price):,}" if r.min_price is not None else "—"
        # Brands = brands with 5+ products (placeholders like "Smartwatch" excluded),
        # so one-off junk values from listing titles do not inflate the count.
        brands = str(r.brands) if r.brands else "n/a"
        table_rows.append(f"| {label} | {r.products:,} | {r.snapshots:,} | {avg_p} | {rng} | {brands} |")
    table_rows.append(f"| **Total** | **{total_products:,}** | **{total_snapshots:,}** | | | |")

    today_str   = datetime.date.today().isoformat()
    table_block = (
        "| Category | Products | Snapshots | Avg Price | Range | Brands (5+ products) |\n"
        "|---|---|---|---|---|---|\n"
        + "\n".join(table_rows)
        + f"\n\nUpdated daily via Task Scheduler · last pipeline run: {today_str}"
    )

    try:
        with open(readme_path, "r", encoding="utf-8") as f:
            content = f.read()
        content = re.sub(
            r"(<!-- STATS:BADGES:START -->\n).*?(\n<!-- STATS:BADGES:END -->)",
            lambda m: m.group(1) + badges_block + m.group(2),
            content, count=1, flags=re.DOTALL,
        )
        content = re.sub(
            r"(<!-- STATS:TABLE:START -->\n).*?(\n<!-- STATS:TABLE:END -->)",
            lambda m: m.group(1) + table_block + m.group(2),
            content, count=1, flags=re.DOTALL,
        )
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        logger.warning(f"README stats: could not rewrite README.md — {e}")
        return

    elapsed = (datetime.datetime.now() - t).total_seconds()
    logger.info(
        f"=== README stats complete in {elapsed:.0f}s — "
        f"{total_products:,} products, {total_snapshots:,} snapshots ==="
    )

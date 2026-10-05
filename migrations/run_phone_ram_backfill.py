"""
One-off Should #3 runner: phone RAM backfill (NULL-only).

Uses the same regex / sanity guard as clean_common.parse_ram_storage().
Writes:
  - logs/phone_ram_backfill_YYYYMMDD-HHMMSS.log   (count + ids)
  - logs/phone_ram_backfill_undo_YYYYMMDD-HHMMSS.csv
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
load_dotenv(ROOT / ".env")

from clean_common import parse_ram_storage  # noqa: E402
from db import get_engine  # noqa: E402


def main() -> int:
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = logs / f"phone_ram_backfill_{stamp}.log"
    undo_path = logs / f"phone_ram_backfill_undo_{stamp}.csv"

    engine = get_engine()
    candidates = []
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, product_name, ram_gb, storage_gb
                FROM products
                WHERE category = 'phone' AND ram_gb IS NULL
                ORDER BY id
                """
            )
        ).mappings().all()

        for r in rows:
            ram, storage = parse_ram_storage(r["product_name"])
            if ram is None:
                continue
            candidates.append(
                {
                    "id": r["id"],
                    "product_name": r["product_name"],
                    "old_ram_gb": r["ram_gb"],
                    "old_storage_gb": r["storage_gb"],
                    "new_ram_gb": ram,
                    "new_storage_gb": storage,
                }
            )

        lines = [
            f"phone RAM backfill @ {stamp}",
            f"phones with ram_gb IS NULL: {len(rows)}",
            f"matching shared parser (will_fill): {len(candidates)}",
            f"ids: {[c['id'] for c in candidates]}",
        ]
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))

        with undo_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "id",
                    "product_name",
                    "old_ram_gb",
                    "old_storage_gb",
                    "new_ram_gb",
                    "new_storage_gb",
                ],
            )
            w.writeheader()
            for c in candidates:
                w.writerow(c)

        if not candidates:
            print(f"No rows to update. Undo CSV (header only): {undo_path}")
            print(f"Log: {log_path}")
            return 0

        with conn.begin():
            for c in candidates:
                conn.execute(
                    text(
                        """
                        UPDATE products
                        SET ram_gb = COALESCE(ram_gb, :ram),
                            storage_gb = COALESCE(storage_gb, :storage)
                        WHERE id = :id
                          AND ram_gb IS NULL
                        """
                    ),
                    {"id": c["id"], "ram": c["new_ram_gb"], "storage": c["new_storage_gb"]},
                )
        print(f"Updated {len(candidates)} rows. Undo CSV: {undo_path}")
        print(f"Log: {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

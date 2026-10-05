"""
Tests for the 2026-10-05 data-quality improvements:
  • shared RAM/storage parser (tablets + phone fallback forms)
  • loader sanitising of price/rating to match the DB CHECK constraints
  • products upsert fills NULLs instead of freezing metadata
  • price_snapshots same-day conflict updates price/rating/installments
"""

import importlib
import inspect
import unittest

import pandas as pd

from etl.clean_common import parse_ram_storage
import etl.clean_phones as Data_Phone
import etl.clean_tablets as Data_Tablets

loader = importlib.import_module("etl.load_postgres")


class TestParseRamStorage(unittest.TestCase):
    CASES = [
        ("BlackView Tab 60 Pro Set 4G 10.1 (8GB/128GB) Γκρι", (8, 128)),
        ("Microsoft Surface Pro 13 (16GB/512GB/Snapdragon X Plus/Windows 11 Home) Platinum", (16, 512)),
        ("Motorola Edge 50 Ultra 5G (16GB/1.0TB) Peach Fuzz", (16, 1000)),
        ("Xiaomi 14T Pro 5G Dual SIM (12GB/1TB) Titan Black", (12, 1000)),
        ("Apple iPhone 17 (8/256GB) Black", (8, 256)),
        ("Oukitel OT6 10.1", (None, None)),
        ("display-like (6.7/128)", (None, None)),   # no unit → not memory
        ("reversed (128GB/8GB)", (None, None)),     # sanity guard
        (None, (None, None)),
    ]

    def test_cases(self):
        for text, expected in self.CASES:
            with self.subTest(text=text):
                self.assertEqual(parse_ram_storage(text), expected)


class TestTabletEnrich(unittest.TestCase):
    def test_enrich_adds_numeric_columns(self):
        df = pd.DataFrame({"Product": ["Lenovo Tab (4GB/64GB) Grey", "Oukitel OT6 10.1"]})
        Data_Tablets.enrich(df)
        self.assertEqual(df.loc[0, "RAM_GB"], 4)
        self.assertEqual(df.loc[0, "Storage_GB"], 64)
        self.assertTrue(pd.isna(df.loc[1, "RAM_GB"]))
        self.assertIn("RAM_GB", Data_Tablets.CONFIG.final_columns)
        self.assertIn("Storage_GB", Data_Tablets.CONFIG.final_columns)


class TestPhoneFallback(unittest.TestCase):
    def test_gb_on_both_sides(self):
        row = {"Product": "Nothing Phone (3a) 5G Dual SIM (12GB/256GB) Λευκό"}
        self.assertEqual(Data_Phone.extract_ram_storage(row), (12, 256))

    def test_original_pattern_unchanged(self):
        row = {"Product": "Samsung Galaxy S25 (12/512GB) Navy"}
        self.assertEqual(Data_Phone.extract_ram_storage(row), (12, 512))


class TestLoaderSanitising(unittest.TestCase):
    def test_price_non_positive_becomes_none(self):
        self.assertIsNone(loader._price({"price_eur": 0}))
        self.assertIsNone(loader._price({"price_eur": -5}))
        self.assertEqual(loader._price({"price_eur": 199.9}), 199.9)

    def test_rating_out_of_range_becomes_none(self):
        self.assertIsNone(loader._rating({"rating": 5.5}))
        self.assertIsNone(loader._rating({"rating": -1}))
        self.assertEqual(loader._rating({"rating": 4.7}), 4.7)

    def test_upsert_fills_nulls_and_keeps_conflict_key(self):
        src = inspect.getsource(loader.load_category)
        self.assertIn("ON CONFLICT (skroutz_link) DO UPDATE", src)
        for col in ("brand", "model", "specs", "ram_gb", "storage_gb", "color"):
            self.assertIn(f"COALESCE(products.{col}", src)
        self.assertIn("GREATEST(products.last_seen, EXCLUDED.last_seen)", src)



class TestSnapshotUpsert(unittest.TestCase):
    def test_same_day_conflict_updates_fields(self):
        src = inspect.getsource(loader.load_category)
        self.assertIn("ON CONFLICT (product_id, date) DO UPDATE SET", src)
        for col in (
            "price_eur",
            "installments_per_month",
            "installments_in_total",
            "rating",
            "reviews",
        ):
            self.assertIn(f"{col}", src)
            self.assertIn(f"EXCLUDED.{col}", src)
        # Product conflict key must stay skroutz_link (unchanged by Should #2).
        self.assertIn("ON CONFLICT (skroutz_link) DO UPDATE", src)
        self.assertNotIn("ON CONFLICT (product_id, date) DO NOTHING", src)

if __name__ == "__main__":
    unittest.main()

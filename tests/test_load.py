"""
tests/test_load.py
Loading: loader coercions, N/A link guard, load_category.
Split from the former tests/test_{clean,load,scrape,alerts,telegram_cmds,ops}.py. No network or DB.
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock

import importlib as _imp

_csvs  = _imp.import_module('etl.load_postgres')
_val   = _csvs._val
_int   = _csvs._int
_float = _csvs._float


# ── Loader helpers: _val, _int, _float
# ─────────────────────────────────────────────────────────────────────────────

class TestLoaderHelpers(unittest.TestCase):

    def test_val_none(self):
        self.assertIsNone(_val({"x": None}, "x"))

    def test_val_nan(self):
        self.assertIsNone(_val({"x": float("nan")}, "x"))

    def test_val_present(self):
        self.assertEqual(_val({"x": "hello"}, "x"), "hello")

    def test_val_missing_key(self):
        self.assertIsNone(_val({}, "x"))

    def test_int_valid(self):
        self.assertEqual(_int({"x": "8"}, "x"), 8)

    def test_int_float_string_returns_none(self):
        # int("8.0") raises ValueError — _int only handles integer-format strings
        self.assertIsNone(_int({"x": "8.0"}, "x"))

    def test_int_non_numeric(self):
        self.assertIsNone(_int({"x": "N/A"}, "x"))

    def test_int_none(self):
        self.assertIsNone(_int({"x": None}, "x"))

    def test_float_valid(self):
        self.assertAlmostEqual(_float({"x": "299.99"}, "x"), 299.99)

    def test_float_none(self):
        self.assertIsNone(_float({"x": None}, "x"))

    def test_float_non_numeric(self):
        self.assertIsNone(_float({"x": "N/A"}, "x"))

    def test_int_negative_is_valid(self):
        # _int is a general coercion helper — negative ints are valid casts
        self.assertEqual(_int({"x": -5}, "x"), -5)


# ── N/A link guard
# ─────────────────────────────────────────────────────────────────────────────

class TestNALinkGuard(unittest.TestCase):

    def _is_invalid(self, link):
        return not link or str(link).upper() == "N/A"

    def test_uppercase_na(self):
        self.assertTrue(self._is_invalid("N/A"))

    def test_lowercase_na(self):
        self.assertTrue(self._is_invalid("n/a"))

    def test_empty_string(self):
        self.assertTrue(self._is_invalid(""))

    def test_none(self):
        self.assertTrue(self._is_invalid(None))

    def test_valid_url(self):
        self.assertFalse(self._is_invalid("https://www.skroutz.gr/s/12345/Product.html"))

    def test_partial_na_not_caught(self):
        # "N/A phone" is not literally N/A
        self.assertFalse(self._is_invalid("N/A phone"))


# ── load_category (Stage 3 core function)
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadCategory(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _csv(self, content: str, name: str = "clean.csv") -> str:
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_missing_file_raises(self):
        conn = MagicMock()
        with self.assertRaises(FileNotFoundError):
            _csvs.load_category(conn, "phone", os.path.join(self.tmp.name, "nope.csv"))
        conn.execute.assert_not_called()

    def test_all_na_links_raises(self):
        path = self._csv("link,price_eur,product,brand,model\nN/A,299,Test,Samsung,X\n")
        conn = MagicMock()
        with self.assertRaises(ValueError):
            _csvs.load_category(conn, "phone", path)
        conn.execute.assert_not_called()

    def test_empty_csv_raises(self):
        path = self._csv("link,price_eur,product,brand,model\n")
        conn = MagicMock()
        with self.assertRaises(ValueError):
            _csvs.load_category(conn, "phone", path)
        conn.execute.assert_not_called()

    def test_executes_two_times_for_valid_row(self):
        url  = "https://www.skroutz.gr/s/1/test.html"
        path = self._csv(
            f"link,price_eur,product,brand,model\n{url},299.00,Samsung Galaxy X,Samsung,Galaxy X\n"
        )
        fake_row               = MagicMock()
        fake_row.skroutz_link  = url
        fake_row.id            = 42
        fake_row.is_new        = True

        conn = MagicMock()
        # calls: upsert products (RETURNING id/skroutz_link/is_new), insert snapshots
        conn.execute.side_effect = [[fake_row], MagicMock()]

        _csvs.load_category(conn, "phone", path)
        self.assertEqual(conn.execute.call_count, 2)

    def test_snapshot_not_inserted_when_id_missing(self):
        # If the upsert RETURNING set is empty, snapshot_rows is empty → no 2nd call
        url  = "https://www.skroutz.gr/s/2/other.html"
        path = self._csv(
            f"link,price_eur,product,brand,model\n{url},199.00,Xiaomi X,Xiaomi,X\n"
        )
        conn = MagicMock()
        conn.execute.side_effect = [[]]  # empty RETURNING set → empty id_map

        _csvs.load_category(conn, "phone", path)
        # 1 call: upsert only — no snapshot insert
        self.assertEqual(conn.execute.call_count, 1)

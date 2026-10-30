"""The shipped schema must support the loader's ON CONFLICT (skroutz_id) on a fresh install."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "sql" / "create_new_schema.sql").read_text(encoding="utf-8")
LOADER = (ROOT / "etl" / "load_postgres.py").read_text(encoding="utf-8")


def _products_table() -> str:
    m = re.search(r"CREATE TABLE IF NOT EXISTS products \((.*?)\n\);", SCHEMA, re.S)
    assert m, "products table not found"
    return m.group(1)


def test_loader_still_upserts_on_skroutz_id():
    assert "ON CONFLICT (skroutz_id)" in LOADER


def test_products_has_skroutz_id_bigint():
    assert re.search(r"^\s*skroutz_id\s+BIGINT", _products_table(), re.M | re.I)


def test_unique_index_on_skroutz_id():
    assert re.search(r"CREATE UNIQUE INDEX IF NOT EXISTS products_skroutz_id_key\s+ON products \(skroutz_id\)", SCHEMA)


def test_existing_db_gets_column_on_rerun():
    assert "ADD COLUMN IF NOT EXISTS skroutz_id BIGINT" in SCHEMA


def test_docker_initdb_mounts_schema_then_analytics():
    dc = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    a = dc.index("create_new_schema.sql:/docker-entrypoint-initdb.d/01_")
    b = dc.index("analytics.sql:/docker-entrypoint-initdb.d/02_")
    assert a < b

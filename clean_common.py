"""
clean_common.py
---------------
Shared cleaning engine for all four category cleaners.

The four Data_*.py files are thin entry points that build a CleanerConfig
(plus any category-specific enrichment, e.g. phone spec extraction) and call
run_clean(cfg) — price normalisation, brand/model split, installment parsing,
review-count recovery, and the standardized read/write logic all live here.

Reads:  <raw_folder>/<raw_prefix>_<today>.csv   (first existing prefix wins)
Writes: Clean/<clean_folder>/clean_<today>.csv
"""

import pandas as pd
import re
import datetime
import os
import sys
from dataclasses import dataclass
from typing import Callable, Optional

BASE = os.path.dirname(os.path.abspath(__file__))


@dataclass(frozen=True)
class CleanerConfig:
    category: str
    raw_folder: str
    raw_prefixes: tuple      # tried in order; lets laptops fall back to the historical "laptos" filename
    clean_folder: str
    final_columns: tuple
    keep_color: bool = False
    color_required: bool = False
    enrich: Optional[Callable] = None   # called with the DataFrame after brand/model split


# ── PRICE ─────────────────────────────────────────────────────────────────────
def clean_price(val):
    """
    Normalise a raw price string to a float.

    Skroutz uses the Greek number format where '.' is the thousands separator
    and ',' is the decimal separator (e.g. "1.800,00 €" means one thousand
    eight hundred euros).  A value with more than one dot has the leading dots
    stripped before the final conversion.
    """
    s = re.sub(r'[^\d.,]', '', str(val)).strip()   # remove currency symbols, spaces, etc.
    if s.count('.') > 1:
        # Multiple dots → all but the last are thousands separators
        parts = s.split('.')
        s = ''.join(parts[:-1]) + '.' + parts[-1]
    s = s.replace(',', '.')   # Greek decimal comma → standard decimal point
    return pd.to_numeric(s, errors='coerce')


# ── REVIEWS ───────────────────────────────────────────────────────────────────
def clean_reviews(series):
    """
    Review counts are plain integers, but the scraper's reviews element sometimes
    captured count and rating joined by a newline (e.g. "1\\n0.0"). Extracting the
    first digit run recovers the count from malformed historical values instead of
    silently nulling them via to_numeric alone.
    """
    return pd.to_numeric(
        series.astype(str).str.extract(r'(\d+)', expand=False),
        errors='coerce',
    )


# ── BRAND / MODEL / COLOR ─────────────────────────────────────────────────────
def split_brand_model(data, keep_color=False, color_required=False):
    """
    Skroutz product names follow a predictable structure:
      "<Brand> <Model> (<RAM>/<Storage>GB) <Color>"  e.g. "Apple iPhone 17 Pro Max (12/512GB) Deep Blue"
      "<Brand> <Model>"                              e.g. "Samsung Galaxy A56 5G Dual SIM"

    pattern_full captures all three fields when a spec block is present;
    pattern_simple is the fallback (first word = Brand, rest = Model).
    color_required controls whether pattern_full demands text after the spec
    block (phones) or allows it to be empty (other categories).
    """
    color_pat = '.+' if color_required else '.*'
    pattern_full = rf"""(?x)
^(?P<Brand>[^ ]+)\s+(?P<Model>.+?)
\(\s*\d+/\d+(?:GB|TB)\)\s*(?P<Color>{color_pat})$
"""
    pattern_simple = r"^(?P<Brand>[^ ]+)\s+(?P<Model>.+)$"

    extracted = data['Product'].str.extract(pattern_full)
    remaining = extracted[extracted['Brand'].isnull()].index   # rows that didn't match pattern_full
    extracted.loc[remaining, ['Brand', 'Model']] = (
        data.loc[remaining, 'Product'].str.extract(pattern_simple)[['Brand', 'Model']]
    )

    data['Brand'] = extracted['Brand']
    data['Model'] = extracted['Model']
    if keep_color:
        data['Color'] = extracted['Color']   # NULL for listings without an explicit colour variant

    # Scraper stores "N/A" when a product field couldn't be read.
    # After regex splitting this becomes Brand="N", Model="/A" — clean it up.
    cols = ['Brand', 'Model', 'Color'] if keep_color else ['Brand', 'Model']
    _na_rows = data['Product'].isin(['N/A']) | data['Brand'].isin(['N', 'N/A'])
    data.loc[_na_rows, cols] = None


# ── INSTALLMENTS ──────────────────────────────────────────────────────────────
def parse_installments(data):
    """
    Raw installment values use Greek decimal commas (e.g. "44,10" = 44.10 €).
    Strip all non-numeric characters, then convert comma → dot before casting to
    float. "N/A" and missing values both become NaN naturally via errors='coerce'.
    """
    for col in ('Installments_per_month', 'Installments_in_total'):
        if col not in data.columns:
            data[col] = None
            continue
        data[col] = data[col].astype(str).str.replace(r'[^\d.,]', '', regex=True)
        data[col] = data[col].str.replace(',', '.', regex=False)
        data[col] = pd.to_numeric(data[col], errors='coerce')


# ── I/O ───────────────────────────────────────────────────────────────────────
def atomic_to_csv(df, path):
    tmp = path + ".tmp"
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, path)   # atomic: never leave a half-written CSV


def run_clean(cfg: CleanerConfig):
    today = datetime.date.today().isoformat()

    file_path = os.path.join(BASE, cfg.raw_folder, f"{cfg.raw_prefixes[0]}_{today}.csv")
    for prefix in cfg.raw_prefixes:
        candidate = os.path.join(BASE, cfg.raw_folder, f"{prefix}_{today}.csv")
        if os.path.exists(candidate):
            file_path = candidate
            break
    # If no candidate exists, read_csv raises FileNotFoundError → non-zero exit
    # → the pipeline aborts loudly (missing raw data must never be silent).

    data = pd.read_csv(file_path, sep=",", quotechar='"', on_bad_lines='skip', engine='python')
    data['date_added'] = today

    if data.empty:
        print(f"No rows in {file_path} — exiting.")
        sys.exit(0)

    data['Price_EUR'] = data['Price_EUR'].apply(clean_price)

    split_brand_model(data, keep_color=cfg.keep_color, color_required=cfg.color_required)

    if cfg.enrich:
        cfg.enrich(data)

    parse_installments(data)
    data['Rating'] = pd.to_numeric(data['Rating'], errors='coerce')
    data['Reviews'] = clean_reviews(data['Reviews'])

    # Column order matches the products + price_snapshots DB schema in 4csvsTOsql.py
    data_export = data[list(cfg.final_columns)]

    output_folder = os.path.join(BASE, 'Clean', cfg.clean_folder)
    os.makedirs(output_folder, exist_ok=True)
    output_path = os.path.join(output_folder, f"clean_{today}.csv")
    atomic_to_csv(data_export, output_path)

    print(f"Clean file saved: {output_path}")
    print(f"Total products:  {len(data_export)}")
    print(f"Brand extracted: {data_export['Brand'].notna().sum()} / {len(data_export)}")
    print(f"Price parsed:    {data_export['Price_EUR'].notna().sum()} / {len(data_export)}")

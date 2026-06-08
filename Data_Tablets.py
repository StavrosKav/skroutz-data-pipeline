"""
Data_Tablets.py
---------------
Cleans and enriches the raw tablet data produced by skroutz_tabletsWHILE.py.

Reads:  Tablets_skroutz/skroutz_tablets_<today>.csv
Writes: Clean/Tablets_skroutz_clean/clean_<today>.csv

Transformations applied:
  - Price:        handles Greek thousands-separator format (e.g. "1.100,00" → 1100.0)
  - Brand/Model:  split from the product name
  - Installments: converts Greek decimal comma format ("25,50") to float
"""

import pandas as pd
import re
import datetime
import os

BASE = os.path.dirname(os.path.abspath(__file__))


# ── PRICE ─────────────────────────────────────────────────────────────────────
def clean_price(val):
    """
    Normalise a raw price string to a float.

    Skroutz uses Greek number format where '.' is the thousands separator
    and ',' is the decimal separator (e.g. "1.100,00 €" = 1100.0 euros).
    A value with more than one dot has the leading dots stripped as thousands separators.
    """
    s = re.sub(r'[^\d.,]', '', str(val)).strip()   # remove currency symbols, spaces, etc.
    if s.count('.') > 1:
        # Multiple dots → all but the last are thousands separators
        parts = s.split('.')
        s = ''.join(parts[:-1]) + '.' + parts[-1]
    s = s.replace(',', '.')   # Greek decimal comma → standard decimal point
    return pd.to_numeric(s, errors='coerce')


if __name__ == "__main__":
    today = datetime.date.today().isoformat()
    base_folder = os.path.join(BASE, 'Tablets_skroutz')
    file_path = os.path.join(base_folder, f"skroutz_tablets_{today}.csv")

    data = pd.read_csv(file_path, sep=",", quotechar='"', on_bad_lines='skip', engine='python')
    data['date_added'] = today

    data['Price_EUR'] = data['Price_EUR'].apply(clean_price)

    # ── BRAND / MODEL ─────────────────────────────────────────────────────────
    # Tablet product names follow the structure: "<Brand> <Model>"
    # e.g. "Apple iPad Mini 2024" or "Samsung Galaxy Tab S10 FE (8/256GB) Silver"
    #
    # pattern_full handles tablets that include a RAM/storage variant in the name.
    # pattern_simple is the standard fallback: first word = Brand, rest = Model.
    pattern_full = r"""(?x)
^(?P<Brand>[^ ]+)\s+(?P<Model>.+?)
\(\s*\d+/\d+(?:GB|TB)\)\s*(?P<Color>.*)$
"""
    pattern_simple = r"^(?P<Brand>[^ ]+)\s+(?P<Model>.+)$"

    extracted = data['Product'].str.extract(pattern_full)
    remaining = extracted[extracted['Brand'].isnull()].index
    extracted.loc[remaining, ['Brand', 'Model']] = (
        data.loc[remaining, 'Product'].str.extract(pattern_simple)[['Brand', 'Model']]
    )

    data['Brand'] = extracted['Brand']
    data['Model'] = extracted['Model']

    # Scraper stores "N/A" when a product field couldn't be read.
    # After regex splitting this becomes Brand="N", Model="/A" — clean it up.
    _na_rows = data['Product'].isin(['N/A']) | data['Brand'].isin(['N', 'N/A'])
    data.loc[_na_rows, ['Brand', 'Model']] = None

    # ── INSTALLMENTS / RATINGS ────────────────────────────────────────────────
    # Raw installment values use Greek decimal commas (e.g. "25,50" = 25.50 €).
    # Strip all non-numeric characters, then convert comma → dot before casting to float.
    # "N/A" and missing values both become NaN naturally via errors='coerce'.
    for col in ['Installments_per_month', 'Installments_in_total']:
        if col not in data.columns:
            data[col] = None
        else:
            data[col] = data[col].astype(str).str.replace(r'[^\d.,]', '', regex=True)
            data[col] = data[col].str.replace(',', '.', regex=False)
            data[col] = pd.to_numeric(data[col], errors='coerce')

    data['Rating']  = pd.to_numeric(data['Rating'],  errors='coerce')
    data['Reviews'] = pd.to_numeric(data['Reviews'], errors='coerce')

    # ── EXPORT ────────────────────────────────────────────────────────────────
    # Column order aligns with the products + price_snapshots DB schema in 4csvsTOsql.py
    final_columns = [
        'date_added', 'Brand', 'Model', 'Product', 'Specs',
        'Price_EUR', 'Installments_per_month', 'Installments_in_total',
        'Rating', 'Reviews', 'Link',
    ]
    data_export = data[final_columns]

    output_folder = os.path.join(BASE, 'Clean', 'Tablets_skroutz_clean')
    os.makedirs(output_folder, exist_ok=True)
    output_path = os.path.join(output_folder, f"clean_{today}.csv")
    data_export.to_csv(output_path, index=False, encoding="utf-8-sig")

    print(f"Clean file saved: {output_path}")
    print(f"Total products:  {len(data_export)}")
    print(f"Brand extracted: {data_export['Brand'].notna().sum()} / {len(data_export)}")
    print(f"Price parsed:    {data_export['Price_EUR'].notna().sum()} / {len(data_export)}")

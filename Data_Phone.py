"""
Data_Phone.py
-------------
Cleans and enriches the raw phone data produced by the scraper (skroutz_phonesWHILE.py).

Reads:  Phones_skroutz/skroutz_phones_<today>.csv
Writes: Clean/Phones_skroutz_clean/clean_<today>.csv

Transformations applied:
  - Price:        handles the Greek thousands-separator format  (e.g. "1.800,00" → 1800.0)
  - RAM/Storage:  parses "(8/256GB)" or "(8/1TB)" patterns; converts TB → GB
  - Brand/Model/Color: split from the structured product name
  - Specs:        extracts camera MP, display size, and battery capacity
  - Installments: converts Greek decimal comma format ("44,10") to float
"""

import pandas as pd
import re
import datetime
import os

today = datetime.date.today().isoformat()
BASE = os.path.dirname(os.path.abspath(__file__))
base_folder = os.path.join(BASE, 'Phones_skroutz')
file_path = os.path.join(base_folder, f"skroutz_phones_{today}.csv")

data = pd.read_csv(file_path, engine='python')
data['date_added'] = today


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

data['Price_EUR'] = data['Price_EUR'].apply(clean_price)


# ── RAM / STORAGE ─────────────────────────────────────────────────────────────
def extract_ram_storage(row):
    """
    Extract RAM (GB) and internal storage (GB) from product text.

    Skroutz product names follow two patterns:
      • "(8/256GB)"  — RAM/Storage in product title
      • "Μνήμη: 8/256GB" — memory label in the specs or Memory_Info field
    TB values are converted to GB (1 TB = 1000 GB) to keep the column consistent.
    Returns (None, None) when no pattern is found.
    """
    text = str(row['Product']) + " " + str(row.get('Memory_Info', '')) + " " + str(row.get('Specs', ''))

    # Pattern 1: "(RAM/StorageGB)"  e.g. "(12/512GB)" or "(8/1TB)"
    # Unit is REQUIRED (not optional) so display-size patterns like "(6.7/128)" don't match
    match = re.search(r'\((\d+)/(\d+)(GB|TB)\)', text, re.IGNORECASE)
    if match:
        ram, storage = int(match.group(1)), int(match.group(2))
        if (match.group(3) or '').upper() == 'TB':
            storage *= 1000
        return ram, storage

    # Pattern 2: Greek label "Μνήμη: RAM/Storage"
    match = re.search(r'Μνήμη:\s*(\d+)/(\d+)(GB|TB)?', text, re.IGNORECASE)
    if match:
        ram, storage = int(match.group(1)), int(match.group(2))
        if (match.group(3) or '').upper() == 'TB':
            storage *= 1000
        return ram, storage

    return None, None

data['RAM_GB'], data['Storage_GB'] = zip(*data.apply(extract_ram_storage, axis=1))


# ── BRAND / MODEL / COLOR ─────────────────────────────────────────────────────
# Skroutz product names follow a predictable structure:
#   "<Brand> <Model> (<RAM>/<Storage>GB) <Color>"   e.g. "Apple iPhone 17 Pro Max (12/512GB) Deep Blue"
#   "<Brand> <Model>"                                e.g. "Samsung Galaxy A56 5G Dual SIM"
#
# pattern_full captures all three fields when color is present.
# pattern_simple is the fallback for entries without storage/color info.
pattern_full = r"""(?x)
^(?P<Brand>[^ ]+)\s+(?P<Model>.+?)
\(\s*\d+/\d+(?:GB|TB)\)\s*(?P<Color>.+)$
"""
pattern_simple = r"^(?P<Brand>[^ ]+)\s+(?P<Model>.+)$"

extracted = data['Product'].str.extract(pattern_full)
remaining = extracted[extracted['Brand'].isnull()].index   # rows that didn't match pattern_full
extracted.loc[remaining, ['Brand', 'Model']] = (
    data.loc[remaining, 'Product'].str.extract(pattern_simple)[['Brand', 'Model']]
)

data['Brand'] = extracted['Brand']
data['Model'] = extracted['Model']
data['Color'] = extracted['Color']   # NULL for listings without an explicit colour variant

# Scraper stores "N/A" when a product field couldn't be read.
# After regex splitting this becomes Brand="N", Model="/A" — clean it up.
_na_rows = data['Product'].isin(['N/A', 'N/A']) | data['Brand'].isin(['N', 'N/A'])
data.loc[_na_rows, ['Brand', 'Model', 'Color']] = None


# ── SPECS ─────────────────────────────────────────────────────────────────────
# The raw Specs column holds a comma-separated string in Greek, e.g.:
#   "Κύρια Κάμερα 48MP, Οθόνη: OLED 6.3", Μπαταρία: 3692mAh"

def extract_camera(specs):
    """Return the camera segment of the specs string (e.g. 'Κύρια 48MP')."""
    if pd.isna(specs):
        return None
    m = re.search(r'([^,]*Κάμερα[^,]*)', specs)
    return m.group(1).strip().replace(' Κάμερα', '') if m else None

def extract_display(specs):
    """Return the display description (e.g. 'OLED 6.3"')."""
    if pd.isna(specs):
        return None
    m = re.search(r'Οθόνη:\s*([^,]+)', specs)
    return m.group(1).strip() if m else None

def extract_battery(specs):
    """Return the battery capacity string (e.g. '3692mAh')."""
    if pd.isna(specs):
        return None
    m = re.search(r'Μπαταρία:\s*([^\s,]+)', specs)
    return m.group(1).strip() if m else None

data['Camera_Type']    = data['Specs'].apply(extract_camera)
data['Display_Info']   = data['Specs'].apply(extract_display)
data['Battery_Info']   = data['Specs'].apply(extract_battery)

# Numeric summaries derived from the text fields above
data['Num_Cameras']    = data['Camera_Type'].str.extract(r'(\d+)MP', expand=False).astype(float)   # main camera megapixels
data['Display_inches'] = data['Display_Info'].str.extract(r'(\d+\.?\d*)"', expand=False).astype(float)


# ── INSTALLMENTS / RATINGS ────────────────────────────────────────────────────
# Raw installment values use Greek decimal commas (e.g. "44,10" = 44.10 €).
# Strip all non-numeric characters, then convert comma → dot before casting to float.
for col in ['Installments_per_month', 'Installments_in_total']:
    data[col] = data[col].astype(str).str.replace(r'[^\d.,]', '', regex=True)
    data[col] = data[col].str.replace(',', '.', regex=False)
    data[col] = pd.to_numeric(data[col], errors='coerce')

data['Rating']  = pd.to_numeric(data['Rating'],  errors='coerce')
data['Reviews'] = pd.to_numeric(data['Reviews'], errors='coerce')


# ── EXPORT ────────────────────────────────────────────────────────────────────
# Column order matches the products + price_snapshots DB schema in 4csvsTOsql.py
final_columns = [
    'date_added', 'Brand', 'Model', 'RAM_GB', 'Storage_GB',
    'Num_Cameras', 'Camera_Type', 'Display_inches', 'Battery_Info',
    'Price_EUR', 'Rating', 'Reviews',
    'Installments_per_month', 'Installments_in_total',
    'Color', 'Display_Info', 'Product', 'Specs', 'Link',
]
data_export = data[final_columns]

output_folder = os.path.join(BASE, 'Clean', 'Phones_skroutz_clean')
os.makedirs(output_folder, exist_ok=True)
output_path = os.path.join(output_folder, f"clean_{today}.csv")
data_export.to_csv(output_path, index=False, encoding="utf-8-sig")

print(f"Clean file saved: {output_path}")
print(f"Total products:      {len(data_export)}")
print(f"RAM extracted:       {data_export['RAM_GB'].notna().sum()} / {len(data_export)}")
print(f"Storage extracted:   {data_export['Storage_GB'].notna().sum()} / {len(data_export)}")
print(f"Brand extracted:     {data_export['Brand'].notna().sum()} / {len(data_export)}")

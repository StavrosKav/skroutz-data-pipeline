"""
Data_Phone.py — phones cleaner entry point.

Shared cleaning logic lives in clean_common.py; this file keeps only the
phone-specific enrichment (RAM/storage, camera, display, battery extraction).

Reads:  Phones_skroutz/skroutz_phones_<today>.csv
Writes: Clean/Phones_skroutz_clean/clean_<today>.csv
"""

import pandas as pd
import re

from clean_common import CleanerConfig, run_clean, clean_price as clean_price  # re-exported for tests


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


def enrich(data):
    data['RAM_GB'], data['Storage_GB'] = zip(*data.apply(extract_ram_storage, axis=1))
    data['Camera_Type']  = data['Specs'].apply(extract_camera)
    data['Display_Info'] = data['Specs'].apply(extract_display)
    data['Battery_Info'] = data['Specs'].apply(extract_battery)

    # Numeric summaries derived from the text fields above
    data['Num_Cameras']    = data['Camera_Type'].str.extract(r'(\d+)MP', expand=False).astype(float)   # main camera megapixels
    data['Display_inches'] = data['Display_Info'].str.extract(r'(\d+\.?\d*)"', expand=False).astype(float)


CONFIG = CleanerConfig(
    category="phones",
    raw_folder="Phones_skroutz",
    raw_prefixes=("skroutz_phones",),
    clean_folder="Phones_skroutz_clean",
    final_columns=(
        'date_added', 'Brand', 'Model', 'RAM_GB', 'Storage_GB',
        'Num_Cameras', 'Camera_Type', 'Display_inches', 'Battery_Info',
        'Price_EUR', 'Rating', 'Reviews',
        'Installments_per_month', 'Installments_in_total',
        'Color', 'Display_Info', 'Product', 'Specs', 'Link',
    ),
    keep_color=True,
    color_required=True,
    enrich=enrich,
)


if __name__ == "__main__":
    run_clean(CONFIG)

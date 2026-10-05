"""
Data_Tablets.py — tablets cleaner entry point.

Shared cleaning logic lives in clean_common.py; this file adds only the
tablet RAM/storage extraction from titles like "... 10.1 (8GB/128GB) Γκρι".

Reads:  Tablets_skroutz/skroutz_tablets_<today>.csv
Writes: Clean/Tablets_skroutz_clean/clean_<today>.csv
"""

import pandas as pd

from clean_common import CleanerConfig, run_clean, parse_ram_storage, clean_price as clean_price  # re-exported for tests


def enrich(data):
    """Fill RAM_GB / Storage_GB from the product title (NaN when not parseable)."""
    parsed = data['Product'].apply(parse_ram_storage)
    data['RAM_GB'] = pd.to_numeric(parsed.str[0], errors='coerce')
    data['Storage_GB'] = pd.to_numeric(parsed.str[1], errors='coerce')


CONFIG = CleanerConfig(
    category="tablets",
    raw_folder="Tablets_skroutz",
    raw_prefixes=("skroutz_tablets",),
    clean_folder="Tablets_skroutz_clean",
    final_columns=(
        'date_added', 'Brand', 'Model', 'Product', 'Specs',
        'Price_EUR', 'Installments_per_month', 'Installments_in_total',
        'Rating', 'Reviews', 'Link', 'RAM_GB', 'Storage_GB',
    ),
    enrich=enrich,
)


if __name__ == "__main__":
    run_clean(CONFIG)

"""
Data_Tablets.py — tablets cleaner entry point.

Shared cleaning logic lives in clean_common.py.

Reads:  Tablets_skroutz/skroutz_tablets_<today>.csv
Writes: Clean/Tablets_skroutz_clean/clean_<today>.csv
"""

from clean_common import CleanerConfig, run_clean, clean_price as clean_price  # re-exported for tests

CONFIG = CleanerConfig(
    category="tablets",
    raw_folder="Tablets_skroutz",
    raw_prefixes=("skroutz_tablets",),
    clean_folder="Tablets_skroutz_clean",
    final_columns=(
        'date_added', 'Brand', 'Model', 'Product', 'Specs',
        'Price_EUR', 'Installments_per_month', 'Installments_in_total',
        'Rating', 'Reviews', 'Link',
    ),
)


if __name__ == "__main__":
    run_clean(CONFIG)

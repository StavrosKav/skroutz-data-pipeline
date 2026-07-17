"""
Data_Smartwatches.py — smartwatches cleaner entry point.

Shared cleaning logic lives in clean_common.py.

Reads:  Smartwatches_skroutz/skroutz_Smartwatches_<today>.csv
Writes: Clean/Smartwatches_skroutz_clean/clean_<today>.csv
"""

from clean_common import CleanerConfig, run_clean, clean_price as clean_price  # re-exported for tests

CONFIG = CleanerConfig(
    category="smartwatches",
    raw_folder="Smartwatches_skroutz",
    raw_prefixes=("skroutz_Smartwatches",),
    clean_folder="Smartwatches_skroutz_clean",
    final_columns=(
        'date_added', 'Brand', 'Model', 'Product', 'Specs',
        'Price_EUR', 'Installments_per_month', 'Installments_in_total',
        'Rating', 'Reviews', 'Link',
    ),
)


if __name__ == "__main__":
    run_clean(CONFIG)

"""
Data_Laptops.py — laptops cleaner entry point.

Shared cleaning logic lives in clean_common.py.

Reads:  Laptops_skroutz/skroutz_laptops_<today>.csv
        (falls back to the historical "skroutz_laptos_" typo so old files still work)
Writes: Clean/Laptops_skroutz_clean/clean_<today>.csv
"""

from clean_common import CleanerConfig, run_clean, clean_price as clean_price  # re-exported for tests

CONFIG = CleanerConfig(
    category="laptops",
    raw_folder="Laptops_skroutz",
    raw_prefixes=("skroutz_laptops", "skroutz_laptos"),
    clean_folder="Laptops_skroutz_clean",
    final_columns=(
        'date_added', 'Brand', 'Model', 'Product', 'Specs',
        'Price_EUR', 'Installments_per_month', 'Installments_in_total',
        'Rating', 'Reviews', 'Link',
    ),
)


if __name__ == "__main__":
    run_clean(CONFIG)

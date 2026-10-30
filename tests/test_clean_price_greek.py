"""clean_price: Greek format ('.' thousands, ',' decimals) and the scraper's all-dots format."""
import math

import pytest

from etl.clean_common import clean_price


@pytest.mark.parametrize("raw, want", [
    ("1.800 €", 1800.0),          # Greek thousands, no decimals (was 1.8)
    ("1.800,50 €", 1800.5),       # Greek thousands + decimals (was NaN)
    ("999,99 €", 999.99),
    ("12,50€", 12.5),
    ("1.234.567,89 €", 1234567.89),
    ("1.800", 1800.0),            # scraper format of "1.800 €"
    ("1.800.00", 1800.0),         # scraper format of "1.800,00 €"
    ("1.209.67", 1209.67),
    ("879.00", 879.0),
    ("28.00", 28.0),
    ("4.48", 4.48),
    ("12.5", 12.5),
    ("€1.348.88", 1348.88),
    ("199", 199.0),
])
def test_clean_price(raw, want):
    assert clean_price(raw) == pytest.approx(want)


@pytest.mark.parametrize("raw", ["N/A", "", None, "abc"])
def test_clean_price_missing(raw):
    assert math.isnan(clean_price(raw))

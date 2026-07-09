"""
Brand normalizer for standardizing brand names, especially for smartwatch pollution.
"""

import re
import logging
from typing import Dict, List, Optional, Tuple
from agents.base import BaseAgent

logger = logging.getLogger(__name__)


class BrandNormalizer(BaseAgent):
    """Normalizes brand names using known lists and variation mapping."""

    def __init__(self, config: Optional[dict] = None):
        super().__init__("brand_normalizer", config)
        # Known brands for each category (can be extended via config)
        self.known_brands = set(
            self.config.get(
                "known_brands",
                [
                    # Phones
                    "Apple", "Samsung", "Xiaomi", "Oppo", "Vivo", "Realme", "OnePlus",
                    "Google", "Huawei", "Honor", "Motorola", "Nokia", "LG", "Sony",
                    "Asus", "ZTE", "Alcatel", "Tecno", "Infinix", "Itel", "UMEOX",
                    "Blackview", "Umidigi", "Cubot", "Doogee", "Elephone", "Gionee",
                    "Lenovo", "Meizu", "Poco", "Redmi", "Sharp", "Panasonic",
                    # Laptops
                    "Dell", "HP", "Lenovo", "Asus", "Acer", "MSI", "Apple", "Microsoft",
                    "Samsung", "LG", "Gateway", "Panasonic", "Fujitsu", "Toshiba",
                    "Alienware", "Razer", "Gigabyte",
                    # Smartwatches
                    "Apple", "Samsung", "Garmin", "Fitbit", "Amazfit", "Huami",
                    "Mobvoi", "Suunto", "Polar", "Withings", "Fossil", "Skagen",
                    "Diesel", "Michael Kors", "Kate Spade", "Fossil Group",
                    "Misfit", "WHO",  # Note: Removed
                    # Tablets
                    "Apple", "Samsung", "Lenovo", "Huawei", "Amazon", "Microsoft",  # Note: This is a comment, but we'll keep it for now
                ]
            )
        )
        # Clean the known_brands set: remove empty strings and comments
        self.known_brands = {
            b.strip()
            for b in self.known_brands
            if b.strip() and not b.strip().startswith("#")
        }

        # Variation mapping: common misspellings/variations to canonical brand
        self.variation_map = {
            # Common variations
            "iphone": "Apple",
            "ipad": "Apple",
            "macbook": "Apple",
            "iwatch": "Apple",
            "galaxy": "Samsung",
            "note": "Samsung",
            "redmi": "Xiaomi",
            "poco": "Xiaomi",
            "mi": "Xiaomi",
            "galaxy watch": "Samsung",
            "galaxy buds": "Samsung",
            "airpods": "Apple",
            "apple watch": "Apple",
            "macbook air": "Apple",
            "macbook pro": "Apple",
            "ipad pro": "Apple",
            "ipad mini": "Apple",
            # Common OCR errors / variations
            "appl": "Apple",
            "sams ung": "Samsung",
            "samsang": "Samsung",
            "xismaomi": "Xiaomi",
            # Add more as needed
        }
        # Compile regex for cleaning unwanted characters (keeping letters, digits, spaces, hyphens, apostrophes)
        self.cleanup_regex = re.compile(r"[^a-zA-Z0-9\s\-']")

        # Load custom brands/variations from config if provided
        if "known_brands" in self.config:
            self.known_brands.update(self.config["known_brands"])
        if "variation_map" in self.config:
            self.variation_map.update(self.config["variation_map"])

    def process(self, input_data: List[dict]) -> List[dict]:
        """
        Normalize brand names in a list of product dictionaries.
        Each dictionary should have a 'brand' key.
        Returns a new list with normalized brands and confidence scores.
        """
        if not isinstance(input_data, list):
            self.logger.warning(
                "Input is not a list, wrapping in list: %s", type(input_data)
            )
            input_data = [input_data]

        normalized_products = []
        for product in input_data:
            # Make a shallow copy to avoid modifying the original
            product_copy = (
                dict(product) if hasattr(product, "copy") else dict(**product)
            )
            original_brand = product_copy.get("brand", "")
            normalized_brand, confidence = self._normalize_brand_with_confidence(original_brand)
            product_copy["brand"] = normalized_brand
            product_copy["_brand_confidence"] = float(confidence)
            # Keep track of original for debugging/auditing
            if "_original_brand" not in product_copy:
                product_copy["_original_brand"] = original_brand
            # Log when we assign to "Other" for monitoring
            if normalized_brand == "Other" and original_brand.strip():
                self.logger.debug(f"Brand '{original_brand}' mapped to 'Other'")
            normalized_products.append(product_copy)

        return normalized_products

    def _normalize_brand_with_confidence(self, brand: str) -> Tuple[str, float]:
        """Normalize a single brand name and return (normalized_brand, confidence)."""
        if not isinstance(brand, str) or not brand.strip():
            return "Other", 0.0

        # Clean the string: remove unwanted characters, extra whitespace
        cleaned = self.cleanup_regex.sub("", brand.strip())
        # Replace multiple spaces with single space
        cleaned = re.sub(r"\s+", " ", cleaned)
        if not cleaned:
            return "Other", 0.0

        # Check exact match (case-sensitive first, then case-insensitive)
        if cleaned in self.known_brands:
            return cleaned, 1.0
        cleaned_lower = cleaned.lower()
        for known_brand in self.known_brands:
            if known_brand.lower() == cleaned_lower:
                return known_brand, 0.9

        # Check if it's a known variation
        if cleaned in self.variation_map:
            return self.variation_map[cleaned], 0.8
        if cleaned_lower in self.variation_map:
            return self.variation_map[cleaned_lower], 0.8

        # Check for partial matches (substring)
        for known_brand in self.known_brands:
            if (
                known_brand.lower() in cleaned_lower
                or cleaned_lower in known_brand.lower()
            ):
                # Prefer the longer match? For now, just return the known brand.
                return known_brand, 0.7

        # If no match found, return as is or map to 'Other' based on configuration
        if self.config.get("map_unknown_to_other", True):
            return "Other", 0.0
        return cleaned, 0.0  # confidence 0.0 for unknown but not mapped to Other


def create_brand_normalizer(config: Optional[dict] = None) -> BrandNormalizer:
    """Factory function to create a BrandNormalizer instance."""
    return BrandNormalizer(config)
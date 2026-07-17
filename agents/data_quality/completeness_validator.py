"""
Completeness Validator subagent for the Data Quality Agent.
Checks for missing values in critical and warning fields.
"""

import logging
from typing import List, Optional
from agents.base import BaseAgent

logger = logging.getLogger(__name__)


class CompletenessValidator(BaseAgent):
    """
    Validates the completeness of data by checking for missing values.
    """

    def __init__(self, config: Optional[dict] = None):
        super().__init__("completeness_validator", config)
        # Fields that are critical (missing is a significant issue)
        self.critical_fields = self.config.get(
            "critical_fields",
            ["product_id", "name", "price_eur", "category"]
        )
        # Fields that are warnings (missing is less severe)
        self.warning_fields = self.config.get(
            "warning_fields",
            ["brand", "model", "sku", "availability"]
        )
        # If not provided, use empty lists
        if not self.critical_fields:
            self.critical_fields = []
        if not self.warning_fields:
            self.warning_fields = []

    def process(self, input_data: List[dict]) -> List[dict]:
        """
        Check each record for missing values in critical and warning fields.
        Adds '_completeness_score' (0.0 to 1.0) and '_missing_fields' (list of missing field names).
        Returns the input data with annotations.
        """
        if not isinstance(input_data, list):
            self.logger.warning(
                "Input is not a list, wrapping in list: %s", type(input_data)
            )
            input_data = [input_data]

        all_fields = set(self.critical_fields + self.warning_fields)

        # Initialize missing fields list for each record
        for record in input_data:
            record["_missing_fields"] = []

        # Check each record
        for record in input_data:
            missing = []
            for field in all_fields:
                if field not in record or record[field] is None or (isinstance(record[field], str) and not record[field].strip()):
                    missing.append(field)
            record["_missing_fields"] = missing

        # Compute completeness score for each record
        # We'll weight critical fields more heavily
        for record in input_data:
            missing = set(record.get("_missing_fields", []))
            # Score: 1.0 - (0. * len(critical_missing) + 0.5 * len(warning_missing)) / (len(critical_fields) + 0.5 * len(warning_fields))
            # But we want a simple score between 0 and 1
            # Let's do: score = 1.0 if no missing, else reduce by 0.1 for each missing critical and 0.05 for each missing warning
            # But we need to normalize by the total possible
            # Alternatively, we can do: score = (number of non-missing fields) / (total fields)
            total_fields = len(self.critical_fields) + len(self.warning_fields)
            if total_fields == 0:
                completeness = 1.0
            else:
                non_missing = total_fields - len(missing)
                completeness = non_missing / total_fields
            record["_completeness_score"] = completeness

        # Count records with missing critical fields
        critical_missing_count = sum(1 for r in input_data if any(f in r.get("_missing_fields", []) for f in self.critical_fields))
        warning_missing_count = sum(1 for r in input_data if any(f in r.get("_missing_fields", []) for f in self.warning_fields))
        self.logger.info(
            f"Completeness check complete: {critical_missing_count} records missing critical fields, "
            f"{warning_missing_count} records missing warning fields out of {len(input_data)}"
        )

        return input_data
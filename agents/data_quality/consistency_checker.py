"""
Consistency Checker subagent for the Data Quality Agent.
Checks for logical inconsistencies in the data.
"""

import logging
from typing import Dict, List, Optional, Any
from agents.base import BaseAgent

logger = logging.getLogger(__name__)


class ConsistencyChecker(BaseAgent):
    """
    Checks for logical inconsistencies in the data based on rules.
    Each rule has a condition and a check.
    """

    def __init__(self, config: Optional[dict] = None):
        super().__init__("consistency_checker", config)
        # Rules for consistency checking
        self.rules = self.config.get("rules", [])
        # If no rules provided, use some default ones
        if not self.rules:
            self.rules = [
                {
                    "condition": {"brand": "Apple"},
                    "check": {"model_contains": ["iPhone", "iPad", "MacBook", "Watch"]},
                    "message": "Apple product model should contain iPhone, iPad, MacBook, or Watch"
                },
                {
                    "condition": {"brand": "Samsung"},
                    "check": {"model_contains": ["Galaxy"]},
                    "message": "Samsung product model should contain Galaxy"
                },
                {
                    "condition": {"brand": "Xiaomi"},
                    "check": {"model_contains": ["Redmi", "Mi", "Poco"]},
                    "message": "Xiaomi product model should contain Redmi, Mi, or Poco"
                }
            ]

    def process(self, input_data: List[dict]) -> List[dict]:
        """
        Check each record for consistency violations.
        Adds '_consistency_score' (0.0 to 1.0) and '_consistency_violations' (list of messages).
        Returns the input data with annotations.
        """
        if not isinstance(input_data, list):
            self.logger.warning(
                "Input is not a list, wrapping in list: %s", type(input_data)
            )
            input_data = [input_data]

        # Initialize scores and violations
        for record in input_data:
            record["_consistency_violations"] = []
            # Start with perfect score, deduct for each violation
            # We'll compute the score as 1 - (violation_weight * number_of_violations)
            # But for simplicity, we'll set score to 0.0 if any violation, 1.0 otherwise
            # Alternatively, we can have a more nuanced scoring
            # Let's do: score = 1.0 / (1.0 + number_of_violations)
            # So 0 violations -> 1.0, 1 violation -> 0.5, 2 -> 0.33, etc.
            pass

        # Process each rule
        for rule in self.rules:
            condition = rule.get("condition", {})
            check = rule.get("check", {})
            message = rule.get("message", "Consistency violation")

            # Apply the rule to each record
            for idx, record in enumerate(input_data):
                # Check if the record matches the condition
                matches_condition = True
                for cond_key, cond_value in condition.items():
                    if cond_key not in record:
                        matches_condition = False
                        break
                    if isinstance(cond_value, list):
                        # For list conditions, we check if the record's value is in the list
                        # But note: in our config, condition is like {brand: Apple} which is not a list
                        # We'll handle both
                        if isinstance(cond_value, list):
                            if record[cond_key] not in cond_value:
                                matches_condition = False
                                break
                        else:
                            # Simple equality
                            if record[cond_key] != cond_value:
                                matches_condition = False
                                break
                    else:
                        # Simple equality
                        if record[cond_key] != cond_value:
                            matches_condition = False
                            break

                if matches_condition:
                    # Now check the check condition
                    check_passed = True
                    check_type = None
                    check_value = None
                    for chk_key, chk_val in check.items():
                        check_type = chk_key
                        check_value = chk_val
                        break  # assuming only one check per rule for simplicity

                    if check_type == "model_contains":
                        # Check if the model contains any of the strings in the list
                        model_val = str(record.get("model", ""))
                        if not any(substr in model_val for substr in check_value):
                            check_passed = False
                    else:
                        # Unknown check type, skip
                        self.logger.warning(f"Unknown check type: {check_type}")
                        check_passed = True

                    if not check_passed:
                        # Add violation
                        record["_consistency_violations"].append(message)

        # Compute consistency score for each record
        for record in input_data:
            num_violations = len(record.get("_consistency_violations", []))
            # Score: 1.0 / (1.0 + num_violations)
            # This gives 1.0 for 0 violations, 0.5 for 1, 0.33 for 2, etc.
            record["_consistency_score"] = 1.0 / (1.0 + num_violations)

        violation_count = sum(1 for r in input_data if r.get("_consistency_violations", []))
        self.logger.info(
            f"Consistency check complete: {violation_count} records with violations out of {len(input_data)}"
        )

        return input_data
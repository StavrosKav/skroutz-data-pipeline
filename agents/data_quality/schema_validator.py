"""
Schema Validator subagent for the Data Quality Agent.
Validates that data conforms to expected schema.
"""

import logging
import re
from typing import List, Optional
from agents.base import BaseAgent

logger = logging.getLogger(__name__)


class SchemaValidator(BaseAgent):
    """
    Validates data against a predefined schema.
    Checks for required fields, data types, and value ranges.
    Supports schema definition with:
      - type: expected type (or tuple of types)
      - min: minimum value (for numeric)
      - max: maximum value (for numeric)
      - enum: list of allowed values
      - regex: regex pattern (for strings)
      - min_length: minimum string length
    """

    def __init__(self, config: Optional[dict] = None):
        super().__init__("schema_validator", config)
        # Schema definition: field name -> definition
        # Definition can be:
        #   - a type or tuple of types (for backward compatibility)
        #   - a dict with keys: type, min, max, enum, regex, min_length
        raw_schema = self.config.get(
            "schema",
            {
                "product_id": {"type": int, "min": 1},
                "sku": {"type": str, "regex": r"^[a-zA-Z0-9_-]+$"},
                "name": {"type": str, "min_length": 1},
                "brand": {"type": str},
                "model": {"type": str},
                "price_eur": {"type": [float, int], "min": 0, "max": 10000},
                "availability": {"type": str, "enum": ["in_stock", "out_of_stock", "preorder"]},
                "category": {"type": str, "enum": ["phones", "laptops", "smartwatches", "tablets"]},
            },
        )
        self.schema = self._parse_schema(raw_schema)
        # Fields that are required (must be present and not None)
        self.required_fields = self.config.get(
            "required_fields",
            ["product_id", "name", "price_eur", "category"]
        )
        # Optional: validation functions for fields
        self.validation_funcs = self.config.get("validators", {})

    # Type names as they appear in JSON config → Python types
    _TYPE_NAMES = {
        "int": int,
        "float": (int, float),
        "number": (int, float),
        "str": str,
        "string": str,
        "bool": bool,
    }

    def _resolve_type(self, t):
        """Resolve a type spec that may be a Python type, a JSON string name,
        or a list/tuple mixing both, into a type or tuple of types."""
        if isinstance(t, type):
            return t
        if isinstance(t, str):
            if t not in self._TYPE_NAMES:
                raise ValueError(f"Unknown type name in schema: '{t}'")
            return self._TYPE_NAMES[t]
        if isinstance(t, (list, tuple)):
            resolved = []
            for item in t:
                r = self._resolve_type(item)
                resolved.extend(r if isinstance(r, tuple) else (r,))
            return tuple(resolved)
        raise ValueError(f"Invalid type spec in schema: {t!r}")

    def _parse_schema(self, raw_schema: dict) -> dict:
        """Parse the raw schema into a normalized format."""
        parsed = {}
        for field, definition in raw_schema.items():
            if isinstance(definition, (type, str)) or isinstance(definition, (list, tuple)):
                # Backward compatibility: just a type (or name, or list of either)
                parsed[field] = {"type": self._resolve_type(definition)}
            elif isinstance(definition, dict):
                # New format: ensure type is present
                if "type" not in definition:
                    raise ValueError(f"Schema for field '{field}' must specify a 'type'")
                definition = dict(definition)
                definition["type"] = self._resolve_type(definition["type"])
                parsed[field] = definition
            else:
                raise ValueError(f"Invalid schema definition for field '{field}': {definition}")
        return parsed

    def process(self, input_data: List[dict]) -> List[dict]:
        """
        Validate each record against the schema.
        Adds validation flags to each record: '_schema_valid' (bool) and '_validation_errors' (list).
        Returns the input data with annotations.
        """
        if not isinstance(input_data, list):
            self.logger.warning(
                "Input is not a list, wrapping in list: %s", type(input_data)
            )
            input_data = [input_data]

        validation_results = []

        for idx, record in enumerate(input_data):
            is_valid = True
            errors = []

            # Check required fields
            for field in self.required_fields:
                if field not in record:
                    is_valid = False
                    errors.append(f"Missing required field: {field}")
                elif record[field] is None:
                    is_valid = False
                    errors.append(f"Required field is null: {field}")

            # Check field types and constraints
            for field, definition in self.schema.items():
                if field not in record:
                    # Skip if field is not present (might be optional)
                    continue
                value = record[field]
                if value is None:
                    # Allow None if not required? For now, we'll skip validation for None
                    continue

                expected_type = definition["type"]
                # Check type
                if isinstance(expected_type, tuple):
                    # Multiple allowed types (e.g., (int, float))
                    type_ok = any(isinstance(value, t) for t in expected_type)
                else:
                    type_ok = isinstance(value, expected_type)

                if not type_ok:
                    is_valid = False
                    expected_str = (
                        ", ".join(t.__name__ for t in expected_type)
                        if isinstance(expected_type, tuple)
                        else expected_type.__name__
                    )
                    errors.append(
                        f"Field '{field}' has incorrect type. "
                        f"Expected {expected_str}, got {type(value).__name__}"
                    )
                    # Skip further checks if type is wrong
                    continue

                # Apply additional constraints if type is correct
                # Note: we only apply these if the type is correct to avoid errors
                try:
                    # Min/Max for numeric types
                    if "min" in definition and isinstance(value, (int, float)):
                        if value < definition["min"]:
                            is_valid = False
                            errors.append(
                                f"Field '{field}' value {value} is less than minimum {definition['min']}"
                            )
                    if "max" in definition and isinstance(value, (int, float)):
                        if value > definition["max"]:
                            is_valid = False
                            errors.append(
                                f"Field '{field}' value {value} is greater than maximum {definition['max']}"
                            )
                    # Enum validation
                    if "enum" in definition:
                        if value not in definition["enum"]:
                            is_valid = False
                            errors.append(
                                f"Field '{field}' value '{value}' is not in allowed values: {definition['enum']}"
                            )
                    # Regex validation
                    if "regex" in definition and isinstance(value, str):
                        if not re.match(definition["regex"], value):
                            is_valid = False
                            errors.append(
                                f"Field '{field}' value '{value}' does not match pattern {definition['regex']}"
                            )
                    # Min length for strings
                    if "min_length" in definition and isinstance(value, str):
                        if len(value) < definition["min_length"]:
                            is_valid = False
                            errors.append(
                                f"Field '{field}' length {len(value)} is less than minimum {definition['min_length']}"
                            )
                except Exception as e:
                    is_valid = False
                    errors.append(
                        f"Error validating field '{field}': {str(e)}"
                    )

            # Run custom validators if defined
            for field, validator_func in self.validation_funcs.items():
                if field in record and record[field] is not None:
                    try:
                        validator_result = validator_func(record[field])
                        if validator_result is not True:  # Assume validator returns True if valid, or error string
                            is_valid = False
                            err_msg = (
                                validator_result
                                if isinstance(validator_result, str)
                                else f"Validation failed for {field}"
                            )
                            errors.append(f"Field '{field}': {err_msg}")
                    except Exception as e:
                        is_valid = False
                        errors.append(
                            f"Validator for {field} raised exception: {str(e)}"
                        )

            # Add validation results to record
            record["_schema_valid"] = is_valid
            record["_validation_errors"] = errors

            if not is_valid:
                self.logger.debug(
                    f"Schema validation failed for record {idx}: {errors}"
                )

            validation_results.append(record)

        valid_count = sum(1 for r in input_data if r.get("_schema_valid", False))
        self.logger.info(
            f"Schema validation complete: {valid_count} valid, {len(input_data) - valid_count} invalid records"
        )

        return input_data
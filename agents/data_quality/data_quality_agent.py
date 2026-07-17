"""
Data Quality Agent that orchestrates data quality subagents.
"""

import logging
from typing import List, Optional
from agents.base import BaseAgent

from .anomaly_detector import AnomalyDetector
from .schema_validator import SchemaValidator
from .completeness_validator import CompletenessValidator

logger = logging.getLogger(__name__)


class DataQualityAgent(BaseAgent):
    """
    Data Quality Agent that runs a pipeline of data quality checks over the
    RAW scraper CSVs:
    1. Schema Validation
    2. Completeness Validation
    3. Anomaly Detection

    Consistency checking and brand normalization are deliberately not run
    here — both operate on brand/model fields that only exist after the
    Clean stage; against raw records (Product, Price_EUR, Link, ...) they
    never match anything and every brand silently normalizes to "Other".
    """

    def __init__(self, config: Optional[dict] = None):
        super().__init__("data_quality", config)
        # Initialize subagents
        self.schema_validator = SchemaValidator(
            config.get("schema_validator", {})
        )
        self.completeness_validator = CompletenessValidator(
            config.get("completeness_validator", {})
        )
        self.anomaly_detector = AnomalyDetector(
            config.get("anomaly_detector", {})
        )

    def process(self, input_data: List[dict]) -> List[dict]:
        """
        Run all data quality checks in sequence.
        Each subagent annotates the data with its findings.
        """
        self.logger.info("Starting Data Quality Agent pipeline")

        # Step 1: Schema Validation
        self.logger.debug("Running schema validation")
        data = self.schema_validator.process(input_data)

        # Step 2: Completeness Validation
        self.logger.debug("Running completeness validation")
        data = self.completeness_validator.process(data)

        # Step 3: Anomaly Detection
        self.logger.debug("Running anomaly detection")
        data = self.anomaly_detector.process(data)

        self.logger.info("Data Quality Agent pipeline completed")
        return data


def create_data_quality_agent(config: Optional[dict] = None) -> DataQualityAgent:
    """Factory function to create a DataQualityAgent instance."""
    return DataQualityAgent(config)
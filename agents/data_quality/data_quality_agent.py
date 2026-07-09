"""
Data Quality Agent that orchestrates data quality subagents.
"""

import logging
from typing import Dict, List, Optional, Any
from agents.base import BaseAgent

from .brand_normalizer import BrandNormalizer
from .anomaly_detector import AnomalyDetector
from .schema_validator import SchemaValidator
from .consistency_checker import ConsistencyChecker
from .completeness_validator import CompletenessValidator

logger = logging.getLogger(__name__)


class DataQualityAgent(BaseAgent):
    """
    Data Quality Agent that runs a pipeline of data quality checks:
    1. Schema Validation
    2. Consistency Check
    3. Completeness Validation
    4. Anomaly Detection
    5. Brand Normalization
    """

    def __init__(self, config: Optional[dict] = None):
        super().__init__("data_quality", config)
        # Initialize subagents
        self.schema_validator = SchemaValidator(
            config.get("schema_validator", {})
        )
        self.consistency_checker = ConsistencyChecker(
            config.get("consistency_checker", {})
        )
        self.completeness_validator = CompletenessValidator(
            config.get("completeness_validator", {})
        )
        self.anomaly_detector = AnomalyDetector(
            config.get("anomaly_detector", {})
        )
        self.brand_normalizer = BrandNormalizer(
            config.get("brand_normalizer", {})
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

        # Step 2: Consistency Check
        self.logger.debug("Running consistency check")
        data = self.consistency_checker.process(data)

        # Step 3: Completeness Validation
        self.logger.debug("Running completeness validation")
        data = self.completeness_validator.process(data)

        # Step 4: Anomaly Detection
        self.logger.debug("Running anomaly detection")
        data = self.anomaly_detector.process(data)

        # Step 5: Brand Normalization
        self.logger.debug("Running brand normalization")
        data = self.brand_normalizer.process(data)

        self.logger.info("Data Quality Agent pipeline completed")
        return data


def create_data_quality_agent(config: Optional[dict] = None) -> DataQualityAgent:
    """Factory function to create a DataQualityAgent instance."""
    return DataQualityAgent(config)
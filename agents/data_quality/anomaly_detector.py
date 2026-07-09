"""
Anomaly Detector subagent for the Data Quality Agent.
Detects outliers in numerical data using statistical methods.
"""

import logging
from typing import Dict, List, Optional, Any
import numpy as np

from agents.base import BaseAgent

logger = logging.getLogger(__name__)


class AnomalyDetector(BaseAgent):
    """
    Detects anomalies in numeric fields using statistical methods.
    Can be extended to use NVIDIA NIM models for more sophisticated detection.
    """

    def __init__(self, config: Optional[dict] = None):
        super().__init__("anomaly_detector", config)
        # Configuration for anomaly detection
        self.numeric_fields = self.config.get(
            "numeric_fields",
            ["price_eur", "discount_pct", "rating"]  # common numeric fields in Skroutz data
        )
        self.method = self.config.get("method", "iqr")  # iqr or zscore
        self.threshold = self.config.get("threshold", 1.5)  # for IQR, or number of std devs for Z-score
        self.min_samples = self.config.get("min_samples", 10)  # minimum samples to compute stats
        self.anomaly_threshold = self.config.get("anomaly_threshold", 1.0)  # threshold for flagging anomalies

    def process(self, input_data: List[dict]) -> List[dict]:
        """
        Detect anomalies in the input data and flag them.
        Adds an '_anomaly_score' field to each record indicating the deviation.
        Returns the input data with annotations.
        """
        if not isinstance(input_data, list):
            self.logger.warning(
                "Input is not a list, wrapping in list: %s", type(input_data)
            )
            input_data = [input_data]

        if len(input_data) < self.min_samples:
            self.logger.info(
                f"Skipping anomaly detection: insufficient samples ({len(input_data)} < {self.min_samples})"
            )
            # Still return the data unchanged
            for record in input_data:
                record["_anomaly_score"] = 0.0  # not an anomaly by default
                record["_is_anomaly"] = False
            return input_data

        # Extract numeric values for each field we want to check
        numeric_data = {field: [] for field in self.numeric_fields}
        valid_indices = []  # indices of records that have all numeric fields

        for idx, record in enumerate(input_data):
            # Check if record has all required numeric fields
            has_all = True
            values = {}
            for field in self.numeric_fields:
                val = record.get(field)
                if val is None:
                    has_all = False
                    break
                try:
                    # Try to convert to float
                    values[field] = float(val)
                except (ValueError, TypeError):
                    has_all = False
                    break
            if has_all:
                valid_indices.append(idx)
                for field in self.numeric_fields:
                    numeric_data[field].append(values[field])

        # If no valid data, return original with no anomalies
        if not valid_indices:
            self.logger.warning("No valid numeric data found for anomaly detection")
            for record in input_data:
                record["_anomaly_score"] = 0.0
                record["_is_anomaly"] = False
            return input_data

        # Compute statistics for each field
        stats = {}
        for field in self.numeric_fields:
            values = np.array(numeric_data[field])
            if self.method == "iqr":
                q1 = np.percentile(values, 25)
                q3 = np.percentile(values, 75)
                iqr = q3 - q1
                lower_bound = q1 - self.threshold * iqr
                upper_bound = q3 + self.threshold * iqr
                stats[field] = {
                    "lower": lower_bound,
                    "upper": upper_bound,
                    "iqr": iqr,
                    "method": "iqr",
                }
            elif self.method == "zscore":
                mean = np.mean(values)
                std = np.std(values)
                if std == 0:
                    # Avoid division by zero
                    lower_bound = upper_bound = mean
                else:
                    lower_bound = mean - self.threshold * std
                    upper_bound = mean + self.threshold * std
                stats[field] = {
                    "lower": lower_bound,
                    "upper": upper_bound,
                    "std": std,
                    "mean": mean,
                    "method": "zscore",
                }
            else:
                self.logger.warning(f"Unknown method {self.method}, defaulting to IQR")
                q1 = np.percentile(values, 25)
                q3 = np.percentile(values, 75)
                iqr = q3 - q1
                lower_bound = q1 - self.threshold * iqr
                upper_bound = q3 + self.threshold * iqr
                stats[field] = {
                    "lower": lower_bound,
                    "upper": upper_bound,
                    "iqr": iqr,
                    "method": "iqr",
                }

        # Annotate each record with anomaly score and flag
        anomaly_scores = [0.0] * len(input_data)
        for idx in valid_indices:
            record = input_data[idx]
            max_deviation = 0.0
            for field in self.numeric_fields:
                val = float(record[field])
                bounds = stats[field]
                if self.method == "iqr":
                    iqr_val = bounds["iqr"]
                    if iqr_val == 0:
                        iqr_val = 1.0  # avoid division by zero
                    if val < bounds["lower"]:
                        deviation = (bounds["lower"] - val) / iqr_val
                    elif val > bounds["upper"]:
                        deviation = (val - bounds["upper"]) / iqr_val
                    else:
                        deviation = 0.0  # inside bounds
                else:  # zscore
                    std_val = bounds["std"]
                    if std_val == 0:
                        std_val = 1.0  # avoid division by zero
                    if val < bounds["lower"]:
                        deviation = (bounds["lower"] - val) / std_val
                    elif val > bounds["upper"]:
                        deviation = (val - bounds["upper"]) / std_val
                    else:
                        deviation = 0.0
                if deviation > max_deviation:
                    max_deviation = deviation
            anomaly_scores[idx] = max_deviation

        # Mark anomalies: score > anomaly_threshold
        threshold = self.anomaly_threshold
        for idx, score in enumerate(anomaly_scores):
            is_anomaly = score > threshold
            input_data[idx]["_anomaly_score"] = float(score)
            input_data[idx]["_is_anomaly"] = bool(is_anomaly)
            if is_anomaly:
                self.logger.debug(
                    f"Anomaly detected in record {idx}: score={score}, record={input_data[idx]}"
                )

        anomaly_count = sum(1 for s in anomaly_scores if s > threshold)
        self.logger.info(
            f"Anomaly detection complete: {anomaly_count} anomalies found in {len(input_data)} records"
        )

        return input_data
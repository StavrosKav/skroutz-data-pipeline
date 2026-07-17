#!/usr/bin/env python
"""
Data Quality Agent runner for the pipeline (observer stage, read-only).
Checks the day's raw scraper CSVs and writes a quality report to
logs/data_quality_YYYY-MM-DD.json. Never modifies the CSVs.
"""

import os
import sys
import logging
import json
from datetime import datetime
import pandas as pd

# Add the project root to the path so we can import agents
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from agents.data_quality import create_data_quality_agent

# Setup logging
log_dir = os.path.join(BASE, "logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, f"data_quality_{datetime.now().date()}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file, encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)

def load_config():
    """Load configuration from JSON files."""
    config = {}
    # Load agent config
    agent_config_path = os.path.join(BASE, "config", "agents.json")
    if os.path.exists(agent_config_path):
        try:
            with open(agent_config_path, 'r') as f:
                agent_config = json.load(f)
                config.update(agent_config)
        except Exception as e:
            logger.warning(f"Failed to load agent config from {agent_config_path}: {e}")

    # Load pipeline config
    pipeline_config_path = os.path.join(BASE, "config", "pipeline.json")
    if os.path.exists(pipeline_config_path):
        try:
            with open(pipeline_config_path, 'r') as f:
                pipeline_config = json.load(f)
                config.update(pipeline_config)
        except Exception as e:
            logger.warning(f"Failed to load pipeline config from {pipeline_config_path}: {e}")

    return config

def process_csv_file(file_path: str, agent):
    """Run the data quality agent over one CSV, read-only.
    Returns a per-file report dict, or None on failure. Never writes to the
    CSV — raw scraper output is the pipeline's source of truth."""
    try:
        df = pd.read_csv(file_path)
        # NaN → None so "missing" means missing to the validators
        df = df.astype(object).where(pd.notnull(df), None)
        records = df.to_dict('records')

        processed = agent.process(records)

        total = len(processed)
        schema_invalid = sum(1 for r in processed if not r.get('_schema_valid', True))
        anomalies = sum(1 for r in processed if r.get('_is_anomaly', False))
        missing_critical = sum(
            1 for r in processed
            if any(f in r.get('_missing_fields', []) for f in agent.completeness_validator.critical_fields)
        )
        consistency_violations = sum(1 for r in processed if r.get('_consistency_violations'))

        # Most common validation errors, for a quick read of what's wrong
        error_counts = {}
        for r in processed:
            for err in r.get('_validation_errors', []):
                error_counts[err] = error_counts.get(err, 0) + 1
        top_errors = sorted(error_counts.items(), key=lambda kv: -kv[1])[:5]

        logger.info(
            f"Processed {os.path.basename(file_path)}: "
            f"{total} records, {schema_invalid} schema invalid, "
            f"{missing_critical} missing critical fields, {anomalies} anomalies"
        )
        return {
            "file": os.path.basename(file_path),
            "records": total,
            "schema_invalid": schema_invalid,
            "missing_critical": missing_critical,
            "consistency_violations": consistency_violations,
            "anomalies": anomalies,
            "top_validation_errors": [{"error": e, "count": c} for e, c in top_errors],
        }
    except Exception as e:
        logger.error(f"Failed to process {file_path}: {e}")
        return None

def main():
    logger.info("=== Data Quality Agent started ===")

    # Load configuration
    config = load_config()

    # Create the data quality agent with the loaded config
    dq_agent = create_data_quality_agent(config.get("data_quality", {}))

    # Define the directories for cleaned data (one per category)
    base_dir = BASE
    # Note: the actual folder names might have different casing or suffixes
    # We'll use the same mapping as in the scraper health monitor
    folder_map = {
        "phones": "Phones_skroutz",
        "laptops": "Laptops_skroutz",
        "smartwatches": "Smartwatches_skroutz",
        "tablets": "Tablets_skroutz"
    }

    success = True
    report = {
        "date": str(datetime.now().date()),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "categories": {},
    }
    for category, folder in folder_map.items():
        folder_path = os.path.join(base_dir, folder)
        if not os.path.exists(folder_path):
            logger.warning(f"Directory not found for category {category}: {folder_path}")
            success = False
            continue

        # Find the most recent CSV file in the folder (assuming naming pattern)
        try:
            files = [f for f in os.listdir(folder_path) if f.endswith('.csv') and f.startswith('skroutz_')]
            if not files:
                logger.warning(f"No CSV files found in {folder_path}")
                success = False
                continue

            # Sort by modification time (newest first)
            files.sort(key=lambda x: os.path.getmtime(os.path.join(folder_path, x)), reverse=True)
            latest_file = os.path.join(folder_path, files[0])

            logger.info(f"Processing latest CSV for {category}: {latest_file}")
            file_report = process_csv_file(latest_file, dq_agent)
            if file_report is None:
                success = False
            else:
                report["categories"][category] = file_report

        except Exception as e:
            logger.error(f"Error processing category {category}: {e}")
            success = False

    report_path = os.path.join(log_dir, f"data_quality_{datetime.now().date()}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    logger.info(f"Quality report written to {report_path}")

    if success:
        logger.info("Data Quality Agent completed successfully.")
        return 0
    else:
        logger.error("Data Quality Agent encountered errors.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
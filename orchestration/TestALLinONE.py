import pandas as pd
import subprocess
import datetime
import os
import logging
import time
from sqlalchemy import create_engine


SCRIPTS1 = [
    r"C:\Users\StavrosKV\Documents\Projects\ProjectsPY\skroutz_phonesWHILE.py",
    r"C:\Users\StavrosKV\Documents\Projects\ProjectsPY\skroutz_SmartwachesFOR.py",
    r"C:\Users\StavrosKV\Documents\Projects\ProjectsPY\skroutz_tabletsWHILE.py",
    r"C:\Users\StavrosKV\Documents\Projects\ProjectsPY\skroutz_laptopsWHILE.py",
]

LOG_DIR1 = r"C:\Users\StavrosKV\Documents\Projects\ProjectsPY\logs"
os.makedirs(LOG_DIR1, exist_ok=True)

# Delay between starting each scraper (in seconds)
LAUNCH_DELAY = 13


def run_all_scrapers1():
    """Spawn each scraper as its own process and write its stdout/stderr to a dated log."""
    date_str = datetime.date.today().isoformat()
    procs = []
    for script in SCRIPTS1:
        name = os.path.splitext(os.path.basename(script))[0]
        log_path = os.path.join(LOG_DIR1, f"{name}_{date_str}.log")
        log_file = open(log_path, "a", encoding="utf-8")
        logging.info(f"Starting {name} → logging to {log_path}")
        p = subprocess.Popen(
            ["python", script],
            stdout=log_file,
            stderr=log_file,
            shell=False,
        )
        procs.append((name, p, log_file))
        # delay before launching next
        logging.info(
            f"Waiting {LAUNCH_DELAY}s before launching next script...")
        time.sleep(LAUNCH_DELAY)

    # wait for all to finish
    for name, proc, log_file in procs:
        ret = proc.wait()
        log_file.close()
        if ret == 0:
            logging.info(f"{name} completed successfully.")
        else:
            logging.error(f"{name} exited with code {ret} (see log).")


def main1():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    # Run right now
    run_all_scrapers1()


if __name__ == "__main__":
    main1()


SCRIPTS = [
    r"C:\Users\StavrosKV\Documents\Projects\ProjectsPY\Data_Phone.py",
    r"C:\Users\StavrosKV\Documents\Projects\ProjectsPY\Data_Smartwatches.py",
    r"C:\Users\StavrosKV\Documents\Projects\ProjectsPY\Data_Tablets.py",
    r"C:\Users\StavrosKV\Documents\Projects\ProjectsPY\Data_Laptops.py",
]

LOG_DIR = r"C:\Users\StavrosKV\Documents\Projects\ProjectsPY\logs4"
os.makedirs(LOG_DIR, exist_ok=True)

# Delay between starting each scraper (in seconds)
LAUNCH_DELAY = 6

# This script runs all the data processing scripts for Skroutz data


def run_all_scrapers():
    """Spawn each scraper as its own process and write its stdout/stderr to a dated log."""
    date_str = datetime.date.today().isoformat()
    procs = []
    for script in SCRIPTS:
        name = os.path.splitext(os.path.basename(script))[0]
        log_path = os.path.join(LOG_DIR, f"{name}_{date_str}.log")
        log_file = open(log_path, "a", encoding="utf-8")
        logging.info(f"Starting {name} → logging to {log_path}")
        p = subprocess.Popen(
            ["python", script],
            stdout=log_file,
            stderr=log_file,
            shell=False,
        )

        procs.append((name, p, log_file))
        # delay before launching next
        logging.info(
            f"Waiting {LAUNCH_DELAY}s before launching next script...")
        time.sleep(LAUNCH_DELAY)

    # wait for all to finish
    for name, proc, log_file in procs:
        ret = proc.wait()
        log_file.close()
        if ret == 0:
            logging.info(f"{name} completed successfully.")
        else:
            logging.error(f"{name} exited with code {ret} (see log).")

# This script runs all the data processing scripts for Skroutz data


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    # Run right now
    run_all_scrapers()


if __name__ == "__main__":
    main()


# Your DB credentials here
DB_USER = ""
DB_PASSWORD = ""
DB_HOST = ""
DB_PORT = ""
DB_NAME = ""

today = datetime.date.today().isoformat()
# Define the base folders for each category
base_folder1 = r'C:\Users\StavrosKV\Documents\Projects\ProjectsPY\Clean\Phones_skroutz_clean'
base_folder2 = r'C:\Users\StavrosKV\Documents\Projects\ProjectsPY\Clean\Laptops_skroutz_clean'
base_folder3 = r'C:\Users\StavrosKV\Documents\Projects\ProjectsPY\Clean\Smartwatches_skroutz_clean'
base_folder4 = r'C:\Users\StavrosKV\Documents\Projects\ProjectsPY\Clean\Tablets_skroutz_clean'
# Define the filename for the cleaned data
filename = f"clean_{today}.csv"
# Construct the full file paths for each category
file_path1 = os.path.join(base_folder1, filename)
file_path2 = os.path.join(base_folder2, filename)
file_path3 = os.path.join(base_folder3, filename)
file_path4 = os.path.join(base_folder4, filename)

# Create a dictionary to map file paths to table names
files_tables = {
    file_path1: "phones",
    file_path2: "laptops",
    file_path3: "smartwaches",
    file_path4: "tablets"
}

# Create the connection engine
engine = create_engine(
    f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

# Check if the connection is successful
for file_path, table_name in files_tables.items():
    print(f"Importing {file_path} to {table_name} ...")
    df = pd.read_csv(file_path)
    df.to_sql(table_name, engine, if_exists='append', index=False)
    print(f"Done importing {file_path}")

print("All files imported successfully!")

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import re
import ast
import datetime
import os


today = datetime.date.today().isoformat()
# r"C:\Users\StavrosKV\Documents\Projects\ProjectsPY\SkroutzProject\Phones_skroutz"
base_folder = os.path.join('.', 'Phones_skroutz')
filename1 = f"skroutz_phones_{today}.csv"
file_path = os.path.join(base_folder, filename1)


data = pd.read_csv(
    file_path,
    sep=",",
    quotechar='"',
    on_bad_lines='skip',
    engine='python'
)

data
data.columns  # Display the columns of the DataFrame
data.shape  # Display the shape of the DataFrame (rows, columns)
data.head(50)  # Display the first 3 rows of the DataFrame
data.info()  # Display information about the DataFrame, including data types and non-null counts
data.describe()  # Display summary statistics for numerical columns in the DataFrame

data['date_added'] = f'{today}'

# Data Cleaning
data['Price_EUR'] = data['Price_EUR'].str.replace(
    '.', '').str.replace('€', '').str.replace('από', '', regex=False)
# Convert Ad! to 0 and clean up the Installments columns
data['Installments_per_month'] = data['Installments_per_month'].str.replace(
    'Ad!', '0').str.replace('€', '').str.replace(',', '.')
data['Installments_in_total'] = data['Installments_in_total'].str.replace(
    'Ad!', '0').str.replace('€', '')
numeric_cols = ['Price_EUR', 'Installments_per_month',
                'Installments_in_total', 'Rating', 'Reviews']
data[numeric_cols] = data[numeric_cols].apply(pd.to_numeric)
data['Price_EUR'] = data['Price_EUR']/100

# Product parser
product_col = data.columns[0]
print(data.columns)
pattern_full = r"""(?x)
^(?P<Brand>[^ ]+)
\s+
(?P<Model>.+?)            # Model: everything up to RAM/Storage, non-greedy
\(\s*(?P<RAM>\d+GB)/(?P<Storage>\d+(?:GB|TB))\)\s*
(?P<Color>.*)$
"""
pattern_simple = r"""(?x)
^(?P<Brand>[^ ]+)
\s+
(?P<Model>.+)$
"""

extracted_full = data[product_col].str.extract(pattern_full)

extracted_full["RAM_GB"] = pd.to_numeric(
    extracted_full["RAM"].str.replace("GB", "", regex=False), errors='coerce')
extracted_full["Storage_GB"] = pd.to_numeric(
    extracted_full["Storage"].str.replace("GB", "", regex=False).str.replace('1TB', '1000', regex=True), errors='coerce')

remaining = extracted_full[extracted_full['Brand'].isnull()].index
extracted_simple = data.loc[remaining, product_col].str.extract(pattern_simple)

# Merge simple extraction into full extraction
extracted_full.loc[remaining, ['Brand', 'Model']
                   ] = extracted_simple[['Brand', 'Model']]

# Finally join with original data
data_final = pd.concat([data, extracted_full], axis=1)

# Specs parser


def extract_camera(specs):
    if pd.isna(specs):
        return None
    # Find the part containing "Κάμερα" up to the next comma or end of string
    match = re.search(r'([^,]*Κάμερα[^,]*)', specs)
    return match.group(1).strip() if match else None


def extract_display(specs):
    if pd.isna(specs):
        return None
    # Find Οθόνη: ... (may or may not have a type before inches)
    match = re.search(r'Οθόνη:\s*([^,]+)', specs)
    return match.group(1).strip() if match else None


def extract_battery(specs):
    if pd.isna(specs):
        return None
    # Find Μπαταρία: ... (may or may not have a type before mAh)
    match = re.search(r'Μπαταρία:\s*([^\s,]+)', specs)
    return match.group(1).strip() if match else None


# Apply functions to create new columns
data_final['Camera_Type'] = data_final['Specs'].apply(
    extract_camera).replace(' Κάμερα', '', regex=True)
data_final['Display_Info'] = data_final['Specs'].apply(extract_display)
data_final['Battery_Info'] = data_final['Specs'].apply(extract_battery)
data_final.columns

# Map camera types to numerical values
camera_map = {
    'Μονή': 1,
    'Διπλή': 2,
    'Τριπλή': 3,
    'Τετραπλή': 4,
    'Πενταπλή': 5
}
# Replace camera types with numerical values
data_final['Num_Cameras'] = data_final['Camera_Type'].replace(camera_map)
# Extract display size in inches
data_final['Display_inches'] = data_final['Display_Info'].str.extract(
    r'(\d+\.?\d*)"', expand=False).astype(float)

# Choose your final columns (example selection)
final_columns = ['date_added',
                 'Brand', 'Model', 'RAM_GB', 'Storage_GB',
                 'Num_Cameras', 'Display_inches', 'Battery_Info',
                 'Price_EUR', 'Rating', 'Reviews', 'Installments_per_month', 'Installments_in_total', 'Color', 'Camera_Type',
                 'Display_Info', 'Product', 'Specs', 'Link'
                 ]
data_export = data_final[final_columns]

filename = f"clean_{today}.csv"
output_folder = os.path.join('.', 'Clean', 'Phones_skroutz_clean')
os.makedirs(output_folder, exist_ok=True)
output_path = output_path = os.path.join(output_folder, filename)
# os.path.join(
# r'C:\Users\StavrosKV\Documents\Projects\ProjectsPY\SkroutzProject\Clean\Phones_skroutz_clean', filename)
data_export.to_csv(output_path, index=False)

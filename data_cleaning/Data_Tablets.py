import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import re
import ast
import datetime
import os


today = datetime.date.today().isoformat()
base_folder = os.path.join('.', 'Tablets_skroutz')
# base_folder = r"C:\Users\StavrosKV\Documents\Projects\ProjectsPY\SkroutzProject\Tablets_skroutz"
filename1 = f"skroutz_tablets_{today}.csv"
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
    '.', '').str.replace('από', '', regex=False)
data['Reviews'] = data['Reviews'].replace('N/A', '0', regex=True)
data['Rating'] = data['Rating'].replace('N/A', '0', regex=True)
# Convert Ad! to 0 and clean up the Installments columns
numeric_cols = ['Price_EUR', 'Rating', 'Reviews']
data[numeric_cols] = data[numeric_cols].apply(pd.to_numeric)
data['Price_EUR'] = data['Price_EUR']/100
data['Price_EUR'].head(25)

# Product parser
product_col = data.columns[0]
print(data.columns)
pattern_full = r"""(?x)
^
(?P<Brand>[^ ]+)
"""
extracted_full = data[product_col].str.extract(pattern_full)

# Finally join with original data
data_final = pd.concat([data, extracted_full], axis=1)
# Check remaining nulls
print(data_final['Brand'].isnull().sum())
data_final['Brand'].head(25)
data_final.isnull().sum()  # Check for any remaining null values in the DataFrame
data_final['Specs'].head(25)

data_final['Product'] = data_final['Product'].str.replace('"', '', regex=False)
data_final['Specs'] = data_final['Specs'].str.replace('"', '', regex=False)
data_final.columns
final_columns = ['date_added',
                 'Brand', 'Product', 'Specs', 'Price_EUR', 'Rating', 'Reviews', 'Link']
data_export = data_final[final_columns]

filename = f"clean_{today}.csv"
output_folder = os.path.join('.', 'Clean', 'Tablets_skroutz_clean')
os.makedirs(output_folder, exist_ok=True)
output_path = output_path = os.path.join(output_folder, filename)
# output_path = os.path.join(
#    r'C:\Users\StavrosKV\Documents\Projects\ProjectsPY\SkroutzProject\Clean\Tablets_skroutz_clean', filename)
data_export.to_csv(output_path, index=False)

"""
Skroutz_data_EDA.py
--------------------
Exploratory Data Analysis (EDA) for the cleaned phone price dataset.

Generates three visualisations from a single day's cleaned phone CSV:
  1. Price distribution — histogram + KDE to show the spread of phone prices
  2. Product count by brand — how many listings each brand has (top 12)
  3. Average price by brand — which brands sit at which price point (top 12)

To use with a different date or category, update FILE_PATH below.
Run interactively (Jupyter / VS Code) or as a standalone script.

Dependencies: pandas, numpy, matplotlib, seaborn
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

# ── Data source ───────────────────────────────────────────────────────────────
# Update this path to analyse a different date or category
FILE_PATH = r'C:\Users\StavrosKV\Documents\Projects\ProjectsPY\Clean\Phones_skroutz_clean\clean_2025-06-23.csv'

data = pd.read_csv(FILE_PATH)

print(f"Dataset: {len(data)} rows, {len(data.columns)} columns")
print(data.info())


# ── 1. Price distribution ─────────────────────────────────────────────────────
plt.figure(figsize=(10, 6))
sns.histplot(data['Price_EUR'], bins=30, kde=True)
plt.title('Distribution of Phone Prices in EUR')
plt.xlabel('Price in EUR')
plt.ylabel('Frequency')
plt.tight_layout()
plt.show()


# ── 2. Product count by brand (top 12) ───────────────────────────────────────
plt.figure(figsize=(12, 6))
sns.countplot(data=data, x='Brand',
              order=data['Brand'].value_counts().head(12).index)
plt.title('Number of Phones by Brand (Top 12)')
plt.xlabel('Brand')
plt.ylabel('Number of Phones')
plt.xticks(rotation=45)
plt.tight_layout()
plt.show()


# ── 3. Average price by brand (top 12 by listing count) ──────────────────────
plt.figure(figsize=(12, 6))
sns.barplot(data=data, x='Brand', y='Price_EUR', estimator=np.mean,
            order=data['Brand'].value_counts().head(12).index)
plt.title('Average Price of Phones by Brand (Top 12)')
plt.xlabel('Brand')
plt.ylabel('Average Price in EUR')
plt.xticks(rotation=45)
plt.tight_layout()
plt.show()

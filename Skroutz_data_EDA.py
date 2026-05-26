"""
Skroutz_data_EDA.py
--------------------
Exploratory Data Analysis (EDA) for the cleaned phone price dataset.

Generates three visualisations from a single day's cleaned phone CSV:
  1. Price distribution — histogram + KDE to show the spread of phone prices
  2. Product count by brand — how many listings each brand has (top 12)
  3. Average price by brand — which brands sit at which price point (top 12)

Charts are saved as PNGs to the charts/ folder and also displayed interactively
when run in Jupyter / VS Code.  The folder is created automatically if missing.

To use with a different date or category, update FILE_PATH below.

Dependencies: pandas, numpy, matplotlib, seaborn
"""

import matplotlib
matplotlib.use("Agg")  # non-interactive backend: saves PNGs without opening windows
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import os

# ── Data source ───────────────────────────────────────────────────────────────
# Update this path to analyse a different date or category
FILE_PATH = r'C:\Users\StavrosKV\Documents\Projects\ProjectsPY\Clean\Phones_skroutz_clean\clean_2026-05-26.csv'

CHARTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "charts")
os.makedirs(CHARTS_DIR, exist_ok=True)

data = pd.read_csv(FILE_PATH)

print(f"Dataset: {len(data)} rows, {len(data.columns)} columns")
print(data.dtypes)


# ── 1. Price distribution ─────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(10, 6))
sns.histplot(data['Price_EUR'], bins=30, kde=True, ax=ax)
ax.set_title('Distribution of Phone Prices in EUR', fontsize=14)
ax.set_xlabel('Price in EUR')
ax.set_ylabel('Frequency')
fig.tight_layout()
fig.savefig(os.path.join(CHARTS_DIR, "price_distribution.png"), dpi=150)
plt.close(fig)


# ── 2. Product count by brand (top 12) ───────────────────────────────────────
fig, ax = plt.subplots(figsize=(12, 6))
sns.countplot(data=data, x='Brand',
              order=data['Brand'].value_counts().head(12).index, ax=ax)
ax.set_title('Number of Phones by Brand (Top 12)', fontsize=14)
ax.set_xlabel('Brand')
ax.set_ylabel('Number of Phones')
ax.tick_params(axis='x', rotation=45)
fig.tight_layout()
fig.savefig(os.path.join(CHARTS_DIR, "phones_by_brand.png"), dpi=150)
plt.close(fig)


# ── 3. Average price by brand (top 12 by listing count) ──────────────────────
fig, ax = plt.subplots(figsize=(12, 6))
sns.barplot(data=data, x='Brand', y='Price_EUR', estimator=np.mean,
            order=data['Brand'].value_counts().head(12).index, ax=ax)
ax.set_title('Average Price of Phones by Brand (Top 12)', fontsize=14)
ax.set_xlabel('Brand')
ax.set_ylabel('Average Price in EUR')
ax.tick_params(axis='x', rotation=45)
fig.tight_layout()
fig.savefig(os.path.join(CHARTS_DIR, "avg_price_by_brand.png"), dpi=150)
plt.close(fig)

print(f"\nCharts saved to: {CHARTS_DIR}")

"""
charts_from_db.py
-----------------
Generates multi-day price trend charts directly from the PostgreSQL database.

For each product category (phone, laptop, smartwatch, tablet), plots the price
history of the 6 most-reviewed products as a line chart — one chart per category.

Output: charts/price_trend_<category>.png  (150 dpi, non-blocking Agg backend)

Prerequisites:
  - analytics.sql must have been run in DBeaver to create vw_price_history and
    vw_latest_prices.
  - .env must contain DB_USER, DB_PASSWORD, and optionally DB_HOST/DB_PORT/DB_NAME.

Run:  python charts_from_db.py
"""

import matplotlib
matplotlib.use("Agg")

import os
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

# ── Database connection (same pattern as 4csvsTOsql.py) ───────────────────────
DB_USER     = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_HOST     = os.environ.get("DB_HOST", "localhost")
DB_PORT     = os.environ.get("DB_PORT", "5432")
DB_NAME     = os.environ.get("DB_NAME", "SkroutzPR")

engine = create_engine(
    f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

CHARTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "charts")
os.makedirs(CHARTS_DIR, exist_ok=True)

CATEGORIES = ["phone", "laptop", "smartwatch", "tablet"]
TOP_N = 6  # products per chart


def fetch_trend_data(conn, category):
    """Return a DataFrame of daily price history for the top-N most-reviewed products."""
    query = text("""
        SELECT
            h.product_id,
            COALESCE(p.brand || ' ' || p.model, p.product_name) AS label,
            h.date,
            h.price_eur
        FROM vw_price_history h
        JOIN products p ON p.id = h.product_id
        WHERE p.category = :cat
          AND h.product_id IN (
              SELECT product_id
              FROM vw_latest_prices
              WHERE category = :cat
                AND reviews IS NOT NULL
              ORDER BY reviews DESC
              LIMIT :n
          )
          AND h.price_eur IS NOT NULL
        ORDER BY h.product_id, h.date
    """)
    return pd.read_sql(query, conn, params={"cat": category, "n": TOP_N})


def plot_trend(df, category, output_path):
    fig, ax = plt.subplots(figsize=(12, 6))

    for product_id, group in df.groupby("product_id"):
        label = group["label"].iloc[0]
        # Truncate long labels so the legend stays readable
        if len(label) > 40:
            label = label[:37] + "..."
        ax.plot(group["date"], group["price_eur"], marker="", linewidth=1.8, label=label)

    ax.set_title(f"Price Trends — {category.capitalize()}s (Top {TOP_N} by Reviews)", fontsize=14)
    ax.set_xlabel("Date")
    ax.set_ylabel("Price (€)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate(rotation=30)
    ax.legend(fontsize=8, loc="upper left", framealpha=0.7)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"  Saved: {output_path}")


def main():
    with engine.connect() as conn:
        for category in CATEGORIES:
            print(f"Plotting {category}...")
            df = fetch_trend_data(conn, category)
            if df.empty:
                print(f"  No data found — skipping.")
                continue
            df["date"] = pd.to_datetime(df["date"])
            out = os.path.join(CHARTS_DIR, f"price_trend_{category}.png")
            plot_trend(df, category, out)

    print(f"\nAll trend charts saved to: {CHARTS_DIR}")


if __name__ == "__main__":
    main()

"""
charts_from_db.py
-----------------
Generates brand-level price trend charts from PostgreSQL.

For each category plots the average daily price for the top 5 brands (by product
count) over the last 180 days, using vw_brand_price_trend + vw_brand_summary.

Output: charts/price_trend_<category>.png

Run:  python charts_from_db.py
"""

import matplotlib
matplotlib.use("Agg")

import os
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DB_USER     = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_HOST     = os.environ.get("DB_HOST", "localhost")
DB_PORT     = os.environ.get("DB_PORT", "5432")
DB_NAME     = os.environ.get("DB_NAME", "SkroutzPR")

CHARTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "charts")
os.makedirs(CHARTS_DIR, exist_ok=True)

CATEGORIES    = ["phone", "laptop", "smartwatch", "tablet"]
TOP_N_BRANDS  = 5
LOOKBACK_DAYS = 180

PALETTE = ["#2196F3", "#F44336", "#4CAF50", "#FF9800", "#9C27B0",
           "#00BCD4", "#795548", "#607D8B"]


def fetch_brand_trend(conn, category):
    """Daily avg price for the top-N brands in the category over the last LOOKBACK_DAYS days."""
    query = text("""
        WITH top_brands AS (
            SELECT brand
            FROM vw_brand_summary
            WHERE category = :cat
              AND brand IS NOT NULL
            ORDER BY product_count DESC
            LIMIT :n
        )
        SELECT bt.brand, bt.date, bt.avg_price
        FROM vw_brand_price_trend bt
        JOIN top_brands tb ON tb.brand = bt.brand
        WHERE bt.category = :cat
          AND bt.date >= CURRENT_DATE - :days
        ORDER BY bt.brand, bt.date
    """)
    return pd.read_sql(query, conn, params={"cat": category, "n": TOP_N_BRANDS, "days": LOOKBACK_DAYS})


def plot_brand_trend(df, category, output_path):
    brands = sorted(df["brand"].unique())

    fig, ax = plt.subplots(figsize=(12, 6))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#F8F9FA")

    for i, brand in enumerate(brands):
        bdf = df[df["brand"] == brand].sort_values("date")
        color = PALETTE[i % len(PALETTE)]
        n = len(bdf)
        # Show a marker every ~10% of points so the line stays readable
        ax.plot(bdf["date"], bdf["avg_price"],
                color=color, linewidth=2.5, label=brand,
                marker="o", markersize=4,
                markevery=max(1, n // 10))
        # Price annotation at the final data point
        if n:
            last = bdf.iloc[-1]
            ax.annotate(
                f"€{last['avg_price']:,.0f}",
                xy=(last["date"], last["avg_price"]),
                xytext=(7, 0), textcoords="offset points",
                fontsize=8.5, color=color, va="center", fontweight="bold"
            )

    cat_label = category.capitalize() + ("s" if not category.endswith("s") else "")
    ax.set_title(
        f"Average Price by Brand — {cat_label}",
        fontsize=15, fontweight="bold", pad=14, color="#1A1A2E"
    )
    ax.set_xlabel("Date", fontsize=11, color="#555555", labelpad=8)
    ax.set_ylabel("Avg Price (€)", fontsize=11, color="#555555", labelpad=8)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate(rotation=30)

    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"€{x:,.0f}"))

    ax.grid(axis="y", linestyle="--", alpha=0.5, color="#CCCCCC")
    ax.grid(axis="x", linestyle=":",  alpha=0.3, color="#CCCCCC")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color("#DDDDDD")
    ax.spines["bottom"].set_color("#DDDDDD")
    ax.tick_params(colors="#666666")

    ax.legend(fontsize=10, framealpha=0.92, edgecolor="#DDDDDD",
              loc="best", ncol=1)

    fig.tight_layout(rect=[0, 0, 0.93, 1])   # leave right margin for annotations
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  Saved: {output_path}")


def main():
    engine = create_engine(
        f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )
    with engine.connect() as conn:
        for category in CATEGORIES:
            print(f"Plotting {category}...")
            df = fetch_brand_trend(conn, category)
            if df.empty:
                print(f"  No data — skipping.")
                continue
            df["date"] = pd.to_datetime(df["date"])
            out = os.path.join(CHARTS_DIR, f"price_trend_{category}.png")
            plot_brand_trend(df, category, out)

    print(f"\nAll charts saved to: {CHARTS_DIR}")


if __name__ == "__main__":
    main()

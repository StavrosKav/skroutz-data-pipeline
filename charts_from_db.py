"""
charts_from_db.py  —  Brand price-trend charts for the Skroutz dashboard.

One dark-themed PNG per category: top-N brands' smoothed daily average price
over the last LOOKBACK_DAYS days.  Direct end-labels (no legend box), shaded
±1-std band per brand, and a Δ% tag so you know the trend at a glance.

Output: charts/price_trend_<category>.png
Run:    python charts_from_db.py
"""

import matplotlib
matplotlib.use("Agg")

import datetime
import logging
import os
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
from dotenv import load_dotenv

import queries
from db import get_engine

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

CHARTS_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "charts")
os.makedirs(CHARTS_DIR, exist_ok=True)

CATEGORIES    = ["phone", "laptop", "smartwatch", "tablet"]
TOP_N_BRANDS  = 6
LOOKBACK_DAYS = 180
SMOOTH        = 7        # rolling-average window (days)

_CAT_LABEL = {"phone": "Phones", "laptop": "Laptops", "smartwatch": "Smartwatches", "tablet": "Tablets"}

# ── Dark palette matching the dashboard ───────────────────────────────────────
BG      = "#0f1117"
SURFACE = "#1a1d27"
BORDER  = "#2a2d3a"
TEXT    = "#e2e8f0"
MUTED   = "#8899aa"

PALETTE = [
    "#4f8ef7",   # blue
    "#22c55e",   # green
    "#f59e0b",   # amber
    "#a78bfa",   # purple
    "#38bdf8",   # sky
    "#fb923c",   # orange
]


def fetch_brand_trend(conn, category):
    return queries.brand_trend(conn, category, top_n=TOP_N_BRANDS, days=LOOKBACK_DAYS)


def plot_brand_trend(df, category, output_path):
    brands = sorted(df["brand"].unique())
    cat_label = _CAT_LABEL.get(category, category.capitalize() + "s")

    date_min  = df["date"].min()
    date_max  = df["date"].max()
    span_days = max((date_max - date_min).days, 1)

    fig, ax = plt.subplots(figsize=(12, 5))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(SURFACE)

    # ── Plot each brand ────────────────────────────────────────────────────────
    for i, brand in enumerate(brands):
        color = PALETTE[i % len(PALETTE)]
        bdf = df[df["brand"] == brand].sort_values("date").copy()
        bdf["smooth"] = bdf["avg_price"].rolling(SMOOTH, min_periods=1, center=True).mean()
        bdf["std"]    = bdf["avg_price"].rolling(SMOOTH, min_periods=1, center=True).std().fillna(0)

        dates  = bdf["date"].values
        raw    = bdf["avg_price"].values
        smooth = bdf["smooth"].values
        std    = bdf["std"].values

        first_p = smooth[0]
        last_p  = smooth[-1]
        pct     = (last_p - first_p) / first_p * 100 if first_p else 0
        arrow   = "▲" if pct >= 0.1 else ("▼" if pct <= -0.1 else "—")
        legend_label = f"{brand}   €{last_p:,.0f}  {arrow}{abs(pct):.0f}%"

        # ±1-std shaded band
        ax.fill_between(dates, smooth - std, smooth + std,
                        color=color, alpha=0.12, linewidth=0, zorder=2)
        # Faint raw line
        ax.plot(dates, raw, color=color, linewidth=0.8, alpha=0.22, zorder=3)
        # Smoothed line (carries the legend label)
        ax.plot(dates, smooth, color=color, linewidth=2.4,
                label=legend_label, zorder=4)
        # End dot
        ax.scatter([bdf["date"].iloc[-1]], [last_p],
                   color=color, s=40, zorder=5, linewidths=0)

    # ── Y-axis: frame tightly around data ─────────────────────────────────────
    y_data_min = df["avg_price"].min()
    y_data_max = df["avg_price"].max()
    y_pad = max((y_data_max - y_data_min) * 0.18, y_data_max * 0.04)
    ax.set_ylim(max(0, y_data_min - y_pad), y_data_max + y_pad)
    ax.set_xlim(date_min - datetime.timedelta(days=1), date_max + datetime.timedelta(days=1))

    # ── X-axis ticks ──────────────────────────────────────────────────────────
    if span_days <= 14:
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    elif span_days <= 60:
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=0))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    else:
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))

    fig.autofmt_xdate(rotation=25, ha="right")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"€{x:,.0f}"))

    # ── Grid & spines ─────────────────────────────────────────────────────────
    ax.yaxis.grid(color=BORDER, linewidth=0.6, alpha=0.8, zorder=0)
    ax.xaxis.grid(color=BORDER, linewidth=0.3, alpha=0.3, zorder=0)
    ax.set_axisbelow(True)

    for spine in ax.spines.values():
        spine.set_edgecolor(BORDER)
        spine.set_linewidth(0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    ax.tick_params(colors=MUTED, labelsize=9, length=3)
    for lbl in ax.get_xticklabels() + ax.get_yticklabels():
        lbl.set_color(MUTED)

    # ── Legend (dark-styled, inside chart) ────────────────────────────────────
    leg = ax.legend(
        loc="best",
        fontsize=8.5,
        framealpha=0.88,
        facecolor=BG,
        edgecolor=BORDER,
        handlelength=1.4,
        handleheight=0.9,
        borderpad=0.7,
        labelspacing=0.45,
    )
    for lbl in leg.get_texts():
        lbl.set_color(TEXT)

    # ── Title & subtitle ──────────────────────────────────────────────────────
    start_str = date_min.strftime("%b %d, %Y") if hasattr(date_min, "strftime") else str(date_min)[:10]
    end_str   = date_max.strftime("%b %d, %Y") if hasattr(date_max, "strftime") else str(date_max)[:10]
    ax.set_title(
        f"Brand Price Trends — {cat_label}",
        fontsize=13, fontweight="bold", color=TEXT,
        loc="left", pad=10,
    )
    ax.text(0, 1.01,
            f"Avg daily price  ·  {start_str} → {end_str}  ·  7-day smoothed",
            transform=ax.transAxes,
            fontsize=8, color=MUTED, va="bottom")

    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    logger.info(f"  Saved: {output_path}")


def main():
    with get_engine().connect() as conn:
        for category in CATEGORIES:
            logger.info(f"Plotting {category}...")
            try:
                df = fetch_brand_trend(conn, category)
                if df.empty:
                    logger.warning(f"  No data for {category} — skipping.")
                    continue
                df["date"] = pd.to_datetime(df["date"])
                out = os.path.join(CHARTS_DIR, f"price_trend_{category}.png")
                plot_brand_trend(df, category, out)
            except Exception as e:
                logger.warning(f"  Chart failed for {category}: {e}")

    logger.info(f"All charts saved to: {CHARTS_DIR}")


if __name__ == "__main__":
    main()

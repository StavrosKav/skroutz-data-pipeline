"""
generate_dashboard.py
---------------------
Generates a self-contained dashboard.html from the PostgreSQL database.

Embeds all data as JSON so the file works offline with no server.
Run standalone:  python generate_dashboard.py
Auto-called by run_pipeline.py after each successful scrape.

Output: dashboard/dashboard_<YYYY-MM-DD>.html
"""

import os
import sys
import json
import base64
import datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

BASE       = Path(__file__).parent
CHARTS_DIR = BASE / "charts"
OUT_DIR    = BASE / "dashboard"
OUT_DIR.mkdir(exist_ok=True)

# ── DB connection ──────────────────────────────────────────────────────────────
def get_engine():
    return create_engine(
        f"postgresql+psycopg2://{os.environ.get('DB_USER','postgres')}:"
        f"{os.environ.get('DB_PASSWORD','')}@"
        f"{os.environ.get('DB_HOST','localhost')}:"
        f"{os.environ.get('DB_PORT','5432')}/"
        f"{os.environ.get('DB_NAME','SkroutzPR')}"
    )

# ── Data queries ───────────────────────────────────────────────────────────────
def fetch_data(conn):
    today = datetime.date.today()

    # Summary stats
    total_products  = conn.execute(text("SELECT COUNT(*) FROM products")).scalar()
    total_snapshots = conn.execute(text("SELECT COUNT(*) FROM price_snapshots")).scalar()
    last_updated    = conn.execute(text("SELECT MAX(date) FROM price_snapshots")).scalar()

    # Per-category product counts and avg price today
    cat_rows = conn.execute(text("""
        SELECT p.category,
               COUNT(DISTINCT p.id)       AS product_count,
               ROUND(AVG(s.price_eur), 2) AS avg_price
        FROM products p
        JOIN price_snapshots s ON s.product_id = p.id
        WHERE s.date = (SELECT MAX(date) FROM price_snapshots)
        GROUP BY p.category
        ORDER BY p.category
    """)).fetchall()
    by_category = {r.category: {"count": r.product_count, "avg_price": float(r.avg_price or 0)}
                   for r in cat_rows}

    # Today's top 15 price drops
    drop_rows = conn.execute(text("""
        SELECT brand, model, category,
               ROUND(prev_price, 2) AS prev_price,
               ROUND(new_price,  2) AS new_price,
               ROUND(drop_eur,   2) AS drop_eur,
               ROUND(drop_pct,   2) AS drop_pct,
               skroutz_link
        FROM vw_biggest_drops
        WHERE drop_date = CURRENT_DATE
        ORDER BY drop_eur ASC
        LIMIT 15
    """)).fetchall()
    drops = [dict(r._mapping) for r in drop_rows]
    for d in drops:
        for k in ("prev_price","new_price","drop_eur","drop_pct"):
            d[k] = float(d[k]) if d[k] is not None else None

    # Brand summary (avg price per brand per category, top 8 brands per cat)
    brand_rows = conn.execute(text("""
        SELECT category, brand,
               product_count,
               ROUND(avg_price, 2) AS avg_price,
               ROUND(min_price, 2) AS min_price,
               ROUND(max_price, 2) AS max_price
        FROM vw_brand_summary
        WHERE brand IS NOT NULL
        ORDER BY category, product_count DESC
    """)).fetchall()
    # Keep top 8 per category for the chart
    brand_data = {}
    for r in brand_rows:
        cat = r.category
        if cat not in brand_data:
            brand_data[cat] = []
        if len(brand_data[cat]) < 8:
            brand_data[cat].append({
                "brand":         r.brand,
                "product_count": r.product_count,
                "avg_price":     float(r.avg_price or 0),
                "min_price":     float(r.min_price or 0),
                "max_price":     float(r.max_price or 0),
            })

    # All latest prices (for the search table)
    lp_rows = conn.execute(text("""
        SELECT id, category, brand, model, product_name,
               ROUND(price_eur, 2) AS price_eur,
               rating, reviews, skroutz_link
        FROM vw_latest_prices
        ORDER BY reviews DESC NULLS LAST
    """)).fetchall()
    products = []
    for r in lp_rows:
        products.append({
            "id":           r.id,
            "category":     r.category or "",
            "brand":        r.brand or "",
            "model":        r.model or "",
            "name":         r.product_name or "",
            "price":        float(r.price_eur) if r.price_eur else None,
            "rating":       float(r.rating) if r.rating else None,
            "reviews":      r.reviews,
            "url":          r.skroutz_link or "",
        })

    # Price history for top 50 most-reviewed products per category (200 total)
    history_rows = conn.execute(text("""
        WITH ranked AS (
            SELECT p.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY p.category
                       ORDER BY (
                           SELECT MAX(reviews) FROM price_snapshots
                           WHERE product_id = p.id
                       ) DESC NULLS LAST
                   ) AS rn
            FROM products p
        )
        SELECT s.product_id,
               s.date,
               ROUND(s.price_eur, 2) AS price_eur
        FROM price_snapshots s
        JOIN ranked r ON r.id = s.product_id
        WHERE r.rn <= 50
          AND s.price_eur IS NOT NULL
        ORDER BY s.product_id, s.date
    """)).fetchall()
    history = {}
    for r in history_rows:
        pid = r.product_id
        if pid not in history:
            history[pid] = []
        history[pid].append({"date": str(r.date), "price": float(r.price_eur)})

    return {
        "generated":       str(today),
        "total_products":  total_products,
        "total_snapshots": total_snapshots,
        "last_updated":    str(last_updated) if last_updated else str(today),
        "by_category":     by_category,
        "drops":           drops,
        "brand_data":      brand_data,
        "products":        products,
        "history":         history,
    }


# ── Chart image encoding ───────────────────────────────────────────────────────
def encode_chart(name):
    p = CHARTS_DIR / name
    if not p.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


# ── HTML template ──────────────────────────────────────────────────────────────
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Skroutz Price Tracker — {generated}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg:       #0f1117;
    --surface:  #1a1d27;
    --border:   #2a2d3a;
    --accent:   #4f8ef7;
    --accent2:  #22c55e;
    --warn:     #f59e0b;
    --text:     #e2e8f0;
    --muted:    #64748b;
    --drop:     #ef4444;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; font-size: 14px; }}
  a {{ color: var(--accent); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}

  /* ── Layout ── */
  .container {{ max-width: 1280px; margin: 0 auto; padding: 24px 16px; }}
  header {{ display: flex; align-items: center; justify-content: space-between;
            border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 24px; }}
  header h1 {{ font-size: 20px; font-weight: 700; color: var(--accent); }}
  header span {{ color: var(--muted); font-size: 12px; }}

  .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
                 gap: 12px; margin-bottom: 28px; }}
  .stat-card {{ background: var(--surface); border: 1px solid var(--border);
                border-radius: 10px; padding: 16px; }}
  .stat-card .val {{ font-size: 26px; font-weight: 700; color: var(--accent); }}
  .stat-card .lbl {{ font-size: 11px; color: var(--muted); margin-top: 4px; text-transform: uppercase; letter-spacing: .05em; }}

  .section {{ margin-bottom: 36px; }}
  .section-title {{ font-size: 15px; font-weight: 600; margin-bottom: 14px;
                    color: var(--text); border-left: 3px solid var(--accent);
                    padding-left: 10px; }}

  /* ── Tables ── */
  .table-wrap {{ overflow-x: auto; border-radius: 10px; border: 1px solid var(--border); }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ background: var(--surface); color: var(--muted); font-size: 11px; text-transform: uppercase;
        letter-spacing: .05em; padding: 10px 14px; text-align: left; border-bottom: 1px solid var(--border); }}
  td {{ padding: 9px 14px; border-bottom: 1px solid var(--border); vertical-align: middle; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #ffffff08; }}
  .cat-badge {{ display: inline-block; padding: 2px 8px; border-radius: 20px;
                font-size: 11px; font-weight: 600; text-transform: capitalize;
                background: #4f8ef722; color: var(--accent); }}
  .drop-eur {{ color: var(--drop); font-weight: 600; }}
  .drop-pct {{ color: var(--drop); }}
  .price {{ font-weight: 600; }}

  /* ── Charts row ── */
  .charts-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(560px, 1fr)); gap: 16px; }}
  .chart-card {{ background: var(--surface); border: 1px solid var(--border);
                 border-radius: 10px; overflow: hidden; }}
  .chart-card img {{ width: 100%; display: block; }}
  .chart-card .chart-lbl {{ padding: 10px 14px; font-size: 12px; color: var(--muted); text-align: center; }}

  /* ── Brand charts ── */
  .brand-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }}
  .brand-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }}
  .brand-card h4 {{ font-size: 12px; text-transform: uppercase; letter-spacing: .05em;
                    color: var(--muted); margin-bottom: 12px; }}
  .brand-card canvas {{ width: 100% !important; }}

  /* ── Search ── */
  .search-bar {{ display: flex; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; }}
  .search-bar input, .search-bar select {{
    background: var(--surface); border: 1px solid var(--border);
    color: var(--text); border-radius: 8px; padding: 8px 12px; font-size: 13px; }}
  .search-bar input {{ flex: 1; min-width: 200px; }}
  .search-bar input:focus, .search-bar select:focus {{ outline: none; border-color: var(--accent); }}
  #search-count {{ color: var(--muted); font-size: 12px; align-self: center; }}

  /* ── History modal ── */
  .modal-overlay {{ display: none; position: fixed; inset: 0; background: #000000bb;
                    z-index: 100; align-items: center; justify-content: center; }}
  .modal-overlay.open {{ display: flex; }}
  .modal {{ background: var(--surface); border: 1px solid var(--border); border-radius: 14px;
            padding: 24px; width: min(720px, 95vw); max-height: 80vh; overflow-y: auto; }}
  .modal-header {{ display: flex; justify-content: space-between; align-items: flex-start;
                   margin-bottom: 18px; }}
  .modal-header h3 {{ font-size: 15px; font-weight: 600; max-width: 85%; }}
  .modal-close {{ cursor: pointer; color: var(--muted); font-size: 20px; line-height: 1;
                  background: none; border: none; color: var(--text); }}
  .modal-close:hover {{ color: var(--drop); }}
  .no-history {{ color: var(--muted); font-size: 13px; padding: 20px 0; text-align: center; }}

  /* ── Responsive ── */
  @media (max-width: 600px) {{
    .charts-grid {{ grid-template-columns: 1fr; }}
    header {{ flex-direction: column; gap: 8px; align-items: flex-start; }}
  }}
</style>
</head>
<body>
<div class="container">

  <header>
    <h1>📊 Skroutz Price Tracker</h1>
    <span>Generated {generated} &nbsp;·&nbsp; {total_products:,} products &nbsp;·&nbsp; {total_snapshots:,} snapshots</span>
  </header>

  <!-- Summary cards -->
  <div class="stats-grid" id="stat-cards"></div>

  <!-- Today's drops -->
  <div class="section">
    <div class="section-title">🔻 Today's Biggest Price Drops</div>
    <div class="table-wrap">
      <table id="drops-table">
        <thead><tr>
          <th>Brand</th><th>Model</th><th>Category</th>
          <th>Was €</th><th>Now €</th><th>Drop €</th><th>Drop %</th>
        </tr></thead>
        <tbody id="drops-body"></tbody>
      </table>
    </div>
  </div>

  <!-- Price trend charts (static PNGs) -->
  <div class="section">
    <div class="section-title">📈 Price Trends (Top 6 by Reviews)</div>
    <div class="charts-grid" id="trend-charts"></div>
  </div>

  <!-- Avg price by brand -->
  <div class="section">
    <div class="section-title">🏷️ Avg Price by Brand</div>
    <div class="brand-grid" id="brand-charts"></div>
  </div>

  <!-- Product search -->
  <div class="section">
    <div class="section-title">🔍 Product Search</div>
    <div class="search-bar">
      <input id="q" type="text" placeholder="Search brand, model…" oninput="filterProducts()"/>
      <select id="cat-filter" onchange="filterProducts()">
        <option value="">All categories</option>
        <option value="phone">Phones</option>
        <option value="laptop">Laptops</option>
        <option value="smartwatch">Smartwatches</option>
        <option value="tablet">Tablets</option>
      </select>
      <span id="search-count"></span>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Brand</th><th>Model</th><th>Category</th>
          <th>Price €</th><th>Rating</th><th>Reviews</th><th>History</th>
        </tr></thead>
        <tbody id="products-body"></tbody>
      </table>
    </div>
  </div>

</div>

<!-- History modal -->
<div class="modal-overlay" id="modal" onclick="closeModal(event)">
  <div class="modal">
    <div class="modal-header">
      <h3 id="modal-title"></h3>
      <button class="modal-close" onclick="closeModal()">✕</button>
    </div>
    <canvas id="history-chart" height="120"></canvas>
    <div class="no-history" id="no-history" style="display:none">No price history available for this product.</div>
  </div>
</div>

<script>
// ── Embedded data ──────────────────────────────────────────────────────────────
const DATA = {data_json};
const HISTORY = {history_json};
const CHARTS = {charts_json};

// ── Colour palette ─────────────────────────────────────────────────────────────
const PALETTE = [
  '#4f8ef7','#22c55e','#f59e0b','#ef4444',
  '#a78bfa','#38bdf8','#fb923c','#e879f9',
];

// ── Stat cards ─────────────────────────────────────────────────────────────────
function buildStats() {{
  const el = document.getElementById('stat-cards');
  const cards = [
    {{ val: DATA.total_products.toLocaleString(), lbl: 'Products Tracked' }},
    {{ val: DATA.total_snapshots.toLocaleString(), lbl: 'Price Snapshots' }},
    {{ val: DATA.last_updated, lbl: 'Last Updated' }},
    {{ val: DATA.drops.length, lbl: "Drops Today" }},
  ];
  for (const [cat, info] of Object.entries(DATA.by_category)) {{
    cards.push({{ val: info.count.toLocaleString(), lbl: cat.charAt(0).toUpperCase() + cat.slice(1) + 's Today' }});
    cards.push({{ val: '€' + info.avg_price.toFixed(0), lbl: 'Avg ' + cat + ' price' }});
  }}
  el.innerHTML = cards.map(c =>
    `<div class="stat-card"><div class="val">${{c.val}}</div><div class="lbl">${{c.lbl}}</div></div>`
  ).join('');
}}

// ── Drops table ────────────────────────────────────────────────────────────────
function buildDrops() {{
  const tb = document.getElementById('drops-body');
  if (!DATA.drops.length) {{
    tb.innerHTML = '<tr><td colspan="7" style="color:var(--muted);text-align:center;padding:20px">No drops recorded today yet.</td></tr>';
    return;
  }}
  tb.innerHTML = DATA.drops.map(d => `
    <tr>
      <td>${{d.brand || ''}}</td>
      <td><a href="${{d.skroutz_link}}" target="_blank">${{(d.model || d.brand || '').slice(0,40)}}</a></td>
      <td><span class="cat-badge">${{d.category}}</span></td>
      <td class="price">€${{d.prev_price?.toFixed(2)}}</td>
      <td class="price">€${{d.new_price?.toFixed(2)}}</td>
      <td class="drop-eur">-€${{Math.abs(d.drop_eur).toFixed(2)}}</td>
      <td class="drop-pct">-${{Math.abs(d.drop_pct).toFixed(1)}}%</td>
    </tr>`).join('');
}}

// ── Trend chart images ─────────────────────────────────────────────────────────
function buildTrendCharts() {{
  const el = document.getElementById('trend-charts');
  const labels = {{
    'price_trend_phone':      'Phones',
    'price_trend_laptop':     'Laptops',
    'price_trend_smartwatch': 'Smartwatches',
    'price_trend_tablet':     'Tablets',
  }};
  el.innerHTML = Object.entries(CHARTS).map(([k, src]) =>
    src ? `<div class="chart-card">
             <img src="${{src}}" alt="${{labels[k] || k}}"/>
             <div class="chart-lbl">${{labels[k] || k}}</div>
           </div>` : ''
  ).join('');
}}

// ── Brand avg-price bar charts ─────────────────────────────────────────────────
let brandChartInstances = [];
function buildBrandCharts() {{
  const el = document.getElementById('brand-charts');
  el.innerHTML = '';
  brandChartInstances.forEach(c => c.destroy());
  brandChartInstances = [];
  for (const [cat, brands] of Object.entries(DATA.brand_data)) {{
    if (!brands.length) continue;
    const id = 'bc-' + cat;
    el.innerHTML += `<div class="brand-card"><h4>${{cat.charAt(0).toUpperCase()+cat.slice(1)+'s'}}</h4><canvas id="${{id}}"></canvas></div>`;
    requestAnimationFrame(() => {{
      const ctx = document.getElementById(id)?.getContext('2d');
      if (!ctx) return;
      const inst = new Chart(ctx, {{
        type: 'bar',
        data: {{
          labels: brands.map(b => b.brand),
          datasets: [{{ label: 'Avg €', data: brands.map(b => b.avg_price),
                        backgroundColor: PALETTE, borderRadius: 4 }}]
        }},
        options: {{
          responsive: true,
          plugins: {{ legend: {{ display: false }},
                      tooltip: {{ callbacks: {{ label: c => ' €' + c.raw.toFixed(0) }} }} }},
          scales: {{
            x: {{ ticks: {{ color: '#64748b', font: {{ size: 10 }} }}, grid: {{ color: '#2a2d3a' }} }},
            y: {{ ticks: {{ color: '#64748b', callback: v => '€'+v }}, grid: {{ color: '#2a2d3a' }} }},
          }}
        }}
      }});
      brandChartInstances.push(inst);
    }});
  }}
}}

// ── Product search ─────────────────────────────────────────────────────────────
let visibleProducts = DATA.products.slice(0, 100);
function filterProducts() {{
  const q   = document.getElementById('q').value.toLowerCase().trim();
  const cat = document.getElementById('cat-filter').value;
  visibleProducts = DATA.products.filter(p => {{
    if (cat && p.category !== cat) return false;
    if (!q) return true;
    return (p.brand + ' ' + p.model + ' ' + p.name).toLowerCase().includes(q);
  }}).slice(0, 200);
  renderProductTable();
}}
function renderProductTable() {{
  const tb = document.getElementById('products-body');
  document.getElementById('search-count').textContent = visibleProducts.length + ' results';
  if (!visibleProducts.length) {{
    tb.innerHTML = '<tr><td colspan="7" style="color:var(--muted);text-align:center;padding:20px">No products found.</td></tr>';
    return;
  }}
  tb.innerHTML = visibleProducts.map(p => `
    <tr>
      <td>${{p.brand}}</td>
      <td><a href="${{p.url}}" target="_blank">${{(p.model || p.name).slice(0,45)}}</a></td>
      <td><span class="cat-badge">${{p.category}}</span></td>
      <td class="price">${{p.price != null ? '€' + p.price.toFixed(2) : '—'}}</td>
      <td>${{p.rating != null ? '⭐ ' + p.rating.toFixed(1) : '—'}}</td>
      <td>${{p.reviews != null ? p.reviews.toLocaleString() : '—'}}</td>
      <td><button onclick="showHistory(${{p.id}}, '${{(p.brand+' '+p.model).replace(/'/g,"\\'")}}')"
                  style="background:var(--accent);color:#fff;border:none;border-radius:6px;
                         padding:3px 10px;cursor:pointer;font-size:12px">Chart</button></td>
    </tr>`).join('');
}}

// ── History modal ──────────────────────────────────────────────────────────────
let historyChartInst = null;
function showHistory(id, name) {{
  document.getElementById('modal-title').textContent = name;
  document.getElementById('no-history').style.display = 'none';
  document.getElementById('history-chart').style.display = 'block';
  const pts = HISTORY[id] || [];
  if (!pts.length) {{
    document.getElementById('no-history').style.display = 'block';
    document.getElementById('history-chart').style.display = 'none';
    document.getElementById('modal').classList.add('open');
    return;
  }}
  if (historyChartInst) historyChartInst.destroy();
  const ctx = document.getElementById('history-chart').getContext('2d');
  historyChartInst = new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: pts.map(p => p.date),
      datasets: [{{
        label: 'Price €',
        data:  pts.map(p => p.price),
        borderColor: '#4f8ef7',
        backgroundColor: '#4f8ef722',
        fill: true,
        tension: 0.3,
        pointRadius: pts.length > 30 ? 0 : 3,
      }}]
    }},
    options: {{
      responsive: true,
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{ callbacks: {{ label: c => ' €' + c.raw.toFixed(2) }} }},
      }},
      scales: {{
        x: {{ ticks: {{ color: '#64748b', maxTicksLimit: 8, font: {{ size: 11 }} }},
               grid: {{ color: '#2a2d3a' }} }},
        y: {{ ticks: {{ color: '#64748b', callback: v => '€' + v }},
               grid: {{ color: '#2a2d3a' }} }},
      }}
    }}
  }});
  document.getElementById('modal').classList.add('open');
}}
function closeModal(e) {{
  if (!e || e.target === document.getElementById('modal') || e.currentTarget === document.querySelector('.modal-close'))
    document.getElementById('modal').classList.remove('open');
}}
document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeModal(); }});

// ── Init ───────────────────────────────────────────────────────────────────────
buildStats();
buildDrops();
buildTrendCharts();
buildBrandCharts();
filterProducts();
</script>
</body>
</html>"""


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print("Generating dashboard…")
    try:
        engine = get_engine()
        with engine.connect() as conn:
            data = fetch_data(conn)
    except Exception as e:
        print(f"Dashboard: DB connection failed — {e}", file=sys.stderr)
        sys.exit(1)

    charts_json = {
        "price_trend_phone":      encode_chart("price_trend_phone.png"),
        "price_trend_laptop":     encode_chart("price_trend_laptop.png"),
        "price_trend_smartwatch": encode_chart("price_trend_smartwatch.png"),
        "price_trend_tablet":     encode_chart("price_trend_tablet.png"),
    }

    # Separate history from main data to keep JSON readable
    history = data.pop("history")

    html = HTML_TEMPLATE.format(
        generated       = data["generated"],
        total_products  = data["total_products"],
        total_snapshots = data["total_snapshots"],
        data_json       = json.dumps(data,    ensure_ascii=False, separators=(",", ":")),
        history_json    = json.dumps(history, ensure_ascii=False, separators=(",", ":")),
        charts_json     = json.dumps(charts_json, ensure_ascii=False, separators=(",", ":")),
    )

    out_path = OUT_DIR / f"dashboard_{data['generated']}.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"Dashboard saved: {out_path}")

    # Also write a fixed-name copy for easy access
    latest = OUT_DIR / "dashboard_latest.html"
    latest.write_text(html, encoding="utf-8")
    print(f"Latest copy:     {latest}")


if __name__ == "__main__":
    main()

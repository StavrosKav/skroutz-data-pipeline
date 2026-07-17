"""
telegram_nim.py
---------------
Optional NIM-powered commands for the Telegram bot.
Provides LLM-enhanced features:
- /analyze <category>      — LLM market analysis for a category
- /summarize               — LLM daily summary with insights
- /chat <message>          — Free-form chat with the price tracker AI

Uses nim_client.py with dynamic model routing.
"""

import asyncio
import logging

from nim_client import NIMClient, TaskType
from nim_routing import route_by_complexity

logger = logging.getLogger(__name__)

_NOT_CONFIGURED = "🤖 NIM not configured. Set NIM_API_KEY in .env"


async def _nim_call(
    messages: list[dict],
    task: TaskType = TaskType.CHAT_DEFAULT,
    temperature: float = 0.3,
) -> str:
    """One NIM chat call with a fresh client; returns the reply text.
    NIMClient() raises ValueError when NIM_API_KEY is unset."""
    try:
        async with NIMClient() as client:
            resp = await client.chat(messages, task=task, temperature=temperature)
            return resp.content
    except ValueError:
        return _NOT_CONFIGURED
    except Exception as e:
        logger.error(f"NIM call failed: {e}")
        return f"❌ NIM error: {e}"


async def nim_explain_drop(product_name: str, drop_pct: float, brand: str, category: str) -> str:
    """
    Generate an explanation for why a price might have dropped.
    Routed to the reasoning-heavy model.
    """
    prompt = f"""
Analyze this price drop for a Greek e-commerce price tracker:

Product: {brand} {product_name}
Category: {category}
Price drop: {drop_pct:.1f}%

Possible reasons for price drops in this market:
1. New model release / generation refresh
2. Seasonal sales (Black Friday, summer sales, back-to-school)
3. Competitor price matching
4. Inventory clearance
5. Promotional campaign
6. Manufacturer rebate/incentive

Provide 2-3 most likely reasons specific to this {category} product.
Be concise, 3-4 sentences max. Include a confidence level (Low/Medium/High).
"""

    return await _nim_call([{"role": "user", "content": prompt}], TaskType.REASONING_HEAVY, 0.3)


async def nim_predict_price(product_name: str, brand: str, category: str, current_price: float, atl: float, ath: float) -> str:
    """
    Predict price trend with reasoning.
    Routed to the reasoning-heavy model.
    """
    atl_pct = ((current_price - atl) / atl * 100) if atl else 0

    prompt = f"""
Price prediction analysis for Greek electronics market:

Product: {brand} {product_name}
Category: {category}
Current price: €{current_price:.0f}
All-time low: €{atl:.0f} ({atl_pct:+.1f}% above current)
All-time high: €{ath:.0f}

Context:
- Greek e-commerce (skroutz.gr) tracks daily prices
- Typical price cycles: new model launch → gradual decline → clearance → EOL
- {category}s typically drop 5-15% in first 6 months, then stabilize
- Major sales events: Black Friday (Nov), Summer sales (Aug), January sales

Predict the 30-day price trajectory:
1. Direction: RISING / STABLE / FALLING
2. Expected change: ±% range
3. Key factors (2-3 bullets)
4. Confidence: Low/Medium/High
5. Recommended action: BUY NOW / WAIT / MONITOR

Format as structured response, concise.
"""

    return await _nim_call([{"role": "user", "content": prompt}], TaskType.REASONING_HEAVY, 0.3)


async def nim_compare_products(
    prod1_name: str, prod1_brand: str, prod1_price: float, prod1_specs: str,
    prod2_name: str, prod2_brand: str, prod2_price: float, prod2_specs: str
) -> str:
    """Compare two products with LLM reasoning."""
    prompt = f"""
Compare these two products for a Greek consumer:

Product 1: {prod1_brand} {prod1_name} — €{prod1_price:.0f}
Specs: {prod1_specs or 'Not available'}

Product 2: {prod2_brand} {prod2_name} — €{prod2_price:.0f}
Specs: {prod2_specs or 'Not available'}

Provide:
1. Value winner: which offers better €/performance
2. Use case fit: who should buy which
3. Key trade-offs (3 bullets)
4. Final recommendation

Concise, consumer-focused.
"""

    return await _nim_call([{"role": "user", "content": prompt}], TaskType.REASONING_HEAVY, 0.3)


async def nim_analyze_category(category: str, days: int = 30) -> str:
    """Market analysis for a category."""
    prompt = f"""
Analyze the Greek {category} market over the last {days} days.

Context: skroutz.gr aggregates 500+ Greek shops. Typical categories:
- phones: 1000+ models, 45+ brands, high competition
- laptops: 2000+ models, 25 brands, refresh cycles
- smartwatches: 2000+ models, many generic brands
- tablets: 500+ models, dominated by Samsung/Apple/Lenovo

Provide:
1. Market trend: PRICES RISING / STABLE / FALLING
2. Top 3 brands by volume & value
3. Discount activity: which brands discount most often
4. Best value segment right now (budget/mid/premium)
5. Buying advice for next 2 weeks

Format as market brief for a deal-hunting consumer.
"""

    return await _nim_call([{"role": "user", "content": prompt}], TaskType.REASONING_HEAVY, 0.4)


async def nim_daily_summary(stats: dict) -> str:
    """Generate AI daily summary with insights."""
    prompt = f"""
Daily Skroutz Price Tracker Summary:

Database: {stats.get('total_products', 0):,} products, {stats.get('total_snapshots', 0):,} snapshots
Today: {stats.get('today_snapshots', 0):,} new prices, {stats.get('today_drops', 0)} significant drops
Categories: {stats.get('categories', {})}

Top drops today:
{stats.get('top_drops', 'None')}

Write a 3-4 sentence executive summary for a deal-hunting newsletter.
Highlight 1 actionable insight.
Tone: knowledgeable, concise, slightly casual.
"""

    return await _nim_call([{"role": "user", "content": prompt}], TaskType.CHAT_DEFAULT, 0.5)


async def nim_chat(user_message: str, context: str = "") -> str:
    """Free-form chat with the price tracker AI."""
    system_prompt = """You are the Skroutz Price Tracker AI.
You have access to: live Greek electronics prices, historical trends, all-time lows/highs,
brand discount patterns, and market analysis.
Help users find deals, understand price movements, and make buying decisions.
Be concise, practical, and data-driven. Use € prices."""

    messages = [{"role": "system", "content": system_prompt}]
    if context:
        messages.append({"role": "assistant", "content": context})
    messages.append({"role": "user", "content": user_message})

    return await _nim_call(messages, route_by_complexity(user_message), 0.6)


# Synchronous wrappers for bot integration
def _run_async(coro):
    """Run async coroutine in sync context."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        # Already in async context — schedule and wait
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    else:
        return loop.run_until_complete(coro)


def nim_analyze_category_sync(*args, **kwargs) -> str:
    return _run_async(nim_analyze_category(*args, **kwargs))


def nim_daily_summary_sync(*args, **kwargs) -> str:
    return _run_async(nim_daily_summary(*args, **kwargs))


def nim_chat_sync(*args, **kwargs) -> str:
    return _run_async(nim_chat(*args, **kwargs))


# Bot command handlers (to be integrated into telegram_bot.py)
def cmd_analyze(args: str) -> str:
    """Usage: /analyze <category>"""
    cat = args.strip().lower()
    if cat not in ["phone", "laptop", "smartwatch", "tablet", "phones", "laptops", "smartwatches", "tablets"]:
        return "Usage: /analyze <phones|laptops|smartwatches|tablets>"
    return nim_analyze_category_sync(cat.rstrip('s'))


def cmd_summarize() -> str:
    """Usage: /summarize — AI daily summary"""
    # Would need to gather stats from DB
    from db import get_engine
    from sqlalchemy import text
    try:
        engine = get_engine()
        with engine.connect() as conn:
            total_p = conn.execute(text("SELECT COUNT(*) FROM products")).scalar()
            total_s = conn.execute(text("SELECT COUNT(*) FROM price_snapshots")).scalar()
            today_s = conn.execute(text("SELECT COUNT(*) FROM price_snapshots WHERE date = CURRENT_DATE")).scalar()
            today_d = conn.execute(text("SELECT COUNT(*) FROM vw_biggest_drops WHERE drop_date = CURRENT_DATE")).scalar()
            drops = conn.execute(text(
                "SELECT brand, model, drop_pct FROM vw_biggest_drops "
                "WHERE drop_date = CURRENT_DATE ORDER BY ABS(drop_eur) DESC LIMIT 3"
            )).fetchall()
    except Exception as e:
        return f"❌ DB error: {e}"

    drop_lines = [f"  {d.brand} {d.model}: {float(d.drop_pct):.1f}%" for d in drops]
    stats = {
        "total_products": total_p,
        "total_snapshots": total_s,
        "today_snapshots": today_s,
        "today_drops": today_d,
        "top_drops": "\n".join(drop_lines) if drop_lines else "None",
    }
    return nim_daily_summary_sync(stats)


def cmd_chat(args: str) -> str:
    """Usage: /chat <your question>"""
    if not args.strip():
        return "Usage: <code>/chat <your question></code>\nExamples:\n  /chat Why did iPhone 15 drop this week?\n  /chat Should I buy a gaming laptop now or wait?\n  /chat Compare Pixel 8 vs Samsung S24 for camera"
    return nim_chat_sync(args.strip())


# Integration helper
def register_nim_commands(dispatch_map: dict, args: str = "") -> None:
    """Register NIM commands into the bot's dispatch map.
    Handlers are zero-arg closures over `args`, matching the shape of
    telegram_bot's dispatch_map entries."""
    dispatch_map.update({
        "/analyze":   lambda: cmd_analyze(args),
        "/summarize": lambda: cmd_summarize(),
        "/chat":      lambda: cmd_chat(args),
    })
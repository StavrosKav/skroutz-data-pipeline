"""
telegram_nim.py
---------------
Optional NIM-powered commands for the Telegram bot.
Provides LLM-enhanced features:
- /analyze <category>      — LLM market analysis for a category
- /summarize               — LLM daily summary with insights
- /chat <message>          — Free-form chat with the price tracker AI
- /ask <question>          — Answer from the database via guarded text-to-SQL

Uses nim_client.py with dynamic model routing.
"""

import asyncio
import logging

from alerts.nim_client import NIMClient, TaskType
from alerts.nim_routing import route_by_complexity

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


_CAT_ALIASES = {
    "phone": "phone", "phones": "phone",
    "laptop": "laptop", "laptops": "laptop",
    "smartwatch": "smartwatch", "smartwatches": "smartwatch", "watches": "smartwatch",
    "tablet": "tablet", "tablets": "tablet",
}


# Bot command handlers (to be integrated into telegram_bot.py)
def cmd_analyze(args: str) -> str:
    """Usage: /analyze <category>"""
    resolved = _CAT_ALIASES.get(args.strip().lower())
    if not resolved:
        return "Usage: /analyze <phones|laptops|smartwatches|tablets>"
    return nim_analyze_category_sync(resolved)


def cmd_summarize() -> str:
    """Usage: /summarize — AI daily summary"""
    from core.db import get_engine
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
        logger.warning(f"DB error: {e}")
        return "🔌 Backend unavailable right now — please try again in a bit."

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


def cmd_ask(args: str) -> str:
    """Usage: /ask <question> — text-to-SQL answer grounded in the database."""
    if not args.strip():
        return ("Usage: <code>/ask &lt;ερώτηση&gt;</code>\nΠαραδείγματα:\n"
                "  /ask ποιο είναι το φθηνότερο iPhone 15;\n"
                "  /ask μεγαλύτερη πτώση τιμής αυτή την εβδομάδα στα laptop")
    from agents.ask.agent import format_telegram
    from agents.ask.runtime import get_agent
    try:
        agent = get_agent()
    except ValueError:
        return _NOT_CONFIGURED
    except Exception as e:
        logger.warning(f"/ask init failed: {type(e).__name__}")
        return "🔌 Backend unavailable right now — please try again in a bit."
    return format_telegram(agent.ask(args))


# Integration helper
def register_nim_commands(dispatch_map: dict, args: str = "") -> None:
    """Register NIM commands into the bot's dispatch map.
    Handlers are zero-arg closures over `args`, matching the shape of
    telegram_bot's dispatch_map entries."""
    dispatch_map.update({
        "/analyze":   lambda: cmd_analyze(args),
        "/summarize": lambda: cmd_summarize(),
        "/chat":      lambda: cmd_chat(args),
        "/ask":       lambda: cmd_ask(args),
    })

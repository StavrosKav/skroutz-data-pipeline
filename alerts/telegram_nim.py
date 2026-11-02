"""
telegram_nim.py
---------------
Optional NIM-powered commands for the Telegram bot. Every answer is grounded
in the database — the LLM never answers from its own knowledge:
- /ask <question>          — guarded text-to-SQL over the DB (agents/ask)
- /chat <question>         — alias of /ask
- /analyze <category>      — fixed read-only DB queries -> LLM summarizes only those facts
"""

import logging

logger = logging.getLogger(__name__)

_NOT_CONFIGURED = "🤖 NIM not configured. Set NIM_API_KEY in .env"
_UNAVAILABLE = "🔌 Backend unavailable right now — please try again in a bit."

_CAT_ALIASES = {
    "phone": "phone", "phones": "phone",
    "laptop": "laptop", "laptops": "laptop",
    "smartwatch": "smartwatch", "smartwatches": "smartwatch", "watches": "smartwatch",
    "tablet": "tablet", "tablets": "tablet",
}


def _analyze_backend():
    """(executor, llm) for /analyze. Separate so tests can patch it."""
    from agents.ask.runtime import make_nim_llm, make_pg_executor
    from alerts.nim_client import TaskType
    executor = make_pg_executor()
    try:
        llm = make_nim_llm(task=TaskType.CHAT_DEFAULT, temperature=0.2)
    except ValueError:
        llm = None
    return executor, llm


def cmd_analyze(args: str) -> str:
    """Usage: /analyze <category> — DB facts + LLM summary of only those facts."""
    resolved = _CAT_ALIASES.get(args.strip().lower())
    if not resolved:
        return "Usage: /analyze <phones|laptops|smartwatches|tablets>"
    from agents.ask.analyze import analyze
    try:
        executor, llm = _analyze_backend()
        return analyze(executor, llm, resolved)
    except Exception as e:  # noqa: BLE001 — LLM/agent backend can fail in many ways; the bot replies 'unavailable'
        logger.warning(f"/analyze failed: {type(e).__name__}: {e}")
        return _UNAVAILABLE


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
    except Exception as e:  # noqa: BLE001 — LLM/agent backend can fail in many ways; the bot replies 'unavailable'
        logger.warning(f"/ask init failed: {type(e).__name__}")
        return _UNAVAILABLE
    return format_telegram(agent.ask(args))


def cmd_chat(args: str) -> str:
    """Usage: /chat <question> — alias of /ask (grounded in the DB)."""
    return cmd_ask(args)


# Integration helper
def register_nim_commands(dispatch_map: dict, args: str = "") -> None:
    """Register NIM commands into the bot's dispatch map.
    Handlers are zero-arg closures over `args`, matching the shape of
    telegram_bot's dispatch_map entries."""
    dispatch_map.update({
        "/analyze":   lambda: cmd_analyze(args),
        "/chat":      lambda: cmd_chat(args),
        "/ask":       lambda: cmd_ask(args),
    })

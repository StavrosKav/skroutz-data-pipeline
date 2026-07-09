"""
NIM Model Routing Configuration
Maps Telegram bot commands and pipeline tasks to optimal NIM models.
"""

from nim_client import TaskType, NIMModel

# =============================================================================
# TELEGRAM BOT COMMAND ROUTING
# =============================================================================
# Format: command_name -> TaskType (auto-resolves to best model)

BOT_COMMAND_ROUTING: dict[str, TaskType] = {
    # Fast, simple commands → Llama-3.1-8B (cheap, fast, 500ms)
    "status": TaskType.FAST_SIMPLE,
    "drops": TaskType.FAST_SIMPLE,
    "watchlist": TaskType.FAST_SIMPLE,
    "stats": TaskType.FAST_SIMPLE,
    "help": TaskType.FAST_SIMPLE,
    "cancel": TaskType.FAST_SIMPLE,

    # Commands needing DB queries + formatting → Llama-3.1-70B
    "find": TaskType.CHAT_DEFAULT,
    "history": TaskType.CHAT_DEFAULT,
    "restock": TaskType.CHAT_DEFAULT,

    # Complex reasoning → Nemotron-3-Ultra
    "add": TaskType.REASONING_HEAVY,        # URL → product parsing + price extraction
    "best": TaskType.REASONING_HEAVY,       # Near-ATL analysis + recommendation

    # Code-related (if bot exposes admin code commands)
    "debug": TaskType.CODE_REVIEW,
}

# =============================================================================
# PIPELINE TASK ROUTING
# =============================================================================
# For use in run_pipeline.py, charts, notifications, etc.

PIPELINE_TASK_ROUTING: dict[str, TaskType] = {
    # Scraper maintenance
    "fix_selector": TaskType.CODE_GENERATION,
    "analyze_scraper_failure": TaskType.REASONING_HEAVY,

    # Data quality
    "normalize_brand": TaskType.CODE_GENERATION,
    "detect_price_anomaly": TaskType.REASONING_HEAVY,
    "deduplicate_products": TaskType.REASONING_HEAVY,

    # Analytics
    "generate_insights": TaskType.REASONING_HEAVY,
    "explain_price_drop": TaskType.REASONING_HEAVY,
    "predict_future_price": TaskType.REASONING_HEAVY,

    # Notifications
    "format_drop_digest": TaskType.FAST_SIMPLE,
    "format_watchlist_alert": TaskType.FAST_SIMPLE,
    "format_disappeared_alert": TaskType.FAST_SIMPLE,

    # Dashboard/Charts
    "generate_chart_config": TaskType.CODE_GENERATION,
}

# =============================================================================
# ADVANCED: DYNAMIC ROUTING BASED ON INPUT
# =============================================================================

def route_by_content_length(prompt: str, short_threshold: int = 500) -> TaskType:
    """Route short prompts to fast model, long to reasoning model."""
    return TaskType.FAST_SIMPLE if len(prompt) < short_threshold else TaskType.REASONING_HEAVY


def route_by_language(text: str) -> TaskType:
    """Route Greek/English multilingual content to Mistral Large."""
    # Simple heuristic: detect Greek characters
    greek_chars = sum(1 for c in text if 'Ͱ' <= c <= 'Ͽ')
    if greek_chars > len(text) * 0.3:
        return TaskType.MULTILINGUAL
    return TaskType.CHAT_DEFAULT


def route_by_complexity(prompt: str) -> TaskType:
    """
    Heuristic: route to reasoning model if prompt contains:
    - Multiple steps/instructions
    - "analyze", "explain why", "compare", "recommend"
    - Code-like patterns (SQL, Python, selectors)
    """
    reasoning_triggers = [
        "analyze", "explain", "why", "reason", "compare", "recommend",
        "tradeoff", "pros and cons", "strategy", "optimize",
        "select", "query", "sql", "python", "css", "xpath",
    ]
    prompt_lower = prompt.lower()
    if any(t in prompt_lower for t in reasoning_triggers):
        return TaskType.REASONING_HEAVY
    return TaskType.CHAT_DEFAULT


# =============================================================================
# CONVENIENCE: GET MODEL FOR COMMAND
# =============================================================================

def get_model_for_command(command: str) -> TaskType:
    """Get TaskType for a bot command, with fallback."""
    return BOT_COMMAND_ROUTING.get(command.lower(), TaskType.CHAT_DEFAULT)


def get_model_for_task(task_name: str) -> TaskType:
    """Get TaskType for a pipeline task, with fallback."""
    return PIPELINE_TASK_ROUTING.get(task_name, TaskType.CHAT_DEFAULT)
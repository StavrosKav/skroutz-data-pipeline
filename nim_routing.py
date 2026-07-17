"""
NIM Model Routing Configuration
Maps prompt content to the optimal NIM model.
"""

from nim_client import TaskType


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

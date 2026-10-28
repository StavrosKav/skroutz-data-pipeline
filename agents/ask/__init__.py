"""Text-to-SQL question answering over the Skroutz price database.

Pure core (no DB, no network): the LLM and the SQL executor are injected,
so the whole flow is unit-testable with fakes. See docs/ASK_AGENT.md.
"""

from agents.ask.agent import AskAgent, AskResult
from agents.ask.guard import SQLGuardError, validate_sql

__all__ = ["AskAgent", "AskResult", "SQLGuardError", "validate_sql"]

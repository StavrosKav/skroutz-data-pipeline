"""
Base agent class for all agents in the system.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional
import logging

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Base class for all agents."""

    def __init__(self, name: str, config: Optional[dict] = None):
        self.name = name
        self.config = config or {}
        self.logger = logging.getLogger(f"agent.{name}")

    @abstractmethod
    def process(self, input_data: Any) -> Any:
        """
        Process input data and return output.
        Must be implemented by subclasses.
        """
        pass

    def __str__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}')"

    def __repr__(self) -> str:
        return self.__str__()

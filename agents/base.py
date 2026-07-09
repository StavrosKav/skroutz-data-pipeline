"""
Base agent class for all agents in the system.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
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

    def validate_input(self, input_data: Any) -> bool:
        """
        Validate input data. Override if needed.
        Return True if valid, False otherwise.
        """
        return input_data is not None

    def health_check(self) -> Dict[str, any]:
        """
        Return health status of the agent.
        Override if needed.
        """
        return {
            "agent": self.name,
            "status": "healthy",
            "config": self.config,
        }

    def __str__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}')"

    def __repr__(self) -> str:
        return self.__str__()
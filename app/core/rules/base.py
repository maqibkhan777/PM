from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from app.core.events.base import BaseEvent

if TYPE_CHECKING:
    from app.core.actions.base import BaseAction


class BaseRule(ABC):
    """Abstract Base Class for all PM workflow rules."""

    def __init__(
        self,
        name: str,
        description: str,
        enabled: bool = True,
        configuration: Optional[Dict[str, Any]] = None
    ):
        self.name = name
        self.description = description
        self.enabled = enabled
        self.configuration = configuration or {}

    @abstractmethod
    def evaluate(
        self,
        event: BaseEvent,
        context: Optional[Dict[str, Any]] = None
    ) -> List[BaseAction]:
        """Evaluate an incoming event against this rule.

        Returns:
            List of BaseAction instances to be passed to the Action Engine.
        """
        pass

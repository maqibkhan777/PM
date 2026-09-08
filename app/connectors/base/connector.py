"""Base connector abstract interface and capability definitions."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set
from app.core.models.enums import Capability, SecurityLevel, CAPABILITY_SECURITY_MAP
from app.core.models.domain import HealthStatus
from app.core.events.base import BaseEvent


class BaseConnector(ABC):
    """Abstract Base Class for all system connectors."""

    def __init__(self, name: str, system_type: str):
        self.name = name
        self.system_type = system_type
        self._is_connected = False

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @abstractmethod
    async def connect(self) -> bool:
        """Initialize connection/session with external service."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully close connection/session."""
        pass

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """Perform health check on external service."""
        pass

    @abstractmethod
    def get_capabilities(self) -> Set[Capability]:
        """Return the set of machine-readable capabilities supported by this connector."""
        pass

    def supports_capability(self, capability: Capability) -> bool:
        """Check if a specific capability is supported by this connector."""
        return capability in self.get_capabilities()

    def get_security_level_for_capability(self, capability: Capability) -> SecurityLevel:
        """Determine security classification (READ, WRITE, DESTRUCTIVE) for a capability."""
        return CAPABILITY_SECURITY_MAP.get(capability, SecurityLevel.WRITE)

    async def handle_incoming_event(
        self,
        raw_payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None
    ) -> Optional[BaseEvent]:
        """Normalize an incoming external webhook payload into an internal BaseEvent."""
        return None

    @abstractmethod
    async def execute_action(self, action: Any) -> Any:
        """Execute a domain action through the connector."""
        pass

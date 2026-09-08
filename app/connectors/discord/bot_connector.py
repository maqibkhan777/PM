"""Discord Bot Connector skeleton for future interactive slash commands and PM control."""

from typing import Any, Dict, Optional, Set
from app.connectors.base.connector import BaseConnector
from app.core.models.enums import Capability
from app.core.models.domain import HealthStatus
from app.utils.logger import logger


class DiscordBotConnector(BaseConnector):
    """Connector for future interactive Discord Bot capabilities (slash commands, approvals).

    Note: V0.1 provides the architectural foundation. Full Discord Gateway / interactions
    can be plugged into this class in future releases without modifying the core.
    """

    def __init__(self, bot_token: Optional[str] = None):
        super().__init__(name="discord_bot", system_type="interactive_chat")
        self.bot_token = bot_token

    def get_capabilities(self) -> Set[Capability]:
        return {
            Capability.SEND_DM,
            Capability.SEND_CHANNEL_MESSAGE,
        }

    async def connect(self) -> bool:
        logger.info("DiscordBotConnector initialized (V0.1 placeholder mode).")
        self._is_connected = False
        return False

    async def disconnect(self) -> None:
        self._is_connected = False

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            name="Discord Bot (Interactive)",
            status="NOT_CONFIGURED",
            is_connected=False,
            details={"version": "V0.2+ ready", "mode": "future_interactive"}
        )

    async def execute_action(self, action: Any) -> Dict[str, Any]:
        logger.info(f"DiscordBotConnector received action {getattr(action, 'action_type', action)} (delegated or stubbed).")
        return {"status": "unsupported_in_v0.1", "detail": "Use DiscordWebhookConnector for V0.1 outbound alerts"}

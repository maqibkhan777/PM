"""Mattermost Connector implementation."""

from typing import Any, Dict, List, Optional, Set
from app.connectors.base.connector import BaseConnector
from app.connectors.mattermost.client import MattermostClient
from app.core.models.enums import Capability
from app.core.models.domain import HealthStatus
from app.config.settings import settings
from app.utils.logger import logger


class MattermostConnector(BaseConnector):
    """Connector for Mattermost chat platform."""

    def __init__(self, client: Optional[MattermostClient] = None):
        super().__init__(name="mattermost", system_type="chat")
        self.client = client or MattermostClient()

    def get_capabilities(self) -> Set[Capability]:
        return {
            Capability.RESOLVE_USER,
            Capability.SEND_DM,
            Capability.SEND_CHANNEL_MESSAGE,
        }

    async def connect(self) -> bool:
        if not settings.is_mattermost_configured():
            logger.info("Mattermost is using placeholder credentials.")
            self._is_connected = False
            return False
        try:
            me = await self.client.get_me()
            self._is_connected = bool(me.get("id"))
            logger.info(f"Connected to Mattermost as bot: {me.get('username')}")
            return self._is_connected
        except Exception as e:
            logger.warning(f"Could not connect to Mattermost: {e}")
            self._is_connected = False
            return False

    async def disconnect(self) -> None:
        await self.client.close()
        self._is_connected = False

    async def health_check(self) -> HealthStatus:
        if not settings.is_mattermost_configured():
            return HealthStatus(
                name="Mattermost",
                status="NOT_CONFIGURED",
                is_connected=False,
                details={"message": "Placeholder token in use"}
            )
        try:
            me = await self.client.get_me()
            return HealthStatus(
                name="Mattermost",
                status="OK",
                is_connected=True,
                details={"botUser": me.get("username"), "id": me.get("id")}
            )
        except Exception as e:
            return HealthStatus(
                name="Mattermost",
                status="DEGRADED",
                is_connected=False,
                details={"error": str(e)}
            )

    async def execute_action(self, action: Any) -> Dict[str, Any]:
        """Execute a chat action (Send DM or Channel message)."""
        action_type = getattr(action, "action_type", str(action))
        params = getattr(action, "parameters", {})
        target_id = getattr(action, "target_id", "")

        logger.info(f"MattermostConnector executing action: {action_type} for target: {target_id}")

        text = params.get("text") or params.get("message") or ""
        recipient_id = params.get("recipient_id") or target_id
        channel_id = params.get("channel_id") or params.get("channel")

        if channel_id and channel_id != "direct":
            return await self.client.create_post(channel_id=channel_id, message=text)
        elif recipient_id:
            return await self.client.send_direct_message(target_user_id=recipient_id, message=text)
        else:
            raise ValueError("Mattermost SendMessage requires either channel_id or recipient_id")

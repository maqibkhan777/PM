"""Discord Bot Connector for interactive slash commands and PM control."""

from typing import Any, Dict, Optional, Set
from app.connectors.base.connector import BaseConnector
from app.core.models.enums import Capability
from app.core.models.domain import HealthStatus
from app.config.settings import settings
from app.connectors.discord.slash_commands import DiscordSlashCommandHandler, discord_slash_command_handler
from app.connectors.discord.gateway_client import DiscordGatewayClient, discord_gateway_client
from app.utils.logger import logger


class DiscordBotConnector(BaseConnector):
    """Connector for interactive Discord Bot capabilities (slash commands, notifications)."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        application_id: Optional[str] = None,
        guild_id: Optional[str] = None,
        slash_handler: Optional[DiscordSlashCommandHandler] = None,
        gateway_client: Optional[DiscordGatewayClient] = None,
    ):
        super().__init__(name="discord_bot", system_type="interactive_chat")
        self.bot_token = bot_token if bot_token is not None else settings.DISCORD_BOT_TOKEN
        self.application_id = application_id if application_id is not None else settings.DISCORD_APPLICATION_ID
        self.guild_id = guild_id if guild_id is not None else settings.DISCORD_GUILD_ID
        self.slash_handler = slash_handler or discord_slash_command_handler
        self.gateway_client = gateway_client or discord_gateway_client


    def get_capabilities(self) -> Set[Capability]:
        return {
            Capability.SEND_DM,
            Capability.SEND_CHANNEL_MESSAGE,
        }

    async def connect(self) -> bool:
        if not settings.DISCORD_PM_COMMAND_ENABLED:
            logger.info("Discord PM commands are disabled (DISCORD_PM_COMMAND_ENABLED=false). Bot connector idle.")
            self._is_connected = False
            return False

        if settings.is_discord_bot_configured():
            logger.info("Starting live Discord Gateway bot client...")
            await self.gateway_client.start()
            self._is_connected = True
            return True

        logger.info("Discord Bot is not fully configured (token or application ID missing). Gateway client idle.")
        self._is_connected = False
        return False

    async def disconnect(self) -> None:
        if self.gateway_client:
            await self.gateway_client.stop()
        self._is_connected = False

    async def health_check(self) -> HealthStatus:
        if not settings.DISCORD_PM_COMMAND_ENABLED:
            return HealthStatus(
                name="Discord Bot (Interactive)",
                status="DISABLED",
                is_connected=False,
                details={"mode": "interactive_slash_commands", "enabled": False}
            )

        token = self.bot_token if self.bot_token is not None else settings.DISCORD_BOT_TOKEN
        app_id = self.application_id if self.application_id is not None else settings.DISCORD_APPLICATION_ID

        if not token or not str(token).strip() or token == "placeholder_bot_token":
            return HealthStatus(
                name="Discord Bot (Interactive)",
                status="NOT_CONFIGURED",
                is_connected=False,
                details={
                    "mode": "live_gateway",
                    "configured": False,
                    "missing": ["DISCORD_BOT_TOKEN"],
                    "note": "Set DISCORD_BOT_TOKEN to enable Discord Gateway bot"
                }
            )

        if not app_id or not str(app_id).strip() or app_id == "placeholder_application_id":
            return HealthStatus(
                name="Discord Bot (Interactive)",
                status="NOT_CONFIGURED",
                is_connected=False,
                details={
                    "mode": "live_gateway",
                    "configured": False,
                    "missing": ["DISCORD_APPLICATION_ID"],
                    "note": "Set DISCORD_APPLICATION_ID to enable Discord slash command registration"
                }
            )

        connected = self.gateway_client.is_connected if self.gateway_client else self._is_connected
        bot_user = self.gateway_client.bot_user if self.gateway_client else None
        guild_id = self.guild_id or settings.DISCORD_GUILD_ID

        if connected:
            return HealthStatus(
                name="Discord Bot (Interactive)",
                status="OK",
                is_connected=True,
                details={
                    "mode": "live_gateway",
                    "configured": True,
                    "bot_username": bot_user.get("username") if bot_user else "connected",
                    "command_scope": f"guild:{guild_id}" if guild_id else "global",
                }
            )

        return HealthStatus(
            name="Discord Bot (Interactive)",
            status="DISCONNECTED",
            is_connected=False,
            details={
                "mode": "live_gateway",
                "configured": True,
                "note": "Bot credentials configured; connecting to Discord Gateway..."
            }
        )

    async def register_slash_commands(self, guild_id: Optional[str] = None) -> bool:
        """Explicitly trigger /pm slash command registration."""
        if self.gateway_client:
            return await self.gateway_client.register_slash_commands(guild_id=guild_id)
        return False

    async def handle_slash_command(
        self,
        subcommand: str,
        options: Dict[str, Any],
        discord_user_id: str,
        channel_id: Optional[str] = None
    ) -> str:
        """Process a /pm slash command."""
        return await self.slash_handler.execute_subcommand(
            subcommand=subcommand,
            options=options,
            discord_user_id=discord_user_id,
            channel_id=channel_id
        )

    async def handle_interaction(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle incoming Discord interaction payload."""
        return await self.slash_handler.handle_interaction(payload)

    async def execute_action(self, action: Any) -> Dict[str, Any]:
        """Execute outbound action via bot if applicable."""
        action_type = getattr(action, "action_type", "")
        logger.info(f"DiscordBotConnector received action {action_type}.")
        return {"status": "success", "detail": f"Action {action_type} processed by DiscordBotConnector"}

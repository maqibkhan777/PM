"""Discord Webhook Connector for outbound notifications and embeds."""

import asyncio
from typing import Any, Dict, Optional, Set
import httpx
from app.connectors.base.connector import BaseConnector
from app.connectors.discord.formatter import (
    DiscordFormatter,
    COLOR_NOTIFICATION,
    COLOR_OVERDUE,
    COLOR_ATTENTION,
    COLOR_ASSIGNMENT,
)
from app.core.models.enums import Capability
from app.core.models.domain import HealthStatus
from app.config.settings import settings
from app.utils.logger import logger, sanitize_dict


class DiscordWebhookConnector(BaseConnector):
    """Connector that dispatches notifications to Discord via incoming webhook URLs."""

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        notifications_webhook_url: Optional[str] = None,
    ):
        super().__init__(name="discord", system_type="notification")
        self.webhook_url = webhook_url or settings.DISCORD_WEBHOOK_URL
        self.notifications_webhook_url = (
            notifications_webhook_url or settings.DISCORD_NOTIFICATIONS_WEBHOOK_URL
        )
        self._client: Optional[httpx.AsyncClient] = None

    def get_capabilities(self) -> Set[Capability]:
        return {
            Capability.SEND_NOTIFICATION,
            Capability.SEND_EMBED,
            Capability.SEND_DM,
            Capability.SEND_CHANNEL_MESSAGE,
        }

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=settings.REQUEST_TIMEOUT_SECONDS)
        return self._client

    async def connect(self) -> bool:
        if not settings.is_discord_configured() and not settings.is_discord_notifications_configured():
            logger.info("Discord webhook URLs are not configured or using placeholders.")
            self._is_connected = False
            return False
        self._is_connected = True
        return True

    async def disconnect(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
        self._is_connected = False

    async def health_check(self) -> HealthStatus:
        pm_configured = settings.is_discord_configured()
        notif_configured = settings.is_discord_notifications_configured()
        if not pm_configured and not notif_configured:
            return HealthStatus(
                name="Discord Webhook",
                status="NOT_CONFIGURED",
                is_connected=False,
                details={"message": "Placeholder webhook URL"}
            )
        return HealthStatus(
            name="Discord Webhook",
            status="OK",
            is_connected=True,
            details={
                "configured": True,
                "pm_alerts_configured": pm_configured,
                "notifications_configured": notif_configured,
            }
        )

    def _resolve_webhook_url(self, action: Any) -> tuple[Optional[str], Optional[str]]:
        """Resolve the target webhook URL based on the action's target channel.

        Returns:
            (resolved_webhook_url, simulation_reason_if_not_configured)
        """
        params = getattr(action, "parameters", {}) or {}
        target_id = getattr(action, "target_id", None)
        target_channel = (params.get("channel") or target_id or "").strip().lower()

        notif_channel = (settings.JIRA_NOTIFICATION_DISCORD_CHANNEL or "notifications").strip().lower()

        # Check if action explicitly targets the real-time Jira notifications channel
        if target_channel in (notif_channel, "notifications", "#notifications"):
            notif_url = self.notifications_webhook_url or settings.DISCORD_NOTIFICATIONS_WEBHOOK_URL
            if not notif_url or notif_url.endswith("placeholder") or notif_url.endswith("placeholder_notifications"):
                logger.warning(
                    f"Dedicated Discord notifications webhook URL is not set (channel='{target_channel}'). Simulating dispatch."
                )
                return None, "no_notifications_webhook_url"
            return notif_url, None

        # Actions targeting PM Operations / pm-alerts or specific report channels
        pm_url = self.webhook_url or settings.DISCORD_WEBHOOK_URL
        if not pm_url or pm_url.endswith("placeholder"):
            logger.warning(f"Discord PM webhook URL is not set (channel='{target_channel}'). Simulating dispatch.")
            return None, "no_webhook_url"
        return pm_url, None

    async def execute_action(self, action: Any) -> Dict[str, Any]:
        """Send notification or embed to appropriate Discord webhook."""
        action_type = getattr(action, "action_type", str(action))
        params = getattr(action, "parameters", {})

        target_webhook_url, sim_reason = self._resolve_webhook_url(action)
        if not target_webhook_url:
            return {"status": "simulated", "reason": sim_reason or "no_webhook_url"}

        # Construct payload
        if "embeds" in params:
            payload = params
        elif "title" in params or "message" in params or "text" in params:
            title = params.get("title", "PM Notification")
            desc = params.get("message") or params.get("text", "")
            level = params.get("level", "INFO").upper()
            color = COLOR_NOTIFICATION
            if level in ("WARNING", "WARN"):
                color = COLOR_ATTENTION
            elif level in ("ERROR", "VIOLATION"):
                color = COLOR_OVERDUE
            elif level == "SUCCESS":
                color = COLOR_ASSIGNMENT

            payload = DiscordFormatter.format_embed(
                title=title,
                description=desc,
                color=color,
                fields=params.get("fields")
            )
        else:
            payload = {"content": params.get("content", str(params))}

        client = self._get_client()
        logger.info(f"Dispatching Discord notification to webhook ({payload.get('embeds', [{}])[0].get('title', 'content')}): {target_webhook_url[:40]}...")

        for attempt in range(1, settings.MAX_RETRIES + 1):
            try:
                resp = await client.post(target_webhook_url, json=payload)
                if resp.status_code in (200, 204):
                    return {"status": "success", "http_code": resp.status_code}
                elif resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", 2.0))
                    logger.warning(f"Discord rate limited. Retrying after {retry_after}s")
                    await asyncio.sleep(retry_after)
                    continue
                else:
                    resp.raise_for_status()
            except (httpx.TimeoutException, httpx.RequestError) as e:
                if attempt < settings.MAX_RETRIES:
                    await asyncio.sleep(settings.RETRY_BACKOFF_FACTOR ** attempt)
                else:
                    logger.error(f"Failed to post to Discord webhook after {settings.MAX_RETRIES} attempts: {e}")
                    raise

        return {"status": "failed"}

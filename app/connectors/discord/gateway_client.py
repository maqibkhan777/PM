"""Discord Gateway WebSocket and REST Interaction Client for live PM Agent slash commands."""

import asyncio
import json
import random
from typing import Any, Dict, List, Optional
import httpx
import websockets
from app.config.settings import settings
from app.connectors.discord.slash_commands import (
    DiscordSlashCommandHandler,
    discord_slash_command_handler,
    INTERACTION_RESPONSE_TYPE_DEFERRED_CHANNEL_MESSAGE,
)
from app.utils.logger import logger

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"

# Discord Gateway Opcodes
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11


def build_pm_slash_command_schema() -> Dict[str, Any]:
    """Construct the official Discord Application Command schema for /pm."""
    return {
        "name": "pm",
        "description": "PM Operations Agent management and status commands",
        "options": [
            {
                "name": "help",
                "description": "Show available PM commands and usage",
                "type": 1,
            },
            {
                "name": "status",
                "description": "Get read-only details of a Jira ticket",
                "type": 1,
                "options": [
                    {
                        "name": "ticket",
                        "description": "Jira ticket key (e.g. WSSS-326)",
                        "type": 3,
                        "required": True,
                    }
                ],
            },
            {
                "name": "transition",
                "description": "Transition a Jira ticket status",
                "type": 1,
                "options": [
                    {
                        "name": "ticket",
                        "description": "Jira ticket key (e.g. WSSS-326)",
                        "type": 3,
                        "required": True,
                    },
                    {
                        "name": "status",
                        "description": "Target status (e.g. In Progress, Done)",
                        "type": 3,
                        "required": True,
                    },
                ],
            },
            {
                "name": "assign",
                "description": "Assign a Jira ticket to a user",
                "type": 1,
                "options": [
                    {
                        "name": "ticket",
                        "description": "Jira ticket key (e.g. WSSS-326)",
                        "type": 3,
                        "required": True,
                    },
                    {
                        "name": "user",
                        "description": "Jira user display name, email, or account ID",
                        "type": 3,
                        "required": True,
                    },
                ],
            },
            {
                "name": "comment",
                "description": "Add a comment to a Jira ticket",
                "type": 1,
                "options": [
                    {
                        "name": "ticket",
                        "description": "Jira ticket key (e.g. WSSS-326)",
                        "type": 3,
                        "required": True,
                    },
                    {
                        "name": "comment",
                        "description": "Comment text to add",
                        "type": 3,
                        "required": True,
                    },
                ],
            },
            {
                "name": "create",
                "description": "Create a new Jira task",
                "type": 1,
                "options": [
                    {
                        "name": "project",
                        "description": "Jira project key (e.g. WSSS)",
                        "type": 3,
                        "required": True,
                    },
                    {
                        "name": "summary",
                        "description": "Task summary/title",
                        "type": 3,
                        "required": True,
                    },
                ],
            },
            {
                "name": "update",
                "description": "Update a field on a Jira ticket",
                "type": 1,
                "options": [
                    {
                        "name": "ticket",
                        "description": "Jira ticket key (e.g. WSSS-326)",
                        "type": 3,
                        "required": True,
                    },
                    {
                        "name": "field",
                        "description": "Field name (e.g. priority, summary, labels)",
                        "type": 3,
                        "required": True,
                    },
                    {
                        "name": "value",
                        "description": "New value for the field",
                        "type": 3,
                        "required": True,
                    },
                ],
            },
            {
                "name": "notify",
                "description": "Send a notification to a channel or user",
                "type": 1,
                "options": [
                    {
                        "name": "user",
                        "description": "Recipient user or channel name",
                        "type": 3,
                        "required": True,
                    },
                    {
                        "name": "message",
                        "description": "Notification message text",
                        "type": 3,
                        "required": True,
                    },
                ],
            },
            {
                "name": "message",
                "description": "Send a direct message to a user",
                "type": 1,
                "options": [
                    {
                        "name": "user",
                        "description": "Recipient user ID or name",
                        "type": 3,
                        "required": True,
                    },
                    {
                        "name": "message",
                        "description": "Message content",
                        "type": 3,
                        "required": True,
                    },
                ],
            },
        ],
    }


class DiscordGatewayClient:
    """Async Discord Gateway WebSocket and REST Interaction Client."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        application_id: Optional[str] = None,
        guild_id: Optional[str] = None,
        slash_handler: Optional[DiscordSlashCommandHandler] = None,
    ):
        self.bot_token = bot_token if bot_token is not None else settings.DISCORD_BOT_TOKEN
        self.application_id = application_id if application_id is not None else settings.DISCORD_APPLICATION_ID
        self.guild_id = guild_id if guild_id is not None else settings.DISCORD_GUILD_ID
        self.slash_handler = slash_handler or discord_slash_command_handler

        self._running = False
        self._is_connected = False
        self._commands_registered = False
        self._bot_user: Optional[Dict[str, Any]] = None
        self._last_sequence: Optional[int] = None
        self._session_id: Optional[str] = None

        self._gateway_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._ws: Optional[Any] = None

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def bot_user(self) -> Optional[Dict[str, Any]]:
        return self._bot_user

    def _get_auth_headers(self) -> Dict[str, str]:
        token = self.bot_token or settings.DISCORD_BOT_TOKEN or ""
        return {
            "Authorization": f"Bot {token.strip()}",
            "Content-Type": "application/json",
        }

    async def register_slash_commands(self, guild_id: Optional[str] = None, force: bool = False) -> bool:
        """Register /pm slash command with Discord REST API (guild-specific or global)."""
        if self._commands_registered and not force and guild_id is None:
            logger.debug("Discord /pm slash commands already registered in current session. Skipping.")
            return True

        app_id = self.application_id if self.application_id is not None else settings.DISCORD_APPLICATION_ID
        if not app_id:
            logger.warning("Cannot register Discord slash commands: DISCORD_APPLICATION_ID is not configured.")
            return False

        target_guild = guild_id if guild_id is not None else (self.guild_id if self.guild_id is not None else settings.DISCORD_GUILD_ID)
        schema = build_pm_slash_command_schema()

        if target_guild and str(target_guild).strip():
            url = f"{DISCORD_API_BASE}/applications/{app_id}/guilds/{target_guild.strip()}/commands"
            reg_type = f"guild '{target_guild.strip()}'"
        else:
            url = f"{DISCORD_API_BASE}/applications/{app_id}/commands"
            reg_type = "global"

        logger.info(f"Registering Discord /pm slash command ({reg_type})...")
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    res = await client.put(url, headers=self._get_auth_headers(), json=[schema])
                    if res.status_code in (200, 201):
                        self._commands_registered = True
                        logger.info(f"Discord /pm slash command successfully registered ({reg_type}).")
                        return True
                    elif res.status_code == 429:
                        # Rate limited by Discord REST API
                        retry_after = 2.0
                        try:
                            data = res.json()
                            if isinstance(data, dict) and "retry_after" in data:
                                retry_after = float(data["retry_after"])
                        except Exception:
                            header_val = res.headers.get("Retry-After")
                            if header_val:
                                try:
                                    retry_after = float(header_val)
                                except ValueError:
                                    pass

                        sleep_time = min(retry_after + random.uniform(0.1, 0.5), 60.0)
                        if attempt < max_retries:
                            logger.warning(
                                f"Discord rate-limit (HTTP 429) registering slash command ({reg_type}). "
                                f"Backing off for {sleep_time:.2f}s (attempt {attempt}/{max_retries})."
                            )
                            await asyncio.sleep(sleep_time)
                            continue
                        else:
                            logger.error(f"Failed to register Discord slash command after {max_retries} attempts: HTTP 429")
                            return False
                    else:
                        logger.error(f"Failed to register Discord slash command: HTTP {res.status_code} - {res.text}")
                        return False
            except httpx.TimeoutException as e:
                if attempt < max_retries:
                    sleep_time = (2 ** attempt) + random.uniform(0.1, 0.5)
                    logger.warning(f"Timeout registering Discord slash commands: {e}. Retrying in {sleep_time:.2f}s (attempt {attempt}/{max_retries})...")
                    await asyncio.sleep(sleep_time)
                else:
                    logger.error(f"Error registering Discord slash commands after {max_retries} attempts: {e}")
                    return False
            except Exception as e:
                if attempt < max_retries:
                    sleep_time = (2 ** attempt) + random.uniform(0.1, 0.5)
                    logger.warning(f"Transient error registering Discord slash commands: {e}. Retrying in {sleep_time:.2f}s (attempt {attempt}/{max_retries})...")
                    await asyncio.sleep(sleep_time)
                else:
                    logger.error(f"Error registering Discord slash commands: {e}")
                    return False

        return False

    async def send_deferred_acknowledgement(self, interaction_id: str, interaction_token: str) -> bool:
        """Immediately acknowledge interaction with Type 5 (DEFERRED_CHANNEL_MESSAGE) to prevent timeout."""
        url = f"{DISCORD_API_BASE}/interactions/{interaction_id}/{interaction_token}/callback"
        payload = {"type": INTERACTION_RESPONSE_TYPE_DEFERRED_CHANNEL_MESSAGE}
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.post(url, headers=self._get_auth_headers(), json=payload)
                return res.status_code in (200, 204)
        except Exception as e:
            logger.warning(f"Error sending deferred interaction acknowledgment: {e}")
            return False

    async def update_deferred_response(self, interaction_token: str, content: str) -> bool:
        """Update original deferred message with the final command result."""
        app_id = self.application_id or settings.DISCORD_APPLICATION_ID
        if not app_id:
            logger.warning("Cannot update deferred response: DISCORD_APPLICATION_ID is not configured.")
            return False

        url = f"{DISCORD_API_BASE}/webhooks/{app_id}/{interaction_token}/messages/@original"
        payload = {"content": content}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.patch(url, headers=self._get_auth_headers(), json=payload)
                return res.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Error updating deferred Discord interaction response: {e}")
            return False

    async def start(self) -> None:
        """Start the background Gateway listener task."""
        if self._running or (self._gateway_task and not self._gateway_task.done()):
            logger.warning("DiscordGatewayClient is already running.")
            return

        token = self.bot_token or settings.DISCORD_BOT_TOKEN
        app_id = self.application_id or settings.DISCORD_APPLICATION_ID

        if not token or not app_id:
            logger.info("Discord Bot is not configured (DISCORD_BOT_TOKEN or DISCORD_APPLICATION_ID missing). Gateway client idle.")
            return

        if not settings.DISCORD_PM_COMMAND_ENABLED:
            logger.info("Discord PM commands are disabled (DISCORD_PM_COMMAND_ENABLED=false). Gateway client idle.")
            return

        self._running = True
        self._gateway_task = asyncio.create_task(self._gateway_loop())
        logger.info("DiscordGatewayClient background worker started.")

    async def stop(self) -> None:
        """Gracefully disconnect and stop background tasks."""
        self._running = False
        self._is_connected = False

        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        if self._gateway_task and not self._gateway_task.done():
            self._gateway_task.cancel()
            self._gateway_task = None

        logger.info("DiscordGatewayClient stopped.")

    async def _gateway_loop(self) -> None:
        """Main connection and reconnection loop for Discord Gateway."""
        backoff = 2
        while self._running:
            try:
                logger.info(f"Connecting to Discord Gateway: {DISCORD_GATEWAY_URL}...")
                async with websockets.connect(DISCORD_GATEWAY_URL, max_size=10_000_000) as ws:
                    self._ws = ws
                    self._is_connected = True
                    backoff = 2  # Reset backoff on successful connect
                    logger.info("Discord Gateway WebSocket connected.")

                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(message)
                            await self._handle_gateway_payload(data)
                        except Exception as err:
                            logger.error(f"Error processing Discord Gateway payload: {err}", exc_info=True)

            except asyncio.CancelledError:
                break
            except websockets.exceptions.ConnectionClosed as cc:
                self._is_connected = False
                logger.info(f"Discord Gateway connection closed (code={cc.code}, reason='{cc.reason}'). Reconnecting in {backoff}s...")
                try:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)
                except asyncio.CancelledError:
                    break
            except Exception as e:
                self._is_connected = False
                logger.info(f"Discord Gateway disconnected: {e}. Reconnecting in {backoff}s...")
                try:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)
                except asyncio.CancelledError:
                    break
            finally:
                self._is_connected = False
                self._ws = None
                if self._heartbeat_task and not self._heartbeat_task.done():
                    self._heartbeat_task.cancel()
                    self._heartbeat_task = None

        self._is_connected = False

    async def _handle_gateway_payload(self, payload: Dict[str, Any]) -> None:
        """Handle incoming Gateway opcode payload."""
        op = payload.get("op")
        seq = payload.get("s")
        if seq is not None:
            self._last_sequence = seq

        # Opcode 10: HELLO -> start heartbeating and send IDENTIFY
        if op == OP_HELLO:
            d = payload.get("d", {})
            interval_ms = d.get("heartbeat_interval", 41250)
            if self._heartbeat_task and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(interval_ms / 1000.0))
            await self._send_identify()

        # Opcode 1: Discord requested immediate heartbeat
        elif op == OP_HEARTBEAT:
            await self._send_heartbeat()

        # Opcode 7: RECONNECT requested by Discord
        elif op == OP_RECONNECT:
            logger.info("Discord Gateway requested RECONNECT.")
            if self._ws:
                await self._ws.close()

        # Opcode 9: INVALID_SESSION
        elif op == OP_INVALID_SESSION:
            logger.warning("Discord Gateway session invalid. Re-identifying...")
            await asyncio.sleep(2)
            await self._send_identify()

        # Opcode 0: DISPATCH event
        elif op == OP_DISPATCH:
            event_type = payload.get("t")
            event_data = payload.get("d", {})
            await self._handle_dispatch_event(event_type, event_data)

    async def _send_identify(self) -> None:
        """Send Opcode 2 IDENTIFY payload."""
        token = self.bot_token or settings.DISCORD_BOT_TOKEN or ""
        identify_payload = {
            "op": OP_IDENTIFY,
            "d": {
                "token": token.strip(),
                "intents": 0,  # Non-privileged / slash commands only
                "properties": {
                    "os": "windows",
                    "browser": "pm_ops_agent",
                    "device": "pm_ops_agent",
                },
            },
        }
        if self._ws:
            await self._ws.send(json.dumps(identify_payload))
            logger.debug("Discord Gateway IDENTIFY sent.")

    async def _send_heartbeat(self) -> None:
        """Send Opcode 1 HEARTBEAT payload with last sequence."""
        if self._ws:
            payload = {"op": OP_HEARTBEAT, "d": self._last_sequence}
            try:
                await self._ws.send(json.dumps(payload))
            except Exception as e:
                logger.debug(f"Failed to send heartbeat: {e}")

    async def _heartbeat_loop(self, interval_seconds: float) -> None:
        """Heartbeat worker sending heartbeats at negotiated interval."""
        while self._running:
            try:
                await asyncio.sleep(interval_seconds)
                await self._send_heartbeat()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Heartbeat loop error: {e}")

    async def _handle_dispatch_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Handle Opcode 0 Dispatch events."""
        if event_type == "READY":
            self._session_id = data.get("session_id")
            self._bot_user = data.get("user")
            username = self._bot_user.get("username", "Bot") if self._bot_user else "Bot"
            logger.info(f"Discord Gateway READY: Connected as @{username} (session={self._session_id}).")

            # Automatically register / update /pm slash command schema once on first connect
            if not self._commands_registered:
                asyncio.create_task(self.register_slash_commands())

        elif event_type == "INTERACTION_CREATE":
            asyncio.create_task(self._process_interaction_create(data))

    async def _process_interaction_create(self, interaction_data: Dict[str, Any]) -> None:
        """Process an incoming slash command interaction with deferred acknowledgment."""
        interaction_id = str(interaction_data.get("id", ""))
        interaction_token = str(interaction_data.get("token", ""))
        int_type = interaction_data.get("type")

        # Handle Type 2: APPLICATION_COMMAND
        if int_type == 2:
            data = interaction_data.get("data", {})
            cmd_name = data.get("name", "").lower()

            if cmd_name != "pm":
                return

            user_info = interaction_data.get("member", {}).get("user") or interaction_data.get("user", {})
            discord_user_id = str(user_info.get("id", ""))
            channel_id = interaction_data.get("channel_id")

            # 1. Immediately acknowledge / defer interaction to avoid 3-second timeout
            await self.send_deferred_acknowledgement(interaction_id, interaction_token)

            # 2. Extract options and execute subcommand via DiscordSlashCommandHandler
            subcommand, options = self.slash_handler.parse_interaction_options(data.get("options"))
            response_text = await self.slash_handler.execute_subcommand(
                subcommand=subcommand,
                options=options,
                discord_user_id=discord_user_id,
                channel_id=channel_id,
            )

            # 3. Patch the deferred original message with the final result
            await self.update_deferred_response(interaction_token, response_text)


# Global singleton Gateway client
discord_gateway_client = DiscordGatewayClient()

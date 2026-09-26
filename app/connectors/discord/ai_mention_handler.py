"""Isolated Discord AI Mention Handler & Client for Phase 1.

Processes Discord MESSAGE_CREATE gateway events for dedicated AI Bot:
- Accepts messages ONLY when the dedicated AI bot is explicitly mentioned (<@BOT_ID> or <@!BOT_ID>)
  or directly referenced in a reply.
- Ignores bot messages (including messages from the PM bot).
- Ignores messages outside explicitly configured AI test channels.
- Enforces strict user authorization against configured allowlist (fails closed).
- Deduplicates message processing with in-memory TTL cache.
- Strips bot mention tokens cleanly before forwarding prompt to AIDiscordRouterService.
- Safely bounds thread context and handles Discord 2000-character limits by chunking.
- Strictly read-only: Zero Jira mutations, zero Action Engine executions, zero approval triggers.
"""

import asyncio
import collections
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import httpx
import websockets

from app.config.settings import settings
from app.connectors.discord.ai_discord_router import AIDiscordRouterService, ai_discord_router
from app.utils.logger import logger
from app.utils.time import utc_now_iso

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

# Discord Gateway Intents: GUILD_MESSAGES (512) | MESSAGE_CONTENT (32768) | DIRECT_MESSAGES (4096) = 37376
AI_BOT_GATEWAY_INTENTS = 512 | 32768 | 4096

MAX_DISCORD_MESSAGE_LENGTH = 2000
DEDUPLICATION_CACHE_SIZE = 1000
DEDUPLICATION_TTL_SECONDS = 300.0  # 5 minutes


class AIDiscordMentionHandler:
    """Processes, filters, authorizes, and handles mention events for the AI bot."""

    def __init__(
        self,
        router: Optional[AIDiscordRouterService] = None,
        bot_user_id: Optional[str] = None,
    ):
        self.router = router or ai_discord_router
        self._bot_user_id = bot_user_id
        # Simple FIFO/LRU-style deduplication set: message_id -> timestamp
        self._processed_messages: collections.OrderedDict[str, float] = collections.OrderedDict()

    def set_bot_user_id(self, bot_id: str) -> None:
        """Set or update the AI bot's Snowflake user ID."""
        self._bot_user_id = str(bot_id).strip() if bot_id else None

    def get_bot_user_id(self) -> Optional[str]:
        """Return configured or discovered AI bot user ID."""
        if self._bot_user_id:
            return self._bot_user_id
        if settings.DISCORD_AI_APPLICATION_ID:
            return str(settings.DISCORD_AI_APPLICATION_ID).strip()
        return None

    def is_duplicate(self, message_id: str) -> bool:
        """Check if message_id has already been processed within TTL window."""
        if not message_id:
            return False
        now = time.monotonic()

        # Clean expired keys
        while self._processed_messages:
            first_key, first_time = next(iter(self._processed_messages.items()))
            if now - first_time > DEDUPLICATION_TTL_SECONDS:
                self._processed_messages.pop(first_key)
            else:
                break

        if message_id in self._processed_messages:
            return True

        self._processed_messages[message_id] = now
        if len(self._processed_messages) > DEDUPLICATION_CACHE_SIZE:
            self._processed_messages.popitem(last=False)
        return False

    def is_bot_mentioned(self, message_data: Dict[str, Any]) -> bool:
        """Check if the AI bot is explicitly mentioned in the message or referenced in mentions list."""
        bot_id = self.get_bot_user_id()
        if not bot_id:
            return False

        # 1. Check mentions array in Discord payload
        mentions = message_data.get("mentions", [])
        if isinstance(mentions, list):
            for m in mentions:
                if isinstance(m, dict) and str(m.get("id")) == bot_id:
                    return True

        # 2. Check raw content for <@BOT_ID> or <@!BOT_ID>
        content = message_data.get("content", "")
        if f"<@{bot_id}>" in content or f"<@!{bot_id}>" in content:
            return True

        # 3. Check referenced message in reply if reply points to bot
        ref = message_data.get("referenced_message")
        if ref and isinstance(ref, dict):
            author = ref.get("author", {})
            if str(author.get("id")) == bot_id:
                return True

        return False

    def strip_mention(self, content: str) -> str:
        """Strip bot mention tokens (<@BOT_ID>, <@!BOT_ID>, @BotName) from content."""
        if not content:
            return ""
        bot_id = self.get_bot_user_id()
        clean = content
        if bot_id:
            clean = re.sub(rf"<@!?{re.escape(bot_id)}>", "", clean)
        # Also clean generic bot mention strings if any
        clean = re.sub(r"^@\w+\s*", "", clean.strip())
        return clean.strip()

    def is_authorized_user(self, user_id: Optional[str]) -> bool:
        """Check if user_id is in DISCORD_AI_ALLOWED_USER_IDS allowlist."""
        return settings.is_discord_ai_user_allowed(user_id)

    def is_allowed_channel(self, channel_id: Optional[str]) -> bool:
        """Check if channel_id is in DISCORD_AI_ALLOWED_CHANNEL_IDS allowlist."""
        return settings.is_discord_ai_channel_allowed(channel_id)

    async def handle_message_create(
        self,
        message_data: Dict[str, Any],
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> Optional[Dict[str, Any]]:
        """Process incoming Discord MESSAGE_CREATE event.
        
        Returns:
            Dict containing processing result, or None if message was ignored.
        """
        # 1. Ignore bot messages (including own messages or existing PM bot)
        author = message_data.get("author", {})
        if author.get("bot", False):
            logger.debug("AI Mention Handler: Ignored bot message.")
            return None

        user_id = str(author.get("id", ""))
        message_id = str(message_data.get("id", ""))
        channel_id = str(message_data.get("channel_id", ""))
        raw_content = message_data.get("content", "")

        # 2. Check if bot is mentioned
        if not self.is_bot_mentioned(message_data):
            return None

        # 3. Check channel authorization (fails closed)
        if not self.is_allowed_channel(channel_id):
            logger.info(f"AI Mention Handler: Ignored message in unapproved channel '{channel_id}'.")
            return {
                "status": "rejected",
                "reason": "unapproved_channel",
                "channel_id": channel_id,
            }

        # 4. Check user authorization (fails closed)
        if not self.is_authorized_user(user_id):
            logger.info(f"AI Mention Handler: Rejected unauthorized user '{user_id}'.")
            # Send rejection reply to user
            rejection_text = "❌ You are not authorized to use the PM AI assistant."
            await self.send_channel_reply(
                channel_id=channel_id,
                message_id=message_id,
                content=rejection_text,
                http_client=http_client,
            )
            return {
                "status": "rejected",
                "reason": "unauthorized_user",
                "user_id": user_id,
            }

        # 5. Deduplication check
        if self.is_duplicate(message_id):
            logger.warning(f"AI Mention Handler: Duplicate message '{message_id}' ignored.")
            return {
                "status": "ignored",
                "reason": "duplicate_message",
                "message_id": message_id,
            }

        # 6. Extract prompt and bounded thread context
        clean_prompt = self.strip_mention(raw_content)
        thread_context: List[str] = []
        ref = message_data.get("referenced_message")
        if ref and isinstance(ref, dict):
            ref_author = ref.get("author", {}).get("username", "User")
            ref_content = ref.get("content", "")
            if ref_content:
                thread_context.append(f"{ref_author}: {ref_content[:300]}")

        # 7. Route request to AI router service
        response_payload = await self.router.route_request(
            prompt=clean_prompt,
            actor_id=user_id,
            channel_id=channel_id,
            message_id=message_id,
            thread_context=thread_context,
        )

        # 8. Send reply back to Discord channel
        send_ok = await self.send_channel_reply(
            channel_id=channel_id,
            message_id=message_id,
            content=response_payload,
            http_client=http_client,
        )

        return {
            "status": "processed",
            "message_id": message_id,
            "channel_id": channel_id,
            "user_id": user_id,
            "prompt": clean_prompt,
            "response": response_payload,
            "sent": send_ok,
        }

    async def send_channel_reply(
        self,
        channel_id: str,
        message_id: str,
        content: Union[str, Dict[str, Any]],
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> bool:
        """Send a reply to a Discord channel, chunking if message exceeds 2000 chars."""
        if not channel_id:
            return False

        token = settings.DISCORD_AI_BOT_TOKEN
        if not token or not token.strip():
            logger.warning("Cannot send Discord reply: DISCORD_AI_BOT_TOKEN is not configured.")
            return False

        headers = {
            "Authorization": f"Bot {token.strip()}",
            "Content-Type": "application/json",
        }
        url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"

        # Handle Embeds vs Text
        if isinstance(content, dict) and "embeds" in content:
            payloads = [{
                "embeds": content["embeds"],
                "message_reference": {"message_id": message_id} if message_id else None,
            }]
        elif isinstance(content, dict):
            payloads = [content]
        else:
            text = str(content) if content is not None else ""
            chunks = self.chunk_text(text, max_len=MAX_DISCORD_MESSAGE_LENGTH)
            payloads = []
            for idx, ch in enumerate(chunks):
                p: Dict[str, Any] = {"content": ch}
                if idx == 0 and message_id:
                    p["message_reference"] = {"message_id": message_id}
                payloads.append(p)

        should_close = False
        client = http_client
        if not client:
            client = httpx.AsyncClient(timeout=15.0)
            should_close = True

        try:
            for p in payloads:
                # Remove null message_reference if not set
                if "message_reference" in p and p["message_reference"] is None:
                    p.pop("message_reference")
                res = await client.post(url, headers=headers, json=p)
                if res.status_code not in (200, 201):
                    logger.error(f"Error sending Discord AI reply: HTTP {res.status_code} - {res.text}")
                    return False
            return True
        except Exception as e:
            logger.error(f"Exception sending Discord AI reply: {e}")
            return False
        finally:
            if should_close:
                await client.aclose()

    @staticmethod
    def chunk_text(text: str, max_len: int = MAX_DISCORD_MESSAGE_LENGTH) -> List[str]:
        """Split text into chunks not exceeding max_len characters."""
        if not text:
            return ["*(No output)*"]
        if len(text) <= max_len:
            return [text]

        chunks = []
        remaining = text
        while len(remaining) > max_len:
            split_idx = remaining[:max_len].rfind("\n")
            if split_idx <= 0:
                split_idx = max_len
            chunks.append(remaining[:split_idx].strip())
            remaining = remaining[split_idx:].strip()

        if remaining:
            chunks.append(remaining)
        return chunks


class AIDiscordGatewayClient:
    """Dedicated Discord Gateway WebSocket client for the AI Bot."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        application_id: Optional[str] = None,
        mention_handler: Optional[AIDiscordMentionHandler] = None,
    ):
        self.bot_token = bot_token if bot_token is not None else settings.DISCORD_AI_BOT_TOKEN
        self.application_id = application_id if application_id is not None else settings.DISCORD_AI_APPLICATION_ID
        self.mention_handler = mention_handler or AIDiscordMentionHandler(bot_user_id=self.application_id)

        self._running = False
        self._is_connected = False
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

    async def start(self) -> None:
        """Start the background Gateway listener for AI Bot."""
        if self._running or (self._gateway_task and not self._gateway_task.done()):
            logger.warning("AIDiscordGatewayClient is already running.")
            return

        token = self.bot_token or settings.DISCORD_AI_BOT_TOKEN
        if not token or not str(token).strip():
            logger.info("Discord AI Bot is not configured (DISCORD_AI_BOT_TOKEN missing). Gateway client idle.")
            return

        if not getattr(settings, "DISCORD_AI_BOT_ENABLED", False):
            logger.info("Discord AI Bot is disabled (DISCORD_AI_BOT_ENABLED=false). Gateway client idle.")
            return

        self._running = True
        self._gateway_task = asyncio.create_task(self._gateway_loop())
        logger.info("AIDiscordGatewayClient background worker started.")

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

        logger.info("AIDiscordGatewayClient stopped.")

    async def _gateway_loop(self) -> None:
        """Main connection and reconnection loop for Discord Gateway."""
        backoff = 2
        while self._running:
            try:
                logger.info(f"Connecting AI Bot to Discord Gateway: {DISCORD_GATEWAY_URL}...")
                async with websockets.connect(DISCORD_GATEWAY_URL, max_size=10_000_000) as ws:
                    self._ws = ws
                    self._is_connected = True
                    backoff = 2
                    logger.info("Discord AI Bot Gateway WebSocket connected.")

                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(message)
                            await self._handle_gateway_payload(data)
                        except Exception as err:
                            logger.error(f"Error processing AI Gateway payload: {err}", exc_info=True)

            except asyncio.CancelledError:
                break
            except websockets.exceptions.ConnectionClosed as cc:
                self._is_connected = False
                logger.info(f"Discord AI Gateway connection closed (code={cc.code}). Reconnecting in {backoff}s...")
                try:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)
                except asyncio.CancelledError:
                    break
            except Exception as e:
                self._is_connected = False
                logger.info(f"Discord AI Gateway disconnected: {e}. Reconnecting in {backoff}s...")
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

        if op == OP_HELLO:
            d = payload.get("d", {})
            interval_ms = d.get("heartbeat_interval", 41250)
            if self._heartbeat_task and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(interval_ms / 1000.0))
            await self._send_identify()

        elif op == OP_HEARTBEAT:
            await self._send_heartbeat()

        elif op == OP_RECONNECT:
            logger.info("Discord AI Gateway requested RECONNECT.")
            if self._ws:
                await self._ws.close()

        elif op == OP_INVALID_SESSION:
            logger.warning("Discord AI Gateway session invalid. Re-identifying...")
            await asyncio.sleep(2)
            await self._send_identify()

        elif op == OP_DISPATCH:
            event_type = payload.get("t")
            event_data = payload.get("d", {})
            await self._handle_dispatch_event(event_type, event_data)

    async def _send_identify(self) -> None:
        """Send Opcode 2 IDENTIFY payload with message content intents."""
        token = self.bot_token or settings.DISCORD_AI_BOT_TOKEN or ""
        identify_payload = {
            "op": OP_IDENTIFY,
            "d": {
                "token": token.strip(),
                "intents": AI_BOT_GATEWAY_INTENTS,
                "properties": {
                    "os": "windows",
                    "browser": "pm_ai_ops_agent",
                    "device": "pm_ai_ops_agent",
                },
            },
        }
        if self._ws:
            await self._ws.send(json.dumps(identify_payload))
            logger.debug("Discord AI Gateway IDENTIFY sent.")

    async def _send_heartbeat(self) -> None:
        if self._ws:
            payload = {"op": OP_HEARTBEAT, "d": self._last_sequence}
            try:
                await self._ws.send(json.dumps(payload))
            except Exception as e:
                logger.debug(f"Failed to send AI heartbeat: {e}")

    async def _heartbeat_loop(self, interval_seconds: float) -> None:
        while self._running:
            try:
                await asyncio.sleep(interval_seconds)
                await self._send_heartbeat()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"AI Heartbeat loop error: {e}")

    async def _handle_dispatch_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Handle Opcode 0 Dispatch events."""
        if event_type == "READY":
            self._session_id = data.get("session_id")
            self._bot_user = data.get("user")
            if self._bot_user and self._bot_user.get("id"):
                self.mention_handler.set_bot_user_id(str(self._bot_user["id"]))
            username = self._bot_user.get("username", "AIBot") if self._bot_user else "AIBot"
            logger.info(f"Discord AI Gateway READY: Connected as @{username} (ID: {self.mention_handler.get_bot_user_id()}).")

        elif event_type == "MESSAGE_CREATE":
            asyncio.create_task(self.mention_handler.handle_message_create(data))


# Global singleton mention handler and AI gateway client
ai_discord_mention_handler = AIDiscordMentionHandler()
ai_discord_gateway_client = AIDiscordGatewayClient(mention_handler=ai_discord_mention_handler)

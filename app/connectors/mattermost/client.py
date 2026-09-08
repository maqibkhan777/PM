"""Mattermost REST API v4 client with timeouts, bearer token auth, and retry handling."""

import asyncio
from typing import Any, Dict, List, Optional
import httpx
from app.config.settings import settings
from app.utils.logger import logger


class MattermostClient:
    """Async HTTP Client for Mattermost REST API v4."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: Optional[float] = None
    ):
        self.base_url = (base_url or settings.MATTERMOST_URL).rstrip("/")
        self.token = token or settings.MATTERMOST_TOKEN
        self.timeout = timeout or settings.REQUEST_TIMEOUT_SECONDS
        self._client: Optional[httpx.AsyncClient] = None
        self._bot_user_id: Optional[str] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "PM-Operations-Agent/0.1.0"
            }
            self._client = httpx.AsyncClient(headers=headers, timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        json_data: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> Any:
        """Execute an HTTP request against Mattermost v4 REST API."""
        client = self._get_client()
        url = f"{self.base_url}/api/v4{path}"
        max_retries = settings.MAX_RETRIES
        backoff = settings.RETRY_BACKOFF_FACTOR

        for attempt in range(1, max_retries + 1):
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    json=json_data,
                    params=params
                )

                if response.status_code in (401, 403):
                    logger.error(f"Mattermost auth failure: HTTP {response.status_code} for {method} {url}")
                    response.raise_for_status()

                if response.status_code in (429, 500, 502, 503, 504):
                    if attempt < max_retries:
                        sleep_time = backoff ** attempt
                        logger.warning(f"Mattermost API error {response.status_code}. Retrying in {sleep_time:.1f}s")
                        await asyncio.sleep(sleep_time)
                        continue
                    else:
                        response.raise_for_status()

                response.raise_for_status()
                if response.status_code == 204 or not response.content:
                    return {}
                return response.json()

            except httpx.TimeoutException as e:
                if attempt < max_retries:
                    await asyncio.sleep(backoff ** attempt)
                else:
                    logger.error(f"Mattermost API request timed out: {e}")
                    raise
            except httpx.RequestError as e:
                if attempt < max_retries:
                    await asyncio.sleep(backoff ** attempt)
                else:
                    logger.error(f"Mattermost API request connection failed: {e}")
                    raise

        raise RuntimeError("Unexpected end of request retry loop")

    async def get_me(self) -> Dict[str, Any]:
        """Fetch bot user profile."""
        data = await self._request("GET", "/users/me")
        if isinstance(data, dict) and data.get("id"):
            self._bot_user_id = data.get("id")
        return data

    async def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Find a Mattermost user by username."""
        try:
            return await self._request("GET", f"/users/username/{username}")
        except Exception as e:
            logger.debug(f"User not found by username '{username}': {e}")
            return None

    async def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Find a Mattermost user by email."""
        try:
            return await self._request("GET", f"/users/email/{email}")
        except Exception as e:
            logger.debug(f"User not found by email '{email}': {e}")
            return None

    async def create_direct_channel(self, user_id_1: str, user_id_2: str) -> Dict[str, Any]:
        """Create or get a 1-on-1 direct channel between two users."""
        payload = [user_id_1, user_id_2]
        return await self._request("POST", "/channels/direct", json_data=payload)

    async def create_post(self, channel_id: str, message: str, props: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send a message to a channel."""
        payload = {
            "channel_id": channel_id,
            "message": message,
            "props": props or {}
        }
        return await self._request("POST", "/posts", json_data=payload)

    async def send_direct_message(self, target_user_id: str, message: str) -> Dict[str, Any]:
        """Send a direct message to a specific user ID."""
        if not self._bot_user_id:
            me = await self.get_me()
            self._bot_user_id = me.get("id")

        channel = await self.create_direct_channel(self._bot_user_id, target_user_id)
        channel_id = channel.get("id")
        if not channel_id:
            raise ValueError(f"Could not establish DM channel with user {target_user_id}")

        return await self.create_post(channel_id=channel_id, message=message)

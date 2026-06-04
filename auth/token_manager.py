"""
Token Manager
===============
Centralized token management for all Amazon SP-API stores.

RULES:
- This is the ONLY module that manages tokens.
- The rest of the system calls get_access_token(store_id).
- Per-store token isolation.
- Automatic refresh before expiry.
- Retry with exponential backoff.
- In-memory cache with expiry tracking.
- Alert on authorization failure.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from config.secrets import SecretsLoader

logger = logging.getLogger("amazon.auth.token_manager")


class TokenExpiredError(Exception):
    """The LWA refresh token has expired or is invalid."""


class AuthFailureError(Exception):
    """Authentication failed — will trigger an alert."""


class TokenEntry:
    """A cached token entry with expiry tracking."""

    def __init__(self, access_token: str, expires_in: int):
        self.access_token = access_token
        self.expires_at = time.time() + expires_in - 60  # 60s safety buffer
        self.refreshed_at = datetime.now(timezone.utc)

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def expires_in_seconds(self) -> int:
        return max(0, int(self.expires_at - time.time()))


class TokenManager:
    """
    Manages LWA access tokens for all stores.

    Public API:
        get_access_token(store_id) → str
    """

    def __init__(self, secrets_loader: SecretsLoader):
        self._secrets = secrets_loader
        self._tokens: Dict[str, TokenEntry] = {}
        self._refresh_locks: Dict[str, asyncio.Lock] = {}
        self._http_client: Optional[httpx.AsyncClient] = None

    async def get_access_token(self, store_id: str) -> str:
        """
        Get a valid LWA access token for a store.
        Returns cached token if still valid, otherwise refreshes.

        Args:
            store_id: Store identifier

        Returns:
            Valid access token string

        Raises:
            AuthFailureError: If authentication fails
        """
        # Check cache
        cached = self._tokens.get(store_id)
        if cached and not cached.is_expired:
            logger.debug("Token cache hit", store_id=store_id)
            return cached.access_token

        # Ensure per-store lock exists
        if store_id not in self._refresh_locks:
            self._refresh_locks[store_id] = asyncio.Lock()

        # Use lock to prevent concurrent refreshes for the same store
        async with self._refresh_locks[store_id]:
            # Double-check after acquiring lock
            cached = self._tokens.get(store_id)
            if cached and not cached.is_expired:
                return cached.access_token

            return await self._refresh_token(store_id)

    async def _refresh_token(self, store_id: str) -> str:
        """
        Refresh the LWA access token for a store.

        Uses exponential backoff with up to 3 retries.
        Alerts on persistent failure.
        """
        secrets = self._secrets.get_all(store_id)
        client_id = secrets.get("lwa_client_id")
        client_secret = secrets.get("lwa_client_secret")
        refresh_token = secrets.get("refresh_token")

        if not all([client_id, client_secret, refresh_token]):
            raise AuthFailureError(
                f"Store '{store_id}': missing LWA credentials. "
                "Set STORE_{ID}_LWA_CLIENT_ID, STORE_{ID}_LWA_CLIENT_SECRET, "
                "and STORE_{ID}_REFRESH_TOKEN in environment."
            )

        try:
            token_str = await self._do_refresh(client_id, client_secret, refresh_token)
            # Cache the token
            self._tokens[store_id] = TokenEntry(token_str, 3600)
            logger.info(
                "Token refreshed",
                store_id=store_id,
                expires_in_seconds=3600,
            )
            return token_str

        except TokenExpiredError:
            logger.critical(
                "LWA refresh token expired for store '%s'. Action required.",
                store_id,
            )
            raise AuthFailureError(f"Store '{store_id}': refresh token expired.")
        except Exception as e:
            logger.error(
                "Token refresh failed for store '%s': %s",
                store_id,
                str(e),
            )
            raise AuthFailureError(f"Store '{store_id}': token refresh failed: {e}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, TokenExpiredError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def _do_refresh(self, client_id: str, client_secret: str, refresh_token: str) -> str:
        """Make the actual LWA token refresh HTTP call."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=15.0)

        payload = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        }

        response = await self._http_client.post(
            "https://api.amazon.com/auth/o2/token",
            json=payload,
        )

        if response.status_code == 400:
            body = response.json()
            error_desc = body.get("error_description", "")
            if "expired" in error_desc.lower() or "invalid" in error_desc.lower():
                raise TokenExpiredError(error_desc)

        response.raise_for_status()
        data = response.json()
        return data["access_token"]

    async def close(self):
        """Clean up HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    def invalidate(self, store_id: str):
        """Force token refresh on next call."""
        if store_id in self._tokens:
            del self._tokens[store_id]
            logger.info("Token cache invalidated", store_id=store_id)

    def get_token_age_seconds(self, store_id: str) -> Optional[int]:
        """Check how long since token was refreshed."""
        entry = self._tokens.get(store_id)
        if entry:
            return int(time.time() - entry.expires_at + 3600)
        return None

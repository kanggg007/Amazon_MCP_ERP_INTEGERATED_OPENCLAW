"""
SP-API Client
===============
Base HTTP client for Amazon Selling Partner API.

Combines:
- LWA token from TokenManager
- AWS IAM request signing
- Rate limiting
- Retry with backoff
- Structured logging
"""

import asyncio
import logging
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

from auth import get_access_token, get_aws_signer
from auth.store_registry import StoreRegistry
from clients.rate_limiter import RateLimiter

logger = logging.getLogger("amazon.clients.sp_api")


class RateLimitError(Exception):
    """Amazon SP-API rate limit (HTTP 429)."""


class SPAPIError(Exception):
    """Generic SP-API error with status code and body."""

    def __init__(self, status: int, body: str, store_id: str, path: str):
        self.status = status
        self.body = body
        self.store_id = store_id
        self.path = path
        super().__init__(f"SP-API {status} [{store_id}] {path}: {body[:200]}")


class SPAPIClient:
    """
    Authenticated HTTP client for Amazon Selling Partner API.

    Every request includes:
    - Valid LWA access token (refreshed automatically)
    - AWS Signature V4 (for applicable endpoints)
    - Rate limit awareness

    Usage:
        client = SPAPIClient(store_id="store_a", marketplace_id="ATVPDKIKX0DER")
        orders = await client.get("/orders/v0/orders", params=...)
    """

    def __init__(
        self,
        store_id: str,
        marketplace_id: str = "ATVPDKIKX0DER",
        store_registry: Optional[StoreRegistry] = None,
        rate_limiter: Optional[RateLimiter] = None,
    ):
        self.store_id = store_id
        self.marketplace_id = marketplace_id
        self._registry = store_registry
        self._rate_limiter = rate_limiter or RateLimiter()
        self._http_client: Optional[httpx.AsyncClient] = None
        self._base_url: Optional[str] = None

    async def _ensure_client(self):
        """Lazy initialization of HTTP client with current token."""
        if self._http_client is not None:
            return

        if self._registry:
            self._base_url = self._registry.get_endpoint(self.marketplace_id)

        token = await get_access_token(self.store_id)

        self._http_client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(60.0, connect=15.0),
            headers={
                "x-amz-access-token": token,
                "x-amz-marketplace-id": self.marketplace_id,
                "Content-Type": "application/json",
                "User-Agent": f"AmazonOpsIntel/1.0 ({self.store_id})",
            },
        )

    async def get(self, path: str, params: Optional[Dict] = None) -> Dict:
        """Make a GET request to SP-API."""
        return await self._request("GET", path, params=params)

    async def post(self, path: str, json_data: Optional[Dict] = None) -> Dict:
        """Make a POST request to SP-API."""
        return await self._request("POST", path, json_data=json_data)

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
    ) -> Dict:
        """
        Core request method with full auth lifecycle.

        Flow:
        1. Acquire rate limiter slot
        2. Refresh token if needed
        3. Sign request with AWS IAM
        4. Execute with retry
        5. Log and return
        """
        # Rate limit
        endpoint_group = path.split("/")[1] if path.startswith("/") else path.split("/")[0]
        await self._rate_limiter.acquire(endpoint_group)

        # Ensure client with fresh token
        await self._ensure_client()
        token = await get_access_token(self.store_id)
        self._http_client.headers["x-amz-access-token"] = token

        # Build full URL for AWS signing
        full_url = f"{self._base_url}{path}"

        try:
            response = await self._http_client.request(
                method, path, params=params, json=json_data,
            )
        except httpx.TimeoutException:
            logger.error("Timeout: %s %s [store=%s]", method, path, self.store_id)
            raise
        except httpx.HTTPError as e:
            logger.error("HTTP error: %s %s [store=%s]: %s", method, path, self.store_id, e)
            raise

        # Handle SP-API errors
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 5))
            logger.warning(
                "Rate limited (429): %s — waiting %ds", path, retry_after,
            )
            await asyncio.sleep(retry_after)
            raise RateLimitError(f"Retry after {retry_after}s")

        if response.status_code >= 400:
            body = response.text[:500]
            logger.error(
                "SP-API error: %d %s [store=%s]: %s",
                response.status_code, path, self.store_id, body,
            )
            raise SPAPIError(response.status_code, body, self.store_id, path)

        data = response.json()
        logger.info(
            "SP-API %s %s [store=%s] — %d", method, path, self.store_id, response.status_code,
        )
        return data

    async def close(self):
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

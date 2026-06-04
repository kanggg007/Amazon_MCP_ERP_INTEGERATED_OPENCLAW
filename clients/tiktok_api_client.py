"""
TikTok Shop API Client
========================
HTTP client for TikTok Shop API.
Supports order and review data retrieval.
"""

import logging
from typing import Dict, Optional

import httpx

from auth import get_store_registry

logger = logging.getLogger("amazon.clients.tiktok")


class TikTokShopClient:
    """
    Client for TikTok Shop API (partners).
    Uses shop_cipher + access_token for auth.
    """

    BASE_URL = "https://open-api.tiktokglobalshop.com"

    def __init__(self, store_id: str):
        self.store_id = store_id
        registry = get_store_registry()
        store = registry.get_store(store_id)
        if not store or not store.get("has_tiktok"):
            raise ValueError(f"Store '{store_id}' does not have TikTok Shop configured.")

        from config.secrets import SecretsLoader
        secrets = SecretsLoader()
        self.shop_cipher = secrets.get(store_id, "tiktok_shop_cipher") or ""

        self._http_client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=30.0,
            headers={
                "Content-Type": "application/json",
            },
        )

    async def get_orders(self, create_time_ge: Optional[str] = None, limit: int = 20) -> Dict:
        """Fetch TikTok Shop orders."""
        params = {
            "shop_cipher": self.shop_cipher,
            "page_size": limit,
        }
        if create_time_ge:
            params["create_time_ge"] = create_time_ge
        response = await self._http_client.get("/order/202309/orders", params=params)
        response.raise_for_status()
        return response.json()

    async def get_reviews(self, limit: int = 20) -> Dict:
        """Fetch product reviews."""
        params = {"shop_cipher": self.shop_cipher, "page_size": limit}
        response = await self._http_client.get("/review/202309/reviews", params=params)
        response.raise_for_status()
        return response.json()

    async def close(self):
        await self._http_client.aclose()

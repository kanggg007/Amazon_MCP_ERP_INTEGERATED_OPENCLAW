"""
Walmart Marketplace API Client
================================
HTTP client for Walmart Marketplace API.
Supports order retrieval, inventory updates, and item search.
"""

import logging
import time
import hashlib
import base64
from typing import Dict, Optional
from urllib.parse import urlencode

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from auth import get_store_registry

logger = logging.getLogger("amazon.clients.walmart")


class WalmartAPIClient:
    """
    Authenticated client for Walmart Marketplace API.
    Uses Walmart's signature-based auth (client_id + client_secret).
    """

    BASE_URL = "https://marketplace.walmartapis.com/v3"

    def __init__(self, store_id: str):
        self.store_id = store_id
        registry = get_store_registry()
        store = registry.get_store(store_id)
        if not store or not store.get("has_walmart"):
            raise ValueError(f"Store '{store_id}' does not have Walmart configured.")

        from config.secrets import SecretsLoader
        secrets = SecretsLoader()
        self.client_id = secrets.get(store_id, "walmart_client_id") or ""
        self.client_secret = secrets.get(store_id, "walmart_client_secret") or ""
        self._http_client = httpx.AsyncClient(base_url=self.BASE_URL, timeout=30.0)

    def _generate_signature(self, method: str, path: str, timestamp: str) -> str:
        """Generate Walmart HMAC-SHA256 signature."""
        raw = f"{self.client_id}{path}{method}{timestamp}"
        signature = hashlib.sha256((raw + self.client_secret).encode()).hexdigest()
        return base64.b64encode(signature.encode()).decode()

    async def _headers(self, method: str, path: str) -> Dict[str, str]:
        ts = str(int(time.time() * 1000))
        return {
            "WM_SVC.NAME": "Walmart Marketplace",
            "WM_QOS.CORRELATION_ID": f"{self.store_id}-{ts}",
            "WM_SEC.AUTH_SIGNATURE": self._generate_signature(method, path, ts),
            "WM_SEC.TIMESTAMP": ts,
            "WM_CONSUMER.ID": self.client_id,
            "WM_CONSUMER.CHANNEL.TYPE": "SWAGGER",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def get_orders(self, status: str = "Created", limit: int = 50) -> Dict:
        """Fetch Walmart orders by status."""
        path = "/orders"
        headers = await self._headers("GET", path)
        params = {"status": status, "limit": limit}
        response = await self._http_client.get(path, headers=headers, params=params)
        response.raise_for_status()
        return response.json()

    async def get_inventory(self, sku: Optional[str] = None) -> Dict:
        """Get inventory for a SKU or all SKUs."""
        path = "/inventory"
        headers = await self._headers("GET", path)
        params = {}
        if sku:
            params["sku"] = sku
        response = await self._http_client.get(path, headers=headers, params=params)
        response.raise_for_status()
        return response.json()

    async def close(self):
        await self._http_client.aclose()

"""
Store Registry
===============
Maintains a registry of configured stores and their marketplace mappings.
Loads store configurations from secrets and environment.
"""

import logging
from typing import Dict, List, Optional

from config.secrets import SecretsLoader
from config.settings import Settings

logger = logging.getLogger("amazon.auth.store_registry")


MARKETPLACE_IDS = {
    "US": "ATVPDKIKX0DER",
    "CA": "A2EUQ1WTGCTBG2",
    "MX": "A1AM78C64UM0Y8",
    "UK": "A1F83G8C2ARO7P",
    "DE": "A1PA6795UKMFR9",
    "FR": "A13V1IB3VIYZZH",
    "IT": "APJ6JRA9NG5V4",
    "ES": "A1RKKUPIHCS9H3",
    "JP": "A1VC38T7YXB528",
    "AU": "A39IBJ37TRP1C6",
}

SP_API_ENDPOINTS = {
    "NA": "https://sellingpartnerapi-na.amazon.com",
    "EU": "https://sellingpartnerapi-eu.amazon.com",
    "FE": "https://sellingpartnerapi-fe.amazon.com",
}

MARKETPLACE_REGION = {
    "ATVPDKIKX0DER": "NA", "A2EUQ1WTGCTBG2": "NA", "A1AM78C64UM0Y8": "NA",
    "A1F83G8C2ARO7P": "EU", "A1PA6795UKMFR9": "EU", "A13V1IB3VIYZZH": "EU",
    "APJ6JRA9NG5V4": "EU", "A1RKKUPIHCS9H3": "EU",
    "A1VC38T7YXB528": "FE", "A39IBJ37TRP1C6": "FE",
}


class StoreRegistry:
    """
    Registry of all configured stores and their capabilities.
    Thread-safe, loaded once at startup.
    """

    def __init__(self, secrets: SecretsLoader, settings: Settings):
        self._secrets = secrets
        self._settings = settings
        self._stores: Dict[str, dict] = {}
        self._load_stores()

    def _load_stores(self):
        """Load all stores from secrets."""
        store_ids = self._secrets.store_ids_available()

        for store_id in store_ids:
            secrets = self._secrets.get_all(store_id)
            marketplaces_str = secrets.get("marketplace_ids", "ATVPDKIKX0DER")
            marketplace_ids = [m.strip() for m in marketplaces_str.split(",") if m.strip()]

            self._stores[store_id] = {
                "store_id": store_id,
                "name": secrets.get("name", store_id),
                "marketplace_ids": marketplace_ids,
                "is_active": secrets.get("is_active", "true").lower() == "true",
                "has_sp_api": bool(secrets.get("lwa_client_id")),
                "has_walmart": bool(secrets.get("walmart_client_id")),
                "has_tiktok": bool(secrets.get("tiktok_shop_cipher")),
                # SP-API credentials (for engines that need them)
                "lwa_client_id": secrets.get("lwa_client_id", ""),
                "lwa_client_secret": secrets.get("lwa_client_secret", ""),
                "refresh_token": secrets.get("refresh_token", ""),
                "aws_access_key_id": secrets.get("aws_access_key_id", ""),
                "aws_secret_access_key": secrets.get("aws_secret_access_key", ""),
                "aws_iam_role_arn": secrets.get("aws_iam_role_arn", ""),
            }

        if not self._stores:
            logger.warning("No stores configured. Set STORE_* environment variables.")
        else:
            logger.info("Loaded %d stores: %s", len(self._stores), list(self._stores.keys()))

    @property
    def active_stores(self) -> Dict[str, dict]:
        return {sid: s for sid, s in self._stores.items() if s["is_active"]}

    @property
    def all_stores(self) -> Dict[str, dict]:
        return dict(self._stores)

    @staticmethod
    def get(store_id: str) -> Optional[dict]:
        """Static shorthand: get store by ID using the global registry."""
        from auth import get_store_registry
        return get_store_registry().get_store(store_id)

    def get_store(self, store_id: str) -> Optional[dict]:
        return self._stores.get(store_id)

    def get_endpoint(self, marketplace_id: str) -> str:
        region = MARKETPLACE_REGION.get(marketplace_id, "NA")
        return SP_API_ENDPOINTS[region]

    def is_valid_store(self, store_id: str) -> bool:
        return store_id in self._stores and self._stores[store_id]["is_active"]

    def list_stores(self) -> List[dict]:
        return list(self._stores.values())

"""
Authentication Layer — Public API
===================================
The rest of the system ONLY calls get_access_token(store_id).
No module outside auth/ directly manages tokens.

Factories:
    get_access_token(store_id) → str
    get_store_registry() → StoreRegistry
"""

import logging
from typing import Optional

from auth.token_manager import TokenManager, AuthFailureError
from auth.store_registry import StoreRegistry
from auth.aws_signer import AWSSigner
from config.secrets import SecretsLoader
from config.settings import Settings, settings as global_settings

logger = logging.getLogger("amazon.auth")

# ─── Singleton instances ───
_secrets: Optional[SecretsLoader] = None
_token_manager: Optional[TokenManager] = None
_store_registry: Optional[StoreRegistry] = None
_aws_signer: Optional[AWSSigner] = None


def _ensure_initialized():
    """Lazy initialization of auth singletons."""
    global _secrets, _token_manager, _store_registry, _aws_signer

    if _secrets is None:
        _secrets = SecretsLoader(mode=global_settings.secrets_mode)
        _token_manager = TokenManager(_secrets)
        _store_registry = StoreRegistry(_secrets, global_settings)
        _aws_signer = AWSSigner(_secrets)

        logger.info("Auth layer initialized. Stores: %s", list(_store_registry.active_stores.keys()))


async def get_access_token(store_id: str) -> str:
    """
    PUBLIC API: Get a valid LWA access token for a store.
    This is the ONLY way other modules access tokens.

    Args:
        store_id: Store identifier

    Returns:
        Valid access token string

    Raises:
        AuthFailureError: If credentials are missing or invalid
        ValueError: If store_id is not configured
    """
    _ensure_initialized()

    if not _store_registry.is_valid_store(store_id):
        available = list(_store_registry.all_stores.keys())
        raise ValueError(
            f"Store '{store_id}' is not configured or inactive. "
            f"Available stores: {available}"
        )

    return await _token_manager.get_access_token(store_id)


def get_store_registry() -> StoreRegistry:
    """Get the initialized StoreRegistry singleton."""
    _ensure_initialized()
    return _store_registry


def get_aws_signer() -> AWSSigner:
    """Get the initialized AWSSigner singleton."""
    _ensure_initialized()
    return _aws_signer


def get_token_manager() -> TokenManager:
    """Get the TokenManager singleton (for cache invalidation)."""
    _ensure_initialized()
    return _token_manager


def invalidate_token(store_id: str):
    """Force token refresh on next get_access_token call."""
    _ensure_initialized()
    _token_manager.invalidate(store_id)


async def close():
    """Clean up auth resources."""
    if _token_manager:
        await _token_manager.close()

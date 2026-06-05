"""
Secrets Loader
===============
Loads secrets from environment variables or AWS Secrets Manager.
Never hardcodes credentials.

Usage:
    secrets = SecretsLoader()
    client_id = secrets.get("store_1", "lwa_client_id")
"""

import os
import json
import logging
from typing import Dict, Optional

logger = logging.getLogger("amazon.config.secrets")


class SecretsLoader:
    """
    Loads store credentials from env or AWS Secrets Manager.

    Environment variable naming convention:
        STORE_{STORE_ID}_{KEY} = value

    Example:
        STORE_DEFAULT_LWA_CLIENT_ID = "amzn1..."
        STORE_DEFAULT_REFRESH_TOKEN = "Atzr|..."
        STORE_STORE_A_LWA_CLIENT_ID = "amzn1..."
    """

    def __init__(self, mode: str = "env"):
        self.mode = mode
        self._cache: Dict[str, Dict[str, str]] = {}

    def get(self, store_id: str, key: str) -> Optional[str]:
        """Get a secret for a store by key."""
        if store_id not in self._cache:
            self._load_store(store_id)
        return self._cache.get(store_id, {}).get(key)

    def get_all(self, store_id: str) -> Dict[str, str]:
        """Get all secrets for a store."""
        if store_id not in self._cache:
            self._load_store(store_id)
        return self._cache.get(store_id, {})

    def _load_store(self, store_id: str):
        """Load secrets for a single store."""
        if self.mode == "aws_secrets_manager":
            self._load_from_aws(store_id)
        else:
            self._load_from_env(store_id)

    def _load_from_env(self, store_id: str):
        """Load secrets from environment variables."""
        prefix = f"STORE_{store_id.upper()}_"
        prefix_short = f"STORE_{store_id}_"
        secrets = {}

        for env_key, env_val in os.environ.items():
            upper_key = env_key.upper()
            if upper_key.startswith(prefix) or upper_key.startswith(prefix_short):
                # STORE_DEFAULT_LWA_CLIENT_ID → lwa_client_id
                config_key = upper_key.replace(prefix.replace("_", ""), "", 1) if not upper_key.startswith(prefix_short) else upper_key.replace(prefix_short, "", 1)
                config_key = config_key.lower()
                secrets[config_key] = env_val

        # Also check for flat env vars with store prefix
        for simple_key in [
            "lwa_client_id", "lwa_client_secret", "refresh_token",
            "aws_iam_role_arn", "aws_access_key_id", "aws_secret_access_key",
        ]:
            env_name = f"STORE_{store_id.upper()}_{simple_key.upper()}"
            if env_name in os.environ:
                secrets[simple_key] = os.environ[env_name]

        if not secrets:
            logger.debug("No secrets found for store '%s' - env vars not set", store_id)

        self._cache[store_id] = secrets

    def _load_from_aws(self, store_id: str):
        """Load secrets from AWS Secrets Manager."""
        try:
            import boto3
            from botocore.exceptions import ClientError

            client = boto3.client("secretsmanager", region_name="us-east-1")
            secret_name = f"amazon-ops-intel/{store_id}"

            response = client.get_secret_value(SecretId=secret_name)
            if "SecretString" in response:
                self._cache[store_id] = json.loads(response["SecretString"])
                logger.info("Secrets loaded from AWS for store '%s'", store_id)
            else:
                logger.error("Secret '%s' is binary, not supported", secret_name)
                self._cache[store_id] = {}

        except ImportError:
            logger.error("boto3 not installed. Cannot load from AWS Secrets Manager.")
            self._cache[store_id] = {}
        except ClientError as e:
            logger.error("AWS Secrets Manager error for store '%s': %s", store_id, e)
            self._cache[store_id] = {}

    def store_ids_available(self) -> list:
        """Discover available store IDs from environment."""
        ids = set()
        for key in os.environ:
            upper = key.upper()
            if upper.startswith("STORE_") and any(
                suffix in upper for suffix in ["_LWA_CLIENT_ID", "_REFRESH_TOKEN", "_AWS_ACCESS_KEY"]
            ):
                parts = upper.split("_")
                if len(parts) >= 2:
                    ids.add(parts[1].lower())
        return sorted(ids) if ids else []

"""
Pydantic Settings — All configuration loaded from environment variables.
No hardcoded secrets. Never commit .env to version control.
"""

from typing import Dict, List, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class StoreConfig(BaseSettings):
    """Configuration for a single store loaded from env."""
    store_id: str
    name: str
    marketplace_ids: List[str] = ["ATVPDKIKX0DER"]  # US by default
    is_active: bool = True

    # LWA credentials (loaded from env / Secrets Manager)
    lwa_client_id: str = ""
    lwa_client_secret: str = ""
    refresh_token: str = ""

    # AWS IAM
    aws_iam_role_arn: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""

    # Walmart
    walmart_client_id: str = ""
    walmart_client_secret: str = ""

    # TikTok Shop
    tiktok_shop_cipher: str = ""
    tiktok_access_token: str = ""

    model_config = {"extra": "ignore"}


class Settings(BaseSettings):
    """Global application settings. All values from environment."""

    # ── Application ──
    app_name: str = "Amazon Ops Intel"
    log_level: str = "INFO"
    debug: bool = False

    # ── Database ──
    database_url: str = Field(
        default="postgresql+psycopg2://user:pass@localhost:5432/amazon_ops",
        description="PostgreSQL connection string",
    )
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # ── Redis ──
    redis_url: str = "redis://localhost:6379/0"

    # ── Data Lake ──
    data_lake_path: str = "./data_lake"
    data_lake_s3_bucket: str = ""

    # ── Secrets Management ──
    secrets_mode: str = Field(
        default="env",
        description="'env' or 'aws_secrets_manager'",
    )
    aws_secrets_prefix: str = "amazon-ops-intel/"

    # ── Alerting ──
    slack_webhook_url: str = ""
    alert_email_from: str = ""
    alert_email_to: str = ""

    # ── OpenClaw ──
    openclaw_api_key: str = ""
    openclaw_allow_auto: bool = False  # Level 1 auto actions

    # ── Stores — loaded dynamically ──
    # Store count
    store_count: int = 1

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    def get_store_ids(self) -> List[str]:
        """Return list of configured store IDs from env."""
        import os
        stores = []
        for key, val in os.environ.items():
            if key.startswith("STORE_") and key.endswith("_ID"):
                stores.append(val)
        return stores or ["default"]


settings = Settings()

"""
ETL — Base: Deduplicator & shared utilities.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger("amazon.etl.base")


def fingerprint(raw: Dict) -> str:
    """Create a deterministic hash of a raw data dict for dedup."""
    serialized = json.dumps(raw, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def map_sku_to_product(sku: str, session=None) -> Optional[str]:
    """Map seller SKU to global_product_id (stub — implement with Product table)."""
    return sku  # Default: SKU is the product ID

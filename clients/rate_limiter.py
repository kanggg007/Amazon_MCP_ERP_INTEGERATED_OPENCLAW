"""
Rate Limiter
=============
Prevents Amazon SP-API throttling.
Tracks request rates per endpoint per store.
Amazon limits vary: 10-40 req/s depending on endpoint.
"""

import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import Dict

logger = logging.getLogger("amazon.clients.rate_limiter")


class RateLimiter:
    """
    Token-bucket rate limiter per endpoint.

    Usage:
        limiter = RateLimiter()
        await limiter.acquire("orders/v0/orders")
    """

    def __init__(self, default_max_rps: int = 10):
        self.default_max_rps = default_max_rps
        self._buckets: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        self._limits: Dict[str, int] = {}

    def set_limit(self, endpoint: str, max_per_second: int):
        """Set a custom rate limit for a specific endpoint."""
        self._limits[endpoint] = max_per_second
        logger.debug("Rate limit set: %s → %d req/s", endpoint, max_per_second)

    async def acquire(self, endpoint: str):
        """
        Wait for a rate slot. Blocks if at capacity.

        Args:
            endpoint: API endpoint path or group name
        """
        max_rps = self._limits.get(endpoint, self.default_max_rps)
        bucket = self._buckets[endpoint]

        now = time.monotonic()
        window = 1.0

        # Prune entries older than 1 second
        while bucket and now - bucket[0] > window:
            bucket.popleft()

        if len(bucket) >= max_rps:
            # Need to wait
            wait_time = bucket[0] + window - now
            if wait_time > 0:
                logger.debug(
                    "Rate limit pause: %s — %.2fs (limit %d req/s)",
                    endpoint, wait_time, max_rps,
                )
                await asyncio.sleep(wait_time)
                # Recheck after sleeping
                return await self.acquire(endpoint)

        bucket.append(time.monotonic())

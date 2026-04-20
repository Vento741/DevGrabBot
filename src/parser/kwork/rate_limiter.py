"""Sliding-window rate limiter для Kwork API (портирован из kwork-mcp).

Паттерн: asyncio.Lock + deque timestamps.
Гарантирует не более `rps` запросов в секунду с burst-capacity.
"""

import asyncio
import logging
import random
import time
from collections import deque

logger = logging.getLogger(__name__)


class InProcessRateLimiter:
    """Sliding-window rate limiter для async-кода."""

    def __init__(self, rps: float = 2.0, burst: int = 5) -> None:
        self._rps = max(0.1, rps)
        self._burst = max(1, burst)
        self._window_sec = 1.0
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Заблокироваться пока не появится слот для запроса."""
        async with self._lock:
            while True:
                now = time.monotonic()
                # Убираем timestamps вне окна
                while self._timestamps and (now - self._timestamps[0]) > self._window_sec:
                    self._timestamps.popleft()

                if len(self._timestamps) < self._burst:
                    self._timestamps.append(now)
                    return

                # Ждём пока освободится слот
                wait_sec = self._window_sec - (now - self._timestamps[0])
                wait_sec = max(0.05, wait_sec) + random.uniform(0, 0.05)
                logger.debug("Rate limit hit: ждём %.3f сек", wait_sec)
                await asyncio.sleep(wait_sec)
